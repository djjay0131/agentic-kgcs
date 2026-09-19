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
history, and which is guarded against the epoch the original plan committed at
(ADR-0018) so it applies exactly once and never races a concurrent writer.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from kg_contracts.assertions import CurationStatus
from kg_contracts.curation import (
    CurationOperation,
    CurationOperationType,
    CurationPlan,
)
from kg_contracts.stores import GraphReadOptions
from kg_contracts.testing.factories import make_assertion, new_identity_id
from kg_contracts.testing.memory import MemoryGraphStore

from e2e_harness import (
    PAPER_SHAPE,
    REGISTRY_CONFIDENCE_POLICY,
    SENSOR_SHAPE,
    SUPPORTED_WITH_RETRACT,
    E2EGraphStore,
    attribute_candidate,
    entity_candidate,
)
from kgcs import (
    Compensator,
    CurationEngine,
    ExecutionOutcome,
    FixedClock,
    PlanExecutor,
)
from kgcs.planner import SNAPSHOT_PRECONDITION_KIND
from kgcs.recuration import ConceptEvolutionPlanner, CurationTrigger, TriggerKind

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
        comp = Compensator().compensate(result.plan, against_snapshot=None)
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
        attach_record = executor.execute(attach_plan)
        assert attach_record.outcome is ExecutionOutcome.COMMITTED
        assert len(store.assertions_for(identity_id)) == 1  # live

        # Generate the compensation (ATTACH ↔ RETRACT), guarded against the
        # epoch the ATTACH committed at — the state the rollback expects to
        # find NOW, which is what `ExecutionRecord.new_epoch` reports.
        assert attach_record.new_epoch is not None
        comp = Compensator().compensate(attach_plan, against_snapshot=attach_record.new_epoch)
        assert comp.fully_compensable is True
        assert comp.snapshot_guarded is True
        assert comp.plan is not None
        assert comp.plan.operations[0].type is CurationOperationType.RETRACT_ASSERTION

        # It applies as generated. This is the regression: until ADR-0018 the
        # compensating plan carried the *source* plan's snapshot guard, which
        # the ATTACH's own commit had already invalidated, so this line read
        # STALE and the caller had to hand-rebuild the precondition before any
        # rollback could commit. The assertion is rolled back to SUPERSEDED
        # (history preserved, not deleted).
        assert executor.execute(comp.plan, is_compensation=True).outcome is (
            ExecutionOutcome.COMMITTED
        )
        assert store.assertions_for(identity_id) == []  # no longer live
        with_history = store.assertions_for(identity_id, GraphReadOptions(include_superseded=True))
        assert len(with_history) == 1  # preserved, marked superseded

        # Replaying it is rejected STALE — the guard is real, and the rollback
        # applies exactly once.
        assert executor.execute(comp.plan, is_compensation=True).outcome is (
            ExecutionOutcome.STALE
        )

    def test_a_compensation_loses_a_race_it_should_lose(self) -> None:
        """The other direction: a concurrent commit invalidates the rollback."""
        graph = "rollback-race"
        store = E2EGraphStore()
        executor = PlanExecutor(store, clock=_CLOCK, supported_operations=SUPPORTED_WITH_RETRACT)

        entity = entity_candidate(PAPER_SHAPE, graph_id=graph)
        entity_plan = _engine(graph, "0").curate([entity]).plan
        assert entity_plan is not None
        executor.execute(entity_plan)
        identity_id = str(entity_plan.operations[0].reversal_data["identity_id"])

        attr = attribute_candidate(
            PAPER_SHAPE,
            graph_id=graph,
            subject=identity_id,
            value=PAPER_SHAPE.value_a,
            evidence_id=PAPER_SHAPE.ev_a,
            cid="cand_race_a",
            trace="trace_race_a",
        )
        attach_plan = _engine(graph, str(store.current_epoch())).curate([attr]).plan
        assert attach_plan is not None
        attach_record = executor.execute(attach_plan)
        assert attach_record.outcome is ExecutionOutcome.COMMITTED
        assert attach_record.new_epoch is not None

        comp = Compensator().compensate(attach_plan, against_snapshot=attach_record.new_epoch)
        assert comp.plan is not None

        # Someone else commits before the rollback runs.
        other = attribute_candidate(
            PAPER_SHAPE,
            graph_id=graph,
            subject=identity_id,
            value=PAPER_SHAPE.value_b,
            evidence_id=PAPER_SHAPE.ev_b,
            cid="cand_race_b",
            trace="trace_race_b",
        )
        other_plan = _engine(graph, str(store.current_epoch())).curate([other]).plan
        assert other_plan is not None
        assert executor.execute(other_plan).outcome is ExecutionOutcome.COMMITTED

        record = executor.execute(comp.plan, is_compensation=True)
        assert record.outcome is ExecutionOutcome.STALE
        assert any(
            p.kind == SNAPSHOT_PRECONDITION_KIND for p in record.failed_preconditions
        )
        # Nothing was rolled back: both assertions are still live.
        assert len(store.assertions_for(identity_id)) == 2


class TestSupersessionRollback:
    """The RETRACT→ATTACH inverse, executed — not merely generated (ADR-0018).

    A supersession is the one v1 shape whose compensation exercises *both*
    directions of the inverse map: undoing it must re-attach the record that
    was marked `SUPERSEDED` and retract the one that replaced it. Nothing
    executed this path before ADR-0018 — the carried snapshot guard rejected
    every compensation `STALE` first, so the `RETRACT`'s `reversal_data` was
    never fed to a store, and it had been built with no `predicate` (nor any
    other `Assertion` field): the payload could not have validated if it had
    arrived.
    """

    def test_a_committed_supersession_is_rolled_back_end_to_end(self) -> None:
        graph = "supersession-rollback"
        store = E2EGraphStore()
        executor = PlanExecutor(store, clock=_CLOCK, supported_operations=SUPPORTED_WITH_RETRACT)

        subject = new_identity_id(graph)
        old = make_assertion(
            graph_id=graph, subject_identity=subject, predicate="proposed_year", object_value=2015
        )
        new = make_assertion(
            graph_id=graph,
            subject_identity=subject,
            predicate="proposed_year",
            object_value=2014,
            recorded_at=old.recorded_at + timedelta(days=1),
        )

        # Seed: the original claim is live at epoch 1.
        seed_payload = {
            k: v for k, v in old.model_dump(mode="json").items() if k != "curation_epoch"
        }
        seed = CurationPlan(
            plan_id="pl_seed",
            candidate_ids=(subject,),
            snapshot_version="0",
            operations=(
                CurationOperation(
                    operation_id="op_seed",
                    type=CurationOperationType.ATTACH_ASSERTION,
                    payload=seed_payload,
                    reversal_data={},
                ),
            ),
            preconditions=(),
            evidence_ids=(),
            policy_version="1",
        )
        assert executor.execute(seed).outcome is ExecutionOutcome.COMMITTED
        assert store.current_epoch() == 1

        # New evidence supersedes it at epoch 2.
        trigger = CurationTrigger.of(
            kind=TriggerKind.NEW_EVIDENCE,
            identity_ids=(subject,),
            assertion_ids=(old.assertion_id,),
            evidence_ids=("ev_b",),
            trace_id="supersession-rollback",
        )
        result = ConceptEvolutionPlanner(
            snapshot_version=str(store.current_epoch()), matcher_version="rules/1"
        ).plan_supersession(old_assertion=old, new_assertion=new, trigger=trigger)
        assert result.plan is not None
        record = executor.execute(result.plan)
        assert record.outcome is ExecutionOutcome.COMMITTED
        assert record.new_epoch == 2
        assert [a.object_value for a in store.assertions_for(subject)] == [2014]

        # Roll it back, guarded against the epoch it committed at.
        comp = Compensator().compensate(result.plan, against_snapshot=record.new_epoch)
        assert comp.fully_compensable is True
        assert comp.snapshot_guarded is True
        assert comp.plan is not None
        # LIFO: restore the record that was superseded, then retract its
        # replacement.
        assert [op.type for op in comp.plan.operations] == [
            CurationOperationType.ATTACH_ASSERTION,
            CurationOperationType.RETRACT_ASSERTION,
        ]

        rollback = executor.execute(comp.plan, is_compensation=True)
        assert rollback.outcome is ExecutionOutcome.COMMITTED
        assert rollback.new_epoch == 3

        # The original claim is live again; its replacement is superseded.
        (live,) = store.assertions_for(subject)
        assert live.assertion_id == old.assertion_id
        assert live.object_value == 2015
        assert live.status is CurationStatus.ACTIVE

        # And nothing was deleted: all three transactions remain queryable
        # (§9 law 10 — rollback never rewrites history).
        history = store.assertions_for(subject, GraphReadOptions(include_superseded=True))
        assert [(a.object_value, a.status, a.curation_epoch) for a in history] == [
            (2015, CurationStatus.SUPERSEDED, 1),
            (2014, CurationStatus.SUPERSEDED, 2),
            (2015, CurationStatus.ACTIVE, 3),
        ]
