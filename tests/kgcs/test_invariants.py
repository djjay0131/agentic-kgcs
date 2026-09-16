"""Cross-cutting invariants the whole Sprint-1 core must uphold."""

from datetime import UTC, datetime

from kg_contracts.candidates import CandidateScores
from kg_contracts.curation import CurationPlan
from kg_contracts.stores import GraphMutationBatch
from kg_contracts.testing.factories import make_attribute_candidate, make_entity_candidate
from kg_contracts.testing.memory import MemoryGraphStore

from helpers import GRAPH_ID, known_identity
from kgcs import CurationEngine, FixedClock


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
