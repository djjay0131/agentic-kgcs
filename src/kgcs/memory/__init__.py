"""In-memory reference adapters for the KGCS curation core.

Pure Python data structures, no I/O — the counterpart to
`kg_contracts.testing.memory`, but for the ports this repo defines
(`kgcs.audit.AuditSink`, `kgcs.executor.ExecutionAuditSink`,
`kgcs.executor.EpochPublisher`). They exist so every component and every
downstream adopter can exercise the engine and executor end-to-end with zero
infrastructure; durable backends are validated against the same behaviour
these doubles show.
"""

from kgcs.memory.audit import InMemoryAuditSink
from kgcs.memory.execution import InMemoryEpochPublisher, InMemoryExecutionAuditSink

__all__ = [
    "InMemoryAuditSink",
    "InMemoryEpochPublisher",
    "InMemoryExecutionAuditSink",
]
