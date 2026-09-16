"""Audit records: one per operation, ordered, deterministic, complete."""

from datetime import UTC, datetime

from kg_contracts.candidates import CandidateScores
from kg_contracts.testing.factories import make_entity_candidate

from helpers import GRAPH_ID
from kgcs import (
    AuditRecorder,
    AuditSink,
    CurationPlanner,
    DerivedIdFactory,
    FixedClock,
    InMemoryAuditSink,
    ResolutionPolicy,
    ResolvedCandidate,
)
from kgcs.testing import AuditSinkContract


def _planned(scores: CandidateScores, n: int) -> tuple:
    policy = ResolutionPolicy()
    resolved = [
        ResolvedCandidate(
            candidate=(c := make_entity_candidate(graph_id=GRAPH_ID, key=f"e{i}", scores=scores)),
            resolution=policy.resolve(c),
        )
        for i in range(n)
    ]
    return CurationPlanner().plan(resolved).planned_operations


class TestAuditBuild:
    def test_one_record_per_operation(self, auto_scores: CandidateScores) -> None:
        recorder = AuditRecorder.create(clock=FixedClock(datetime(2026, 7, 17, tzinfo=UTC)))
        records = recorder.build(_planned(auto_scores, 3))
        assert len(records) == 3

    def test_records_follow_operation_order(self, auto_scores: CandidateScores) -> None:
        planned = _planned(auto_scores, 3)
        records = AuditRecorder.create(
            clock=FixedClock(datetime(2026, 7, 17, tzinfo=UTC))
        ).build(planned)
        assert [r.operation_id for r in records] == [p.operation.operation_id for p in planned]

    def test_recorded_at_uses_the_injected_clock(self, auto_scores: CandidateScores) -> None:
        instant = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
        records = AuditRecorder.create(clock=FixedClock(instant)).build(_planned(auto_scores, 1))
        assert records[0].recorded_at == instant

    def test_fixed_clock_makes_audit_deterministic(self, auto_scores: CandidateScores) -> None:
        planned = _planned(auto_scores, 2)
        recorder = AuditRecorder.create(clock=FixedClock(datetime(2026, 7, 17, tzinfo=UTC)))
        assert recorder.build(planned) == recorder.build(planned)

    def test_record_captures_trace_score_and_policy(self, auto_scores: CandidateScores) -> None:
        planned = _planned(auto_scores, 1)
        record = AuditRecorder.create(
            clock=FixedClock(datetime(2026, 7, 17, tzinfo=UTC)),
            id_factory=DerivedIdFactory(),
            policy_version="1",
        ).build(planned)[0]
        candidate = planned[0].candidate
        assert record.trace_id == candidate.trace_id
        assert record.score_vector["extraction_confidence"] == candidate.scores.extraction_confidence
        assert record.policy_version == "1"

    def test_empty_operations_yield_no_records(self) -> None:
        recorder = AuditRecorder.create(clock=FixedClock(datetime(2026, 7, 17, tzinfo=UTC)))
        assert recorder.build(()) == ()


class TestInMemoryAuditSink(AuditSinkContract):
    def make_sink(self) -> AuditSink:
        return InMemoryAuditSink()
