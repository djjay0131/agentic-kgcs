"""Compensating-plan generation: how a committed plan is rolled back.

Rollback in this architecture is never deletion of history (governance
principle 5, `kg_contracts.curation`): every `CurationOperation` carries
`reversal_data` — "whatever is needed to undo this operation" — and undoing it
is itself a compensating operation applied through the same executor and the
same `GraphMutationStore`. The `Compensator` reads a committed `CurationPlan`
and emits a new `CurationPlan` whose operations reverse the originals, in
reverse order, so the last thing applied is the first thing undone.

Three facts make this honest rather than aspirational:

- **The inverse map is explicit, and some operations have no inverse in the
  v1 vocabulary.** `ATTACH_ASSERTION`↔`RETRACT_ASSERTION`,
  `MERGE_IDENTITIES`↔`SPLIT_IDENTITY`, and `REASSIGN_ASSERTION` (self-inverse)
  are compensable. `CREATE_IDENTITY` and `PROMOTE_ONTOLOGY_TERM` have **no**
  reversing operation type in `CurationOperationType` — there is no
  "un-create identity" or "demote ontology term" — so they are reported as
  `non_compensable` rather than papered over. That is invariant 8 stated
  precisely: every operation is either compensable or *explicitly declared
  non-compensable* (and a caller must block auto-execution of a rollback that
  cannot fully reverse).
- **The reversal payload comes from the contract's own `reversal_data`, not a
  guess — and it is a *payload*, not the whole dict.** A producer puts the
  inverse operation's payload under `INVERSE_PAYLOAD_KEY`; the rest of
  `reversal_data` is lineage and provenance (`candidate_id`, `trigger_id`,
  `evidence_ids`, matcher/adviser/policy versions) that names *why* the
  forward operation happened and must never leak into the inverse's payload.
  ADR-0018 records why: when the whole dict was used verbatim, a compensating
  `ATTACH_ASSERTION`'s payload was a provenance block rather than an
  `Assertion`, and a compensating `RETRACT_ASSERTION` silently dropped
  `new_status`/`superseded_at`. A plan whose ops predate the key (hand-built,
  or a third-party producer) still works: the whole `reversal_data` is used,
  as before.
- **A compensating plan asserts the state it expects to find NOW (ADR-0018).**
  A precondition is an optimistic-concurrency guard — "is the world still as
  it was when I computed this?" — and the compensation was computed against
  the world the *original plan produced*. Carrying the source plan's snapshot
  guard over made it false by construction (the original plan's own commit is
  what invalidated it), so every compensation was rejected `STALE` before it
  reached a store. `compensate` therefore takes a required keyword
  `against_snapshot`: the epoch the original plan committed at
  (`ExecutionRecord.new_epoch`). The guard is rebased onto it, so the rollback
  applies while the graph is still at that epoch and is correctly `STALE` once
  anything else has committed. Passing `against_snapshot=None` is the explicit
  "structure only, I am not going to execute this" request: the compensating
  plan then carries **no** snapshot guard and `snapshot_guarded` is False —
  a caller must not auto-execute it, exactly as it must not auto-execute a
  partial (`fully_compensable is False`) rollback.

The `Compensator` is pure and deterministic: inverse operation ids are derived
from the originals, so a replayed compensation is byte-identical. It generates
plans; it never applies them — that is `PlanExecutor`'s job, and on the Plan-1
reference store a `RETRACT_ASSERTION`/`SPLIT_IDENTITY` compensation currently
returns `UNSUPPORTED_OPERATION` (ADR candidate 0015), which the executor
reports explicitly rather than crashing.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from kg_contracts.curation import (
    CurationOperation,
    CurationOperationType,
    CurationPlan,
    Precondition,
)

from kgcs.ids import DerivedIdFactory, IdFactory
from kgcs.planner import (
    DEFAULT_POLICY_VERSION,
    INVERSE_PAYLOAD_KEY,
    SNAPSHOT_PRECONDITION_KIND,
)
from kgcs.policy import DEFAULT_SNAPSHOT_VERSION

#: The reversing operation type for each `CurationOperationType`, or `None`
#: when the v1 vocabulary has no inverse. Kept declarative so the compensable
#: set is auditable at a glance and widening it is a data change.
INVERSE_OPERATION: dict[CurationOperationType, CurationOperationType | None] = {
    CurationOperationType.ATTACH_ASSERTION: CurationOperationType.RETRACT_ASSERTION,
    CurationOperationType.RETRACT_ASSERTION: CurationOperationType.ATTACH_ASSERTION,
    CurationOperationType.MERGE_IDENTITIES: CurationOperationType.SPLIT_IDENTITY,
    CurationOperationType.SPLIT_IDENTITY: CurationOperationType.MERGE_IDENTITIES,
    CurationOperationType.REASSIGN_ASSERTION: CurationOperationType.REASSIGN_ASSERTION,
    CurationOperationType.CREATE_IDENTITY: None,
    CurationOperationType.PROMOTE_ONTOLOGY_TERM: None,
}


@dataclass(frozen=True)
class CompensationResult:
    """The outcome of compensating one plan.

    `plan` is the compensating `CurationPlan` of inverse operations (in
    reverse order), or `None` when no operation in the source plan had an
    inverse. `non_compensable` lists the source operations with no v1 inverse;
    when it is non-empty the rollback is *partial* and a caller must not treat
    the compensation as a full reversal.
    """

    plan: CurationPlan | None
    non_compensable: tuple[CurationOperation, ...]

    @property
    def fully_compensable(self) -> bool:
        """True iff every source operation had an inverse."""
        return not self.non_compensable

    @property
    def snapshot_guarded(self) -> bool:
        """True iff the compensating plan carries a snapshot precondition.

        False when `compensate` was called with `against_snapshot=None` (or
        the source plan carried no snapshot guard to rebase): the plan is
        structurally correct but **unguarded**, and applying it would race
        anything that committed since. Like `fully_compensable`, this is an
        explicit declaration a caller must honour — do not auto-execute an
        unguarded compensation; re-derive it with the epoch the original plan
        committed at (ADR-0018).
        """
        return self.plan is not None and any(
            p.kind == SNAPSHOT_PRECONDITION_KIND for p in self.plan.preconditions
        )


class Compensator:
    """Builds a compensating `CurationPlan` from a committed plan.

    Injected only with an `IdFactory` and the snapshot/policy versions to
    stamp on the compensating plan. Pure and stateless.
    """

    def __init__(
        self,
        *,
        id_factory: IdFactory | None = None,
        snapshot_version: str = DEFAULT_SNAPSHOT_VERSION,
        policy_version: str = DEFAULT_POLICY_VERSION,
    ) -> None:
        self._ids = id_factory or DerivedIdFactory()
        self._snapshot_version = snapshot_version
        self._policy_version = policy_version

    def compensate(
        self, plan: CurationPlan, *, against_snapshot: str | int | None
    ) -> CompensationResult:
        """Compute the compensation for `plan`, guarded against `against_snapshot`.

        Operations are reversed in LIFO order. Each compensable operation
        becomes its inverse; each operation with no v1 inverse is collected in
        `non_compensable` and contributes nothing to the compensating plan.

        `against_snapshot` is the snapshot the compensation expects to find —
        normally the epoch the *original* plan committed at, which
        `PlanExecutor.execute` returns as `ExecutionRecord.new_epoch`. The
        source plan's snapshot guards are rebased onto it (same kind, same
        subject, new `expected`) and the compensating plan's own
        `snapshot_version` is stamped with it. Carrying the *source* plan's
        expectation instead would be stale by construction — the original
        plan's commit is precisely what invalidated it (ADR-0018, superseding
        ADR candidate 0016).

        The keyword is required, with `None` the explicit "structure only"
        request: no snapshot guard is emitted and `snapshot_guarded` is False,
        which a caller must treat as "do not auto-execute", exactly as it
        treats `fully_compensable is False`. Per-subject `entity_version=0`
        guards are always dropped (they guarded creation, not reversal).
        """
        inverse_ops: list[CurationOperation] = []
        non_compensable: list[CurationOperation] = []
        for op in reversed(plan.operations):
            inverse_type = INVERSE_OPERATION.get(op.type)
            if inverse_type is None:
                non_compensable.append(op)
                continue
            inverse_ops.append(self._invert(op, inverse_type))

        if not inverse_ops:
            return CompensationResult(plan=None, non_compensable=tuple(non_compensable))

        snapshot = None if against_snapshot is None else str(against_snapshot)
        compensating = CurationPlan(
            plan_id=self._ids.plan_id(f"{plan.plan_id}:compensate"),
            candidate_ids=plan.candidate_ids,
            snapshot_version=snapshot if snapshot is not None else self._snapshot_version,
            operations=tuple(inverse_ops),
            preconditions=self._rebased_snapshot_preconditions(plan, snapshot),
            evidence_ids=plan.evidence_ids,
            policy_version=self._policy_version,
        )
        return CompensationResult(plan=compensating, non_compensable=tuple(non_compensable))

    def _invert(
        self, op: CurationOperation, inverse_type: CurationOperationType
    ) -> CurationOperation:
        """Build the inverse of one operation.

        The inverse's payload is the original's `reversal_data[INVERSE_PAYLOAD_KEY]`
        — the contract's "what is needed to undo this", separated from the
        lineage/provenance that shares the dict. Operations whose producer
        predates the key fall back to the whole `reversal_data`, which is what
        this did before ADR-0018. The inverse's own `reversal_data` records the
        original type and payload (and offers that payload as *its* inverse
        payload) so the compensation is itself reversible and traceable back to
        what it undid.
        """
        raw = op.reversal_data.get(INVERSE_PAYLOAD_KEY)
        payload = dict(raw) if isinstance(raw, Mapping) else dict(op.reversal_data)
        return CurationOperation(
            operation_id=self._ids.operation_id(f"{op.operation_id}:compensate"),
            type=inverse_type,
            payload=payload,
            reversal_data={
                INVERSE_PAYLOAD_KEY: dict(op.payload),
                "compensates_operation_id": op.operation_id,
                "original_type": op.type.value,
                "original_payload": op.payload,
            },
        )

    def _rebased_snapshot_preconditions(
        self, plan: CurationPlan, snapshot: str | None
    ) -> tuple[Precondition, ...]:
        """The source plan's snapshot guards, re-expected against `snapshot`.

        Empty when `snapshot` is None (see `compensate`) or when the source
        plan carried no snapshot guard — there is then no subject to guard,
        and inventing one would be a guess.
        """
        if snapshot is None:
            return ()
        return tuple(
            Precondition(kind=p.kind, subject=p.subject, expected=snapshot)
            for p in plan.preconditions
            if p.kind == SNAPSHOT_PRECONDITION_KIND
        )


__all__ = [
    "INVERSE_OPERATION",
    "INVERSE_PAYLOAD_KEY",
    "CompensationResult",
    "Compensator",
]
