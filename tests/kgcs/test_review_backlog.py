"""Wave 6 / §7.7 / §9 law 15: backlog metrics detect priority inversion and a
starving high-value cluster, and pressure produces a machine-readable
throttle/quarantine `BackpressureSignal`. Metrics are a pure function of queue
state + an injected clock.
"""

from datetime import UTC, datetime, timedelta

from kg_contracts.curation import ReviewItem

from kgcs.clock import FixedClock
from kgcs.review.backlog import BacklogAnalyzer, BacklogConfig, BackpressureAction
from kgcs.review.model import ReviewCase

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


def _case_item(
    *,
    priority: str,
    source: str,
    enqueued_at: datetime,
    value: float | None = None,
    entity_type: str = "Person",
    snapshot_ref: str | None = None,
) -> ReviewItem:
    case = ReviewCase(
        kind="er_merge",
        proposed_action="AUTO_LINK",
        priority=priority,  # type: ignore[arg-type]
        source=source,
        entity_type=entity_type,
        value=value,
        snapshot_ref=snapshot_ref,
    )
    return case.to_item(enqueued_at=enqueued_at)


def _analyzer(config: BacklogConfig | None = None) -> BacklogAnalyzer:
    return BacklogAnalyzer(clock=FixedClock(NOW), config=config)


def test_priority_inversion_and_starving_high_value_detected() -> None:
    # A high-value P1 aged 48h (past its 24h SLA) while easy P3s flow within SLA.
    starving = _case_item(
        priority="P1",
        source="s_hard",
        enqueued_at=NOW - timedelta(hours=48),
        value=0.9,
        snapshot_ref="cluster-42",
    )
    easy = [
        _case_item(priority="P3", source="s_easy", enqueued_at=NOW) for _ in range(3)
    ]
    metrics = _analyzer().metrics([starving, *easy])

    assert metrics.priority_inversion is True
    assert starving.item_id in metrics.inverted_item_ids
    assert starving.item_id in metrics.starving_high_value
    assert metrics.sla_breaches == 1
    assert metrics.sla_breaches_by_priority == {"P1": 1}
    assert metrics.depth_by_source == {"s_hard": 1, "s_easy": 3}


def test_no_inversion_when_everything_flows_within_sla() -> None:
    items = [_case_item(priority="P2", source="s", enqueued_at=NOW) for _ in range(4)]
    metrics = _analyzer().metrics(items)
    assert metrics.priority_inversion is False
    assert metrics.inverted_item_ids == ()
    assert metrics.sla_breaches == 0


def test_unresolved_cluster_size_and_max_value() -> None:
    items = [
        _case_item(
            priority="P2",
            source="s",
            enqueued_at=NOW - timedelta(hours=200),
            value=0.5,
            snapshot_ref="cluster-7",
        ),
        _case_item(
            priority="P2",
            source="s",
            enqueued_at=NOW,
            value=0.95,
            snapshot_ref="cluster-7",
        ),
    ]
    metrics = _analyzer().metrics(items)
    (cluster,) = metrics.clusters
    assert cluster.snapshot_ref == "cluster-7"
    assert cluster.size == 2
    assert cluster.max_value == 0.95
    assert cluster.breaching is True  # the 200h-old P2 breaches its 7d SLA


def test_backpressure_emits_machine_readable_throttle_and_quarantine() -> None:
    config = BacklogConfig(throttle_depth=2, quarantine_depth=4, sla_breach_quarantine=99)
    # s_flood: 4 items → quarantine; s_warm: 2 items → throttle; s_calm: 1 → none.
    items: list[ReviewItem] = []
    items += [_case_item(priority="P3", source="s_flood", enqueued_at=NOW) for _ in range(4)]
    items += [_case_item(priority="P3", source="s_warm", enqueued_at=NOW) for _ in range(2)]
    items += [_case_item(priority="P3", source="s_calm", enqueued_at=NOW)]

    signals = _analyzer(config).backpressure(items)
    by_source = {s.source: s for s in signals}

    assert by_source["s_flood"].action is BackpressureAction.QUARANTINE
    assert by_source["s_flood"].recommended_max_intake == config.quarantine_intake
    assert by_source["s_warm"].action is BackpressureAction.THROTTLE
    assert by_source["s_warm"].recommended_max_intake == config.throttle_intake
    assert "s_calm" not in by_source

    # Machine-readable: survives a JSON round trip byte-for-byte.
    flood = by_source["s_flood"]
    from kgcs.review.backlog import BackpressureSignal

    assert BackpressureSignal.model_validate_json(flood.model_dump_json()) == flood


def test_backpressure_quarantines_on_sla_breaches() -> None:
    config = BacklogConfig(throttle_depth=99, quarantine_depth=99, sla_breach_quarantine=2)
    items = [
        _case_item(priority="P1", source="s", enqueued_at=NOW - timedelta(hours=48))
        for _ in range(2)
    ]
    (signal,) = _analyzer(config).backpressure(items)
    assert signal.action is BackpressureAction.QUARANTINE
    assert signal.sla_breaches == 2


def test_global_exposure_guard_throttles_largest_source() -> None:
    config = BacklogConfig(
        max_provisional_exposure=3, throttle_depth=99, quarantine_depth=99, sla_breach_quarantine=99
    )
    items = [_case_item(priority="P3", source="big", enqueued_at=NOW) for _ in range(3)]
    items += [_case_item(priority="P3", source="small", enqueued_at=NOW)]
    (signal,) = _analyzer(config).backpressure(items)
    assert signal.source == "big"
    assert signal.action is BackpressureAction.THROTTLE


def test_metrics_are_deterministic_pure_function() -> None:
    items = [
        _case_item(priority="P1", source="s", enqueued_at=NOW - timedelta(hours=48), value=0.9),
        _case_item(priority="P3", source="t", enqueued_at=NOW),
    ]
    a = _analyzer().metrics(items)
    b = _analyzer().metrics(items)
    assert a == b
    assert a.model_dump_json() == b.model_dump_json()
