"""The transaction-aware executor: the one place a `CurationPlan` is applied.

This is the write-path seam (ADR-0010, governance principle 1). The
deterministic core (Wave 0) produces an immutable `CurationPlan` and stops;
`PlanExecutor` is the *only* component that turns that plan into a
`GraphMutationBatch` and hands it to a `GraphMutationStore`. Applications never
hold a `GraphMutationStore` — they submit candidates and, downstream, the
executor alone mutates canonical state.

Four properties the executor guarantees, each an invariant test:

- **Every canonical mutation originates from a `CurationPlan`.** `execute`
  takes a `CurationPlan` and nothing else; there is no path here that mutates
  the graph from a raw operation, a candidate, or ad-hoc arguments.
- **Stale plans fail preconditions, they do not race.** The plan's
  `preconditions` are handed to `GraphMutationStore.apply` unchanged; a
  non-empty `failed_preconditions` in the `CommitResult` means the plan was
  computed against a snapshot the graph has moved past, and the executor
  reports `STALE` — the caller must re-evaluate, never blindly retry (the
  contract's `CommitResult` says exactly this).
- **Unsupported operations fail explicitly, they do not crash.** An adapter
  implements a subset of `CurationOperationType`; asked to apply an operation
  outside `supported_operations`, the executor returns `UNSUPPORTED_OPERATION`
  *without touching the store* (fail-closed, atomic), rather than letting a
  `NotImplementedError` escape. A defensive catch backstops adapters whose
  real support is narrower than declared.
- **Idempotent replay.** Re-executing a committed plan is rejected `STALE`,
  not applied a second time, and so is a *re-planned* replay of the same
  candidates against the graph's current snapshot. Three guards combine, two of
  them per-subject and enforced here:

  - a `CREATE_IDENTITY` carries an `entity_version=0` precondition the *store*
    enforces (it fails once the identity exists);
  - an `ATTACH_ASSERTION` carries an `assertion_absent` precondition naming its
    subject and the assertion id it would mint. The reference store ignores
    every kind but `entity_version`, so the **executor** enforces this one, by
    reading the subject's assertions through its `GraphReader` (the executor
    may read; it is not application-facing). Superseded assertions count as
    present — supersession is not deletion, so a superseded record still makes
    a re-attach of that same id a replay (ADR-0019);
  - the plan-level `snapshot_version` precondition, likewise enforced here
    against the graph's current epoch. A plan computed against epoch *N* only
    applies while the graph is still at *N*, so an unmodified replay of a
    committed plan is `STALE` on the snapshot alone. This is ADR-0003 option 1,
    realized here rather than by mutating the frozen contract.

  The snapshot guard alone is not enough: re-planning the same candidates
  against the *current* snapshot satisfies it, and before ADR-0019 that
  re-attached a byte-identical assertion — the same `assertion_id` twice on one
  subject — while the same replay of a `CREATE_IDENTITY` was correctly refused.
  Callers that pass the real snapshot they planned against get true optimistic
  concurrency; the default `snapshot_version="0"` gives
  single-use-against-a-virgin-graph semantics.

  Both executor-enforced guards need a reader. Constructed over a store that is
  neither a `GraphReader` nor paired with one, the executor has nothing to
  check them against and leaves them to the store — which, for the reference
  adapter's precondition contract, means they are not enforced at all.

Every attempt — committed, stale, unsupported, empty, errored, and every
compensation execution — is recorded as a KGCS-local `ExecutionRecord`. That
is deliberately *not* `kg_contracts.curation.AuditRecord`: the build plan
(Wave 1, Wave 7) rules that ledger/execution audit is a different object from
the core's decision audit and they must not be conflated. `ExecutionRecord`
links back by `plan_id` (and, through the plan, to candidate ids); the richer
semantic curation audit sink lands in Wave 7 on top of this linkage.
"""

from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from kg_contracts.curation import CurationOperationType, CurationPlan, Precondition
from kg_contracts.stores import (
    CommitResult,
    GraphMutationBatch,
    GraphMutationStore,
    GraphReader,
    GraphReadOptions,
)
from pydantic import BaseModel, ConfigDict

from kgcs.clock import Clock, SystemClock
from kgcs.ids import DerivedIdFactory, IdFactory
from kgcs.planner import ASSERTION_ABSENT_PRECONDITION_KIND, SNAPSHOT_PRECONDITION_KIND

DEFAULT_EXECUTED_BY = "kgcs.executor/auto"

#: The operation types the reference `MemoryGraphStore` actually applies.
#: Widened by later waves / richer adapters via the ``supported_operations``
#: argument, never by editing the frozen contract.
#:
#: `REVOKE_IDENTITY` joined the set with KGIS ADR-0025: the store implements it,
#: so a `CREATE_IDENTITY` rollback now reaches the store instead of being
#: refused `UNSUPPORTED_OPERATION` before it gets there. This constant is what
#: decides that — leaving it stale would have kept the compensation path dead
#: while every other piece of it worked.
DEFAULT_SUPPORTED_OPERATIONS: frozenset[CurationOperationType] = frozenset(
    {
        CurationOperationType.CREATE_IDENTITY,
        CurationOperationType.ATTACH_ASSERTION,
        CurationOperationType.REVOKE_IDENTITY,
    }
)


class ExecutionOutcome(StrEnum):
    """The result class of one `PlanExecutor.execute` attempt.

    Disjoint, exhaustive outcomes so a caller can branch without inspecting
    the store: `COMMITTED` advanced the graph to `new_epoch`; `STALE` hit a
    failed precondition and must be re-evaluated; `UNSUPPORTED_OPERATION` named
    an operation the adapter cannot apply (store untouched); `EMPTY` was a plan
    with no operations (store untouched); `ERROR` is any other apply failure
    the store reported.
    """

    COMMITTED = "COMMITTED"
    STALE = "STALE"
    UNSUPPORTED_OPERATION = "UNSUPPORTED_OPERATION"
    EMPTY = "EMPTY"
    ERROR = "ERROR"


class ExecutionRecord(BaseModel):
    """The immutable audit entry for one execution attempt (KGCS-local).

    Not a `kg_contracts.curation.AuditRecord` (see module docstring): this is
    the execution/ledger-side record, linked to the decision audit by
    `plan_id`. `is_compensation` distinguishes a compensating execution from a
    forward one so the audit stream tells the two apart.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    execution_id: str
    plan_id: str
    batch_id: str | None
    outcome: ExecutionOutcome
    new_epoch: int | None = None
    operation_ids: tuple[str, ...] = ()
    failed_preconditions: tuple[Precondition, ...] = ()
    unsupported_types: tuple[str, ...] = ()
    error: str | None = None
    is_compensation: bool = False
    executed_by: str = DEFAULT_EXECUTED_BY
    recorded_at: datetime

    @property
    def committed(self) -> bool:
        """True iff this attempt advanced canonical state."""
        return self.outcome is ExecutionOutcome.COMMITTED


@runtime_checkable
class EpochPublisher(Protocol):
    """Publishes the curation-epoch watermark after a committed apply.

    Derived projections read only *published* epochs (governance principle 8),
    so the executor advances a watermark rather than letting readers observe a
    partially promoted batch. The store owns the epoch counter; this port
    records the last epoch the executor declared published.
    """

    def publish(self, epoch: int) -> None: ...

    def published_epoch(self) -> int | None: ...


@runtime_checkable
class ExecutionAuditSink(Protocol):
    """Append-only destination for `ExecutionRecord`s.

    Mirrors `kgcs.audit.AuditSink`: the executor *builds* records, a sink
    *keeps* them. `record` appends one; `records` returns them in order.
    """

    def record(self, record: ExecutionRecord) -> None: ...

    def records(self) -> list[ExecutionRecord]: ...


class PlanExecutor:
    """Applies `CurationPlan`s to a `GraphMutationStore`, transactionally.

    Injected with the store and (optionally) an `IdFactory`, `Clock`, the set
    of operation types this adapter supports, an `EpochPublisher`, and an
    `ExecutionAuditSink`. Holds no application-facing write surface of its own;
    the only mutation path is `store.apply`, which is executor-only by
    contract.
    """

    def __init__(
        self,
        store: GraphMutationStore,
        *,
        id_factory: IdFactory | None = None,
        clock: Clock | None = None,
        supported_operations: frozenset[CurationOperationType] | None = None,
        epoch_publisher: EpochPublisher | None = None,
        audit_sink: ExecutionAuditSink | None = None,
        graph_reader: GraphReader | None = None,
        executed_by: str = DEFAULT_EXECUTED_BY,
    ) -> None:
        self._store = store
        self._ids = id_factory or DerivedIdFactory()
        self._clock = clock or SystemClock()
        self._supported = (
            supported_operations
            if supported_operations is not None
            else DEFAULT_SUPPORTED_OPERATIONS
        )
        self._epochs = epoch_publisher
        self._audit_sink = audit_sink
        self._executed_by = executed_by
        # The executor enforces plan-level snapshot preconditions itself when it
        # can read the graph's epoch (ADR-0003 option 1). Prefer an explicit
        # reader; otherwise use the store if it also implements `GraphReader`
        # (the reference `MemoryGraphStore` does). Without a reader, snapshot
        # preconditions are left to the store, which may not enforce them.
        if graph_reader is not None:
            self._reader: GraphReader | None = graph_reader
        elif isinstance(store, GraphReader):
            self._reader = store
        else:
            self._reader = None

    def compile_batch(self, plan: CurationPlan) -> GraphMutationBatch:
        """Compile a `CurationPlan` into the `GraphMutationBatch` to apply.

        A pure, deterministic transform: the batch id is derived from the plan
        id so a replayed plan compiles to a byte-identical batch. Raises
        `ValueError` if the plan has no operations — `GraphMutationBatch`
        requires at least one, and an empty plan has no epoch to commit
        (callers should route empty plans to `ExecutionOutcome.EMPTY`, which
        `execute` does).
        """
        if not plan.operations:
            raise ValueError("cannot compile a batch from a plan with no operations")
        return GraphMutationBatch(
            batch_id=self._ids.batch_id(plan.plan_id),
            plan_id=plan.plan_id,
            operations=plan.operations,
        )

    def execute(self, plan: CurationPlan, *, is_compensation: bool = False) -> ExecutionRecord:
        """Apply one plan and return its immutable `ExecutionRecord`.

        Sequence: guard empty plans; pre-check every operation type against
        `supported_operations` and bail *before touching the store* if any is
        unsupported; compile the batch; `store.apply` it with the plan's
        preconditions; classify the `CommitResult`. On a committed apply the
        epoch watermark is published. The record is appended to the audit sink
        if one was injected.
        """
        recorded_at = self._clock.now()
        operation_ids = tuple(op.operation_id for op in plan.operations)

        if not plan.operations:
            return self._finish(
                self._record(
                    plan,
                    batch_id=None,
                    outcome=ExecutionOutcome.EMPTY,
                    operation_ids=(),
                    recorded_at=recorded_at,
                    is_compensation=is_compensation,
                )
            )

        unsupported = tuple(
            sorted({op.type.value for op in plan.operations if op.type not in self._supported})
        )
        if unsupported:
            return self._finish(
                self._record(
                    plan,
                    batch_id=None,
                    outcome=ExecutionOutcome.UNSUPPORTED_OPERATION,
                    operation_ids=operation_ids,
                    unsupported_types=unsupported,
                    recorded_at=recorded_at,
                    is_compensation=is_compensation,
                )
            )

        stale = self._unmet_preconditions(plan)
        if stale:
            # Either the plan was computed against a snapshot the graph has
            # moved past, or a record it would mint is already there (a replay).
            # Reject before touching the store — re-evaluate, never blind-retry.
            return self._finish(
                self._record(
                    plan,
                    batch_id=None,
                    outcome=ExecutionOutcome.STALE,
                    operation_ids=operation_ids,
                    failed_preconditions=stale,
                    recorded_at=recorded_at,
                    is_compensation=is_compensation,
                )
            )

        batch = self.compile_batch(plan)
        try:
            result: CommitResult = self._store.apply(batch, plan.preconditions)
        except NotImplementedError as exc:
            # Backstop: an adapter whose real support is narrower than what
            # `supported_operations` declared. Fail explicitly, do not crash.
            return self._finish(
                self._record(
                    plan,
                    batch_id=batch.batch_id,
                    outcome=ExecutionOutcome.UNSUPPORTED_OPERATION,
                    operation_ids=operation_ids,
                    # Best effort: the store did not name which op it rejected,
                    # so report those beyond the known-safe baseline.
                    unsupported_types=tuple(
                        sorted(
                            {
                                op.type.value
                                for op in plan.operations
                                if op.type not in DEFAULT_SUPPORTED_OPERATIONS
                            }
                        )
                    ),
                    error=str(exc),
                    recorded_at=recorded_at,
                    is_compensation=is_compensation,
                )
            )

        outcome, new_epoch, error = self._classify(result)
        if outcome is ExecutionOutcome.COMMITTED and new_epoch is not None and self._epochs is not None:
            self._epochs.publish(new_epoch)

        return self._finish(
            self._record(
                plan,
                batch_id=batch.batch_id,
                outcome=outcome,
                operation_ids=operation_ids,
                new_epoch=new_epoch,
                failed_preconditions=result.failed_preconditions,
                error=error,
                recorded_at=recorded_at,
                is_compensation=is_compensation,
            )
        )

    # --- internals ------------------------------------------------------------

    def _unmet_preconditions(self, plan: CurationPlan) -> tuple[Precondition, ...]:
        """The plan's guards the graph does not satisfy, in plan order.

        Enforced by the executor because the reference `GraphMutationStore`
        checks only `entity_version` preconditions and silently passes every
        other kind. Two kinds are checked here:

        - `snapshot_version` — the plan was computed against a graph epoch the
          store has already moved past;
        - `assertion_absent` — the assertion this plan would mint is already on
          its subject, i.e. this is a replay (ADR-0019).

        `entity_version` is deliberately *not* re-checked here: the store owns
        that counter and enforces it itself. Returns the empty tuple when no
        reader is available — nothing the executor can check — or when every
        guard it can check holds.
        """
        if self._reader is None:
            return ()
        epoch = str(self._reader.current_epoch())
        unmet: list[Precondition] = []
        for p in plan.preconditions:
            if p.kind == SNAPSHOT_PRECONDITION_KIND:
                if p.expected != epoch:
                    unmet.append(p)
            elif p.kind == ASSERTION_ABSENT_PRECONDITION_KIND:
                if self._assertion_present(p.subject, p.expected):
                    unmet.append(p)
        return tuple(unmet)

    def _assertion_present(self, subject_identity: str, assertion_id: str) -> bool:
        """True iff `assertion_id` is already attached to `subject_identity`.

        `include_superseded=True` is load-bearing: supersession marks a record,
        it never deletes it (governance principle 5), and the default canonical
        read hides superseded records. Without the flag, re-attaching the same
        assertion id after its record was superseded would read as "absent" and
        the replay would be waved through.
        """
        assert self._reader is not None  # only reached from _unmet_preconditions
        existing = self._reader.assertions_for(
            subject_identity, GraphReadOptions(include_superseded=True)
        )
        return any(a.assertion_id == assertion_id for a in existing)

    @staticmethod
    def _classify(
        result: CommitResult,
    ) -> tuple[ExecutionOutcome, int | None, str | None]:
        """Map a `CommitResult` to an outcome, epoch, and error string."""
        if result.committed:
            return ExecutionOutcome.COMMITTED, result.new_epoch, None
        if result.failed_preconditions:
            return ExecutionOutcome.STALE, None, result.error
        return ExecutionOutcome.ERROR, None, result.error or "apply failed without a reason"

    def _record(
        self,
        plan: CurationPlan,
        *,
        batch_id: str | None,
        outcome: ExecutionOutcome,
        operation_ids: tuple[str, ...],
        recorded_at: datetime,
        is_compensation: bool,
        new_epoch: int | None = None,
        failed_preconditions: tuple[Precondition, ...] = (),
        unsupported_types: tuple[str, ...] = (),
        error: str | None = None,
    ) -> ExecutionRecord:
        # A deterministic content-address of the execution *event*, not a
        # physical-attempt counter: replaying the same plan against an
        # equivalent graph state yields the same id (determinism), while a
        # different outcome or a different committed epoch yields a different
        # id, so each distinct canonical mutation is uniquely keyed. Repeated
        # identical-outcome retries intentionally share an id (idempotent); a
        # durable sink that needs per-arrival keys adds its own sequence.
        kind = "comp" if is_compensation else "exec"
        epoch_tag = f":{new_epoch}" if new_epoch is not None else ""
        seed = f"{plan.plan_id}:{kind}:{outcome.value}{epoch_tag}"
        return ExecutionRecord(
            execution_id=self._ids.execution_id(seed),
            plan_id=plan.plan_id,
            batch_id=batch_id,
            outcome=outcome,
            new_epoch=new_epoch,
            operation_ids=operation_ids,
            failed_preconditions=failed_preconditions,
            unsupported_types=unsupported_types,
            error=error,
            is_compensation=is_compensation,
            executed_by=self._executed_by,
            recorded_at=recorded_at,
        )

    def _finish(self, record: ExecutionRecord) -> ExecutionRecord:
        if self._audit_sink is not None:
            self._audit_sink.record(record)
        return record


# Re-exported for callers that only need the field name, not the constant.
__all__ = [
    "DEFAULT_EXECUTED_BY",
    "DEFAULT_SUPPORTED_OPERATIONS",
    "EpochPublisher",
    "ExecutionAuditSink",
    "ExecutionOutcome",
    "ExecutionRecord",
    "PlanExecutor",
]
