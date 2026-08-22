"""Backlog metrics + backpressure signals (spec §7.7, §9 law 15).

The dangerous ledger failure is **priority inversion, not storage growth**: easy
candidates flow, hard high-value clusters starve, and throughput *looks*
healthy. So the backlog is measured by pressure, not size. `BacklogAnalyzer`
computes, as a pure function of the queued items and an injected `Clock`:

- queue depth, and depth broken down by source and entity type;
- age and SLA breaches (a breach = past `model.sla_deadline`), by priority;
- unresolved-cluster size (items grouped by their snapshot ref);
- **priority inversion** — a higher-priority item breaching its SLA while
  lower-priority items sit within theirs (the easy-flows/hard-starves failure);
- **starving high-value** items — high `value` cases breaching their SLA.

When a source's pressure exceeds the configured curation SLO / maximum
provisional exposure, the analyzer emits a machine-readable `BackpressureSignal`
(THROTTLE or QUARANTINE) — the KGIS-facing seam that keeps curation debt from
becoming a silent system failure (§9 law 15). Determinism is structural: every
number is derived from the items plus one `clock.now()` reading, and sources are
processed in sorted order, so the same queue state yields the same metrics and
the same signals.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime
from enum import StrEnum

from kg_contracts.curation import ReviewItem
from pydantic import BaseModel, ConfigDict, Field

from kgcs.clock import Clock
from kgcs.review.model import ReviewCase, sla_deadline

_UNKNOWN = "unknown"
"""Bucket label for an item whose payload names no source / entity type."""

# Priority ordering for inversion detection: P1 is most urgent.
_PRIORITY_RANK: dict[str, int] = {"P1": 3, "P2": 2, "P3": 1}


class BackpressureAction(StrEnum):
    """What KGIS should do with a source under curation backpressure."""

    THROTTLE = "THROTTLE"
    QUARANTINE = "QUARANTINE"


class BacklogConfig(BaseModel):
    """The curation SLO / exposure budgets that trigger backpressure (data, not code).

    `max_provisional_exposure` bounds total pending items across all sources;
    `throttle_depth`/`quarantine_depth` are per-source depth budgets; and
    `sla_breach_quarantine` quarantines a source once its SLA breaches pile up.
    A high-`value` case above `high_value_threshold` counts as high-value for
    starvation detection.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_provisional_exposure: int = 100
    throttle_depth: int = 25
    quarantine_depth: int = 75
    sla_breach_quarantine: int = 5
    high_value_threshold: float = 0.7
    throttle_intake: int = 10
    quarantine_intake: int = 0


class BackpressureSignal(BaseModel):
    """A machine-readable throttle/quarantine recommendation for one source.

    The KGIS-facing integration seam: frozen and JSON-serializable, so it
    survives being written to a queue/log and read back. `recommended_max_intake`
    is the intake ceiling KGIS should apply to `source` (0 = quarantine).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    action: BackpressureAction
    reason: str
    depth: int
    sla_breaches: int
    oldest_age_hours: float
    recommended_max_intake: int
    measured_at: datetime


class SourceBacklog(BaseModel):
    """Per-source pressure summary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    depth: int
    oldest_age_hours: float
    sla_breaches: int
    by_priority: dict[str, int] = Field(default_factory=dict)


class ClusterBacklog(BaseModel):
    """Per-cluster (snapshot-ref) pressure summary — unresolved-cluster size."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_ref: str
    size: int
    max_value: float | None
    breaching: bool


class QueueMetrics(BaseModel):
    """A deterministic snapshot of queue pressure (pure fn of items + clock)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    measured_at: datetime
    depth: int
    depth_by_source: dict[str, int] = Field(default_factory=dict)
    depth_by_entity_type: dict[str, int] = Field(default_factory=dict)
    oldest_age_hours: float = 0.0
    sla_breaches: int = 0
    sla_breaches_by_priority: dict[str, int] = Field(default_factory=dict)
    priority_inversion: bool = False
    inverted_item_ids: tuple[str, ...] = ()
    starving_high_value: tuple[str, ...] = ()
    sources: tuple[SourceBacklog, ...] = ()
    clusters: tuple[ClusterBacklog, ...] = ()


class _ItemView:
    """A parsed view of one queued item (contract fields + optional case)."""

    item_id: str
    priority: str
    age_hours: float
    breaching: bool
    source: str
    entity_type: str
    value: float | None
    snapshot_ref: str | None

    def __init__(self, item: ReviewItem, now: datetime) -> None:
        case = ReviewCase.try_from_item(item)
        self.item_id = item.item_id
        self.priority = item.priority
        self.age_hours = max(0.0, (now - item.enqueued_at).total_seconds() / 3600.0)
        self.breaching = now > sla_deadline(item)
        self.source = case.source if case and case.source else _UNKNOWN
        self.entity_type = case.entity_type if case and case.entity_type else _UNKNOWN
        self.value = case.value if case else None
        self.snapshot_ref = case.snapshot_ref if case and case.snapshot_ref else None


class BacklogAnalyzer:
    """Computes queue metrics and backpressure signals (spec §7.7).

    Injected with a `Clock` (the one non-pure input) and a `BacklogConfig`.
    Holds no mutable state: `metrics` and `backpressure` are deterministic given
    the same items and clock reading.
    """

    def __init__(self, *, clock: Clock, config: BacklogConfig | None = None) -> None:
        self._clock = clock
        self._config = config or BacklogConfig()

    def metrics(self, items: Sequence[ReviewItem]) -> QueueMetrics:
        """A full pressure snapshot over `items` (pure fn of items + `clock.now()`)."""
        now = self._clock.now()
        views = [_ItemView(item, now) for item in items]

        depth_by_source = _counts(v.source for v in views)
        depth_by_entity_type = _counts(v.entity_type for v in views)
        oldest = max((v.age_hours for v in views), default=0.0)

        breaches = [v for v in views if v.breaching]
        breaches_by_priority = _counts(v.priority for v in breaches)

        inverted = self._priority_inversion(views)
        starving = tuple(
            v.item_id
            for v in breaches
            if v.value is not None and v.value >= self._config.high_value_threshold
        )

        return QueueMetrics(
            measured_at=now,
            depth=len(views),
            depth_by_source=depth_by_source,
            depth_by_entity_type=depth_by_entity_type,
            oldest_age_hours=oldest,
            sla_breaches=len(breaches),
            sla_breaches_by_priority=breaches_by_priority,
            priority_inversion=bool(inverted),
            inverted_item_ids=inverted,
            starving_high_value=starving,
            sources=self._source_backlogs(views),
            clusters=self._cluster_backlogs(views),
        )

    def backpressure(self, items: Sequence[ReviewItem]) -> tuple[BackpressureSignal, ...]:
        """Actionable throttle/quarantine signals for sources exceeding the SLO.

        A source is QUARANTINEd when its depth reaches `quarantine_depth` or its
        SLA breaches reach `sla_breach_quarantine`, and THROTTLEd when its depth
        reaches `throttle_depth`. When total pending exceeds
        `max_provisional_exposure`, the source contributing the most depth is
        throttled even if under its own budget — global exposure is bounded too.
        """
        now = self._clock.now()
        views = [_ItemView(item, now) for item in items]
        metrics = self.metrics(items)

        signals: dict[str, BackpressureSignal] = {}
        for source in sorted(metrics.depth_by_source):
            source_views = [v for v in views if v.source == source]
            depth = len(source_views)
            sla_breaches = sum(1 for v in source_views if v.breaching)
            oldest = max((v.age_hours for v in source_views), default=0.0)
            signal = self._signal_for(source, depth, sla_breaches, oldest, now)
            if signal is not None:
                signals[source] = signal

        # Global exposure guard: bound total provisional exposure even if no
        # single source has crossed its per-source budget.
        if metrics.depth > self._config.max_provisional_exposure and metrics.depth_by_source:
            top = max(sorted(metrics.depth_by_source), key=lambda s: metrics.depth_by_source[s])
            if top not in signals:
                source_views = [v for v in views if v.source == top]
                signals[top] = BackpressureSignal(
                    source=top,
                    action=BackpressureAction.THROTTLE,
                    reason=(
                        f"total provisional exposure {metrics.depth} exceeds "
                        f"{self._config.max_provisional_exposure}; throttling largest source"
                    ),
                    depth=len(source_views),
                    sla_breaches=sum(1 for v in source_views if v.breaching),
                    oldest_age_hours=max((v.age_hours for v in source_views), default=0.0),
                    recommended_max_intake=self._config.throttle_intake,
                    measured_at=now,
                )

        return tuple(signals[source] for source in sorted(signals))

    # -- internals -----------------------------------------------------------

    def _signal_for(
        self, source: str, depth: int, sla_breaches: int, oldest: float, now: datetime
    ) -> BackpressureSignal | None:
        cfg = self._config
        if depth >= cfg.quarantine_depth or sla_breaches >= cfg.sla_breach_quarantine:
            reason = (
                f"depth {depth} >= quarantine_depth {cfg.quarantine_depth}"
                if depth >= cfg.quarantine_depth
                else f"sla_breaches {sla_breaches} >= {cfg.sla_breach_quarantine}"
            )
            return BackpressureSignal(
                source=source,
                action=BackpressureAction.QUARANTINE,
                reason=reason,
                depth=depth,
                sla_breaches=sla_breaches,
                oldest_age_hours=oldest,
                recommended_max_intake=cfg.quarantine_intake,
                measured_at=now,
            )
        if depth >= cfg.throttle_depth:
            return BackpressureSignal(
                source=source,
                action=BackpressureAction.THROTTLE,
                reason=f"depth {depth} >= throttle_depth {cfg.throttle_depth}",
                depth=depth,
                sla_breaches=sla_breaches,
                oldest_age_hours=oldest,
                recommended_max_intake=cfg.throttle_intake,
                measured_at=now,
            )
        return None

    def _priority_inversion(self, views: Sequence[_ItemView]) -> tuple[str, ...]:
        """Higher-priority items breaching SLA while lower-priority ones are healthy.

        The inverted ids are the *starved* high-priority breaching items — the
        ones aging past SLA while easier (lower-priority) work sits within its
        own SLA and is free to flow.
        """
        healthy_ranks = {
            _PRIORITY_RANK.get(v.priority, 0) for v in views if not v.breaching
        }
        if not healthy_ranks:
            return ()
        lowest_healthy = min(healthy_ranks)
        inverted = tuple(
            v.item_id
            for v in views
            if v.breaching and _PRIORITY_RANK.get(v.priority, 0) > lowest_healthy
        )
        return inverted

    def _source_backlogs(self, views: Sequence[_ItemView]) -> tuple[SourceBacklog, ...]:
        by_source: dict[str, list[_ItemView]] = defaultdict(list)
        for view in views:
            by_source[view.source].append(view)
        return tuple(
            SourceBacklog(
                source=source,
                depth=len(group),
                oldest_age_hours=max((v.age_hours for v in group), default=0.0),
                sla_breaches=sum(1 for v in group if v.breaching),
                by_priority=_counts(v.priority for v in group),
            )
            for source, group in sorted(by_source.items())
        )

    def _cluster_backlogs(self, views: Sequence[_ItemView]) -> tuple[ClusterBacklog, ...]:
        by_cluster: dict[str, list[_ItemView]] = defaultdict(list)
        for view in views:
            if view.snapshot_ref is not None:
                by_cluster[view.snapshot_ref].append(view)
        clusters: list[ClusterBacklog] = []
        for ref, group in sorted(by_cluster.items()):
            values = [v.value for v in group if v.value is not None]
            clusters.append(
                ClusterBacklog(
                    snapshot_ref=ref,
                    size=len(group),
                    max_value=max(values) if values else None,
                    breaching=any(v.breaching for v in group),
                )
            )
        return tuple(clusters)


def _counts(values: Iterable[str]) -> dict[str, int]:
    """Ordered occurrence counts over an iterable of strings."""
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts
