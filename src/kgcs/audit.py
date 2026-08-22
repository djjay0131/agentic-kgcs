"""Immutable audit records for planned operations. The core's final stage.

Every operation the planner would apply gets one `AuditRecord` — the entry
that later justifies raising auto-promotion thresholds
(`kg_contracts.curation`). A record captures the full score vector, the
policy version, the evidence, the universal trace id, and *when* it was
decided. Only the timestamp is not a pure function of the input, so the
recorder takes an injected `Clock`; under a `FixedClock`, a replay produces
byte-identical audit records.

**Traceability, and a contract gap made explicit.** The contract's
`AuditRecord` keys on `operation_id`, and carries no `candidate_id`, no
validation outcome, and no resolution decision (it is `extra="forbid"`, so
those cannot be bolted on). Sprint 1 recovers full explainability three ways
without touching the contract:

- the **universal `trace_id`** flows candidate → validation → resolution →
  operation → audit, so an audit record joins back to its candidate by trace;
- each operation's `reversal_data` carries its source `candidate_id`
  (lineage), so `operation_id` → candidate is recoverable from the plan;
- the `ValidationDecision` and `ResolutionDecision` are themselves immutable,
  policy-versioned, trace-stamped records — they *are* the audit of the
  validation and resolution stages — and the engine retains them per
  candidate in its `EngineResult`.

The residual friction (no explicit `candidate_id`/validation link *on the
audit record itself*) is written up in the ADR candidate on audit lineage.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, Sequence, runtime_checkable

from kg_contracts.curation import AuditRecord

from kgcs.clock import Clock, SystemClock
from kgcs.ids import DerivedIdFactory, IdFactory
from kgcs.planner import PlannedOperation
from kgcs.scores import score_vector

DEFAULT_DECIDED_BY = "kgcs.curation-core/auto"
DEFAULT_POLICY_VERSION = "1"
"""Who decided a Sprint-1 operation: the deterministic core's automatic path.
Every planned operation is `AUTO`-routed (the planner emits nothing else), so
the decider is the core itself, not an LLM or a human."""


@runtime_checkable
class AuditSink(Protocol):
    """Append-only destination for audit records.

    The recorder *builds* records; a sink *keeps* them. Kept a separate port
    so the in-memory reference (tests) and a durable append-only log (Plan 2)
    are interchangeable. `record` appends one; `records` returns them in the
    order appended.
    """

    def record(self, audit: AuditRecord) -> None: ...

    def records(self) -> list[AuditRecord]: ...


@dataclass(frozen=True)
class AuditRecorder:
    """Builds one immutable `AuditRecord` per planned operation.

    Stateless apart from its injected `Clock` and `IdFactory`; `build`
    preserves operation order so the audit stream mirrors the plan. Pass
    `decided_by` to attribute the decision to something other than the
    default automatic core, and `policy_version` to stamp the confidence
    policy the routing came from (the contract's `ResolutionDecision` does
    not itself carry a policy version, so it is supplied here).
    """

    clock: Clock
    id_factory: IdFactory
    decided_by: str = DEFAULT_DECIDED_BY
    policy_version: str = DEFAULT_POLICY_VERSION

    @staticmethod
    def create(
        *,
        clock: Clock | None = None,
        id_factory: IdFactory | None = None,
        decided_by: str = DEFAULT_DECIDED_BY,
        policy_version: str = DEFAULT_POLICY_VERSION,
    ) -> "AuditRecorder":
        """Build a recorder with sensible defaults (`SystemClock`, derived ids)."""
        return AuditRecorder(
            clock=clock or SystemClock(),
            id_factory=id_factory or DerivedIdFactory(),
            decided_by=decided_by,
            policy_version=policy_version,
        )

    def build(self, planned_operations: Sequence[PlannedOperation]) -> tuple[AuditRecord, ...]:
        """One `AuditRecord` per operation, in operation order.

        `recorded_at` is read from the clock once per record; everything else
        is a pure function of the operation's candidate and decision.
        """
        recorded_at = self.clock.now()
        return tuple(self._record(item, recorded_at) for item in planned_operations)

    def _record(self, item: PlannedOperation, recorded_at: datetime) -> AuditRecord:
        candidate = item.candidate
        operation_id = item.operation.operation_id
        return AuditRecord(
            audit_id=self.id_factory.audit_id(f"{operation_id}:audit"),
            operation_id=operation_id,
            decided_by=self.decided_by,
            score_vector=score_vector(candidate.scores),
            evidence_ids=tuple(ref.evidence_id for ref in candidate.evidence_refs),
            policy_version=self.policy_version,
            trace_id=candidate.trace_id,
            recorded_at=recorded_at,
        )
