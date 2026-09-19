"""The transaction-aware executor: applies `CurationPlan`s and compensates them.

The write-path half of KGCS v1. The deterministic core (`kgcs.engine`) emits
an immutable `CurationPlan`; this package is the only place a plan becomes a
`GraphMutationBatch` applied to a `GraphMutationStore` (ADR-0010). It handles
preconditions/stale-plan rejection, curation-epoch publication, explicit
unsupported-operation failures, execution audit, and compensating-plan
generation for rollback.
"""

from kgcs.executor.compensate import (
    INVERSE_OPERATION,
    INVERSE_PAYLOAD_KEY,
    CompensationResult,
    Compensator,
)
from kgcs.executor.executor import (
    DEFAULT_EXECUTED_BY,
    DEFAULT_SUPPORTED_OPERATIONS,
    EpochPublisher,
    ExecutionAuditSink,
    ExecutionOutcome,
    ExecutionRecord,
    PlanExecutor,
)

__all__ = [
    "DEFAULT_EXECUTED_BY",
    "DEFAULT_SUPPORTED_OPERATIONS",
    "INVERSE_OPERATION",
    "INVERSE_PAYLOAD_KEY",
    "CompensationResult",
    "Compensator",
    "EpochPublisher",
    "ExecutionAuditSink",
    "ExecutionOutcome",
    "ExecutionRecord",
    "PlanExecutor",
]
