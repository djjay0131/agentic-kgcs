"""E2E scenarios 4 & 5 — stale-snapshot safety and compensating rollback.

Scenario 4 (§9 law 4): a plan computed against an old snapshot is rejected
`STALE` by the executor rather than racing a second commit; re-evaluating it
against the current snapshot then commits safely.

Scenario 5 (§9 law 8): a merge plan and the compensating plan the `Compensator`
generates for it. On the Plan-1 reference store a merge is honestly
`UNSUPPORTED_OPERATION` (never silently skipped), and so is its `SPLIT`
compensation — the rollback path is explicit, not pretended. A second test shows
a genuinely *executed* compensation: an `ATTACH` is rolled back by its `RETRACT`
inverse, which — like every rollback here — is a status change preserving
history, and which must be re-evaluated against the current snapshot before it
applies (never blindly retried).
"""

from __future__ import annotations

from datetime import UTC, datetime

from e2e_harness import (
    PAPER_SHAPE,
    REGISTRY_CONFIDENCE_POLICY,
    SENSOR_SHAPE,
    SUPPORTED_WITH_RETRACT,
    E2EGraphStore,
    attribute_candidate,
    entity_candidate,
)
from kg_contracts.curation import CurationOperationType, Precondition
from kg_contracts.stores import GraphReadOptions
from kg_contracts.testing.memory import MemoryGraphStore
from kgcs.planner import SNAPSHOT_PRECONDITION_KIND
from kgcs.recuration import ConceptEvolutionPlanner, CurationTrigger, TriggerKind

from kgcs import (
    Compensator,
    CurationEngine,
    ExecutionOutcome,
    FixedClock,
    PlanExecutor,
)

_CLOCK = FixedClock(datetime(2026, 8, 22, tzinfo=UTC))


def _engine(graph_id: str, snapshot_version: str) -> CurationEngine:
    return CurationEngine.create(
        graph_id=graph_id,
        confidence_policy=REGISTRY_CONFIDENCE_POLICY,
        clock=_CLOCK,
        snapshot_version=snapshot_version,
    )


class TestStaleSnapshotIsRejectedThenReevaluated:
    def test_stale_plan_rejected_then_safe_commit_no_race(self) -> None:
        graph = "stale"
        store = E2EGraphStore()
        executor = PlanExecutor(store, clock=_CLOCK)

        # A subject to attach to (epoch 1).
        entity = entity_candidate(PAPER_SHAPE, graph_id=graph)
        entity_plan = _engine(graph, "0").curate([entity]).plan
        assert entity_plan is not None
        executor.execute(entity_plan)
        identity_id = str(entity_plan.operations[0].reversal_data["identity_id"])

        # Plan P is computed against snapshot "1" (the current epoch)...
        attr_p = attribute_candidate(
            PAPER_SHAPE,
            graph_id=graph,
            subject=identity_id,
            value=PAPER_SHAPE.value_a,
            evidence_id=PAPER_SHAPE.ev_a,
            cid="cand_p",
            trace="trace_p",
        )
        plan_p = _engine(graph, str(store.current_epoch())).curate([attr_p]).plan
        assert plan_p is not None

        # ...but another commit lands first, advancing the graph to epoch 2.
        attr_x = attribute_candidate(
            SENSOR_SHAPE,
            graph_id=graph,
            subject=identity_id,
            value=SENSOR_SHAPE.value_a,
            evidence_id=SENSOR_SHAPE.ev_a,
            cid="cand_x",
            trace="trace_x",
        )
        plan_x = _engine(graph, str(store.current_epoch())).curate([attr_x]).plan
        assert plan_x is not None
        assert executor.execute(plan_x).outcome is ExecutionOutcome.COMMITTED
        assert store.current_epoch() == 2

        # Executing the now-stale plan P is rejected — it does not race.
        stale = executor.execute(plan_p)
        assert stale.outcome is ExecutionOutcome.STALE
        assert any(p.kind == SNAPSHOT_PRECONDITION_KIND for p in stale.failed_preconditions)
        assert store.current_epoch() == 2  # nothing committed by the stale attempt

        # Re-evaluate against the current snapshot → safe commit at epoch 3.
        plan_p2 = _engine(graph, str(store.current_epoch())).curate([attr_p]).plan
        assert plan_p2 is not None
        assert executor.execute(plan_p2).outcome is ExecutionOutcome.COMMITTED
        assert store.current_epoch() == 3
        assert len(store.assertions_for(identity_id)) == 2  # X and P, no duplicate


class TestMergeAndCompensation:
    def _merge_plan(self):  # type: ignore[no-untyped-def]
        planner = ConceptEvolutionPlanner(matcher_version="rules/1")
        trigger = CurationTrigger.of(
            kind=TriggerKind.NEW_EVIDENCE, evidence_ids=("ev_merge",), trace_id="merge-1"
        )
        return planner.plan_merge(
            members=("kg://g1/identity/BBB", "kg://g1/identity/AAA"), trigger=trigger
        )

    def test_merge_is_unsupported_and_its_compensation_is_honestly_unsupported(self) -> None:
        result = self._merge_plan()
        assert result.plan is not None
        store = MemoryGraphStore()

        # The reference store cannot apply a merge — reported explicitly, and it
        # never touched the store (fail-closed).
        record = PlanExecutor(store, clock=_CLOCK).execute(result.plan)
        assert record.outcome is ExecutionOutcome.UNSUPPORTED_OPERATION
        assert "MERGE_IDENTITIES" in record.unsupported_types
        assert store.current_epoch() == 0

        # A compensation IS generated: MERGE ↔ SPLIT, fully reversible.
        comp = Compensator().compensate(result.plan)
        assert comp.fully_compensable is True
        assert comp.plan is not None
        assert comp.plan.operations[0].type is CurationOperationType.SPLIT_IDENTITY
        # ...and applying it on the reference store is honestly unsupported too —
        # the rollback path is explicit, not pretended.
        comp_record = PlanExecutor(store, clock=_CLOCK).execute(comp.plan)
        assert comp_record.outcome is ExecutionOutcome.UNSUPPORTED_OPERATION
        assert store.current_epoch() == 0

    def test_executed_attach_is_rolled_back_by_its_compensation(self) -> None:
        graph = "rollback"
        store = E2EGraphStore()
        executor = PlanExecutor(store, clock=_CLOCK, supported_operations=SUPPORTED_WITH_RETRACT)

        # Commit an entity then an assertion.
        entity = entity_candidate(SENSOR_SHAPE, graph_id=graph)
        entity_plan = _engine(graph, "0").curate([entity]).plan
        assert entity_plan is not None
        executor.execute(entity_plan)
        identity_id = str(entity_plan.operations[0].reversal_data["identity_id"])

        attr = attribute_candidate(
            SENSOR_SHAPE,
            graph_id=graph,
            subject=identity_id,
            value=SENSOR_SHAPE.value_a,
            evidence_id=SENSOR_SHAPE.ev_a,
            cid="cand_attr",
            trace="trace_attr",
        )
        attach_plan = _engine(graph, str(store.current_epoch())).curate([attr]).plan
        assert attach_plan is not None
        assert executor.execute(attach_plan).outcome is ExecutionOutcome.COMMITTED
        assert len(store.assertions_for(identity_id)) == 1  # live

        # Generate the compensation (ATTACH ↔ RETRACT).
        comp = Compensator().compensate(attach_plan)
        assert comp.fully_compensable is True
        assert comp.plan is not None
        assert comp.plan.operations[0].type is CurationOperationType.RETRACT_ASSERTION

        # Blindly retrying it is rejected STALE — a rollback carries the original
        # snapshot guard, so it must be re-evaluated, never raced.
        assert executor.execute(comp.plan).outcome is ExecutionOutcome.STALE

        # Re-evaluate against the current snapshot, then apply: the assertion is
        # rolled back to SUPERSEDED (history preserved, not deleted).
        reevaluated = comp.plan.model_copy(
            update={
                "preconditions": (
                    Precondition(
                        kind=SNAPSHOT_PRECONDITION_KIND,
                        subject=identity_id,
                        expected=str(store.current_epoch()),
                    ),
                )
            }
        )
        assert executor.execute(reevaluated).outcome is ExecutionOutcome.COMMITTED
        assert store.assertions_for(identity_id) == []  # no longer live
        with_history = store.assertions_for(identity_id, GraphReadOptions(include_superseded=True))
        assert len(with_history) == 1  # preserved, marked superseded
