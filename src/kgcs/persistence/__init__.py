"""Durable persistence adapters for the KGCS ports (issue #48).

Where `kgcs.memory` holds the in-memory reference doubles, this package holds
the durable ones. The first — and so far only — durable backend is SQLite:
`kgcs.persistence.sqlite` implements the three audit sinks
(`kgcs.audit.AuditSink`, `kgcs.executor.ExecutionAuditSink`,
`kgcs.observability.SemanticAuditSink`) as append-only-at-rest tables whose
immutability is enforced by SQLite triggers, modelled on `agentic-kgis`
`src/kgis/ledger/audit.py`. The in-memory sinks remain the defaults everywhere;
a caller opts into durability by constructing the SQLite sink over a
connection.
"""

from kgcs.persistence.sqlite import (
    SqliteAuditSink,
    SqliteExecutionSink,
    SqliteSemanticAuditSink,
)

__all__ = [
    "SqliteAuditSink",
    "SqliteExecutionSink",
    "SqliteSemanticAuditSink",
]
