"""Assertion / concept-evolution semantic audit (issue #48).

`kgcs.observability.semantic_audit` records an ER (`ErDecision`) decision; this
module records the *other* decision family on the same stream: an assertion /
concept-evolution decision produced by the re-curation path
(`kgcs.recuration.evolution.EvolutionResult`). The record type itself lives with
its sibling in `semantic_audit` (so the two share one `SemanticAuditSink` union);
this module holds the adapter that turns a live `EvolutionResult` into that
record and appends it.

`EvolutionAuditRecorder` is what "wire `SemanticAuditBuilder` into the evolution
path" means concretely: `EvolutionRouter` holds an optional recorder and calls
`record_evolution(...)` after it has produced a plan, so every adviser-driven
re-curation decision leaves a durable, replayable audit entry joined to the plan
and the trigger by id. The recorder is deliberately separate from the router so
`kgcs.recuration` never imports `kgcs.observability` at runtime — the router
depends only on the call it makes.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from kg_contracts.assertions import Assertion

from kgcs.advisers.base import AdviserAssessment
from kgcs.observability.semantic_audit import (
    AssertionReplayInputs,
    AssertionSemanticAuditRecord,
    EvolutionDecision,
    SemanticAuditBuilder,
    SemanticAuditSink,
)
from kgcs.recuration.evolution import ConceptEvolutionPlanner, EvolutionKind, EvolutionResult
from kgcs.recuration.triggers import CurationTrigger

#: The deterministic baseline an adviser-influenced decision is measured against:
#: without advice the core preserves both competing assertions and routes to
#: review (§9 law 1 / law 10). A constant, so it is not fabricated per call.
_BASELINE_RATIONALE = (
    "deterministic baseline: preserve both assertions and route to review "
    "until an adviser supplies cited evidence"
)


def evolution_decision_of(result: EvolutionResult) -> EvolutionDecision:
    """Reduce an `EvolutionResult` to its auditable decision shape."""
    return EvolutionDecision(
        kind=result.kind.value,
        rationale=result.rationale,
        review_required=result.review_required,
        conflict=result.conflict_record is not None,
        plan_id=result.plan.plan_id if result.plan is not None else None,
        assertion_ids=assertion_ids_of(result),
    )


def baseline_decision_of(
    result: EvolutionResult, assessment: AdviserAssessment | None
) -> EvolutionDecision:
    """The baseline decision to stamp on the record.

    When no adviser assessment influenced the decision (absent or abstained),
    the baseline *is* the final decision — the deterministic core alone produced
    it. When an assessment was folded in, the baseline is the conservative
    preserve-both route the core would have taken on its own consensus (§9 law
    1), so the record shows what the advice changed.
    """
    if assessment is None or assessment.abstained:
        return evolution_decision_of(result)
    return EvolutionDecision.deterministic_baseline(
        EvolutionKind.CONFLICT.value, rationale=_BASELINE_RATIONALE
    )


def assertion_ids_of(result: EvolutionResult) -> tuple[str, ...]:
    """Every assertion id an evolution decision touches, in a stable order.

    Drawn from the produced plan's operations (attach/retract/reassign payloads)
    and from the result's superseded / reassigned bookkeeping, so
    `records_for_assertion` can join a canonical assertion back to the decisions
    that shaped it. Deterministic and first-seen de-duplicated.
    """
    values: list[str] = []
    for assertion in result.superseded_assertions:
        values.append(assertion.assertion_id)
    for reassignment in result.reassignments:
        values.append(reassignment.assertion_id)
    if result.plan is not None:
        for operation in result.plan.operations:
            payload_id = operation.payload.get("assertion_id")
            if isinstance(payload_id, str):
                values.append(payload_id)
    return _dedupe(values)


def operation_ids_of(result: EvolutionResult) -> tuple[str, ...]:
    """Every operation id in the produced plan, in plan order."""
    if result.plan is None:
        return ()
    return tuple(operation.operation_id for operation in result.plan.operations)


@dataclass(frozen=True)
class EvolutionAuditRecorder:
    """Appends an `AssertionSemanticAuditRecord` for each evolution decision.

    Holds the shared `SemanticAuditBuilder` (for ids, versions, and the injected
    clock) and a `SemanticAuditSink` (in-memory or SQLite). `record_evolution`
    captures the live inputs, builds the record, appends it, and returns it. Pure
    apart from the sink it writes to.
    """

    builder: SemanticAuditBuilder
    sink: SemanticAuditSink

    def record_evolution(
        self,
        result: EvolutionResult,
        *,
        old_assertion: Assertion,
        new_assertion: Assertion,
        recommendation: str,
        trigger: CurationTrigger,
        supersession_allowed: bool,
        planner: ConceptEvolutionPlanner,
        assessment: AdviserAssessment | None = None,
    ) -> AssertionSemanticAuditRecord:
        """Build and persist the semantic audit record for `result`."""
        replay_inputs = AssertionReplayInputs(
            old_assertion=old_assertion,
            new_assertion=new_assertion,
            recommendation=recommendation,
            trigger=trigger,
            supersession_allowed=supersession_allowed,
            snapshot_version=planner.snapshot_version,
            matcher_version=planner.matcher_version,
            adviser_version=planner.adviser_version,
            policy_version=planner.policy_version,
            trace_id=trigger.trace_id,
        )
        assessments: Sequence[AdviserAssessment] = (
            (assessment,) if assessment is not None else ()
        )
        record = self.builder.build_assertion(
            baseline=baseline_decision_of(result, assessment),
            final=evolution_decision_of(result),
            replay_inputs=replay_inputs,
            assessments=assessments,
            trigger=trigger,
            plan_id=result.plan.plan_id if result.plan is not None else None,
            assertion_ids=assertion_ids_of(result),
            operation_ids=operation_ids_of(result),
            source_candidate_ids=new_assertion.source_candidate_ids,
        )
        self.sink.record(record)
        return record


def _dedupe(values: Sequence[str]) -> tuple[str, ...]:
    """First-seen-order de-duplication (deterministic)."""
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return tuple(ordered)
