"""The transaction-aware executor: commit, epoch, stale-plan, unsupported ops.

Every test drives the executor against the reference `MemoryGraphStore` from
`kg_contracts.testing`, the same adapter the deterministic core's plans were
proven to apply to in Wave 0.
"""

from datetime import UTC, datetime
from typing import Sequence

import pytest
from kg_contracts.candidates import CandidateScores
from kg_contracts.curation import (
    CurationOperation,
    CurationOperationType,
    CurationPlan,
    Precondition,
)
from kg_contracts.evidence import EvidenceRef, EvidenceRelationship
from kg_contracts.stores import (
    CommitResult,
    GraphMutationBatch,
    GraphReader,
    GraphReadOptions,
)
from kg_contracts.testing.factories import make_attribute_candidate, make_entity_candidate
from kg_contracts.testing.memory import MemoryGraphStore

from helpers import GRAPH_ID, known_identity
from kgcs import (
    INVERSE_PAYLOAD_KEY,
    CurationEngine,
    DerivedIdFactory,
    ExecutionOutcome,
    FixedClock,
    InMemoryEpochPublisher,
    InMemoryExecutionAuditSink,
    PlanExecutor,
)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 8, 22, tzinfo=UTC))


@pytest.fixture
def engine(clock: FixedClock) -> CurationEngine:
    return CurationEngine.create(graph_id=GRAPH_ID, clock=clock)


def _entity_plan(engine: CurationEngine, scores: CandidateScores) -> CurationPlan:
    result = engine.curate([make_entity_candidate(graph_id=GRAPH_ID, scores=scores)])
    assert result.plan is not None
    return result.plan


def _attribute_plan(engine: CurationEngine, scores: CandidateScores) -> CurationPlan:
    result = engine.curate([make_attribute_candidate(graph_id=GRAPH_ID, scores=scores)])
    assert result.plan is not None
    return result.plan


class TestCommit:
    def test_create_identity_commits_and_advances_epoch(
        self, engine: CurationEngine, auto_scores: CandidateScores, clock: FixedClock
    ) -> None:
        plan = _entity_plan(engine, auto_scores)
        store = MemoryGraphStore()
        publisher = InMemoryEpochPublisher()
        executor = PlanExecutor(store, clock=clock, epoch_publisher=publisher)

        record = executor.execute(plan)

        assert record.outcome is ExecutionOutcome.COMMITTED
        assert record.committed
        assert record.new_epoch == 1
        assert store.current_epoch() == 1
        assert publisher.published_epoch() == 1
        # the minted identity is now readable from canonical state
        identity_id = plan.operations[0].reversal_data["identity_id"]
        assert store.get_entity(str(identity_id)) is not None

    def test_attach_assertion_commits(
        self, engine: CurationEngine, auto_scores: CandidateScores, clock: FixedClock
    ) -> None:
        plan = _attribute_plan(engine, auto_scores)
        assert plan.operations[0].type is CurationOperationType.ATTACH_ASSERTION
        store = MemoryGraphStore()
        record = PlanExecutor(store, clock=clock).execute(plan)
        assert record.outcome is ExecutionOutcome.COMMITTED
        assert store.current_epoch() == 1

    def test_execution_audit_is_recorded(
        self, engine: CurationEngine, auto_scores: CandidateScores, clock: FixedClock
    ) -> None:
        plan = _entity_plan(engine, auto_scores)
        sink = InMemoryExecutionAuditSink()
        PlanExecutor(MemoryGraphStore(), clock=clock, audit_sink=sink).execute(plan)

        assert len(sink.records()) == 1
        rec = sink.records()[0]
        assert rec.plan_id == plan.plan_id
        assert rec.operation_ids == tuple(op.operation_id for op in plan.operations)
        assert rec.outcome is ExecutionOutcome.COMMITTED
        assert not rec.is_compensation


class TestStalePlan:
    def test_reexecuting_a_committed_plan_is_rejected_stale(
        self, engine: CurationEngine, auto_scores: CandidateScores, clock: FixedClock
    ) -> None:
        # Idempotent operation ids: a CREATE_IDENTITY carries entity_version=0,
        # which no longer holds after the first commit, so the second execute
        # is rejected rather than double-applied.
        plan = _entity_plan(engine, auto_scores)
        store = MemoryGraphStore()
        executor = PlanExecutor(store, clock=clock)

        first = executor.execute(plan)
        second = executor.execute(plan)

        assert first.outcome is ExecutionOutcome.COMMITTED
        assert second.outcome is ExecutionOutcome.STALE
        assert second.failed_preconditions  # non-empty
        assert second.new_epoch is None
        # store advanced exactly once; the stale re-apply changed nothing
        assert store.current_epoch() == 1

    def test_stale_plan_leaves_store_unchanged(
        self, engine: CurationEngine, auto_scores: CandidateScores, clock: FixedClock
    ) -> None:
        plan = _entity_plan(engine, auto_scores)
        store = MemoryGraphStore()
        # Pre-mint the identity by committing once, then a fresh publisher must
        # never advance on the stale retry.
        PlanExecutor(store, clock=clock).execute(plan)
        publisher = InMemoryEpochPublisher()
        retry = PlanExecutor(store, clock=clock, epoch_publisher=publisher).execute(plan)
        assert retry.outcome is ExecutionOutcome.STALE
        assert publisher.published_epoch() is None

    def test_reexecuting_a_committed_attach_plan_is_rejected_stale(
        self, engine: CurationEngine, auto_scores: CandidateScores, clock: FixedClock
    ) -> None:
        # Regression: an ATTACH_ASSERTION plan carries no per-subject
        # entity_version guard (ADR-0003), so idempotency rests on the
        # executor enforcing the plan-level snapshot precondition against the
        # graph epoch. Without that, a replay would double-attach.
        plan = _attribute_plan(engine, auto_scores)
        assert plan.operations[0].type is CurationOperationType.ATTACH_ASSERTION
        store = MemoryGraphStore()
        executor = PlanExecutor(store, clock=clock)

        first = executor.execute(plan)
        second = executor.execute(plan)

        assert first.outcome is ExecutionOutcome.COMMITTED
        assert second.outcome is ExecutionOutcome.STALE
        assert second.failed_preconditions
        assert store.current_epoch() == 1  # exactly one commit, no double-attach
        # and nothing was double-attached to canonical state
        inverse_payload = plan.operations[0].reversal_data[INVERSE_PAYLOAD_KEY]
        assert isinstance(inverse_payload, dict)
        subject = str(inverse_payload["subject_identity"])
        assert len(store.assertions_for(subject)) == 1

    def test_executor_enforces_snapshot_precondition_against_epoch(
        self, engine: CurationEngine, auto_scores: CandidateScores, clock: FixedClock
    ) -> None:
        # A plan built against snapshot "0" cannot apply once the graph has
        # advanced to epoch 1, even though the reference store itself never
        # enforces snapshot_version preconditions.
        plan = _attribute_plan(engine, auto_scores)
        store = MemoryGraphStore()
        # advance the graph to epoch 1 with an unrelated commit
        PlanExecutor(store, clock=clock).execute(_entity_plan(engine, auto_scores))
        assert store.current_epoch() == 1

        record = PlanExecutor(store, clock=clock).execute(plan)
        assert record.outcome is ExecutionOutcome.STALE
        assert any(p.kind == "snapshot_version" for p in record.failed_preconditions)


class TestAttachReplay:
    """ADR-0019: an `ATTACH_ASSERTION` carries its own per-subject guard.

    The snapshot guard alone only refuses an *unmodified* replay of a committed
    plan. Re-planning the same candidate against the graph's current snapshot
    satisfies it, which is the hole these tests pin shut — while leaving
    re-assertion of the same fact from a new candidate (new evidence) open.
    """

    @staticmethod
    def _attach_plan_at(
        engine_clock: FixedClock, candidate: object, snapshot: str
    ) -> CurationPlan:
        """Plan one candidate against an explicit snapshot version."""
        engine = CurationEngine.create(
            graph_id=GRAPH_ID, clock=engine_clock, snapshot_version=snapshot
        )
        result = engine.curate([candidate])  # type: ignore[list-item]
        assert result.plan is not None, "fixture broken: no plan to execute"
        attach_ops = [
            op
            for op in result.plan.operations
            if op.type is CurationOperationType.ATTACH_ASSERTION
        ]
        # Non-vacuity: a replay test that plans zero attach operations would
        # pass no matter what the executor does.
        assert attach_ops, "fixture broken: no ATTACH_ASSERTION operation planned"
        return result.plan

    def test_replanned_attach_against_the_current_snapshot_is_refused(
        self, auto_scores: CandidateScores, clock: FixedClock
    ) -> None:
        candidate = make_attribute_candidate(graph_id=GRAPH_ID, scores=auto_scores)
        store = MemoryGraphStore()
        executor = PlanExecutor(store, clock=clock)

        first = self._attach_plan_at(clock, candidate, "0")
        subject = str(first.operations[0].payload["subject_identity"])
        assertion_id = str(first.operations[0].payload["assertion_id"])
        assert executor.execute(first).outcome is ExecutionOutcome.COMMITTED

        # Re-plan the SAME candidate against the snapshot the graph is now at,
        # so the plan-level snapshot guard is satisfied and cannot be what
        # refuses the replay.
        replay = self._attach_plan_at(clock, candidate, str(store.current_epoch()))
        record = executor.execute(replay)

        assert record.outcome is ExecutionOutcome.STALE
        assert not [p for p in record.failed_preconditions if p.kind == "snapshot_version"]
        assert [
            (p.subject, p.expected)
            for p in record.failed_preconditions
            if p.kind == "assertion_absent"
        ] == [(subject, assertion_id)]
        # Canonical state holds the assertion once, by identity — not merely a
        # count that happens to be one.
        attached = store.assertions_for(subject)
        assert [a.assertion_id for a in attached] == [assertion_id]
        assert store.current_epoch() == 1

    def test_reassertion_from_a_new_candidate_with_new_evidence_still_applies(
        self, auto_scores: CandidateScores, clock: FixedClock
    ) -> None:
        # Release-critical downstream: the same fact re-attached later with NEW
        # evidence is a distinct candidate, mints a distinct assertion id, and
        # must still apply. The replay guard must not foreclose it.
        subject = known_identity()
        first_evidence = (
            EvidenceRef(evidence_id="ev_first", relationship=EvidenceRelationship.SUPPORTS),
        )
        later_evidence = (
            EvidenceRef(evidence_id="ev_later", relationship=EvidenceRelationship.SUPPORTS),
        )
        original = make_attribute_candidate(
            graph_id=GRAPH_ID, subject=subject, attribute="height_cm", value=200,
            scores=auto_scores,
        ).model_copy(update={"evidence_refs": first_evidence})
        corroborating = make_attribute_candidate(
            graph_id=GRAPH_ID, subject=subject, attribute="height_cm", value=200,
            scores=auto_scores,
        ).model_copy(update={"evidence_refs": later_evidence})
        assert original.candidate_id != corroborating.candidate_id

        store = MemoryGraphStore()
        executor = PlanExecutor(store, clock=clock)

        plan_a = self._attach_plan_at(clock, original, "0")
        assert executor.execute(plan_a).outcome is ExecutionOutcome.COMMITTED
        plan_b = self._attach_plan_at(clock, corroborating, str(store.current_epoch()))
        second = executor.execute(plan_b)

        assert second.outcome is ExecutionOutcome.COMMITTED, (
            f"re-assertion with new evidence was refused: {second.failed_preconditions}"
        )
        attached = store.assertions_for(subject)
        assert len(set(a.assertion_id for a in attached)) == 2  # two distinct records
        assert {ref.evidence_id for a in attached for ref in a.evidence_refs} == {
            "ev_first",
            "ev_later",
        }
        assert store.current_epoch() == 2

    def test_a_superseded_assertion_still_blocks_a_replay_of_its_id(
        self, auto_scores: CandidateScores, clock: FixedClock
    ) -> None:
        # Supersession marks a record, it never deletes it. A default canonical
        # read hides SUPERSEDED rows, so an absence check that forgot
        # `include_superseded` would read "absent" and wave the replay through.
        candidate = make_attribute_candidate(graph_id=GRAPH_ID, scores=auto_scores)
        store = MemoryGraphStore()
        executor = PlanExecutor(store, clock=clock)

        first = self._attach_plan_at(clock, candidate, "0")
        subject = str(first.operations[0].payload["subject_identity"])
        assertion_id = str(first.operations[0].payload["assertion_id"])
        assert executor.execute(first).outcome is ExecutionOutcome.COMMITTED
        store.mark_superseded(assertion_id, clock.now())
        assert store.assertions_for(subject) == []  # invisible to a default read

        replay = self._attach_plan_at(clock, candidate, str(store.current_epoch()))
        record = executor.execute(replay)

        assert record.outcome is ExecutionOutcome.STALE
        assert any(p.kind == "assertion_absent" for p in record.failed_preconditions)
        visible = store.assertions_for(subject, GraphReadOptions(include_superseded=True))
        assert [a.assertion_id for a in visible] == [assertion_id]

    def test_without_a_reader_the_attach_guard_is_unenforceable(
        self, auto_scores: CandidateScores, clock: FixedClock
    ) -> None:
        # The documented residual limit, pinned rather than left silent: both
        # executor-enforced guards need a `GraphReader`. Over a write-only
        # store the executor has nothing to check them against, and the
        # reference precondition contract passes every non-`entity_version`
        # kind, so the replay commits.
        candidate = make_attribute_candidate(graph_id=GRAPH_ID, scores=auto_scores)
        store = _WriteOnlyStore()
        assert not isinstance(store, GraphReader)
        executor = PlanExecutor(store, clock=clock)

        plan = self._attach_plan_at(clock, candidate, "0")
        assert executor.execute(plan).outcome is ExecutionOutcome.COMMITTED
        assert executor.execute(plan).outcome is ExecutionOutcome.COMMITTED
        assert store.applied == 2


class _WriteOnlyStore:
    """A `GraphMutationStore` that is deliberately not a `GraphReader`.

    Mirrors the reference adapter's precondition contract — only
    `entity_version` is enforced, every other kind passes — so the only
    difference from `MemoryGraphStore` under test is the missing read surface.
    """

    def __init__(self) -> None:
        self.applied = 0
        self._epoch = 0

    def apply(
        self, batch: GraphMutationBatch, preconditions: Sequence[Precondition]
    ) -> CommitResult:
        self.applied += 1
        self._epoch += 1
        return CommitResult(batch_id=batch.batch_id, committed=True, new_epoch=self._epoch)


class TestUnsupportedOperations:
    def _merge_plan(self) -> CurationPlan:
        return CurationPlan(
            plan_id="pl_merge_test",
            candidate_ids=("cand_1",),
            snapshot_version="0",
            operations=(
                CurationOperation(
                    operation_id="op_merge_1",
                    type=CurationOperationType.MERGE_IDENTITIES,
                    payload={"survivor": "x", "merged": ["y"]},
                    reversal_data={"members": ["x", "y"]},
                ),
            ),
            preconditions=(),
            evidence_ids=(),
            policy_version="1",
        )

    def test_unsupported_operation_fails_without_touching_store(
        self, clock: FixedClock
    ) -> None:
        store = MemoryGraphStore()
        record = PlanExecutor(store, clock=clock).execute(self._merge_plan())
        assert record.outcome is ExecutionOutcome.UNSUPPORTED_OPERATION
        assert "MERGE_IDENTITIES" in record.unsupported_types
        assert record.batch_id is None  # never compiled a batch
        assert store.current_epoch() == 0  # store untouched

    def test_not_implemented_is_backstopped(self, clock: FixedClock) -> None:
        # Lie about support so the pre-check passes; the store then raises
        # NotImplementedError, which must become an explicit UNSUPPORTED result.
        store = MemoryGraphStore()
        executor = PlanExecutor(
            store,
            clock=clock,
            supported_operations=frozenset(CurationOperationType),
        )
        record = executor.execute(self._merge_plan())
        assert record.outcome is ExecutionOutcome.UNSUPPORTED_OPERATION
        assert record.error is not None
        assert store.current_epoch() == 0


class TestEmptyPlan:
    def test_empty_plan_is_empty_outcome(self, clock: FixedClock) -> None:
        plan = CurationPlan(
            plan_id="pl_empty",
            candidate_ids=("cand_1",),
            snapshot_version="0",
            operations=(),
            preconditions=(),
            evidence_ids=(),
            policy_version="1",
        )
        store = MemoryGraphStore()
        record = PlanExecutor(store, clock=clock).execute(plan)
        assert record.outcome is ExecutionOutcome.EMPTY
        assert store.current_epoch() == 0


class TestDeterminism:
    def test_same_plan_same_execution_record(
        self, engine: CurationEngine, auto_scores: CandidateScores, clock: FixedClock
    ) -> None:
        plan = _entity_plan(engine, auto_scores)
        rec_a = PlanExecutor(
            MemoryGraphStore(), id_factory=DerivedIdFactory(), clock=clock
        ).execute(plan)
        rec_b = PlanExecutor(
            MemoryGraphStore(), id_factory=DerivedIdFactory(), clock=clock
        ).execute(plan)
        assert rec_a.model_dump_json() == rec_b.model_dump_json()


class TestEndToEnd:
    def test_engine_to_canonical_graph_advances_visible_epoch(
        self, engine: CurationEngine, auto_scores: CandidateScores, clock: FixedClock
    ) -> None:
        # Wave 1 exit criterion: one in-memory path from Candidate to canonical
        # state that advances a visible epoch.
        store = MemoryGraphStore()
        publisher = InMemoryEpochPublisher()
        executor = PlanExecutor(store, clock=clock, epoch_publisher=publisher)

        assert store.current_epoch() == 0
        result = engine.curate([make_entity_candidate(graph_id=GRAPH_ID, scores=auto_scores)])
        assert result.plan is not None
        record = executor.execute(result.plan)

        assert record.committed
        assert store.current_epoch() == 1
        assert publisher.published_epoch() == 1
        found = store.find_entities(entity_type="TestEntity")
        assert len(found) == 1
        assert found[0].curation_epoch == 1
