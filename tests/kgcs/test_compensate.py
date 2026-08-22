"""Compensating-plan generation: inverse map, ordering, non-compensable ops.

Proves invariant 8: every operation is either compensable (an inverse is
emitted) or explicitly declared non-compensable, and a compensation executed
against the reference store fails explicitly rather than crashing.
"""

from datetime import UTC, datetime

import pytest
from helpers import GRAPH_ID
from kg_contracts.candidates import CandidateScores
from kg_contracts.curation import CurationOperation, CurationOperationType, CurationPlan
from kg_contracts.testing.factories import make_attribute_candidate, make_entity_candidate
from kg_contracts.testing.memory import MemoryGraphStore

from kgcs import (
    Compensator,
    CurationEngine,
    ExecutionOutcome,
    FixedClock,
    PlanExecutor,
)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 8, 22, tzinfo=UTC))


@pytest.fixture
def engine(clock: FixedClock) -> CurationEngine:
    return CurationEngine.create(graph_id=GRAPH_ID, clock=clock)


def _handbuilt(op_type: CurationOperationType) -> CurationPlan:
    return CurationPlan(
        plan_id=f"pl_{op_type.value.lower()}",
        candidate_ids=("cand_1",),
        snapshot_version="0",
        operations=(
            CurationOperation(
                operation_id=f"op_{op_type.value.lower()}",
                type=op_type,
                payload={"forward": "payload"},
                reversal_data={"undo": "data"},
            ),
        ),
        preconditions=(),
        evidence_ids=(),
        policy_version="1",
    )


class TestInverseMap:
    def test_attach_assertion_inverts_to_retract(
        self, engine: CurationEngine, auto_scores: CandidateScores
    ) -> None:
        plan = engine.curate(
            [make_attribute_candidate(graph_id=GRAPH_ID, scores=auto_scores)]
        ).plan
        assert plan is not None
        result = Compensator().compensate(plan)

        assert result.fully_compensable
        assert result.plan is not None
        (op,) = result.plan.operations
        assert op.type is CurationOperationType.RETRACT_ASSERTION
        # payload is the original attach's reversal_data, which names the
        # assertion a retract must target
        assert "assertion_id" in op.payload
        assert op.reversal_data["original_type"] == "ATTACH_ASSERTION"

    def test_create_identity_is_non_compensable(
        self, engine: CurationEngine, auto_scores: CandidateScores
    ) -> None:
        plan = engine.curate(
            [make_entity_candidate(graph_id=GRAPH_ID, scores=auto_scores)]
        ).plan
        assert plan is not None
        result = Compensator().compensate(plan)

        assert not result.fully_compensable
        assert result.plan is None  # the only op had no inverse
        assert len(result.non_compensable) == 1
        assert result.non_compensable[0].type is CurationOperationType.CREATE_IDENTITY

    @pytest.mark.parametrize(
        ("forward", "inverse"),
        [
            (CurationOperationType.ATTACH_ASSERTION, CurationOperationType.RETRACT_ASSERTION),
            (CurationOperationType.RETRACT_ASSERTION, CurationOperationType.ATTACH_ASSERTION),
            (CurationOperationType.MERGE_IDENTITIES, CurationOperationType.SPLIT_IDENTITY),
            (CurationOperationType.SPLIT_IDENTITY, CurationOperationType.MERGE_IDENTITIES),
            (CurationOperationType.REASSIGN_ASSERTION, CurationOperationType.REASSIGN_ASSERTION),
        ],
    )
    def test_compensable_types_map_to_their_inverse(
        self, forward: CurationOperationType, inverse: CurationOperationType
    ) -> None:
        result = Compensator().compensate(_handbuilt(forward))
        assert result.plan is not None
        (op,) = result.plan.operations
        assert op.type is inverse
        # the inverse payload is the forward op's reversal_data
        assert op.payload == {"undo": "data"}
        assert op.reversal_data["original_payload"] == {"forward": "payload"}

    @pytest.mark.parametrize(
        "non_inverse",
        [CurationOperationType.CREATE_IDENTITY, CurationOperationType.PROMOTE_ONTOLOGY_TERM],
    )
    def test_non_compensable_types(self, non_inverse: CurationOperationType) -> None:
        result = Compensator().compensate(_handbuilt(non_inverse))
        assert result.plan is None
        assert result.non_compensable[0].type is non_inverse


class TestOrderingAndMixing:
    def test_operations_are_reversed_lifo(self) -> None:
        plan = CurationPlan(
            plan_id="pl_two_attach",
            candidate_ids=("cand_1", "cand_2"),
            snapshot_version="0",
            operations=(
                CurationOperation(
                    operation_id="op_first",
                    type=CurationOperationType.ATTACH_ASSERTION,
                    payload={},
                    reversal_data={"assertion_id": "as_first"},
                ),
                CurationOperation(
                    operation_id="op_second",
                    type=CurationOperationType.ATTACH_ASSERTION,
                    payload={},
                    reversal_data={"assertion_id": "as_second"},
                ),
            ),
            preconditions=(),
            evidence_ids=(),
            policy_version="1",
        )
        result = Compensator().compensate(plan)
        assert result.plan is not None
        undone = [op.reversal_data["compensates_operation_id"] for op in result.plan.operations]
        assert undone == ["op_second", "op_first"]  # last applied, first undone

    def test_mixed_plan_compensates_assertion_reports_create(
        self, engine: CurationEngine, auto_scores: CandidateScores
    ) -> None:
        plan = engine.curate(
            [
                make_entity_candidate(graph_id=GRAPH_ID, scores=auto_scores),
                make_attribute_candidate(graph_id=GRAPH_ID, scores=auto_scores),
            ]
        ).plan
        assert plan is not None
        result = Compensator().compensate(plan)
        assert not result.fully_compensable
        assert result.plan is not None
        assert {op.type for op in result.plan.operations} == {
            CurationOperationType.RETRACT_ASSERTION
        }
        assert {op.type for op in result.non_compensable} == {
            CurationOperationType.CREATE_IDENTITY
        }


class TestDeterminismAndExecution:
    def test_compensation_is_deterministic_and_serializable(
        self, engine: CurationEngine, auto_scores: CandidateScores
    ) -> None:
        plan = engine.curate(
            [make_attribute_candidate(graph_id=GRAPH_ID, scores=auto_scores)]
        ).plan
        assert plan is not None
        a = Compensator().compensate(plan).plan
        b = Compensator().compensate(plan).plan
        assert a is not None and b is not None
        assert a.model_dump_json() == b.model_dump_json()
        # round-trips through the executor seam
        assert CurationPlan.model_validate_json(a.model_dump_json()) == a

    def test_executing_compensation_on_reference_store_is_unsupported(
        self, engine: CurationEngine, auto_scores: CandidateScores, clock: FixedClock
    ) -> None:
        # The reference store does not implement RETRACT_ASSERTION (lands in a
        # later wave), so a compensation is generated but explicitly reported
        # unsupported rather than crashing.
        plan = engine.curate(
            [make_attribute_candidate(graph_id=GRAPH_ID, scores=auto_scores)]
        ).plan
        assert plan is not None
        compensation = Compensator().compensate(plan).plan
        assert compensation is not None
        store = MemoryGraphStore()
        record = PlanExecutor(store, clock=clock).execute(compensation, is_compensation=True)
        assert record.outcome is ExecutionOutcome.UNSUPPORTED_OPERATION
        assert record.is_compensation
        assert "RETRACT_ASSERTION" in record.unsupported_types
        assert store.current_epoch() == 0
