"""`InMemoryExecutionAuditSink` and `InMemoryEpochPublisher`: executor doubles.

The list-backed reference implementations of the two executor ports
(`kgcs.executor.ExecutionAuditSink`, `kgcs.executor.EpochPublisher`) for tests
and local runs. Append-only / last-writer-wins by discipline; durable
backends land later and are validated against the same behaviour.
"""

from kgcs.executor.executor import ExecutionRecord


class InMemoryExecutionAuditSink:
    """Keeps `ExecutionRecord`s in memory, in append order."""

    def __init__(self) -> None:
        self._records: list[ExecutionRecord] = []

    def record(self, record: ExecutionRecord) -> None:
        self._records.append(record)

    def records(self) -> list[ExecutionRecord]:
        """Every execution record appended so far, in order (a defensive copy)."""
        return list(self._records)


class InMemoryEpochPublisher:
    """Records the last curation epoch the executor declared published.

    `published_epoch()` starts `None` (nothing published yet) and advances to
    the highest epoch handed to `publish()`; a stale (lower) epoch never
    regresses the watermark.
    """

    def __init__(self) -> None:
        self._epoch: int | None = None

    def publish(self, epoch: int) -> None:
        if self._epoch is None or epoch > self._epoch:
            self._epoch = epoch

    def published_epoch(self) -> int | None:
        return self._epoch
