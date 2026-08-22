"""Audit, observability, and evaluation for the KGCS curation core (Wave 7).

Spec §7.8 (audit is training/eval data) and §10.1 (the `kg_eval` seam, ADR-0009
honest null); build plan Wave 7 and §9 laws 9 (evidence/honest null) and 17
(replayable decisions). ADR candidates 0002 and 0012 predicted a "semantic
curation audit sink" joined by trace_id landing in this wave — this is it.

**Package naming.** The build plan's file-ownership guidance names an `audit/`
slot for this wave, but `src/kgcs/audit.py` already exists (the Wave-0
operation-scoped decision audit, `AuditRecorder`). A package `src/kgcs/audit/`
would clash with that module, so this wave lives in `observability/` instead.

The four seams:

- `semantic_audit` — the third audit object, `SemanticAuditRecord`: the full
  decision lineage joined to `AuditRecord` (operation-scoped) and
  `ExecutionRecord` (execution-scoped) by trace/plan/candidate ids, never by
  conflation.
- `replay` — re-run a recorded decision from its captured inputs and prove it
  reproduces byte-identically (law 17).
- `metrics` / `arms` — honest-null ER and curation metrics, named comparison
  arms, and a promotion gate that never raises a threshold on anecdote.
- `provider` — the `MetricProvider` seam KGCS implements and `kg_eval` consumes,
  defined here so there is no reverse dependency into KGCS.
"""

from kgcs.observability.arms import (
    DEFAULT_ENABLED_ARMS,
    ArmResult,
    ArmSpec,
    ComparisonArm,
    ThresholdVerdict,
    is_experimental,
    production_arms,
    run_arms,
    should_raise_threshold,
)
from kgcs.observability.metrics import (
    CurationMetrics,
    ErMetrics,
    cluster_precision_recall,
    curation_metrics,
    er_metrics,
)
from kgcs.observability.provider import (
    AuditMetricProvider,
    MetricProvider,
    MetricSnapshot,
)
from kgcs.observability.replay import ReplayResult, replay
from kgcs.observability.semantic_audit import (
    DEFAULT_POLICY_VERSION,
    ExecutionRef,
    InMemorySemanticAuditSink,
    ReplayInputs,
    ReviewSummary,
    SemanticAuditBuilder,
    SemanticAuditRecord,
    SemanticAuditSink,
    VersionSet,
)

__all__ = [
    # semantic audit (the third audit object)
    "SemanticAuditRecord",
    "SemanticAuditSink",
    "InMemorySemanticAuditSink",
    "SemanticAuditBuilder",
    "ReplayInputs",
    "VersionSet",
    "ReviewSummary",
    "ExecutionRef",
    "DEFAULT_POLICY_VERSION",
    # replay (law 17)
    "replay",
    "ReplayResult",
    # metrics (honest null)
    "ErMetrics",
    "CurationMetrics",
    "er_metrics",
    "curation_metrics",
    "cluster_precision_recall",
    # comparison arms + promotion gate
    "ComparisonArm",
    "ArmSpec",
    "ArmResult",
    "ThresholdVerdict",
    "run_arms",
    "should_raise_threshold",
    "DEFAULT_ENABLED_ARMS",
    "production_arms",
    "is_experimental",
    # kg_eval seam (no reverse dependency)
    "MetricProvider",
    "MetricSnapshot",
    "AuditMetricProvider",
]
