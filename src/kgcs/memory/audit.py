"""`InMemoryAuditSink`: a list-backed, append-only `AuditSink`.

The reference `AuditSink` for tests and local runs. Append-only *by
discipline* here — it exposes no update or delete, and `records()` hands back
a defensive copy so a caller cannot mutate the log through the returned list.
Durable append-only-at-rest (no in-place edits once written) is the store
layer's job in Plan 2, exactly as `kg_contracts` notes for `AuditRecord`;
this double models only the in-memory shape.
"""

from kg_contracts.curation import AuditRecord


class InMemoryAuditSink:
    """Keeps audit records in memory, in append order."""

    def __init__(self) -> None:
        self._records: list[AuditRecord] = []

    def record(self, audit: AuditRecord) -> None:
        self._records.append(audit)

    def records(self) -> list[AuditRecord]:
        """Every record appended so far, in order (a defensive copy)."""
        return list(self._records)
