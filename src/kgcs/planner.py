"""Immutable `CurationPlan` generation. The core's output stage.

Stage 3 turns validated, resolved candidates into a `CurationPlan`: a frozen,
JSON-round-trippable description of *exactly what would happen* to the
canonical graph — which never executes here. The plan is the unit a later
transaction-aware executor (Plan 3) applies; Sprint 1 stops at producing it.

Only candidates the core is fully confident about become operations:

- route is `AUTO` (anything needing an LLM or a human is deferred, not
  planned), and
- the candidate maps to an operation type the v1 executor implements —
  `CREATE_IDENTITY` for an entity, `ATTACH_ASSERTION` for an attribute or
  relation. This mirrors exactly what the reference `MemoryGraphStore.apply`
  supports in Plan 1; the other five `CurationOperationType`s land in Plan 3.

`artifact` candidates are the deliberate gap: they validate and resolve, but
there is no artifact operation type in the contract, so they produce no
operation (see `llm/governance/adr/candidates/0001-artifact-has-no-operation-type.md`).

Determinism is structural, not incidental. A `CurationPlan` carries **no
timestamp** — every field is a pure function of the input candidates and the
injected `IdFactory`. So the planner needs no clock, and two plans over the
same resolved candidates are byte-for-byte equal (verified by round-tripping
through `model_dump_json`). The planner holds no mutable state and copies
nothing in place; it only reads frozen inputs and constructs frozen outputs
(the "planner never mutates state" invariant).

Three guarantees keep an emitted plan applicable:

- A `CREATE_IDENTITY` carries an `entity_version`/`expected="0"` precondition:
  the minted identity must not already exist. `MemoryGraphStore` enforces it.
- An `ATTACH_ASSERTION` carries the symmetric guard for the record *it* mints:
  an `assertion_absent` precondition naming the subject identity and the
  assertion id, i.e. "this assertion must not already be on this subject."
  Read-free, because the planner minted that assertion id itself. `PlanExecutor`
  enforces it (ADR-0019); the reference store ignores unknown kinds.
- A plan-level `snapshot_version` precondition records the graph snapshot the
  plan was computed against. The Plan-1 *store* does not enforce non-
  `entity_version` preconditions, but the guard is part of the plan's
  immutable provenance and `PlanExecutor` enforces it against the graph epoch.
"""

from dataclasses import dataclass
from typing import Sequence

from kg_contracts.assertions import Assertion, CanonicalEntity, CurationStatus
from kg_contracts.candidates import (
    AttributeAssertionCandidate,
    Candidate,
    EntityCandidate,
    RelationCandidate,
)
from kg_contracts.curation import (
    CurationOperation,
    CurationOperationType,
    CurationPlan,
    Precondition,
    ResolutionDecision,
)
from kg_contracts.evidence import Provenance, ValidPeriod
from kg_contracts.policy import AdjudicationRoute

from kgcs.ids import DerivedIdFactory, IdFactory
from kgcs.policy import DEFAULT_SNAPSHOT_VERSION
from kgcs.records import fact_key, record_seed

SNAPSHOT_PRECONDITION_KIND = "snapshot_version"
ENTITY_VERSION_PRECONDITION_KIND = "entity_version"
#: Per-subject guard on an `ATTACH_ASSERTION` (ADR-0019). `subject` is the
#: assertion's subject identity — the same field shape `entity_version` uses —
#: and `expected` is the `assertion_id` that must **not** already be attached
#: to it. The `kind` supplies the polarity: absence, not a version match.
ASSERTION_ABSENT_PRECONDITION_KIND = "assertion_absent"
DEFAULT_POLICY_VERSION = "1"

#: The `reversal_data` key holding the *inverse operation's payload*.
#:
#: `reversal_data` carries two different things, and conflating them is a bug
#: (ADR-0018): the payload the reversing operation needs, and the lineage /
#: provenance of the forward operation (`candidate_id`, `trigger_id`,
#: `evidence_ids`, matcher/adviser/policy versions). `kgcs.executor.compensate`
#: reads the payload from this key; everything else in the dict stays lineage
#: and never reaches an operation payload. Defined here, with the other plan
#: vocabulary, because both producers (this planner and
#: `kgcs.recuration.evolution`) and the consumer need it and the consumer
#: already imports from this module.
INVERSE_PAYLOAD_KEY = "inverse_payload"


@dataclass(frozen=True)
class ResolvedCandidate:
    """A validated candidate paired with its `ResolutionDecision`.

    The planner's unit of input: it needs the candidate for the operation's
    content and the decision for the identity disposition and route.
    """

    candidate: Candidate
    resolution: ResolutionDecision


@dataclass(frozen=True)
class PlannedOperation:
    """One built operation with the provenance the audit stage needs.

    Links an emitted `CurationOperation` back to the candidate and decision
    that produced it — the operation↔candidate mapping the audit recorder
    uses to attribute each audit record (the contract's `AuditRecord` keys on
    `operation_id`, not `candidate_id`).
    """

    operation: CurationOperation
    candidate: Candidate
    resolution: ResolutionDecision


@dataclass(frozen=True)
class PlanResult:
    """The planner's output: the plan plus its operation provenance.

    `plan` is `None` when no candidate produced an operation (nothing auto-
    applies), which keeps the contract's `candidate_ids`/`operations`
    non-empty invariants satisfiable — an empty plan is expressed as *no
    plan*, never a plan with zero operations.
    """

    plan: CurationPlan | None
    planned_operations: tuple[PlannedOperation, ...]


class CurationPlanner:
    """Builds an immutable `CurationPlan` from resolved candidates.

    Injected only with an `IdFactory` (deterministic by default) and the
    snapshot/policy versions to stamp. Pure and stateless: `plan()` reads
    frozen inputs and returns frozen outputs, mutating nothing.
    """

    def __init__(
        self,
        *,
        id_factory: IdFactory | None = None,
        snapshot_version: str = DEFAULT_SNAPSHOT_VERSION,
        policy_version: str = DEFAULT_POLICY_VERSION,
    ) -> None:
        self._ids = id_factory or DerivedIdFactory()
        self._snapshot_version = snapshot_version
        self._policy_version = policy_version

    def plan(self, resolved: Sequence[ResolvedCandidate]) -> PlanResult:
        """Compile resolved candidates into a `PlanResult`.

        Preserves input order throughout: operations, `candidate_ids`,
        preconditions, and `evidence_ids` all follow the order candidates
        were supplied, so the plan is reproducible.
        """
        planned: list[PlannedOperation] = []
        for item in resolved:
            operation = self._build_operation(item.candidate, item.resolution)
            if operation is not None:
                planned.append(
                    PlannedOperation(
                        operation=operation,
                        candidate=item.candidate,
                        resolution=item.resolution,
                    )
                )

        if not planned:
            return PlanResult(plan=None, planned_operations=())

        operations = tuple(p.operation for p in planned)
        candidate_ids = tuple(p.candidate.candidate_id for p in planned)
        graph_id = planned[0].candidate.graph_id
        plan = CurationPlan(
            plan_id=self._ids.plan_id(self._plan_seed(candidate_ids)),
            candidate_ids=candidate_ids,
            snapshot_version=self._snapshot_version,
            operations=operations,
            preconditions=self._preconditions(planned, graph_id),
            evidence_ids=self._evidence_ids(planned),
            policy_version=self._policy_version,
        )
        return PlanResult(plan=plan, planned_operations=tuple(planned))

    # --- operation building ---------------------------------------------------

    def _build_operation(
        self, candidate: Candidate, resolution: ResolutionDecision
    ) -> CurationOperation | None:
        """The one operation this candidate would apply, or `None` to defer.

        Anything not routed `AUTO` is deferred (no operation). Beyond that,
        dispatch is by concrete candidate type; a kind with no v1 operation
        (artifact, or any candidate whose content cannot form a valid
        operation) yields `None`.
        """
        if resolution.route is not AdjudicationRoute.AUTO:
            return None
        if isinstance(candidate, EntityCandidate):
            return self._create_identity(candidate, resolution)
        if isinstance(candidate, AttributeAssertionCandidate):
            return self._attach_attribute(candidate, resolution)
        if isinstance(candidate, RelationCandidate):
            return self._attach_relation(candidate, resolution)
        return None

    def _create_identity(
        self, candidate: EntityCandidate, resolution: ResolutionDecision
    ) -> CurationOperation | None:
        """A `CREATE_IDENTITY` for a new entity identity.

        The identity id was minted by the resolution stage and travels here
        via `resolved_identity`; the planner never mints a second one. Entity
        `properties` are not materialized in v1 — an identity is created as a
        shell, and its properties would become attribute assertions later.

        `reversal_data` carries a `REVOKE_IDENTITY` payload under
        `INVERSE_PAYLOAD_KEY`, which is what makes this operation compensable
        (KGIS ADR-0025). Before that type existed there was no inverse to
        carry and the field held lineage alone.
        """
        identity_id = resolution.resolved_identity
        if identity_id is None or not resolution.create_new_identity:
            return None
        entity = CanonicalEntity(
            identity_id=identity_id,
            entity_type=candidate.entity_type,
            aliases=candidate.aliases,
            status=CurationStatus.ACTIVE,
            display_name=candidate.display_name,
            created_at=candidate.created_at,
            curation_epoch=0,
        )
        operation_id = self._ids.operation_id(
            self._op_seed(candidate, CurationOperationType.CREATE_IDENTITY)
        )
        return CurationOperation(
            operation_id=operation_id,
            type=CurationOperationType.CREATE_IDENTITY,
            payload=_entity_payload(entity),
            reversal_data={
                INVERSE_PAYLOAD_KEY: revoke_inverse_payload(identity_id, operation_id),
                "identity_id": identity_id,
                "candidate_id": candidate.candidate_id,
            },
        )

    def _attach_attribute(
        self, candidate: AttributeAssertionCandidate, resolution: ResolutionDecision
    ) -> CurationOperation | None:
        """An `ATTACH_ASSERTION` carrying an attribute value.

        Requires a known subject and a non-null value — an attribute whose
        value is `None` cannot form a valid `Assertion` (exactly one of
        object_value/object_identity must be set), so it is deferred rather
        than forced.
        """
        subject_identity = resolution.resolved_identity
        if subject_identity is None or candidate.value is None:
            return None
        assertion = self._assertion(
            candidate=candidate,
            subject_identity=subject_identity,
            predicate=candidate.attribute,
            object_value=candidate.value,
            object_identity=None,
            valid_period=candidate.valid_period,
        )
        return self._attach_operation(candidate, subject_identity, assertion)

    def _attach_relation(
        self, candidate: RelationCandidate, resolution: ResolutionDecision
    ) -> CurationOperation | None:
        """An `ATTACH_ASSERTION` linking subject to object identity.

        Both endpoints are guaranteed to be minted identity ids here: the
        resolution stage escalated the route away from `AUTO` for any
        relation whose subject or object still needed resolution, so a
        relation reaching this point with `AUTO` has both.
        """
        subject_identity = resolution.resolved_identity
        object_identity = candidate.object
        if subject_identity is None or not isinstance(object_identity, str):
            return None
        assertion = self._assertion(
            candidate=candidate,
            subject_identity=subject_identity,
            predicate=candidate.relation_type,
            object_value=None,
            object_identity=object_identity,
            valid_period=candidate.valid_period,
        )
        return self._attach_operation(candidate, subject_identity, assertion)

    def _assertion(
        self,
        *,
        candidate: Candidate,
        subject_identity: str,
        predicate: str,
        object_value: object | None,
        object_identity: str | None,
        valid_period: ValidPeriod | None,
    ) -> Assertion:
        """Build the `Assertion` an `ATTACH_ASSERTION` carries.

        Fields are derived deterministically from the candidate: `recorded_at`
        (transaction time) from the candidate's own `created_at`; `authority`
        and `provenance` from the producer and source coordinates (Sprint 1
        has no separate authority model — see the ADR candidate on authority
        provenance). `assertion_id` is pinned via the `IdFactory` so the plan
        is fully deterministic rather than defaulting to a random ULID.

        `assertion_id` is a **record** id, not a fact id (ADR-0021). It is
        minted from `records.record_seed` over the fact this candidate asserts
        — `fact_key(subject, predicate)` — plus the record-distinguishing
        content: the asserted object, the valid period, and the cited evidence.
        It was `f"{candidate.candidate_id}:assertion"`, which made the record
        id a pure function of the *fact* id, so the same fact re-asserted with
        new evidence could not mint a second record and the three routes to
        evidence evolution all lost data (module `kgcs.records`). Still
        clock-free and still a pure function of the candidate, so an identical
        candidate replays to an identical plan.
        """
        return Assertion(
            assertion_id=self._ids.assertion_id(
                record_seed(
                    fact_id=fact_key(subject_identity, predicate),
                    object_value=object_value,
                    object_identity=object_identity,
                    valid_period=valid_period,
                    evidence_refs=candidate.evidence_refs,
                )
            ),
            subject_identity=subject_identity,
            predicate=predicate,
            object_value=object_value,
            object_identity=object_identity,
            status=CurationStatus.ACTIVE,
            valid_period=valid_period if valid_period is not None else ValidPeriod(),
            recorded_at=candidate.created_at,
            superseded_at=None,
            scores=candidate.scores,
            evidence_refs=candidate.evidence_refs,
            authority=candidate.producer,
            provenance=Provenance(
                source=candidate.source_coordinates.source_type,
                source_ref=candidate.source_coordinates.locator,
                actor=candidate.producer,
            ),
            derivation=None,
            curation_epoch=0,
            trace_id=candidate.trace_id,
        )

    def _attach_operation(
        self, candidate: Candidate, subject_identity: str, assertion: Assertion
    ) -> CurationOperation:
        return CurationOperation(
            operation_id=self._ids.operation_id(
                self._op_seed(candidate, CurationOperationType.ATTACH_ASSERTION)
            ),
            type=CurationOperationType.ATTACH_ASSERTION,
            payload=_assertion_payload(assertion),
            reversal_data={
                INVERSE_PAYLOAD_KEY: retract_inverse_payload(assertion, subject_identity),
                "candidate_id": candidate.candidate_id,
            },
        )

    # --- plan assembly --------------------------------------------------------

    def _preconditions(
        self, planned: Sequence[PlannedOperation], graph_id: str
    ) -> tuple[Precondition, ...]:
        """The plan's optimistic-concurrency guards, in a fixed order.

        The snapshot guard comes first (the whole plan was computed against
        one snapshot), then one per-subject guard per operation, in operation
        order:

        - `CREATE_IDENTITY` → `entity_version=0`: the minted identity must not
          already exist, so a create refuses to clobber an id that is there.
        - `ATTACH_ASSERTION` → `assertion_absent`: the minted assertion id must
          not already be attached to this subject, so a replayed attach is
          refused instead of silently duplicating the record (ADR-0019).

        Both guards are read-free and *symmetric*: each names the record this
        operation would mint and requires it to be absent. Neither is a guard
        on the subject's current version, which would need the graph read the
        deterministic core does not do (ADR candidate 0003) and would also
        refuse any unrelated concurrent change to the subject.
        """
        preconditions: list[Precondition] = [
            Precondition(
                kind=SNAPSHOT_PRECONDITION_KIND,
                subject=graph_id,
                expected=self._snapshot_version,
            )
        ]
        for item in planned:
            if item.operation.type is CurationOperationType.CREATE_IDENTITY:
                identity_id = item.resolution.resolved_identity
                if identity_id is not None:
                    preconditions.append(
                        Precondition(
                            kind=ENTITY_VERSION_PRECONDITION_KIND,
                            subject=identity_id,
                            expected="0",
                        )
                    )
            elif item.operation.type is CurationOperationType.ATTACH_ASSERTION:
                guard = _assertion_absent_guard(item.operation)
                if guard is not None:
                    preconditions.append(guard)
        return tuple(preconditions)

    def _evidence_ids(self, planned: Sequence[PlannedOperation]) -> tuple[str, ...]:
        """Evidence ids justifying the plan: first-seen order, deduplicated."""
        seen: set[str] = set()
        ordered: list[str] = []
        for item in planned:
            for ref in item.candidate.evidence_refs:
                if ref.evidence_id not in seen:
                    seen.add(ref.evidence_id)
                    ordered.append(ref.evidence_id)
        return tuple(ordered)

    def _plan_seed(self, candidate_ids: tuple[str, ...]) -> str:
        return "|".join(candidate_ids) + f"@{self._snapshot_version}#{self._policy_version}"

    def _op_seed(self, candidate: Candidate, op_type: CurationOperationType) -> str:
        return f"{candidate.candidate_id}:{op_type.value}"


def assertion_absent_guard(
    subject_identity: str, assertion_id: str
) -> Precondition:
    """Build the `assertion_absent` guard for one attach (ADR-0019).

    Paired with `read_assertion_absent_guard`: between them they are the only
    two places that decide which `Precondition` field holds the subject and
    which holds the assertion id. Writers call this, the enforcing executor
    calls the reader, and neither re-derives the field roles positionally —
    which is what makes "producers and the enforcer cannot disagree" a fact
    about the code rather than a hope.
    """
    return Precondition(
        kind=ASSERTION_ABSENT_PRECONDITION_KIND,
        subject=subject_identity,
        expected=assertion_id,
    )


def read_assertion_absent_guard(precondition: Precondition) -> tuple[str, str]:
    """Read an `assertion_absent` guard back as `(subject_identity, assertion_id)`.

    The inverse of `assertion_absent_guard`, and the only supported way to
    interpret one. Raises `ValueError` for any other kind, so a caller cannot
    quietly read a `snapshot_version` or `entity_version` guard through it and
    get a plausible-looking pair of strings back.
    """
    if precondition.kind != ASSERTION_ABSENT_PRECONDITION_KIND:
        raise ValueError(
            f"not an {ASSERTION_ABSENT_PRECONDITION_KIND} precondition: "
            f"kind={precondition.kind!r}"
        )
    return precondition.subject, precondition.expected


def _assertion_absent_guard(operation: CurationOperation) -> Precondition | None:
    """Read the attach guard's two coordinates off the operation's payload.

    Returns `None` only if the payload is not a well-formed assertion (no
    string `subject_identity`/`assertion_id`) — impossible for an operation
    this planner built, but the payload type is `dict[str, object]`, so the
    narrowing is explicit rather than an unchecked cast.
    """
    subject = operation.payload.get("subject_identity")
    assertion_id = operation.payload.get("assertion_id")
    if not isinstance(subject, str) or not isinstance(assertion_id, str):
        return None
    return assertion_absent_guard(subject, assertion_id)


def _entity_payload(entity: CanonicalEntity) -> dict[str, object]:
    """A JSON-native `CREATE_IDENTITY` payload, minus the executor-owned epoch.

    `curation_epoch` is assigned atomically by the executor at apply time
    (`MemoryGraphStore.apply`), so the plan omits it rather than pinning a
    placeholder. `mode="json"` gives primitive types, keeping the plan's
    `model_dump_json` round trip exact.
    """
    payload = entity.model_dump(mode="json")
    payload.pop("curation_epoch", None)
    return payload


def _assertion_payload(assertion: Assertion) -> dict[str, object]:
    """A JSON-native `ATTACH_ASSERTION` payload, minus the executor-owned epoch."""
    payload = assertion.model_dump(mode="json")
    payload.pop("curation_epoch", None)
    return payload


def revoke_inverse_payload(identity_id: str, created_by_operation_id: str) -> dict[str, object]:
    """The `REVOKE_IDENTITY` payload that undoes creating `identity_id`.

    **An identity reference, not an entity dump** (KGIS ADR-0025). The executor
    revokes the entity actually in the graph, so a stale copy carried in the
    plan cannot overwrite it; the pre-revoke entity travels instead in the
    compensating operation's own `reversal_data`, which is what makes the
    revoke compensable by a `CREATE_IDENTITY` in turn. `_entity_payload` — the
    forward payload — is that entity dump, and `Compensator._invert` already
    puts it there generically, so this function deliberately does *not*
    duplicate it.

    `reason` is optional in the contract and supplied here because a tombstone
    with no stated cause is an audit dead end. It names the operation being
    undone rather than a timestamp or a run id: the compensator is pure and
    holds no clock, so every field must be a function of the plan alone or a
    replayed compensation would not be byte-identical.
    """
    return {
        "identity_id": identity_id,
        "reason": f"rollback of CREATE_IDENTITY {created_by_operation_id}",
    }


def retract_inverse_payload(assertion: Assertion, subject_identity: str) -> dict[str, object]:
    """The `RETRACT_ASSERTION` payload that undoes attaching `assertion`.

    Four of the five keys a forward supersession emits — `assertion_id`,
    `subject_identity`, `new_status`, `superseded_at` — so a store applies a
    compensating retract through exactly the path it applies a planned one.
    The fifth, `superseded_by`, is deliberately absent: a rollback has no
    superseding assertion, and naming one would be a lie. "The same shape"
    would overclaim; what holds is that every field the operation is *defined*
    by is present, and the only omission is the one that does not apply.
    Before ADR-0018 the inverse carried only the two identifiers, and a store
    had to invent the rest (the E2E harness substituted a fixed instant); a
    reversal that loses the fields the operation is defined by is a lossy
    inverse, not a rollback.

    Two deliberate choices, both recorded in ADR-0018:

    - **`new_status=SUPERSEDED`, not `REVOKED`.** `REVOKED` is the semantically
      purer reading of "this attachment is withdrawn" — nothing superseded it.
      ADR-0018 chose `SUPERSEDED` because the canonical read surface then hid
      only `SUPERSEDED` by default, so a record marked `REVOKED` would have
      stayed visible to an ordinary read — the opposite of a rollback — and
      said the fix was "a read-semantics ADR upstream, not a decision to
      smuggle in here."

      **That ADR has since happened:** KGIS ADR-0025 adds
      `GraphReadOptions.include_revoked` and hides `REVOKED` by default, so the
      obstacle ADR-0018 named is gone and the purer reading is now available.
      Switching this to `REVOKED` is deliberately **not** done here: it would
      change the observable behaviour of an ADR-0018 decision the owner has
      not finished reviewing, and it is a KGCS-local durable decision in its
      own right. Filed as an open question on this PR, not smuggled in either.
    - **`superseded_at` is the assertion's own `recorded_at`.** The compensator
      is pure and holds no clock (a replayed compensation must be
      byte-identical), so the instant must come from the plan. Closing the
      record's transaction-time interval at the instant it opened is the exact
      bitemporal statement a rollback makes: as of any query time, this record
      was never validly live. History is preserved, not rewritten (§9 law 10).
    """
    return {
        "assertion_id": assertion.assertion_id,
        "subject_identity": subject_identity,
        "new_status": CurationStatus.SUPERSEDED.value,
        "superseded_at": assertion.recorded_at.isoformat(),
    }
