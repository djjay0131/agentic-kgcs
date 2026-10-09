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

import json
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
    DecisionKind,
    EvolutionAuditRecorder,
    InMemorySemanticAuditSink,
    ReplayInputs,
    SemanticAuditBuilder,
    SemanticAuditRecord,
    replay,
)
from kgcs.persistence import (
    SchemaVersionError,
    SqliteAuditSink,
    SqliteExecutionSink,
    SqliteSemanticAuditSink,
)
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

    def test_audit_sink_rejects_insert_or_replace(self) -> None:
        conn = sqlite3.connect(":memory:")
        sink = SqliteAuditSink(conn)
        original = _audit_record()
        sink.record(original)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT OR REPLACE INTO audit_records "
                "(audit_id, operation_id, trace_id, recorded_at, record_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    original.audit_id,
                    "op_replaced",
                    "trace_replaced",
                    _CLOCK.now().isoformat(),
                    original.model_dump_json(),
                ),
            )
        assert [r.trace_id for r in sink.records()] == [_TRACE]

    def test_audit_sink_rejects_rowid_replace(self) -> None:
        # `INSERT OR REPLACE` can also conflict on the rowid primary key; the
        # `seq` guard closes that path as well as the content-key one.
        conn = sqlite3.connect(":memory:")
        sink = SqliteAuditSink(conn)
        sink.record(_audit_record())
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT OR REPLACE INTO audit_records "
                "(seq, audit_id, operation_id, trace_id, recorded_at, record_json) "
                "VALUES (1, 'au_replaced', 'op_x', 'trace_x', ?, ?)",
                (_CLOCK.now().isoformat(), '{"x": 1}'),
            )
        assert [r.audit_id for r in sink.records()] == ["au_1"]

    def test_semantic_sink_rejects_rowid_replace(self) -> None:
        conn = sqlite3.connect(":memory:")
        sink = SqliteSemanticAuditSink(conn)
        original = _er_record()
        sink.record(original)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT OR REPLACE INTO semantic_audit_records "
                "(seq, audit_id, decision_kind, trace_id, plan_id, recorded_at, record_json) "
                "VALUES (1, 'au_replaced', 'ER', 'trace_x', NULL, ?, ?)",
                (_CLOCK.now().isoformat(), '{"x": 1}'),
            )
        assert [r.audit_id for r in sink.records()] == [original.audit_id]

    def test_execution_sink_allows_repeated_execution_ids(self) -> None:
        # `execution_id` is a content address and deliberately non-unique: a
        # repeated identical-outcome retry is a distinct arrival and must append
        # (see `PlanExecutor._record`). The per-arrival key is `seq`.
        conn = sqlite3.connect(":memory:")
        sink = SqliteExecutionSink(conn)
        original = _execution_record()
        sink.record(original)
        sink.record(original)
        assert [r.execution_id for r in sink.records()] == ["ex_1", "ex_1"]

    def test_execution_sink_rejects_rowid_replace(self) -> None:
        conn = sqlite3.connect(":memory:")
        sink = SqliteExecutionSink(conn)
        sink.record(_execution_record())
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT OR REPLACE INTO execution_records "
                "(seq, execution_id, plan_id, outcome, recorded_at, record_json) "
                "VALUES (1, 'ex_replaced', 'pl_replaced', 'ERROR', ?, ?)",
                (_CLOCK.now().isoformat(), '{"x": 1}'),
            )
        assert [r.plan_id for r in sink.records()] == ["pl_1"]

    def test_semantic_sink_rejects_insert_or_replace(self) -> None:
        conn = sqlite3.connect(":memory:")
        sink = SqliteSemanticAuditSink(conn)
        original = _er_record()
        sink.record(original)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT OR REPLACE INTO semantic_audit_records "
                "(audit_id, decision_kind, trace_id, plan_id, recorded_at, record_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    original.audit_id,
                    original.decision_kind.value,
                    "trace_replaced",
                    None,
                    _CLOCK.now().isoformat(),
                    original.model_dump_json(),
                ),
            )
        assert [r.trace_id for r in sink.records()] == [_TRACE]

    def test_semantic_ref_table_rejects_insert_or_replace(self) -> None:
        conn = sqlite3.connect(":memory:")
        sink = SqliteSemanticAuditSink(conn)
        record = _assertion_record(sink)
        assertion_id = record.assertion_ids[0]
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT OR REPLACE INTO semantic_audit_assertions "
                "(audit_id, assertion_id) VALUES (?, ?)",
                (record.audit_id, assertion_id),
            )
        assert [r.audit_id for r in sink.records_for_assertion(assertion_id)] == [
            record.audit_id
        ]


# --- per-append atomicity -----------------------------------------------------


class TestAppendAtomicity:
    def test_a_failed_ref_insert_rolls_back_the_main_row(self) -> None:
        record = _assertion_record(InMemorySemanticAuditSink())
        conn = sqlite3.connect(":memory:")
        sink = SqliteSemanticAuditSink(conn)
        # Pre-seed the exact (audit_id, assertion_id) the append will write, so
        # the main insert succeeds but the ref insert aborts on the duplicate
        # guard. The whole record must roll back, not leave a half-written row.
        conn.execute(
            "INSERT INTO semantic_audit_assertions (audit_id, assertion_id) VALUES (?, ?)",
            (record.audit_id, record.assertion_ids[0]),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            sink.record(record)
        assert (
            conn.execute("SELECT COUNT(*) FROM semantic_audit_records").fetchone()[0] == 0
        )
        assert (
            conn.execute("SELECT COUNT(*) FROM semantic_audit_assertions").fetchone()[0] == 1
        )


# --- schema versioning --------------------------------------------------------


class TestSchemaVersioning:
    def test_fresh_connection_is_stamped_with_the_current_version(self) -> None:
        conn = sqlite3.connect(":memory:")
        SqliteAuditSink(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1

    def test_all_three_sinks_agree_on_the_version(self) -> None:
        conn = sqlite3.connect(":memory:")
        SqliteAuditSink(conn)
        SqliteExecutionSink(conn)
        SqliteSemanticAuditSink(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1

    def test_unknown_schema_version_is_refused_on_open(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA user_version = 99")
        with pytest.raises(SchemaVersionError):
            SqliteAuditSink(conn)


# --- transactions on a shared connection --------------------------------------


class TestSharedConnection:
    def test_schema_creation_does_not_commit_a_callers_transaction(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE probe (x INTEGER)")
        conn.execute("INSERT INTO probe VALUES (1)")
        assert conn.in_transaction
        SqliteSemanticAuditSink(conn)
        # `executescript` (the old code) would have committed here; a savepoint
        # leaves the caller's transaction intact.
        assert conn.in_transaction
        conn.rollback()
        assert conn.execute("SELECT COUNT(*) FROM probe").fetchone()[0] == 0

    def test_constructing_a_sink_twice_is_idempotent(self) -> None:
        conn = sqlite3.connect(":memory:")
        SqliteSemanticAuditSink(conn)
        SqliteSemanticAuditSink(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1


# --- backward compatibility with pre-#48 records ------------------------------


class TestPre48BackwardCompatibility:
    def test_a_pre_48_record_loads_replays_and_persists(self, tmp_path) -> None:
        current = _er_record()
        data = json.loads(current.model_dump_json())
        # A main-era record carried neither of #48's new fields.
        del data["recorded_at"]
        del data["decision_kind"]
        legacy = SemanticAuditRecord.model_validate_json(json.dumps(data))
        assert legacy.recorded_at is None
        assert legacy.decision_kind is DecisionKind.ER
        assert replay(legacy).reproduced is True

        path = tmp_path / "legacy.sqlite3"
        conn = sqlite3.connect(path)
        SqliteSemanticAuditSink(conn).record(legacy)
        conn.close()

        reopened = sqlite3.connect(path)
        (restored,) = SqliteSemanticAuditSink(reopened).records()
        assert restored.recorded_at is None
        assert replay(restored).reproduced is True


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
