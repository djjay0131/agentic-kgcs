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
`BEFORE UPDATE`/`BEFORE DELETE` alone is not enough: SQLite's `INSERT OR
REPLACE` conflict resolution deletes the conflicting row *without firing a
`DELETE` trigger* (unless `recursive_triggers` is on), so it silently rewrote
history. Every table therefore also carries a `BEFORE INSERT` trigger that
aborts on a duplicate key, which fires before conflict resolution and closes
that bypass. The in-memory sinks are append-only *by discipline*; a durable log
must be append-only *against a direct `UPDATE`/`DELETE`/`INSERT OR REPLACE` on
the file*, so the guarantee lives in the schema rather than in the adapter's
method surface. A write is committed per append so an acknowledged record is
durable before the call returns.

**Transactions on a shared connection.** The schema is created with individual
`execute()` calls inside a `SAVEPOINT`, never `executescript` — `executescript`
issues an implicit `COMMIT` first, which would commit whatever transaction the
caller already had open on the connection. A `SAVEPOINT` releases into an
existing transaction instead of committing it. Each append wraps its main row
and its ref rows in one transaction (`with conn:`), so a failed ref insert rolls
back the whole record rather than leaving a half-written entry.

**Schema versioning.** `PRAGMA user_version` carries the schema version; it is
stamped on first open and checked on every open, so a database written by an
unknown future schema is refused rather than misread. A version-1 database
(pre-#55, ref tables without the rowid guard) is brought forward on open: the
guard triggers are created idempotently, no data is rewritten.

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
from collections.abc import Iterable, Iterator
from contextlib import contextmanager

from kg_contracts.curation import AuditRecord

from kgcs.executor.executor import ExecutionRecord
from kgcs.observability.semantic_audit import (
    AssertionSemanticAuditRecord,
    DecisionKind,
    SemanticAuditRecord,
    SemanticAuditRecordT,
)

_SCHEMA_VERSION = 2
"""The current on-disk audit schema version (`PRAGMA user_version`).

Version 2 adds a `rowid` duplicate guard to the semantic ref tables
(`semantic_audit_assertions`, `semantic_audit_operations`): a raw `INSERT OR
REPLACE INTO … (rowid, …)` conflicted on the rowid primary key, deleting the
existing row through a path the `DELETE` trigger never fired on (issue #55). A
version-1 database is migrated in place on open — the supplied guard triggers
are created idempotently by `_create_append_only`, with no data rewrite.

Bumped only for a change that an older reader would misread; a bump must ship a
migration in `_check_schema_version`.
"""

_PRIOR_VERSIONS = (0, 1)
"""Schema versions this build can open and bring forward.

`0` is unstamped (a fresh database, or one written before versioning); `1` is a
pre-#55 database whose ref tables lack the rowid guard. Both are accepted and
stamped with `_SCHEMA_VERSION`; any other version is refused.
"""


class SchemaVersionError(RuntimeError):
    """An audit database carries a schema version this build cannot read.

    Raised on open rather than reading an unknown layout and returning wrong
    records: an append-only audit that can be misread is worse than one that
    refuses to open.
    """


@contextmanager
def _schema_transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """Run schema DDL in a savepoint, preserving any transaction the caller holds.

    Deliberately *not* `executescript`: that commits any open transaction on the
    connection (issue #48 review finding 4). A `SAVEPOINT` commits only when it
    is the outermost scope, so a shared connection's in-flight transaction is
    left open.
    """
    conn.execute("SAVEPOINT kgcs_schema")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK TO SAVEPOINT kgcs_schema")
        conn.execute("RELEASE SAVEPOINT kgcs_schema")
        raise
    conn.execute("RELEASE SAVEPOINT kgcs_schema")


def _check_schema_version(conn: sqlite3.Connection) -> None:
    """Stamp a fresh or migratable database with `_SCHEMA_VERSION`; refuse others.

    `user_version == 0` is a database this module has not stamped yet (its tables
    are created idempotently below), so it is initialized. Version `1` predates
    the ref-table rowid guards (issue #55); it is brought forward here and the
    missing triggers are created idempotently by `_create_append_only` — no data
    is rewritten. Any other version is either current or unreadable.
    """
    (version,) = conn.execute("PRAGMA user_version").fetchone()
    if version == _SCHEMA_VERSION:
        return
    if version in _PRIOR_VERSIONS:
        conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        return
    raise SchemaVersionError(
        f"audit schema version {version} is not {_SCHEMA_VERSION}; refusing to open"
    )


def _append_only_statements(
    table: str, key_groups: tuple[tuple[str, ...], ...]
) -> tuple[str, ...]:
    """The append-only triggers for `table`, keyed on each uniqueness constraint.

    The `BEFORE INSERT` guards close the `INSERT OR REPLACE` bypass: they fire
    before conflict resolution, so a replacement of an existing key is aborted
    instead of deleting the old row through a path the `DELETE` trigger never
    sees. `key_groups` names one group of columns per uniqueness constraint —
    the rowid primary key (`seq` where the table declares an `INTEGER PRIMARY
    KEY`, the bare `rowid` where it does not) *and* any content `UNIQUE` key —
    because `INSERT OR REPLACE` can conflict on either. A table whose content id
    is deliberately non-unique (its retries are distinct arrivals) is guarded on
    its rowid alone.
    """
    statements = [
        f"CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table}\n"
        f"BEGIN SELECT RAISE(ABORT, '{table} is append-only (issue #48)'); END;",
        f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table}\n"
        f"BEGIN SELECT RAISE(ABORT, '{table} is append-only (issue #48)'); END;",
    ]
    for key_columns in key_groups:
        duplicate_where = " AND ".join(f"{column} = NEW.{column}" for column in key_columns)
        joined_key = ", ".join(key_columns)
        trigger = f"{table}_no_replace_{'_'.join(key_columns)}"
        statements.append(
            f"CREATE TRIGGER IF NOT EXISTS {trigger} BEFORE INSERT ON {table}\n"
            f"WHEN EXISTS (SELECT 1 FROM {table} WHERE {duplicate_where})\n"
            f"BEGIN SELECT RAISE(ABORT, "
            f"'{table} is append-only (issue #48): duplicate {joined_key}'); END;"
        )
    return tuple(statements)


def _create_append_only(
    conn: sqlite3.Connection,
    table: str,
    columns: str,
    key_groups: tuple[tuple[str, ...], ...],
) -> None:
    """Create `table` with `columns` and its append-only triggers (idempotent)."""
    conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ({columns})")
    for statement in _append_only_statements(table, key_groups):
        conn.execute(statement)


class SqliteAuditSink:
    """Append-only SQLite destination for operation-scoped `AuditRecord`s.

    The durable counterpart of `kgcs.memory.InMemoryAuditSink`. Construct over
    an open `sqlite3.Connection`; the table and its append-only triggers are
    created if absent. `record` commits immediately, so a returned call means
    the record is on disk.
    """

    _TABLE = "audit_records"
    _KEYS = (("seq",), ("audit_id",))

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        with _schema_transaction(conn):
            _check_schema_version(conn)
            _create_append_only(
                conn,
                self._TABLE,
                "seq INTEGER PRIMARY KEY AUTOINCREMENT, "
                "audit_id TEXT NOT NULL UNIQUE, "
                "operation_id TEXT NOT NULL, "
                "trace_id TEXT NOT NULL, "
                "recorded_at TEXT NOT NULL, "
                "record_json TEXT NOT NULL",
                self._KEYS,
            )

    def record(self, audit: AuditRecord) -> None:
        """Append one record and commit it."""
        with self._conn:
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
    executor attempt (forward or compensation) persists here. `execution_id` is
    a content address and is **deliberately non-unique** — a repeated
    identical-outcome retry is a distinct arrival and must append (see
    `PlanExecutor._record`) — so this table is guarded against rowid replacement
    only, never on `execution_id`. The `seq` column is the per-arrival key.
    """

    _TABLE = "execution_records"
    _KEYS = (("seq",),)

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        with _schema_transaction(conn):
            _check_schema_version(conn)
            _create_append_only(
                conn,
                self._TABLE,
                "seq INTEGER PRIMARY KEY AUTOINCREMENT, "
                "execution_id TEXT NOT NULL, "
                "plan_id TEXT NOT NULL, "
                "outcome TEXT NOT NULL, "
                "recorded_at TEXT NOT NULL, "
                "record_json TEXT NOT NULL",
                self._KEYS,
            )

    def record(self, record: ExecutionRecord) -> None:
        """Append one execution record and commit it."""
        with self._conn:
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
        with _schema_transaction(conn):
            _check_schema_version(conn)
            _create_append_only(
                conn,
                self._TABLE,
                "seq INTEGER PRIMARY KEY AUTOINCREMENT, "
                "audit_id TEXT NOT NULL UNIQUE, "
                "decision_kind TEXT NOT NULL, "
                "trace_id TEXT NOT NULL, "
                "plan_id TEXT, "
                # Nullable: a pre-#48 record has no recorded_at (honest unknown).
                "recorded_at TEXT, "
                "record_json TEXT NOT NULL",
                (("seq",), ("audit_id",)),
            )
            _create_append_only(
                conn,
                self._ASSERTION_REFS,
                "audit_id TEXT NOT NULL, assertion_id TEXT NOT NULL",
                (("rowid",), ("audit_id", "assertion_id")),
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS {self._ASSERTION_REFS}_by_assertion "
                f"ON {self._ASSERTION_REFS} (assertion_id)"
            )
            _create_append_only(
                conn,
                self._OPERATION_REFS,
                "audit_id TEXT NOT NULL, operation_id TEXT NOT NULL",
                (("rowid",), ("audit_id", "operation_id")),
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS {self._OPERATION_REFS}_by_operation "
                f"ON {self._OPERATION_REFS} (operation_id)"
            )

    def record(self, record: SemanticAuditRecordT) -> None:
        """Append one semantic record (and its ref rows) in one transaction."""
        assertion_ids, operation_ids = _refs_of(record)
        recorded_at = record.recorded_at.isoformat() if record.recorded_at is not None else None
        with self._conn:
            self._conn.execute(
                f"INSERT INTO {self._TABLE} "
                "(audit_id, decision_kind, trace_id, plan_id, recorded_at, record_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    record.audit_id,
                    record.decision_kind.value,
                    record.trace_id,
                    record.plan_id,
                    recorded_at,
                    record.model_dump_json(),
                ),
            )
            self._insert_refs(
                self._ASSERTION_REFS, "assertion_id", record.audit_id, assertion_ids
            )
            self._insert_refs(
                self._OPERATION_REFS, "operation_id", record.audit_id, operation_ids
            )

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
        # De-duplicated, order-stable: a plan may name the same ref twice, but the
        # append-only guard keys on (audit_id, ref) and must not abort a legible
        # record for a repeat that carries no new information.
        unique = list(dict.fromkeys(values))
        self._conn.executemany(
            f"INSERT INTO {table} (audit_id, {column}) VALUES (?, ?)",
            [(audit_id, value) for value in unique],
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
