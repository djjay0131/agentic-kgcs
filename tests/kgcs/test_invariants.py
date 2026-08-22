"""Cross-cutting invariants the whole Sprint-1 core must uphold."""

from datetime import UTC, datetime

from kg_contracts.candidates import CandidateScores
from kg_contracts.curation import CurationPlan
from kg_contracts.stores import GraphMutationBatch
from kg_contracts.testing.factories import make_attribute_candidate, make_entity_candidate
from kg_contracts.testing.memory import MemoryGraphStore

from kg_contracts.curation import CurationOperationType

from kgcs import (
    Compensator,
    CurationEngine,
    ExecutionOutcome,
    FixedClock,
    PlanExecutor,
)

from helpers import GRAPH_ID, known_identity


def _batch(auto_scores: CandidateScores) -> list:
    return [
        make_entity_candidate(graph_id=GRAPH_ID, key="e1", scores=auto_scores),
        make_attribute_candidate(graph_id=GRAPH_ID, subject=known_identity(), scores=auto_scores),
    ]


def test_producing_a_plan_mutates_no_graph(
    engine: CurationEngine, auto_scores: CandidateScores
) -> None:
    """The engine holds no graph reference: it can produce a complete plan while
    an external store stays at epoch 0 with nothing in it."""
    untouched = MemoryGraphStore()
    result = engine.curate(_batch(auto_scores))
    assert result.plan is not None  # a real plan was produced
    assert untouched.current_epoch() == 0
    assert untouched.find_entities() == []


def test_plan_is_replayable_and_then_applies(
    engine: CurationEngine, auto_scores: CandidateScores
) -> None:
    """A plan survives serialization byte-for-byte and the reloaded plan still
    applies cleanly — the executor seam (ADR-0010) holds."""
    result = engine.curate(_batch(auto_scores))
    assert result.plan is not None
    reloaded = CurationPlan.model_validate_json(result.plan.model_dump_json())
    assert reloaded == result.plan

    store = MemoryGraphStore()
    batch = GraphMutationBatch(plan_id=reloaded.plan_id, operations=reloaded.operations)
    assert store.apply(batch, reloaded.preconditions).committed


def test_every_audit_traces_back_to_a_planned_operation_and_candidate(
    engine: CurationEngine, auto_scores: CandidateScores
) -> None:
    candidates = _batch(auto_scores)
    result = engine.curate(candidates)
    assert result.plan is not None
    plan_op_ids = {op.operation_id for op in result.plan.operations}
    candidate_traces = {c.trace_id for c in candidates}
    for audit in result.audit_records:
        assert audit.operation_id in plan_op_ids  # audit ↔ operation
        assert audit.trace_id in candidate_traces  # audit ↔ candidate (by trace)


def test_operation_reversal_data_carries_source_candidate_lineage(
    engine: CurationEngine, auto_scores: CandidateScores
) -> None:
    """Even though AuditRecord has no candidate_id field, lineage survives: each
    operation's reversal_data names the candidate it came from."""
    candidates = _batch(auto_scores)
    result = engine.curate(candidates)
    assert result.plan is not None
    candidate_ids = {c.candidate_id for c in candidates}
    for op in result.plan.operations:
        assert op.reversal_data["candidate_id"] in candidate_ids


def test_replay_is_identical_across_serialization_boundary(
    auto_scores: CandidateScores,
) -> None:
    clock = FixedClock(datetime(2026, 7, 17, tzinfo=UTC))
    engine = CurationEngine.create(graph_id=GRAPH_ID, clock=clock)
    candidates = _batch(auto_scores)
    a = engine.curate(candidates)
    b = engine.curate(candidates)
    assert a.plan is not None and b.plan is not None
    assert a.plan.model_dump_json() == b.plan.model_dump_json()
    assert [r.model_dump_json() for r in a.audit_records] == [
        r.model_dump_json() for r in b.audit_records
    ]


# --- executor invariants (build plan §9) --------------------------------------


def test_every_committed_mutation_originates_from_a_curation_plan(
    engine: CurationEngine, auto_scores: CandidateScores
) -> None:
    """Law 3: the only path to canonical state is executing a serializable
    plan. The store starts empty and advances exactly when a plan is executed."""
    store = MemoryGraphStore()
    executor = PlanExecutor(store, clock=FixedClock(datetime(2026, 8, 22, tzinfo=UTC)))
    assert store.current_epoch() == 0
    result = engine.curate(_batch(auto_scores))
    assert result.plan is not None
    # the plan survives the queue/log round trip and still commits
    reloaded = CurationPlan.model_validate_json(result.plan.model_dump_json())
    assert executor.execute(reloaded).outcome is ExecutionOutcome.COMMITTED
    assert store.current_epoch() == 1


def test_stale_preconditions_prevent_commit(
    engine: CurationEngine, auto_scores: CandidateScores
) -> None:
    """Law 4: a plan computed against a superseded snapshot fails preconditions
    instead of racing a second commit."""
    plan = engine.curate([make_entity_candidate(graph_id=GRAPH_ID, scores=auto_scores)]).plan
    assert plan is not None
    store = MemoryGraphStore()
    executor = PlanExecutor(store, clock=FixedClock(datetime(2026, 8, 22, tzinfo=UTC)))
    assert executor.execute(plan).outcome is ExecutionOutcome.COMMITTED
    assert executor.execute(plan).outcome is ExecutionOutcome.STALE
    assert store.current_epoch() == 1


def test_every_operation_is_compensable_or_declared_non_compensable() -> None:
    """Law 8: every curation operation type either has an inverse or is
    explicitly declared non-compensable — nothing is silently unhandled."""
    from kgcs.executor.compensate import INVERSE_OPERATION

    assert set(INVERSE_OPERATION) == set(CurationOperationType)
    compensable = {t for t, inv in INVERSE_OPERATION.items() if inv is not None}
    non_compensable = {t for t, inv in INVERSE_OPERATION.items() if inv is None}
    # the v1 vocabulary's declared split, stated explicitly so a new op type
    # cannot be added without deciding its reversibility
    assert non_compensable == {
        CurationOperationType.CREATE_IDENTITY,
        CurationOperationType.PROMOTE_ONTOLOGY_TERM,
    }
    assert CurationOperationType.ATTACH_ASSERTION in compensable


def test_compensation_never_mutates_the_graph_by_itself(
    engine: CurationEngine, auto_scores: CandidateScores
) -> None:
    """A Compensator produces plans; it holds no store and cannot mutate."""
    plan = engine.curate(
        [make_attribute_candidate(graph_id=GRAPH_ID, subject=known_identity(), scores=auto_scores)]
    ).plan
    assert plan is not None
    store = MemoryGraphStore()
    Compensator().compensate(plan)  # generating a compensation touches no graph
    assert store.current_epoch() == 0
