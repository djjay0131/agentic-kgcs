"""The engine: orchestration invariants over the whole pipeline."""

from datetime import UTC, datetime

from kg_contracts.candidates import Candidate, CandidateScores
from kg_contracts.curation import ResolutionDecision
from kg_contracts.testing.factories import make_attribute_candidate, make_entity_candidate

from kgcs import (
    AuditRecorder,
    CurationEngine,
    CurationPlanner,
    FixedClock,
    InMemoryAuditSink,
    ResolutionPolicy,
    default_validator,
)

from helpers import GRAPH_ID, known_identity


class _SpyPolicy(ResolutionPolicy):
    """A resolution policy that counts how many candidates reached it."""

    def __init__(self) -> None:
        super().__init__()
        self.seen: list[str] = []

    def resolve(self, candidate: Candidate) -> ResolutionDecision:
        self.seen.append(candidate.candidate_id)
        return super().resolve(candidate)


def _engine_with(policy: ResolutionPolicy, sink: InMemoryAuditSink | None = None) -> CurationEngine:
    return CurationEngine(
        validator=default_validator(graph_id=GRAPH_ID),
        policy=policy,
        planner=CurationPlanner(),
        audit_recorder=AuditRecorder.create(clock=FixedClock(datetime(2026, 7, 17, tzinfo=UTC))),
        audit_sink=sink,
    )


class TestFailClosedOrdering:
    def test_validation_failure_never_reaches_policy(self, auto_scores: CandidateScores) -> None:
        spy = _SpyPolicy()
        invalid = make_entity_candidate(graph_id="WRONG", scores=auto_scores)  # bad graph scope
        valid = make_entity_candidate(graph_id=GRAPH_ID, scores=auto_scores)
        result = _engine_with(spy).curate([invalid, valid])
        assert spy.seen == [valid.candidate_id]  # invalid one skipped
        assert result.outcomes[0].resolution is None
        assert result.outcomes[1].resolution is not None

    def test_invalid_candidate_contributes_no_operation(
        self, auto_scores: CandidateScores
    ) -> None:
        invalid = make_entity_candidate(graph_id="WRONG", scores=auto_scores)
        result = _engine_with(_SpyPolicy()).curate([invalid])
        assert result.plan is None
        assert result.audit_records == ()


class TestPipelineOutputs:
    def test_outcomes_are_in_input_order(self, auto_scores: CandidateScores) -> None:
        candidates = [
            make_entity_candidate(graph_id=GRAPH_ID, key=f"e{i}", scores=auto_scores)
            for i in range(3)
        ]
        result = _engine_with(ResolutionPolicy()).curate(candidates)
        assert [o.candidate_id for o in result.outcomes] == [c.candidate_id for c in candidates]

    def test_one_audit_record_per_planned_operation(self, auto_scores: CandidateScores) -> None:
        entity = make_entity_candidate(graph_id=GRAPH_ID, scores=auto_scores)
        attribute = make_attribute_candidate(
            graph_id=GRAPH_ID, subject=known_identity(), scores=auto_scores
        )
        result = _engine_with(ResolutionPolicy()).curate([entity, attribute])
        assert result.plan is not None
        assert len(result.audit_records) == len(result.plan.operations)

    def test_audit_records_reach_the_sink(self, auto_scores: CandidateScores) -> None:
        sink = InMemoryAuditSink()
        entity = make_entity_candidate(graph_id=GRAPH_ID, scores=auto_scores)
        result = _engine_with(ResolutionPolicy(), sink=sink).curate([entity])
        assert sink.records() == list(result.audit_records)

    def test_deferred_candidate_has_outcome_but_no_operation(
        self, human_scores: CandidateScores
    ) -> None:
        result = _engine_with(ResolutionPolicy()).curate(
            [make_entity_candidate(graph_id=GRAPH_ID, scores=human_scores)]
        )
        assert result.plan is None
        assert result.outcomes[0].resolution is not None  # resolved, just not auto-applied


class TestDeterminismAndIdempotency:
    def test_identical_input_yields_identical_result(
        self, engine: CurationEngine, auto_scores: CandidateScores
    ) -> None:
        entity = make_entity_candidate(graph_id=GRAPH_ID, scores=auto_scores)
        attribute = make_attribute_candidate(
            graph_id=GRAPH_ID, subject=known_identity(), scores=auto_scores
        )
        first = engine.curate([entity, attribute])
        second = engine.curate([entity, attribute])
        assert first.plan == second.plan
        assert first.audit_records == second.audit_records
        assert first.outcomes == second.outcomes

    def test_two_independent_engines_agree(self, auto_scores: CandidateScores) -> None:
        """Determinism is not per-engine state — a fresh engine replays it."""
        entity = make_entity_candidate(graph_id=GRAPH_ID, scores=auto_scores)
        clock = FixedClock(datetime(2026, 7, 17, tzinfo=UTC))
        a = CurationEngine.create(graph_id=GRAPH_ID, clock=clock).curate([entity])
        b = CurationEngine.create(graph_id=GRAPH_ID, clock=clock).curate([entity])
        assert a.plan == b.plan
        assert a.audit_records == b.audit_records

    def test_empty_batch_produces_empty_result(self, engine: CurationEngine) -> None:
        result = engine.curate([])
        assert result.outcomes == ()
        assert result.plan is None
        assert result.audit_records == ()
