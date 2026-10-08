"""The semantic curation audit: a third, decision-lineage audit object (Wave 7).

Spec §7.8 ("audit is training/eval data"), §10.1, §9 laws 9 & 17, ADR
candidates 0002 and 0012. This module is the "semantic curation audit sink"
those candidates predicted would land in this wave.

There are now **three distinct audit objects**, deliberately not conflated:

- `kg_contracts.curation.AuditRecord` — *operation-scoped* (one per planned
  operation; keys on `operation_id`), built by `kgcs.audit.AuditRecorder`;
- `kgcs.executor.ExecutionRecord` — *execution-scoped* (one per apply attempt;
  keys on `plan_id`), built by `kgcs.executor.PlanExecutor`;
- `SemanticAuditRecord` (here) — *decision-scoped*: the full DECISION lineage
  for one curation decision, joined to the other two by `trace_id` / `plan_id` /
  candidate refs, never by embedding or replacing them.

The linkage is by id, not by containment: a `SemanticAuditRecord` carries the
`trace_id` that flows trigger → resolution → adviser → review → plan →
commit/compensation, the `plan_id` of the resulting plan, and compact refs to
the review outcome and the `ExecutionRecord` — so an auditor can join across the
three streams without any one being authoritative over another (ADR candidate
0002's trace-join direction, now built on top of it rather than mutating the
frozen contract).

**Decision timestamp (issue #48).** A `SemanticAuditRecord` now carries its own
`recorded_at`, read from an injected `Clock` by the builder — a decision audit
must say *when* the decision was made to be joinable against the operation and
execution streams, which both carry timestamps. Like every other clocked field
in the core it is injected, so a `FixedClock` keeps the record a pure function
of its inputs and a replay assembles a byte-identical record. The *content*
(ids, decisions, versions) remains clock-free; only `recorded_at` moves.

**Assertion and re-curation decisions (issue #48).** The ER-decision record has
a sibling, `AssertionSemanticAuditRecord`, with the same decision-lineage shape
(baseline, adviser assessments, final, plan_id, versions, replay inputs) for the
assertion / concept-evolution path (`kgcs.recuration.evolution`). It is produced
by the same `SemanticAuditBuilder` (via `build_assertion`) and recorded through
the same `SemanticAuditSink`, so the two decision families are one stream joined
by `trace_id` / `plan_id` / assertion refs.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from kg_contracts.assertions import Assertion
from kg_contracts.identity import IdentityLinkKind
from pydantic import BaseModel, ConfigDict, Field

from kgcs.advisers.base import AdviserAssessment
from kgcs.advisers.orchestrator import OrchestrationResult
from kgcs.advisers.specialists import AssertionRecommendation
from kgcs.clock import Clock, SystemClock
from kgcs.er.cluster import ClusterValidation
from kgcs.er.matcher import MatchResult
from kgcs.er.resolution import ErDecision
from kgcs.executor.executor import ExecutionOutcome, ExecutionRecord
from kgcs.ids import DerivedIdFactory, IdFactory
from kgcs.profiles import CurationProfile
from kgcs.recuration.triggers import CurationTrigger
from kgcs.review.operations import ReviewOutcome

DEFAULT_POLICY_VERSION = "1"
"""The confidence/routing policy version stamped when none is supplied. Mirrors
`kgcs.audit.DEFAULT_POLICY_VERSION`: the contract's `ResolutionDecision` carries
no policy version, so it is supplied here (ADR candidate 0002)."""


class DecisionKind(StrEnum):
    """Which decision family a semantic audit record belongs to.

    A discriminant on the stored record so a durable reader can reconstruct the
    concrete type (a `union` of records is persisted as JSON, and JSON carries no
    class). `ER` is an entity-resolution decision (`SemanticAuditRecord`);
    `ASSERTION` is an assertion / concept-evolution decision
    (`AssertionSemanticAuditRecord`).
    """

    ER = "ER"
    ASSERTION = "ASSERTION"


class EvolutionDecision(BaseModel):
    """An assertion / concept-evolution decision, reduced to its auditable shape.

    The assertion analogue of `ErDecision`: the evolution-spine decision is
    `EvolutionResult` (`kgcs.recuration.evolution`), a dataclass carrying a full
    `CurationPlan`. The audit records the *decision*, not the plan — the kind,
    the rationale, whether it routed to review, whether it preserved both sides
    as a conflict, the resulting `plan_id`, and the assertion ids it touched —
    linked to the plan by id, exactly as `SemanticAuditRecord` links to its plan.
    `kind` is the `EvolutionKind` *value* (a string, so this module does not
    depend on the re-curation package at import time).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    rationale: str = ""
    review_required: bool = False
    conflict: bool = False
    plan_id: str | None = None
    assertion_ids: tuple[str, ...] = ()

    @classmethod
    def deterministic_baseline(cls, kind: str, *, rationale: str) -> "EvolutionDecision":
        """The conservative core decision *without* adviser input.

        The auto re-curation path, absent advice, preserves both competing
        assertions and routes to review (§9 law 1 / law 10). This is the baseline
        a `final` adviser-influenced decision is measured against.
        """
        return cls(kind=kind, rationale=rationale, review_required=True, conflict=True)


class VersionSet(BaseModel):
    """Every version coordinate a decision was made under (honest null).

    Spec §7.8: "every decision logs the full score vector and model versions."
    Each field is optional and left `None` when that stage did not participate
    (e.g. `adviser_version` is `None` for a purely deterministic decision) — an
    honest absence, never a fabricated version string.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    matcher_version: str | None = None
    adviser_version: str | None = None
    model_id: str | None = None
    model_version: str | None = None
    prompt_version: str | None = None
    policy_version: str | None = None
    ontology_version: str | None = None
    profile_id: str | None = None
    profile_version: str | None = None


class ReviewSummary(BaseModel):
    """A compact, JSON-serializable projection of a `ReviewOutcome`.

    The semantic audit *links to* the review decision; it does not embed the
    whole `ReviewOutcome` (a dataclass carrying a full `CurationPlan` and
    `EvolutionResult`). This keeps the record immutable and JSON-round-trippable
    while preserving the join: `plan_id` ties back to the resulting plan.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: str
    status: str
    kind: str | None = None
    plan_id: str | None = None
    rationale: str = ""

    @classmethod
    def of(cls, outcome: ReviewOutcome) -> "ReviewSummary":
        """Project a `ReviewOutcome` into its audit summary."""
        return cls(
            action=outcome.action.value,
            status=outcome.status.value,
            kind=outcome.kind.value if outcome.kind is not None else None,
            plan_id=outcome.plan.plan_id if outcome.plan is not None else None,
            rationale=outcome.rationale,
        )


class ExecutionRef(BaseModel):
    """A compact ref to the commit/compensation `ExecutionRecord` (join by id).

    The execution-scoped audit stays its own object; the semantic audit carries
    only the id, outcome, epoch, and compensation flag needed to join to it and
    to compute rollback metrics without re-reading the execution stream.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    execution_id: str
    plan_id: str
    outcome: ExecutionOutcome
    new_epoch: int | None = None
    is_compensation: bool = False

    @classmethod
    def of(cls, record: ExecutionRecord) -> "ExecutionRef":
        """Project an `ExecutionRecord` into its audit ref."""
        return cls(
            execution_id=record.execution_id,
            plan_id=record.plan_id,
            outcome=record.outcome,
            new_epoch=record.new_epoch,
            is_compensation=record.is_compensation,
        )


class ReplayInputs(BaseModel):
    """The exact inputs needed to re-run the decision (law 17).

    Everything `CurationOrchestrator.resolve` was called with, captured so
    `kgcs.observability.replay.replay` can reproduce the decision from the audit
    alone — the whole `MatchResult` and `CurationProfile` (both frozen and
    JSON-serializable), the cluster verdict, and the evidence/trace threaded into
    the adviser question. The one input *not* captured is the recorded LLM
    completion fixture: the replay caller supplies the same
    `RecordedCompletionClient` (a missing fixture is a wiring error surfaced
    loudly, never silently reproduced).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    match_result: MatchResult
    profile: CurationProfile
    cluster_validation: ClusterValidation | None = None
    snapshot_stale: bool = False
    evidence_count: int | None = None
    malformed: bool = False
    evidence_ids: tuple[str, ...] = ()
    trace_id: str = ""


class AssertionReplayInputs(BaseModel):
    """The exact inputs needed to re-run an assertion/evolution decision (law 17).

    The assertion-side analogue of `ReplayInputs`: everything
    `EvolutionRouter.route_assertion` was called with — the two `Assertion`s, the
    bounded adviser recommendation, the `CurationTrigger`, and the policy gate —
    plus the planner version coordinates, captured so
    `kgcs.observability.replay.replay` can rebuild the planner + router and
    reproduce the decision from the audit alone. Both assertions and the trigger
    are frozen and JSON-serializable, so the block survives a persist/reopen.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    old_assertion: Assertion
    new_assertion: Assertion
    recommendation: str
    trigger: CurationTrigger
    supersession_allowed: bool = True
    snapshot_version: str = "0"
    matcher_version: str | None = None
    adviser_version: str | None = None
    policy_version: str = DEFAULT_POLICY_VERSION
    trace_id: str = ""

    def recommendation_enum(self) -> AssertionRecommendation:
        """The parsed recommendation (a `ValueError` on an unknown value)."""
        return AssertionRecommendation(self.recommendation)


class SemanticAuditRecord(BaseModel):
    """The full decision lineage for one curation decision (decision-scoped).

    Distinct from `AuditRecord` (operation-scoped) and `ExecutionRecord`
    (execution-scoped): it captures the deterministic `baseline`, the adviser
    `assessments` (with DG-4 provenance), the optional `review`, the `final`
    action, the resulting `plan_id`, the optional `execution` ref, the
    `score_vector`, and the full `versions` set — joined to the other two audit
    streams by `trace_id` / `plan_id` / candidate refs. Immutable and
    JSON-serializable; a pure function of the artifacts it was built from apart
    from `recorded_at`, which the builder reads from its injected `Clock`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    audit_id: str = Field(min_length=1)
    decision_kind: DecisionKind = DecisionKind.ER
    trace_id: str = ""
    trigger_id: str | None = None
    affected_refs: tuple[str, ...] = ()

    baseline: ErDecision
    assessments: tuple[AdviserAssessment, ...] = ()
    review: ReviewSummary | None = None
    final: ErDecision

    plan_id: str | None = None
    execution: ExecutionRef | None = None

    score_vector: dict[str, float | None] = Field(default_factory=dict)
    versions: VersionSet = Field(default_factory=VersionSet)
    replay_inputs: ReplayInputs
    recorded_at: datetime

    @property
    def final_action(self) -> str:
        """The final ER action value — the decision the executor would apply."""
        return self.final.action.value

    @property
    def final_link_kind(self) -> IdentityLinkKind | None:
        """The identity link the final action asserts, or `None`."""
        return self.final.link_kind

    @property
    def consulted_adviser(self) -> bool:
        """True iff at least one adviser assessment was folded into the decision."""
        return bool(self.assessments)


class AssertionSemanticAuditRecord(BaseModel):
    """The decision lineage for one assertion / concept-evolution decision.

    The sibling of `SemanticAuditRecord` for the re-curation path
    (`kgcs.recuration.evolution`): the deterministic `baseline`, the adviser
    `assessments` (with DG-4 provenance), the optional `review`, the `final`
    evolution decision, the resulting `plan_id`, the optional `execution` ref,
    the `versions` set, and the `AssertionReplayInputs` that make it replayable —
    joined to the operation/execution streams by `trace_id` / `plan_id` /
    assertion and operation ids. Immutable and JSON-serializable; the only
    non-content field is the injected-clock `recorded_at`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    audit_id: str = Field(min_length=1)
    decision_kind: DecisionKind = DecisionKind.ASSERTION
    trace_id: str = ""
    trigger_id: str | None = None
    affected_refs: tuple[str, ...] = ()

    baseline: EvolutionDecision
    assessments: tuple[AdviserAssessment, ...] = ()
    review: ReviewSummary | None = None
    final: EvolutionDecision

    plan_id: str | None = None
    execution: ExecutionRef | None = None

    assertion_ids: tuple[str, ...] = ()
    operation_ids: tuple[str, ...] = ()
    versions: VersionSet = Field(default_factory=VersionSet)
    replay_inputs: AssertionReplayInputs
    recorded_at: datetime

    @property
    def final_kind(self) -> str:
        """The final evolution kind value — the decision the executor would apply."""
        return self.final.kind

    @property
    def consulted_adviser(self) -> bool:
        """True iff at least one adviser assessment was folded into the decision."""
        return bool(self.assessments)


#: Any record a `SemanticAuditSink` may hold — ER or assertion/evolution.
SemanticAuditRecordT = SemanticAuditRecord | AssertionSemanticAuditRecord


@runtime_checkable
class SemanticAuditSink(Protocol):
    """Append-only destination for semantic audit records.

    Mirrors `kgcs.audit.AuditSink` and `kgcs.executor.ExecutionAuditSink`: the
    builder *creates* records, a sink *keeps* them. `record` appends one;
    `records` returns them in append order. Holds both decision families
    (`SemanticAuditRecord` and `AssertionSemanticAuditRecord`).
    """

    def record(self, record: SemanticAuditRecordT) -> None: ...

    def records(self) -> list[SemanticAuditRecordT]: ...


class InMemorySemanticAuditSink:
    """A list-backed, append-only `SemanticAuditSink` (reference double).

    Append-only *by discipline*: it exposes no update or delete, and `records()`
    hands back a defensive copy so a caller cannot mutate the log through the
    returned list. Durable append-only-at-rest is a store's job; this models the
    in-memory shape only.
    """

    def __init__(self) -> None:
        self._records: list[SemanticAuditRecordT] = []

    def record(self, record: SemanticAuditRecordT) -> None:
        self._records.append(record)

    def records(self) -> list[SemanticAuditRecordT]:
        """Every record appended so far, in order (a defensive copy)."""
        return list(self._records)


@dataclass(frozen=True)
class SemanticAuditBuilder:
    """Assembles a `SemanticAuditRecord` from the available pipeline artifacts.

    Stateless apart from its injected `IdFactory` (default: derived, so the
    `audit_id` is a pure function of the decision content), its injected `Clock`
    (the one non-content field, `recorded_at`), and the stamped `policy_version`.
    `build` folds an `OrchestrationResult` (baseline + final + assessments), the
    `ReplayInputs`, and the optional trigger / review / execution artifacts into
    one immutable record; `build_assertion` does the same for an
    assertion/evolution decision, propagating the universal `trace_id`
    throughout.
    """

    id_factory: IdFactory = field(default_factory=DerivedIdFactory)
    clock: Clock = field(default_factory=SystemClock)
    policy_version: str = DEFAULT_POLICY_VERSION

    def build(
        self,
        orchestration: OrchestrationResult,
        replay_inputs: ReplayInputs,
        *,
        trigger: CurationTrigger | None = None,
        review: ReviewOutcome | None = None,
        execution: ExecutionRecord | None = None,
        plan_id: str | None = None,
        ontology_version: str | None = None,
    ) -> SemanticAuditRecord:
        """Assemble one immutable `SemanticAuditRecord`.

        `trace_id` is taken from `replay_inputs` (the universal trace that flowed
        through the pipeline). The resulting `plan_id` is resolved from the
        explicit argument, else the review's plan, else the execution's plan.
        """
        baseline = orchestration.baseline
        final = orchestration.decision
        assessments = orchestration.assessments
        trace_id = replay_inputs.trace_id

        review_summary = ReviewSummary.of(review) if review is not None else None
        execution_ref = ExecutionRef.of(execution) if execution is not None else None
        resolved_plan_id = _resolve_plan_id(plan_id, review, execution)

        return SemanticAuditRecord(
            audit_id=self._audit_id(trace_id, final, resolved_plan_id),
            trace_id=trace_id,
            trigger_id=trigger.trigger_id if trigger is not None else None,
            affected_refs=_affected_refs(trigger, replay_inputs.match_result),
            baseline=baseline,
            assessments=assessments,
            review=review_summary,
            final=final,
            plan_id=resolved_plan_id,
            execution=execution_ref,
            score_vector=dict(replay_inputs.match_result.feature_vector),
            versions=self._versions(replay_inputs, assessments, ontology_version),
            replay_inputs=replay_inputs,
            recorded_at=self.clock.now(),
        )

    def build_assertion(
        self,
        *,
        baseline: EvolutionDecision,
        final: EvolutionDecision,
        replay_inputs: AssertionReplayInputs,
        assessments: Sequence[AdviserAssessment] = (),
        trigger: CurationTrigger | None = None,
        review: ReviewOutcome | None = None,
        execution: ExecutionRecord | None = None,
        plan_id: str | None = None,
        ontology_version: str | None = None,
        assertion_ids: Sequence[str] = (),
        operation_ids: Sequence[str] = (),
    ) -> AssertionSemanticAuditRecord:
        """Assemble one immutable `AssertionSemanticAuditRecord`.

        The assertion/evolution analogue of `build`: the caller (the evolution
        path) supplies the deterministic baseline and final decisions plus the
        `AssertionReplayInputs`; the builder stamps the ids, versions, and
        injected-clock `recorded_at`.
        """
        trace_id = replay_inputs.trace_id
        resolved_plan_id = _resolve_plan_id(plan_id, review, execution)

        return AssertionSemanticAuditRecord(
            audit_id=self._assertion_audit_id(trace_id, final),
            trace_id=trace_id,
            trigger_id=trigger.trigger_id if trigger is not None else None,
            affected_refs=_assertion_affected_refs(trigger, final),
            baseline=baseline,
            assessments=tuple(assessments),
            review=ReviewSummary.of(review) if review is not None else None,
            final=final,
            plan_id=resolved_plan_id,
            execution=ExecutionRef.of(execution) if execution is not None else None,
            assertion_ids=tuple(assertion_ids),
            operation_ids=tuple(operation_ids),
            versions=self._assertion_versions(replay_inputs, assessments, ontology_version),
            replay_inputs=replay_inputs,
            recorded_at=self.clock.now(),
        )

    def _audit_id(self, trace_id: str, final: ErDecision, plan_id: str | None) -> str:
        """A deterministic content address of the decision (`au_…`)."""
        pair = final.pair
        seed = f"semantic:{trace_id}:{pair.left}:{pair.right}:{final.action.value}:{plan_id or ''}"
        return self.id_factory.audit_id(seed)

    def _assertion_audit_id(self, trace_id: str, final: EvolutionDecision) -> str:
        """A deterministic content address of an evolution decision (`au_…`)."""
        seed = (
            f"semantic:assertion:{trace_id}:{final.kind}:{final.plan_id or ''}:"
            f"{','.join(final.assertion_ids)}"
        )
        return self.id_factory.audit_id(seed)

    def _versions(
        self,
        replay_inputs: ReplayInputs,
        assessments: Sequence[AdviserAssessment],
        ontology_version: str | None,
    ) -> VersionSet:
        """Collect every version coordinate; honest-null where a stage was absent."""
        lead = assessments[0] if assessments else None
        return VersionSet(
            matcher_version=replay_inputs.match_result.matcher_version,
            adviser_version=lead.adviser_version if lead is not None else None,
            model_id=lead.model_id if lead is not None else None,
            model_version=lead.model_version if lead is not None else None,
            prompt_version=lead.prompt_version if lead is not None else None,
            policy_version=self.policy_version,
            ontology_version=ontology_version,
            profile_id=replay_inputs.profile.profile_id,
            profile_version=replay_inputs.profile.version,
        )

    def _assertion_versions(
        self,
        replay_inputs: AssertionReplayInputs,
        assessments: Sequence[AdviserAssessment],
        ontology_version: str | None,
    ) -> VersionSet:
        """Version coordinates for an evolution decision (honest-null where absent)."""
        lead = assessments[0] if assessments else None
        return VersionSet(
            matcher_version=replay_inputs.matcher_version,
            adviser_version=lead.adviser_version if lead is not None else replay_inputs.adviser_version,
            model_id=lead.model_id if lead is not None else None,
            model_version=lead.model_version if lead is not None else None,
            prompt_version=lead.prompt_version if lead is not None else None,
            policy_version=replay_inputs.policy_version or self.policy_version,
            ontology_version=ontology_version,
        )


def _resolve_plan_id(
    plan_id: str | None, review: ReviewOutcome | None, execution: ExecutionRecord | None
) -> str | None:
    """The resulting plan id: explicit, else the review's plan, else the execution's."""
    if plan_id is not None:
        return plan_id
    if review is not None and review.plan is not None:
        return review.plan.plan_id
    if execution is not None:
        return execution.plan_id
    return None


def _affected_refs(trigger: CurationTrigger | None, match_result: MatchResult) -> tuple[str, ...]:
    """The canonical/candidate refs this decision touched.

    A re-curation decision names its trigger's target refs; a first-pass ER
    decision names the pair's two endpoints. Deterministic and order-stable.
    """
    if trigger is not None and trigger.target_refs:
        return trigger.target_refs
    pair = match_result.pair
    return (pair.left, pair.right)


def _assertion_affected_refs(
    trigger: CurationTrigger | None, final: EvolutionDecision
) -> tuple[str, ...]:
    """The refs an evolution decision touched: the trigger's targets + assertions.

    Deterministic and order-stable: trigger target refs first (identity/assertion
    /concept refs, in the trigger's fixed order), then any assertion ids named by
    the decision that the trigger did not already list.
    """
    refs: list[str] = list(trigger.target_refs) if trigger is not None else []
    for assertion_id in final.assertion_ids:
        if assertion_id not in refs:
            refs.append(assertion_id)
    return tuple(refs)
