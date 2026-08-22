"""In-memory reference adapters for the KGCS curation core (Sprint 1).

Pure Python data structures, no I/O — the counterpart to
`kg_contracts.testing.memory`, but for the ports this repo defines
(`kgcs.audit.AuditSink`). They exist so every component and every downstream
adopter can exercise the engine end-to-end with zero infrastructure; durable
backends (Plan 2) are validated against the same contract suites these pass.
"""

from kgcs.memory.audit import InMemoryAuditSink

__all__ = ["InMemoryAuditSink"]
