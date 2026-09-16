"""Deterministic candidate validation → `ValidationDecision`.

This is the first stage of the curation core and the cheapest gate in the
whole system (governance principle 1: "deterministic admission checks are
cheap and unbypassable"). A `Candidate` arriving here is already
*structurally* valid — it is a parsed `kg_contracts` model, so its fields,
types, and cross-field invariants held at construction. Validation here is
the next layer: is this well-formed candidate one this curation core will
actually act on, in this graph, against this contract?

The design mirrors `kgis.validate`'s composite discipline: small
single-purpose `ValidationRule`s run in a fixed order, each returning zero or
more `RuleViolation`s, and `RuleBasedValidator` folds them into one
`ValidationDecision`. Determinism falls out of three choices:

- rules run in a fixed order, so `reasons` is always assembled the same way;
- when several rules fail, `failure_kind` is chosen by a fixed severity order
  (most *permanent* wins), never by which rule happened to run first — a
  candidate with any permanent defect is never mislabeled retryable;
- nothing here reads a clock, a graph, or a random source.

What is deliberately **not** a validation concern: whether a candidate's
subject can be resolved to an identity. An `EntityRef` subject that needs
entity resolution is a perfectly *valid* candidate — it simply cannot be
auto-applied, which is a routing decision the policy stage makes, not a
rejection. Validation answers "should this enter curation at all", not "can
we act on it without a human".
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from kg_contracts.candidates import IMPLEMENTED_KINDS, Candidate
from kg_contracts.curation import FailureKind, ValidationDecision
from kg_contracts.versioning import CONTRACT_VERSION

from kgcs.ids import is_well_formed_graph_id

DEFAULT_VALIDATION_POLICY_VERSION = "1"

# Fixed severity order for picking one `failure_kind` when a candidate trips
# several rules. Higher = more severe. Both permanent kinds outrank the
# transient one so a candidate that is *also* permanently broken is never
# reported as retryable; the two permanent kinds are ordered arbitrarily but
# stably so the choice is reproducible.
_SEVERITY: dict[FailureKind, int] = {
    FailureKind.BAD_DATA: 2,
    FailureKind.UNSUPPORTED_ONTOLOGY: 1,
    FailureKind.TRANSIENT_FAULT: 0,
}


@dataclass(frozen=True)
class RuleViolation:
    """One reason a candidate failed a rule: its `FailureKind` and an
    explanation. `reason` is human-readable and lands verbatim in
    `ValidationDecision.reasons`."""

    failure_kind: FailureKind
    reason: str


@runtime_checkable
class ValidationRule(Protocol):
    """One deterministic check over a candidate.

    Returns every violation it finds (empty tuple = passed). A rule must be a
    pure function of the candidate: no clock, no graph, no randomness.
    """

    def check(self, candidate: Candidate) -> tuple[RuleViolation, ...]: ...


@runtime_checkable
class CandidateValidator(Protocol):
    """Turns a candidate into a `ValidationDecision`. The stage-1 port."""

    def validate(self, candidate: Candidate) -> ValidationDecision: ...


class SupportedKindRule:
    """Reject the five spec-level candidate kinds not implemented in v1.

    The nine-variant union is contract-complete, but only four kinds
    (`IMPLEMENTED_KINDS`: entity, relation, attribute_assertion, artifact)
    have a v1 curation path. The other five are well-formed contracts with no
    wired producer, so they are rejected as `UNSUPPORTED_ONTOLOGY` — "defined
    but not implemented", never a data error (`kg_contracts.candidates`).
    """

    def check(self, candidate: Candidate) -> tuple[RuleViolation, ...]:
        if candidate.candidate_kind in IMPLEMENTED_KINDS:
            return ()
        return (
            RuleViolation(
                failure_kind=FailureKind.UNSUPPORTED_ONTOLOGY,
                reason=(
                    f"candidate_kind {candidate.candidate_kind!r} is defined in the "
                    "contract but not implemented by the v1 curation core"
                ),
            ),
        )


class GraphIdWellFormedRule:
    """Reject candidates whose `graph_id` cannot anchor a canonical identity.

    A malformed `graph_id` is `BAD_DATA`: the planner could not mint a
    `kg://<graph_id>/identity/<ulid>` for it even if every score were
    perfect, so it must never reach the planner.
    """

    def check(self, candidate: Candidate) -> tuple[RuleViolation, ...]:
        if is_well_formed_graph_id(candidate.graph_id):
            return ()
        return (
            RuleViolation(
                failure_kind=FailureKind.BAD_DATA,
                reason=f"graph_id {candidate.graph_id!r} is not a well-formed graph id",
            ),
        )


class GraphScopeRule:
    """Reject candidates addressed to a different graph than this engine curates.

    Only wired when the engine is bound to a single `graph_id`. A candidate
    for another graph is `BAD_DATA` in this engine's context — curating it
    here would silently write cross-graph.
    """

    def __init__(self, graph_id: str) -> None:
        self._graph_id = graph_id

    def check(self, candidate: Candidate) -> tuple[RuleViolation, ...]:
        if candidate.graph_id == self._graph_id:
            return ()
        return (
            RuleViolation(
                failure_kind=FailureKind.BAD_DATA,
                reason=(
                    f"candidate graph_id {candidate.graph_id!r} does not match this "
                    f"engine's graph_id {self._graph_id!r}"
                ),
            ),
        )


class ContractVersionRule:
    """Reject candidates built against a different contract version.

    Fail-closed (governance principle 3): a candidate whose `contract_version`
    disagrees with the core's cannot be assumed field-compatible, so it is
    `BAD_DATA` rather than curated on optimistic faith. Candidates default
    their `contract_version` to `CONTRACT_VERSION`, so a match is the normal
    case; this rule catches stale or foreign producers.
    """

    def __init__(self, expected: str = CONTRACT_VERSION) -> None:
        self._expected = expected

    def check(self, candidate: Candidate) -> tuple[RuleViolation, ...]:
        if candidate.contract_version == self._expected:
            return ()
        return (
            RuleViolation(
                failure_kind=FailureKind.BAD_DATA,
                reason=(
                    f"candidate contract_version {candidate.contract_version!r} does not "
                    f"match the curation core's {self._expected!r}"
                ),
            ),
        )


class ProducerPresentRule:
    """Reject candidates with a blank `producer`.

    `CandidateEnvelope.producer` has no length constraint, but the planner
    copies it into `Assertion.authority` (spec §7.5), which requires
    `min_length=1`. An `AUTO`-routed attribute/relation candidate with an
    empty producer would therefore pass validation and then raise *inside*
    the planner when it builds the assertion — an exception where governance
    principle 3 demands data ("rejections are data; exceptions are bugs").
    This rule turns that latent crash into an ordinary `BAD_DATA` rejection
    at the cheap admission gate, before policy or the planner ever run.
    """

    def check(self, candidate: Candidate) -> tuple[RuleViolation, ...]:
        if candidate.producer.strip():
            return ()
        return (
            RuleViolation(
                failure_kind=FailureKind.BAD_DATA,
                reason="producer is blank; a candidate must name its producer",
            ),
        )


class RuleBasedValidator:
    """Folds a fixed sequence of `ValidationRule`s into one `ValidationDecision`.

    Rules run in construction order; `reasons` preserves that order. When any
    rule fails, `failure_kind` is the most severe kind seen (`_SEVERITY`),
    and the decision is `valid=False`. With no violations the decision is
    `valid=True` with empty `reasons` and no `failure_kind`, exactly as the
    contract requires.
    """

    def __init__(
        self,
        rules: tuple[ValidationRule, ...],
        *,
        policy_version: str = DEFAULT_VALIDATION_POLICY_VERSION,
    ) -> None:
        self._rules = rules
        self._policy_version = policy_version

    @property
    def policy_version(self) -> str:
        """The validation-rules version stamped onto every decision."""
        return self._policy_version

    def validate(self, candidate: Candidate) -> ValidationDecision:
        violations: list[RuleViolation] = []
        for rule in self._rules:
            violations.extend(rule.check(candidate))

        if not violations:
            return ValidationDecision(
                candidate_id=candidate.candidate_id,
                valid=True,
                failure_kind=None,
                reasons=(),
                policy_version=self._policy_version,
                trace_id=candidate.trace_id,
            )

        failure_kind = max(
            (v.failure_kind for v in violations), key=lambda kind: _SEVERITY[kind]
        )
        return ValidationDecision(
            candidate_id=candidate.candidate_id,
            valid=False,
            failure_kind=failure_kind,
            reasons=tuple(v.reason for v in violations),
            policy_version=self._policy_version,
            trace_id=candidate.trace_id,
        )


def default_validator(
    *,
    graph_id: str | None = None,
    contract_version: str = CONTRACT_VERSION,
    policy_version: str = DEFAULT_VALIDATION_POLICY_VERSION,
) -> RuleBasedValidator:
    """Assemble the v1 default validator.

    Always checks supported kind, well-formed graph id, producer presence,
    and contract version. Adds a `GraphScopeRule` only when `graph_id` is
    given (an engine bound to one graph). Rule order is fixed, so the
    resulting validator is deterministic and its `reasons` ordering is stable.
    """
    rules: list[ValidationRule] = [
        SupportedKindRule(),
        GraphIdWellFormedRule(),
        ProducerPresentRule(),
    ]
    if graph_id is not None:
        rules.append(GraphScopeRule(graph_id))
    rules.append(ContractVersionRule(contract_version))
    return RuleBasedValidator(tuple(rules), policy_version=policy_version)
