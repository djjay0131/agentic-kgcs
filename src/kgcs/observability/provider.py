"""The `kg_eval` seam: KGCS emits metrics, an eval harness reads them (Wave 7).

Build plan Wave 7: "metric-provider integration with `kg_eval` **without
introducing a reverse dependency** from `kg_eval` into KGCS." The dependency
direction is fixed: KGCS imports only `kg_contracts` (+ kgcs), and `kg_eval` is
a downstream consumer that may import KGCS — never the other way around. So the
seam is *defined here* as a `MetricProvider` Protocol that KGCS implements and
`kg_eval` consumes; there is no `import kg_eval` anywhere in this package (or in
`src/kgcs` at all), and no code path that would create the reverse edge.

`AuditMetricProvider` is the concrete provider over the in-memory semantic audit
sink: it reads the recorded `SemanticAuditRecord`s and emits an honest-null
`MetricSnapshot`. A downstream harness that wants label-dependent ER metrics
supplies its own `GoldenSet` + `Matcher` at construction; everything derivable
from the audit stream alone (abstention, review yield/agreement, rollback) is
emitted without any labels.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from kgcs.er.matcher import GoldenSet, Matcher
from kgcs.observability.metrics import (
    CurationMetrics,
    ErMetrics,
    curation_metrics,
    er_metrics,
)
from kgcs.observability.semantic_audit import SemanticAuditRecord, SemanticAuditSink


class MetricSnapshot(BaseModel):
    """The emitted metric bundle a `kg_eval`-style harness reads.

    Frozen and JSON-serializable so it can cross the seam as data, never as a
    live KGCS object graph. Both metric families are honest-null: thin or absent
    data surfaces as `None` fields (with counts), never as fabricated scores.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    er: ErMetrics
    curation: CurationMetrics


@runtime_checkable
class MetricProvider(Protocol):
    """The seam KGCS implements and a downstream eval harness consumes.

    Defined *here* precisely so `kg_eval` can depend on KGCS to read metrics
    without KGCS ever depending on `kg_eval`. A provider emits a `MetricSnapshot`;
    the consumer reads it. Nothing about this Protocol references `kg_eval`.
    """

    def snapshot(self) -> MetricSnapshot: ...


class AuditMetricProvider:
    """A `MetricProvider` over the in-memory semantic audit sink.

    Emits an honest-null `MetricSnapshot` computed from the recorded
    `SemanticAuditRecord`s. Optional `golden` + `matcher` enable the
    label-dependent ER metrics (pairwise P/R, false-merge/split, calibration
    error); without them those stay `None` while abstention/review/rollback are
    still emitted from the audit stream. Optional cluster labels and queue/latency
    series augment the snapshot when a harness supplies them.
    """

    def __init__(
        self,
        sink: SemanticAuditSink,
        *,
        golden: GoldenSet | None = None,
        matcher: Matcher | None = None,
        threshold: float = 0.5,
        predicted_clusters: Sequence[frozenset[str]] = (),
        gold_clusters: Sequence[frozenset[str]] = (),
        queue_ages_hours: Sequence[float] = (),
        canonicalization_latencies_hours: Sequence[float] = (),
    ) -> None:
        self._sink = sink
        self._golden = golden
        self._matcher = matcher
        self._threshold = threshold
        self._predicted_clusters = tuple(predicted_clusters)
        self._gold_clusters = tuple(gold_clusters)
        self._queue_ages_hours = tuple(queue_ages_hours)
        self._canonicalization_latencies_hours = tuple(canonicalization_latencies_hours)

    def snapshot(self) -> MetricSnapshot:
        """Read the audit stream and emit the current honest-null metric bundle."""
        records: list[SemanticAuditRecord] = self._sink.records()
        return MetricSnapshot(
            er=er_metrics(
                records,
                matcher=self._matcher,
                golden=self._golden,
                threshold=self._threshold,
                predicted_clusters=self._predicted_clusters,
                gold_clusters=self._gold_clusters,
            ),
            curation=curation_metrics(
                records,
                queue_ages_hours=self._queue_ages_hours,
                canonicalization_latencies_hours=self._canonicalization_latencies_hours,
            ),
        )
