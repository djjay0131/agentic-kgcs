"""Injected time: `Clock`, `SystemClock`, `FixedClock`.

The Sprint-1 curation core is deterministic, but `AuditRecord.recorded_at`
is a wall-clock instant — the one field in the whole pipeline that cannot be
a pure function of the input. So the core never calls `datetime.now()`
directly; it asks an injected `Clock`, and a replay test injects
`FixedClock` to get byte-identical audit records.

Note the asymmetry that makes this a small dependency rather than a large
one: `CurationPlan` carries no timestamp (see `kg_contracts.curation`), so a
plan is already deterministic from its inputs alone — no clock required. The
clock exists only for the audit stream. `now()` (wall time, stamped onto
audit records) and `monotonic()` (elapsed time, for optional engine metrics)
are kept separate for the same reason `kgis.clock` keeps them separate: a
wall clock jumps backwards under NTP correction, so a duration must never be
two `now()` readings subtracted.

This mirrors `kgis.clock` deliberately — KGIS and KGCS share the same
"time is a dependency, not an ambient fact" discipline, but do not share a
module (KGCS depends only on `kg_contracts`, not on `kgis`).
"""

import time
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """The curation core's only source of time (audit timestamps)."""

    def now(self) -> datetime:
        """Current wall-clock instant, timezone-aware, for stamping audit records."""
        ...

    def monotonic(self) -> float:
        """Monotonically non-decreasing seconds, for measuring elapsed time."""
        ...


class SystemClock:
    """The real clock. The production default."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()


class FixedClock:
    """A deterministic clock: `now()` never moves unless you move it.

    `monotonic()` advances by `tick_seconds` on each call (default `0.0`, i.e.
    every run measures as zero elapsed) so a replay test can assert
    byte-identical audit output without special-casing timing.
    """

    def __init__(self, instant: datetime, *, tick_seconds: float = 0.0) -> None:
        if instant.tzinfo is None:
            raise ValueError("FixedClock requires a timezone-aware instant")
        self._instant = instant
        self._tick_seconds = tick_seconds
        self._elapsed = 0.0

    def now(self) -> datetime:
        return self._instant

    def monotonic(self) -> float:
        current = self._elapsed
        self._elapsed += self._tick_seconds
        return current

    def advance(self, seconds: float) -> None:
        """Move wall time forward — for tests that need two distinct instants."""
        self._instant = self._instant + timedelta(seconds=seconds)
