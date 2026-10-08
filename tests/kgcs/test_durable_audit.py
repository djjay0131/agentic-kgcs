"""Durable audit storage + assertion/re-curation semantic audit (issue #48).

Proves the three acceptance criteria:

- audit, execution, and semantic audit **persist** in SQLite and are
  **append-only** — a raw `UPDATE`/`DELETE` is aborted by the table's trigger;
- assertion and re-curation decisions produce semantic audit records
  (`AssertionSemanticAuditRecord`), joined to the plan and the trigger by id;
- `replay()` works from a record **read back from the durable sink** and
  reproduces the decision byte-identically.

Plus the read API (`records_for_trace` / `records_for_assertion` /
`records_for_operation`) and the injected-clock `recorded_at`.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from kg_contracts.assertions import Assertion
from kg_contracts.curation import AuditRecord
from kg_contracts.identity import new_identity_id
from kg_contracts.testing.factories import make_assertion

from kgcs.advisers.base import AdviserAssessment
from kgcs.advisers.orchestrator import CurationOrchestrator
from kgcs.advisers.specialists import AssertionRecommendation
from kgcs.clock import FixedClock
from kgcs.er.blocking import CandidatePair
from kgcs.er.matcher import CalibrationKey, MatchResult
from kgcs.er.resolution import ErAction
from kgcs.executor import ExecutionOutcome, ExecutionRecord
from kgcs.observability import (
    AssertionSemanticAuditRecord,
    EvolutionAuditRecorder,
    InMemorySemanticAuditSink,
    ReplayInputs,
    SemanticAuditBuilder,
    SemanticAuditRecord,
    replay,
)
from kgcs.persistence import SqliteAuditSink, SqliteExecutionSink, SqliteSemanticAuditSink
from kgcs.profiles import default_profile
from kgcs.recuration import ConceptEvolutionPlanner, EvolutionKind, EvolutionRouter
from kgcs.recuration.triggers import CurationTrigger, TriggerKind

_TRACE = "trace-durable"
_CLOCK = FixedClock(datetime(2026, 8, 1, 12, 0, tzinfo=UTC))
_PAIR = CandidatePair.of("paper/a", "paper/b")
_KEY = CalibrationKey.of(
    graph_id="g1",
    entity_type="Paper",
    source_pair=("source_a", "source_b"),
    matcher_version="rules/1",
    consequence_class="standard",
)


def _match_result(probability: float = 0.999) -> MatchResult:
    return MatchResult(
        pair=_PAIR,
        probability=probability,
        matcher_version="rules/1",
        feature_vector={"mutually_exclusive": 0.0},
        calibration_key=_KEY,
    )


def _er_record(*, trace_id: str = _TRACE) -> SemanticAuditRecord:
    """A deterministic AUTO_LINK ER decision (no adviser), so it replays bare."""
    mr = _match_result()
    result = CurationOrchestrator().resolve(mr, profile=default_profile(), trace_id=trace_id)
    replay_inputs = ReplayInputs(match_result=mr, profile=default_profile(), trace_id=trace_id)
    record = SemanticAuditBuilder(clock=_CLOCK).build(result, replay_inputs)
    assert record.final.action is ErAction.AUTO_LINK
    return record


def _assertions(name: str = "a") -> tuple[Assertion, Assertion]:
    subject = new_identity_id("g1")
    old = make_assertion(
        subject_identity=subject, predicate="proposed_year", object_value=2015
    )
    new = make_assertion(
        subject_identity=subject,
        predicate="proposed_year",
        object_value=2014,
        recorded_at=old.recorded_at + timedelta(days=1),
    ).model_copy(update={"assertion_id": f"as_new_{name}"})
    return old, new


def _assessment(recommendation: str) -> AdviserAssessment:
    return AdviserAssessment(
        adviser_type="assertion",
        adviser_version="assertion/1",
        model_id="recorded/echo",
        model_version="1",
        prompt_version="1",
        recommendation=recommendation,
        evidence_ids=("ev_new",),
        abstained=False,
        trace_id=_TRACE,
    )


def _assertion_record(
    sink: SqliteSemanticAuditSink, *, name: str = "a"
) -> AssertionSemanticAuditRecord:
    old, new = _assertions(name)
    trigger = CurationTrigger.of(
        kind=TriggerKind.NEW_EVIDENCE,
        identity_ids=(old.subject_identity,),
        assertion_ids=(old.assertion_id,),
        evidence_ids=("ev_new",),
        trace_id=_TRACE,
    )
    planner = ConceptEvolutionPlanner(
        snapshot_version="1", matcher_version="rules/1", adviser_version="assertion/1"
    )
    recorder = EvolutionAuditRecorder(builder=SemanticAuditBuilder(clock=_CLOCK), sink=sink)
    router = EvolutionRouter(planner=planner, audit=recorder)
    result = router.route_assertion(
        recommendation=AssertionRecommendation.SUPERSEDES,
        old_assertion=old,
        new_assertion=new,
        trigger=trigger,
        assessment=_assessment("supersedes"),
    )
    assert result.kind is EvolutionKind.SUPERSESSION
    assertion_records = [
        r for r in sink.records() if isinstance(r, AssertionSemanticAuditRecord)
    ]
    assert len(assertion_records) == 1
    return assertion_records[0]


def _audit_record() -> AuditRecord:
    return AuditRecord(
        audit_id="au_1",
        operation_id="op_1",
        decided_by="kgcs",
        score_vector={},
        evidence_ids=(),
        policy_version="1",
        trace_id=_TRACE,
        recorded_at=_CLOCK.now(),
    )


def _execution_record() -> ExecutionRecord:
    return ExecutionRecord(
        execution_id="ex_1",
        plan_id="pl_1",
        batch_id=None,
        outcome=ExecutionOutcome.COMMITTED,
        new_epoch=1,
        recorded_at=_CLOCK.now(),
    )


# --- append-only --------------------------------------------------------------


class TestAppendOnly:
    def test_audit_sink_rejects_update_and_delete(self) -> None:
        conn = sqlite3.connect(":memory:")
        sink = SqliteAuditSink(conn)
        sink.record(_audit_record())
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE audit_records SET trace_id = 'x'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM audit_records")

    def test_execution_sink_rejects_update_and_delete(self) -> None:
        conn = sqlite3.connect(":memory:")
        sink = SqliteExecutionSink(conn)
        sink.record(_execution_record())
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE execution_records SET outcome = 'ERROR'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM execution_records")

    def test_semantic_sink_rejects_update_and_delete(self) -> None:
        conn = sqlite3.connect(":memory:")
        sink = SqliteSemanticAuditSink(conn)
        sink.record(_er_record())
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE semantic_audit_records SET trace_id = 'x'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM semantic_audit_records")

    def test_semantic_ref_tables_are_append_only(self) -> None:
        conn = sqlite3.connect(":memory:")
        sink = SqliteSemanticAuditSink(conn)
        _assertion_record(sink)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE semantic_audit_assertions SET assertion_id = 'x'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM semantic_audit_operations")


# --- persistence across a reopen ----------------------------------------------


class TestPersistenceAcrossReopen:
    def test_all_three_streams_survive_a_reopen(self, tmp_path) -> None:
        path = tmp_path / "audit.sqlite3"
        conn = sqlite3.connect(path)
        SqliteAuditSink(conn).record(_audit_record())
        SqliteExecutionSink(conn).record(_execution_record())
        semantic = SqliteSemanticAuditSink(conn)
        semantic.record(_er_record())
        _assertion_record(semantic)
        conn.close()

        reopened = sqlite3.connect(path)
        assert len(SqliteAuditSink(reopened).records()) == 1
        assert len(SqliteExecutionSink(reopened).records()) == 1
        restored = SqliteSemanticAuditSink(reopened).records()
        assert len(restored) == 2
        assert {type(r).__name__ for r in restored} == {
            "SemanticAuditRecord",
            "AssertionSemanticAuditRecord",
        }


# --- read API -----------------------------------------------------------------


class TestReadApi:
    def test_semantic_records_for_trace_assertion_and_operation(self) -> None:
        conn = sqlite3.connect(":memory:")
        sink = SqliteSemanticAuditSink(conn)
        er = _er_record(trace_id="trace-er")
        sink.record(er)
        assertion_record = _assertion_record(sink)

        assert [r.audit_id for r in sink.records_for_trace("trace-er")] == [er.audit_id]
        assert [r.audit_id for r in sink.records_for_trace(_TRACE)] == [
            assertion_record.audit_id
        ]

        assertion_id = assertion_record.assertion_ids[0]
        found = sink.records_for_assertion(assertion_id)
        assert [r.audit_id for r in found] == [assertion_record.audit_id]

        operation_id = assertion_record.operation_ids[0]
        assert [r.audit_id for r in sink.records_for_operation(operation_id)] == [
            assertion_record.audit_id
        ]

        assert sink.records_for_assertion("as_absent") == []
        assert sink.records_for_operation("op_absent") == []

    def test_audit_records_for_trace_and_operation(self) -> None:
        conn = sqlite3.connect(":memory:")
        sink = SqliteAuditSink(conn)
        sink.record(_audit_record())
        assert len(sink.records_for_trace(_TRACE)) == 1
        assert len(sink.records_for_operation("op_1")) == 1
        assert sink.records_for_trace("absent") == []

    def test_execution_records_for_plan(self) -> None:
        conn = sqlite3.connect(":memory:")
        sink = SqliteExecutionSink(conn)
        sink.record(_execution_record())
        assert len(sink.records_for_plan("pl_1")) == 1
        assert sink.records_for_plan("pl_absent") == []


# --- assertion / re-curation audit --------------------------------------------


class TestAssertionAudit:
    def test_evolution_decision_produces_a_semantic_record(self) -> None:
        conn = sqlite3.connect(":memory:")
        sink = SqliteSemanticAuditSink(conn)
        record = _assertion_record(sink)

        assert record.decision_kind.value == "ASSERTION"
        assert record.final.kind == EvolutionKind.SUPERSESSION.value
        assert record.final.plan_id is not None
        assert record.plan_id == record.final.plan_id
        # The baseline is the conservative preserve-both route the core takes
        # without the adviser's cited evidence.
        assert record.baseline.kind == EvolutionKind.CONFLICT.value
        assert record.consulted_adviser is True
        assert record.versions.adviser_version == "assertion/1"
        assert record.versions.matcher_version == "rules/1"
        assert record.recorded_at == _CLOCK.now()

    def test_abstained_adviser_leaves_baseline_equal_to_final(self) -> None:
        old, new = _assertions("b")
        trigger = CurationTrigger.of(
            kind=TriggerKind.NEW_EVIDENCE, evidence_ids=("ev_new",), trace_id=_TRACE
        )
        sink = SqliteSemanticAuditSink(sqlite3.connect(":memory:"))
        recorder = EvolutionAuditRecorder(builder=SemanticAuditBuilder(clock=_CLOCK), sink=sink)
        router = EvolutionRouter(
            planner=ConceptEvolutionPlanner(snapshot_version="1"), audit=recorder
        )
        router.route_assertion(
            recommendation=AssertionRecommendation.CONTRADICTS,
            old_assertion=old,
            new_assertion=new,
            trigger=trigger,
            assessment=_assessment("insufficient").model_copy(update={"abstained": True}),
        )
        record = sink.records()[0]
        assert isinstance(record, AssertionSemanticAuditRecord)
        assert record.baseline.model_dump_json() == record.final.model_dump_json()

    def test_in_memory_sink_accepts_the_assertion_family(self) -> None:
        sink = InMemorySemanticAuditSink()
        old, new = _assertions("c")
        trigger = CurationTrigger.of(
            kind=TriggerKind.NEW_EVIDENCE, evidence_ids=("ev_new",), trace_id=_TRACE
        )
        recorder = EvolutionAuditRecorder(builder=SemanticAuditBuilder(clock=_CLOCK), sink=sink)
        EvolutionRouter(
            planner=ConceptEvolutionPlanner(snapshot_version="1"), audit=recorder
        ).route_assertion(
            recommendation=AssertionRecommendation.SUPPORTS,
            old_assertion=old,
            new_assertion=new,
            trigger=trigger,
        )
        assert isinstance(sink.records()[0], AssertionSemanticAuditRecord)


# --- replay from persisted records --------------------------------------------


class TestReplayFromPersisted:
    def test_er_decision_replays_byte_identically_after_reopen(self, tmp_path) -> None:
        path = tmp_path / "er.sqlite3"
        conn = sqlite3.connect(path)
        SqliteSemanticAuditSink(conn).record(_er_record())
        conn.close()

        reopened = sqlite3.connect(path)
        (restored,) = SqliteSemanticAuditSink(reopened).records_for_trace(_TRACE)
        assert isinstance(restored, SemanticAuditRecord)
        outcome = replay(restored)
        assert outcome.reproduced is True
        assert outcome.divergence is None

    def test_assertion_decision_replays_byte_identically_after_reopen(self, tmp_path) -> None:
        path = tmp_path / "assertion.sqlite3"
        conn = sqlite3.connect(path)
        original = _assertion_record(SqliteSemanticAuditSink(conn))
        conn.close()

        reopened = sqlite3.connect(path)
        (restored,) = SqliteSemanticAuditSink(reopened).records_for_trace(_TRACE)
        assert isinstance(restored, AssertionSemanticAuditRecord)
        assert restored.model_dump_json() == original.model_dump_json()
        outcome = replay(restored)
        assert outcome.reproduced is True
        assert outcome.divergence is None

    def test_persisted_assertion_record_carries_its_discriminant(self) -> None:
        conn = sqlite3.connect(":memory:")
        sink = SqliteSemanticAuditSink(conn)
        record = _assertion_record(sink)
        row = conn.execute(
            "SELECT decision_kind, record_json FROM semantic_audit_records"
        ).fetchone()
        assert row == ("ASSERTION", record.model_dump_json())


# --- recorded_at --------------------------------------------------------------


class TestRecordedAt:
    def test_er_record_is_stamped_from_the_injected_clock(self) -> None:
        assert _er_record().recorded_at == _CLOCK.now()

    def test_audit_id_is_independent_of_the_clock(self) -> None:
        first = _er_record()
        mr = first.replay_inputs.match_result
        result = CurationOrchestrator().resolve(mr, profile=default_profile(), trace_id=_TRACE)
        later = FixedClock(_CLOCK.now() + timedelta(hours=1))
        second = SemanticAuditBuilder(clock=later).build(result, first.replay_inputs)
        assert first.audit_id == second.audit_id
        assert first.recorded_at != second.recorded_at
