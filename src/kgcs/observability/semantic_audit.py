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

**Determinism (law 17).** A `SemanticAuditRecord` is a pure function of the
pipeline artifacts it is assembled from — it holds no wall-clock field of its
own (the linked `ExecutionRecord` carries the only timestamp), so replaying the
same decision assembles a byte-identical record. It also carries a
`ReplayInputs` block: the exact inputs needed to re-run the decision
(`kgcs.observability.replay`), which is what makes the decision explainable and
reproducible from the audit alone.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from kg_contracts.identity import IdentityLinkKind
from pydantic import BaseModel, ConfigDict, Field

from kgcs.advisers.base import AdviserAssessment
from kgcs.advisers.orchestrator import OrchestrationResult
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


class SemanticAuditRecord(BaseModel):
    """The full decision lineage for one curation decision (decision-scoped).

    Distinct from `AuditRecord` (operation-scoped) and `ExecutionRecord`
    (execution-scoped): it captures the deterministic `baseline`, the adviser
    `assessments` (with DG-4 provenance), the optional `review`, the `final`
    action, the resulting `plan_id`, the optional `execution` ref, the
    `score_vector`, and the full `versions` set — joined to the other two audit
    streams by `trace_id` / `plan_id` / candidate refs. Immutable and
    JSON-serializable; a pure function of the artifacts it was built from.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    audit_id: str = Field(min_length=1)
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


@runtime_checkable
class SemanticAuditSink(Protocol):
    """Append-only destination for `SemanticAuditRecord`s.

    Mirrors `kgcs.audit.AuditSink` and `kgcs.executor.ExecutionAuditSink`: the
    builder *creates* records, a sink *keeps* them. `record` appends one;
    `records` returns them in append order.
    """

    def record(self, record: SemanticAuditRecord) -> None: ...

    def records(self) -> list[SemanticAuditRecord]: ...


class InMemorySemanticAuditSink:
    """A list-backed, append-only `SemanticAuditSink` (reference double).

    Append-only *by discipline*: it exposes no update or delete, and `records()`
    hands back a defensive copy so a caller cannot mutate the log through the
    returned list. Durable append-only-at-rest is a store's job; this models the
    in-memory shape only.
    """

    def __init__(self) -> None:
        self._records: list[SemanticAuditRecord] = []

    def record(self, record: SemanticAuditRecord) -> None:
        self._records.append(record)

    def records(self) -> list[SemanticAuditRecord]:
        """Every record appended so far, in order (a defensive copy)."""
        return list(self._records)


@dataclass(frozen=True)
class SemanticAuditBuilder:
    """Assembles a `SemanticAuditRecord` from the available pipeline artifacts.

    Stateless apart from its injected `IdFactory` (default: derived, so the
    `audit_id` is a pure function of the decision content) and the stamped
    `policy_version`. `build` folds an `OrchestrationResult` (baseline + final +
    assessments), the `ReplayInputs`, and the optional trigger / review /
    execution artifacts into one immutable record, propagating the universal
    `trace_id` throughout.
    """

    id_factory: IdFactory = field(default_factory=DerivedIdFactory)
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
        )

    def _audit_id(self, trace_id: str, final: ErDecision, plan_id: str | None) -> str:
        """A deterministic content address of the decision (`au_…`)."""
        pair = final.pair
        seed = f"semantic:{trace_id}:{pair.left}:{pair.right}:{final.action.value}:{plan_id or ''}"
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
