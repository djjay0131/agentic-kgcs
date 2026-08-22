"""Honest-null evaluation metrics from labeled data + the audit stream (Wave 7).

Spec §10.1, ADR-0009, §9 law 9: **insufficient or empty data yields an explicit
`None` (with a count), never a fabricated `0.0` or `1.0`.** "Not measured" is a
different fact from "measured zero" and from "perfect", and this module refuses
to launder the one into the other — the same discipline `er.matcher.evaluate`
already applies (which this module reuses for the pairwise ER metrics rather than
re-deriving them).

Two metric families, both frozen result models with a `count`:

- `ErMetrics` — from labeled data + the audit stream: pairwise precision/recall,
  cluster precision/recall, false-merge/false-split rate, calibration error, and
  abstention rate.
- `CurationMetrics` — from the audit/queue/execution streams: review yield,
  review agreement rate, rollback frequency, mean queue age, and time to
  canonicalization.

Every function is a pure function of its inputs (no clock, no randomness): the
queue-age and canonicalization-latency inputs are supplied as already-computed
hour deltas so the metric layer never reads a wall clock itself.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from kg_contracts.curation import ReviewAction, ReviewDecision
from kgcs.er.matcher import CalibrationMetrics, GoldenSet, Matcher, evaluate
from kgcs.executor.executor import ExecutionRecord
from kgcs.observability.semantic_audit import SemanticAuditRecord


class ErMetrics(BaseModel):
    """Entity-resolution quality metrics — every rate honest-null (ADR-0009).

    A rate is `None` when its denominator is empty ("not measured"), never a
    fabricated `0.0`/`1.0`. `false_merge_rate` is reported alongside
    `false_split_rate` because §7.4 weights a false merge more heavily (a merge
    contaminates every attached fact). `abstention_rate` is the share of adviser
    assessments in the audit stream that abstained.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    count: int
    pairwise_precision: float | None = None
    pairwise_recall: float | None = None
    cluster_precision: float | None = None
    cluster_recall: float | None = None
    false_merge_rate: float | None = None
    false_split_rate: float | None = None
    calibration_error: float | None = None
    abstention_rate: float | None = None


class CurationMetrics(BaseModel):
    """Curation-throughput metrics from the audit/queue/execution streams.

    Every rate/mean is honest-null: no review outcomes ⇒ `review_yield` is
    `None`, not `0.0`; no executions ⇒ `rollback_frequency` is `None`; an empty
    age/latency series ⇒ its mean is `None`. `count` is the number of semantic
    audit records the throughput view was computed over.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    count: int
    review_yield: float | None = None
    review_agreement_rate: float | None = None
    rollback_frequency: float | None = None
    mean_queue_age_hours: float | None = None
    time_to_canonicalization_hours: float | None = None


def cluster_precision_recall(
    predicted: Sequence[frozenset[str]],
    gold: Sequence[frozenset[str]],
) -> tuple[float | None, float | None]:
    """Pairwise cluster precision/recall over co-membership (honest-null).

    Precision = (pairs together in both predicted and gold) / (pairs together in
    predicted); recall = the same numerator / (pairs together in gold). Each is
    `None` when its denominator is zero — no predicted co-memberships means
    precision is undefined, not `0.0`. Distinct from the matcher's *pairwise*
    metrics: this scores a whole clustering, not a labeled pair set.
    """
    predicted_pairs = _co_membership_pairs(predicted)
    gold_pairs = _co_membership_pairs(gold)
    true_positive = len(predicted_pairs & gold_pairs)
    precision = _ratio(true_positive, len(predicted_pairs))
    recall = _ratio(true_positive, len(gold_pairs))
    return precision, recall


def er_metrics(
    records: Sequence[SemanticAuditRecord] = (),
    *,
    matcher: Matcher | None = None,
    golden: GoldenSet | None = None,
    threshold: float = 0.5,
    predicted_clusters: Sequence[frozenset[str]] = (),
    gold_clusters: Sequence[frozenset[str]] = (),
) -> ErMetrics:
    """ER metrics from labeled data (pairwise/cluster) + the audit stream.

    Pairwise precision/recall, false-merge/split, and calibration error come
    from `er.matcher.evaluate` when a `matcher` and a non-empty `golden` set are
    supplied (else honest-null). Cluster precision/recall come from the predicted
    vs gold clusterings (else honest-null). Abstention rate is the share of
    adviser assessments across `records` that abstained (else honest-null). The
    `count` is the labeled golden-pair count.
    """
    pairwise: CalibrationMetrics | None = None
    if matcher is not None and golden is not None and golden.pairs:
        pairwise = evaluate(matcher, golden, threshold=threshold)

    cluster_precision: float | None = None
    cluster_recall: float | None = None
    if predicted_clusters and gold_clusters:
        cluster_precision, cluster_recall = cluster_precision_recall(
            predicted_clusters, gold_clusters
        )

    assessments = [a for record in records for a in record.assessments]
    abstention_rate = (
        _ratio(sum(1 for a in assessments if a.abstained), len(assessments))
        if assessments
        else None
    )

    return ErMetrics(
        count=pairwise.count if pairwise is not None else 0,
        pairwise_precision=pairwise.precision if pairwise is not None else None,
        pairwise_recall=pairwise.recall if pairwise is not None else None,
        cluster_precision=cluster_precision,
        cluster_recall=cluster_recall,
        false_merge_rate=pairwise.false_merge_rate if pairwise is not None else None,
        false_split_rate=pairwise.false_split_rate if pairwise is not None else None,
        calibration_error=pairwise.calibration_error if pairwise is not None else None,
        abstention_rate=abstention_rate,
    )


def curation_metrics(
    records: Sequence[SemanticAuditRecord] = (),
    *,
    executions: Sequence[ExecutionRecord] = (),
    review_decisions: Sequence[ReviewDecision] = (),
    queue_ages_hours: Sequence[float] = (),
    canonicalization_latencies_hours: Sequence[float] = (),
) -> CurationMetrics:
    """Curation-throughput metrics — every field honest-null on empty inputs.

    `review_yield` = share of the semantic audit's reviewed decisions that
    produced a plan (`status == PLANNED`). `review_agreement_rate` = share of
    reviewer decisions that were `APPROVE` (reviewer agreed with the machine
    proposal); read from `review_decisions` when supplied, else from the audit
    stream's review summaries. `rollback_frequency` = share of executions that
    were compensations; read from `executions` when supplied, else from the audit
    stream's execution refs. Queue age and canonicalization latency are the means
    of the supplied hour series. Any empty denominator ⇒ `None`, never `0.0`.
    """
    reviewed = [r.review for r in records if r.review is not None]
    review_yield = (
        _ratio(sum(1 for rv in reviewed if rv.status == "PLANNED"), len(reviewed))
        if reviewed
        else None
    )

    if review_decisions:
        review_agreement_rate = _ratio(
            sum(1 for d in review_decisions if d.action is ReviewAction.APPROVE),
            len(review_decisions),
        )
    elif reviewed:
        review_agreement_rate = _ratio(
            sum(1 for rv in reviewed if rv.action == ReviewAction.APPROVE.value),
            len(reviewed),
        )
    else:
        review_agreement_rate = None

    if executions:
        rollback_frequency = _ratio(
            sum(1 for e in executions if e.is_compensation), len(executions)
        )
    else:
        exec_refs = [r.execution for r in records if r.execution is not None]
        rollback_frequency = (
            _ratio(sum(1 for e in exec_refs if e.is_compensation), len(exec_refs))
            if exec_refs
            else None
        )

    return CurationMetrics(
        count=len(records),
        review_yield=review_yield,
        review_agreement_rate=review_agreement_rate,
        rollback_frequency=rollback_frequency,
        mean_queue_age_hours=_mean(queue_ages_hours),
        time_to_canonicalization_hours=_mean(canonicalization_latencies_hours),
    )


# --- honest-null primitives --------------------------------------------------


def _ratio(numerator: int, denominator: int) -> float | None:
    """`numerator / denominator`, or `None` when the denominator is zero.

    The honest null the whole module is built on: an undefined rate is `None`,
    never `0.0` (ADR-0009, §9 law 9)."""
    return numerator / denominator if denominator else None


def _mean(values: Sequence[float]) -> float | None:
    """The arithmetic mean, or `None` for an empty series (never a fake `0.0`)."""
    return sum(values) / len(values) if values else None


def _co_membership_pairs(clusters: Sequence[frozenset[str]]) -> set[tuple[str, str]]:
    """Every unordered same-cluster pair, canonically ordered, across `clusters`."""
    pairs: set[tuple[str, str]] = set()
    for cluster in clusters:
        members = sorted(cluster)
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                pairs.add((members[i], members[j]))
    return pairs
