"""SQLite append-only audit sinks (issue #48).

Three durable sinks that replace the in-memory doubles when the audit must
survive a process restart:

- `SqliteAuditSink` — operation-scoped `kg_contracts.curation.AuditRecord`s,
  the output of `kgcs.audit.AuditRecorder`;
- `SqliteExecutionSink` — execution-scoped `kgcs.executor.ExecutionRecord`s;
- `SqliteSemanticAuditSink` — decision-scoped semantic records (both
  `SemanticAuditRecord` and `AssertionSemanticAuditRecord`), the output of
  `kgcs.observability.SemanticAuditBuilder`.

**Immutability at rest, enforced by the database.** Each table carries `BEFORE
UPDATE` / `BEFORE DELETE` triggers that `RAISE(ABORT, …)`, exactly the pattern
`agentic-kgis` uses for its ledger audit stream (`src/kgis/ledger/audit.py`).
The in-memory sinks are append-only *by discipline*; a durable log must be
append-only *against a direct `UPDATE`/`DELETE` on the file*, so the guarantee
lives in the schema rather than in the adapter's method surface. A write is
committed per append so an acknowledged record is durable before the call
returns.

**Read API.** Besides append `record()` and full `records()`, the sinks answer
the joins the audit exists for: `records_for_trace`, `records_for_assertion`,
and `records_for_operation`. The semantic sink keeps small indexed ref tables
(`semantic_audit_assertions`, `semantic_audit_operations`) populated at append
time, so a lookup is an indexed join rather than a full scan (and needs no
SQLite JSON1 build).

**Semantic type reconstruction.** A semantic record is stored as its JSON, with
a `decision_kind` discriminant column so a read reconstructs the concrete type
(`SemanticAuditRecord` vs `AssertionSemanticAuditRecord`).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from kg_contracts.curation import AuditRecord

from kgcs.executor.executor import ExecutionRecord
from kgcs.observability.semantic_audit import (
    AssertionSemanticAuditRecord,
    DecisionKind,
    SemanticAuditRecord,
    SemanticAuditRecordT,
)

_APPEND_ONLY_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table}
BEGIN SELECT RAISE(ABORT, '{table} is append-only (issue #48)'); END;
CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table}
BEGIN SELECT RAISE(ABORT, '{table} is append-only (issue #48)'); END;
"""


def _create_append_only(conn: sqlite3.Connection, table: str, columns: str) -> None:
    """Create `table` with `columns` and its append-only triggers (idempotent)."""
    conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ({columns})")
    conn.executescript(_APPEND_ONLY_TRIGGERS.format(table=table))


class SqliteAuditSink:
    """Append-only SQLite destination for operation-scoped `AuditRecord`s.

    The durable counterpart of `kgcs.memory.InMemoryAuditSink`. Construct over
    an open `sqlite3.Connection`; the table and its append-only triggers are
    created if absent. `record` commits immediately, so a returned call means
    the record is on disk.
    """

    _TABLE = "audit_records"

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        _create_append_only(
            conn,
            self._TABLE,
            "seq INTEGER PRIMARY KEY AUTOINCREMENT, "
            "audit_id TEXT NOT NULL UNIQUE, "
            "operation_id TEXT NOT NULL, "
            "trace_id TEXT NOT NULL, "
            "recorded_at TEXT NOT NULL, "
            "record_json TEXT NOT NULL",
        )

    def record(self, audit: AuditRecord) -> None:
        """Append one record and commit it."""
        self._conn.execute(
            f"INSERT INTO {self._TABLE} "
            "(audit_id, operation_id, trace_id, recorded_at, record_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                audit.audit_id,
                audit.operation_id,
                audit.trace_id,
                audit.recorded_at.isoformat(),
                audit.model_dump_json(),
            ),
        )
        self._conn.commit()

    def records(self) -> list[AuditRecord]:
        """Every record in append order."""
        rows = self._conn.execute(
            f"SELECT record_json FROM {self._TABLE} ORDER BY seq"
        ).fetchall()
        return [_audit_from_json(row[0]) for row in rows]

    def records_for_trace(self, trace_id: str) -> list[AuditRecord]:
        """Every record carrying `trace_id`, in append order."""
        return self._query("trace_id = ?", (trace_id,))

    def records_for_operation(self, operation_id: str) -> list[AuditRecord]:
        """Every record for `operation_id`, in append order."""
        return self._query("operation_id = ?", (operation_id,))

    def _query(self, where: str, params: tuple[object, ...]) -> list[AuditRecord]:
        rows = self._conn.execute(
            f"SELECT record_json FROM {self._TABLE} WHERE {where} ORDER BY seq",
            params,
        ).fetchall()
        return [_audit_from_json(row[0]) for row in rows]


class SqliteExecutionSink:
    """Append-only SQLite destination for execution-scoped `ExecutionRecord`s.

    The durable counterpart of `kgcs.memory.InMemoryExecutionAuditSink`; every
    executor attempt (forward or compensation) persists here.
    """

    _TABLE = "execution_records"

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        _create_append_only(
            conn,
            self._TABLE,
            "seq INTEGER PRIMARY KEY AUTOINCREMENT, "
            "execution_id TEXT NOT NULL, "
            "plan_id TEXT NOT NULL, "
            "outcome TEXT NOT NULL, "
            "recorded_at TEXT NOT NULL, "
            "record_json TEXT NOT NULL",
        )

    def record(self, record: ExecutionRecord) -> None:
        """Append one execution record and commit it."""
        self._conn.execute(
            f"INSERT INTO {self._TABLE} "
            "(execution_id, plan_id, outcome, recorded_at, record_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                record.execution_id,
                record.plan_id,
                record.outcome.value,
                record.recorded_at.isoformat(),
                record.model_dump_json(),
            ),
        )
        self._conn.commit()

    def records(self) -> list[ExecutionRecord]:
        """Every execution record in append order."""
        rows = self._conn.execute(
            f"SELECT record_json FROM {self._TABLE} ORDER BY seq"
        ).fetchall()
        return [ExecutionRecord.model_validate_json(row[0]) for row in rows]

    def records_for_plan(self, plan_id: str) -> list[ExecutionRecord]:
        """Every execution attempt for `plan_id`, in append order."""
        rows = self._conn.execute(
            f"SELECT record_json FROM {self._TABLE} WHERE plan_id = ? ORDER BY seq",
            (plan_id,),
        ).fetchall()
        return [ExecutionRecord.model_validate_json(row[0]) for row in rows]


class SqliteSemanticAuditSink:
    """Append-only SQLite destination for decision-scoped semantic audit records.

    Holds both decision families (`SemanticAuditRecord` and
    `AssertionSemanticAuditRecord`) in one stream, reconstructing the concrete
    type on read from the stored `decision_kind`. Assertion/operation refs are
    written to indexed child tables so `records_for_assertion` /
    `records_for_operation` are joins, not scans. The child tables are
    append-only too.
    """

    _TABLE = "semantic_audit_records"
    _ASSERTION_REFS = "semantic_audit_assertions"
    _OPERATION_REFS = "semantic_audit_operations"

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        _create_append_only(
            conn,
            self._TABLE,
            "seq INTEGER PRIMARY KEY AUTOINCREMENT, "
            "audit_id TEXT NOT NULL UNIQUE, "
            "decision_kind TEXT NOT NULL, "
            "trace_id TEXT NOT NULL, "
            "plan_id TEXT, "
            "recorded_at TEXT NOT NULL, "
            "record_json TEXT NOT NULL",
        )
        _create_append_only(
            conn,
            self._ASSERTION_REFS,
            "audit_id TEXT NOT NULL, assertion_id TEXT NOT NULL",
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS {self._ASSERTION_REFS}_by_assertion "
            f"ON {self._ASSERTION_REFS} (assertion_id)"
        )
        _create_append_only(
            conn,
            self._OPERATION_REFS,
            "audit_id TEXT NOT NULL, operation_id TEXT NOT NULL",
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS {self._OPERATION_REFS}_by_operation "
            f"ON {self._OPERATION_REFS} (operation_id)"
        )
        conn.commit()

    def record(self, record: SemanticAuditRecordT) -> None:
        """Append one semantic record (and its ref rows) and commit."""
        assertion_ids, operation_ids = _refs_of(record)
        self._conn.execute(
            f"INSERT INTO {self._TABLE} "
            "(audit_id, decision_kind, trace_id, plan_id, recorded_at, record_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                record.audit_id,
                record.decision_kind.value,
                record.trace_id,
                record.plan_id,
                record.recorded_at.isoformat(),
                record.model_dump_json(),
            ),
        )
        self._insert_refs(self._ASSERTION_REFS, "assertion_id", record.audit_id, assertion_ids)
        self._insert_refs(self._OPERATION_REFS, "operation_id", record.audit_id, operation_ids)
        self._conn.commit()

    def records(self) -> list[SemanticAuditRecordT]:
        """Every semantic record in append order."""
        rows = self._conn.execute(
            f"SELECT decision_kind, record_json FROM {self._TABLE} ORDER BY seq"
        ).fetchall()
        return [_semantic_from_json(row[0], row[1]) for row in rows]

    def records_for_trace(self, trace_id: str) -> list[SemanticAuditRecordT]:
        """Every semantic record carrying `trace_id`, in append order."""
        rows = self._conn.execute(
            f"SELECT decision_kind, record_json FROM {self._TABLE} "
            "WHERE trace_id = ? ORDER BY seq",
            (trace_id,),
        ).fetchall()
        return [_semantic_from_json(row[0], row[1]) for row in rows]

    def records_for_assertion(self, assertion_id: str) -> list[SemanticAuditRecordT]:
        """Every decision whose plan touched `assertion_id`, in append order."""
        return self._join_refs(self._ASSERTION_REFS, "assertion_id", assertion_id)

    def records_for_operation(self, operation_id: str) -> list[SemanticAuditRecordT]:
        """Every decision whose plan carried `operation_id`, in append order."""
        return self._join_refs(self._OPERATION_REFS, "operation_id", operation_id)

    def _insert_refs(
        self, table: str, column: str, audit_id: str, values: Iterable[str]
    ) -> None:
        self._conn.executemany(
            f"INSERT INTO {table} (audit_id, {column}) VALUES (?, ?)",
            [(audit_id, value) for value in values],
        )

    def _join_refs(self, table: str, column: str, value: str) -> list[SemanticAuditRecordT]:
        rows = self._conn.execute(
            f"SELECT r.decision_kind, r.record_json FROM {self._TABLE} r "
            f"JOIN {table} ref ON ref.audit_id = r.audit_id "
            f"WHERE ref.{column} = ? ORDER BY r.seq",
            (value,),
        ).fetchall()
        return [_semantic_from_json(row[0], row[1]) for row in rows]


def _audit_from_json(text: str) -> AuditRecord:
    return AuditRecord.model_validate_json(text)


def _semantic_from_json(decision_kind: str, text: str) -> SemanticAuditRecordT:
    """Reconstruct the concrete semantic record from its stored discriminant."""
    if decision_kind == DecisionKind.ASSERTION.value:
        return AssertionSemanticAuditRecord.model_validate_json(text)
    return SemanticAuditRecord.model_validate_json(text)


def _refs_of(record: SemanticAuditRecordT) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The assertion/operation refs a record exposes (empty for ER records)."""
    if isinstance(record, AssertionSemanticAuditRecord):
        return record.assertion_ids, record.operation_ids
    return (), ()
