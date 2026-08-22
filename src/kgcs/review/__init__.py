"""Review & backpressure: the human-governed curation path (Wave 6, §9 laws 14, 15).

Completes the non-auto route and keeps curation debt from becoming silent
system failure. Five seams:

- `queue` — a persistent `ReviewQueue` (`PersistentReviewQueue` over a swappable
  `ReviewStore`, in-memory or JSON-file) satisfying the frozen contract Protocol
  and `ReviewQueueContract`; storage faults surface as typed retryable/permanent
  failures, never a silent drop (§9 law 15).
- `model` — the `ReviewCase` enrichment carried in a `ReviewItem.payload`
  (snapshot, proposed action, evidence, adviser assessments, risk/value, SLA,
  trace) plus the SLA math.
- `operations` — `ReviewRouter` folds a reviewer's `ReviewAction` through the
  *same* `ConceptEvolutionPlanner` the auto path uses, so a human decision
  reaches a byte-identical plan/executor/audit pipeline (§9 law 14).
- `backlog` — `BacklogAnalyzer` computes queue-pressure metrics (depth/age by
  source & entity type, priority inversion, starving high-value clusters) and
  emits machine-readable `BackpressureSignal`s for the KGIS-facing seam.
- `cli` — a stdlib-argparse terminal CLI to work the queue without editing
  storage by hand.
"""

from kgcs.review.backlog import (
    BacklogAnalyzer,
    BacklogConfig,
    BackpressureAction,
    BackpressureSignal,
    ClusterBacklog,
    QueueMetrics,
    SourceBacklog,
)
from kgcs.review.model import (
    SLA_HOURS_BY_PRIORITY,
    ReviewCase,
    sla_deadline,
    sla_hours,
)
from kgcs.review.operations import (
    ReviewOperationError,
    ReviewOutcome,
    ReviewOutcomeStatus,
    ReviewProposal,
    ReviewRouter,
)
from kgcs.review.queue import (
    InMemoryReviewStore,
    JsonFileReviewStore,
    PermanentQueueError,
    PersistentReviewQueue,
    QueueState,
    RetryableQueueError,
    ReviewQueueError,
    ReviewStore,
)

__all__ = [
    # queue (persistent + typed failures, §9 law 15)
    "PersistentReviewQueue",
    "ReviewStore",
    "InMemoryReviewStore",
    "JsonFileReviewStore",
    "QueueState",
    "ReviewQueueError",
    "RetryableQueueError",
    "PermanentQueueError",
    # model (review-item enrichment + SLA)
    "ReviewCase",
    "SLA_HOURS_BY_PRIORITY",
    "sla_hours",
    "sla_deadline",
    # operations (reviewer action → same plan pipeline, §9 law 14)
    "ReviewRouter",
    "ReviewProposal",
    "ReviewOutcome",
    "ReviewOutcomeStatus",
    "ReviewOperationError",
    # backlog / backpressure (§7.7, §9 law 15)
    "BacklogAnalyzer",
    "BacklogConfig",
    "QueueMetrics",
    "SourceBacklog",
    "ClusterBacklog",
    "BackpressureSignal",
    "BackpressureAction",
]
