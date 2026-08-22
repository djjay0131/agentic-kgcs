"""Reusable contract suites for `AuditSink` and `CandidateValidator`.

Subclass a suite, implement its one factory method, and the implementation is
held to the port's rules. These suites test *contract behavior* — ordering,
determinism, the decision invariants the whole pipeline relies on — not any
one implementation's internals.
"""

from datetime import UTC, datetime

from kg_contracts.curation import AuditRecord, FailureKind
from kg_contracts.testing.factories import make_entity_candidate, make_scores

from kgcs.audit import AuditSink
from kgcs.validation import CandidateValidator

_AUTO_SCORES = make_scores(
    extraction_confidence=0.99, source_reliability=0.99, identity_confidence=0.99
)


def _sample_audit(operation_id: str) -> AuditRecord:
    """A minimal valid `AuditRecord` for exercising an `AuditSink`."""
    return AuditRecord(
        operation_id=operation_id,
        decided_by="test",
        score_vector={"extraction_confidence": 0.99},
        evidence_ids=(),
        policy_version="1",
        trace_id="trace_test",
        recorded_at=datetime(2026, 7, 17, tzinfo=UTC),
    )


class AuditSinkContract:
    """Subclass and implement `make_sink()`.

    An `AuditSink` is append-only and order-preserving, and `records()` must
    hand back a snapshot a caller cannot use to mutate the log.
    """

    def make_sink(self) -> AuditSink:
        """Return a fresh, empty `AuditSink`."""
        raise NotImplementedError

    def test_starts_empty(self) -> None:
        assert self.make_sink().records() == []

    def test_records_appear_in_append_order(self) -> None:
        sink = self.make_sink()
        first = _sample_audit("op_a")
        second = _sample_audit("op_b")
        sink.record(first)
        sink.record(second)
        assert [r.operation_id for r in sink.records()] == ["op_a", "op_b"]

    def test_records_returns_a_defensive_copy(self) -> None:
        """Mutating the returned list must not corrupt the log."""
        sink = self.make_sink()
        sink.record(_sample_audit("op_a"))
        snapshot = sink.records()
        snapshot.clear()
        assert [r.operation_id for r in sink.records()] == ["op_a"]

    def test_records_are_audit_records(self) -> None:
        sink = self.make_sink()
        sink.record(_sample_audit("op_a"))
        assert all(isinstance(r, AuditRecord) for r in sink.records())


class CandidateValidatorContract:
    """Subclass and implement `make_validator()`.

    Every `CandidateValidator` must honor the `ValidationDecision` invariants
    and be a deterministic, pure function of the candidate.
    """

    def make_validator(self) -> CandidateValidator:
        """Return a validator that accepts a well-formed `entity` candidate for
        graph `g1` (the fixture below)."""
        raise NotImplementedError

    def test_accepts_a_well_formed_candidate(self) -> None:
        candidate = make_entity_candidate(graph_id="g1", scores=_AUTO_SCORES)
        decision = self.make_validator().validate(candidate)
        assert decision.valid
        assert decision.failure_kind is None
        assert decision.reasons == ()

    def test_decision_echoes_candidate_identity(self) -> None:
        candidate = make_entity_candidate(graph_id="g1", scores=_AUTO_SCORES)
        decision = self.make_validator().validate(candidate)
        assert decision.candidate_id == candidate.candidate_id
        assert decision.trace_id == candidate.trace_id

    def test_invalid_decision_names_a_failure_kind(self) -> None:
        """A candidate the validator rejects must carry a `FailureKind` and a
        reason — the contract forbids a bare rejection."""
        candidate = make_entity_candidate(graph_id="OTHER-GRAPH-!", scores=_AUTO_SCORES)
        decision = self.make_validator().validate(candidate)
        if decision.valid:
            return  # an implementation may legitimately accept this; nothing to assert
        assert isinstance(decision.failure_kind, FailureKind)
        assert decision.reasons != ()

    def test_validation_is_deterministic(self) -> None:
        candidate = make_entity_candidate(graph_id="g1", scores=_AUTO_SCORES)
        validator = self.make_validator()
        assert validator.validate(candidate) == validator.validate(candidate)
