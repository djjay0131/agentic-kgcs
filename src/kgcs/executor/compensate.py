"""Compensating-plan generation: how a committed plan is rolled back.

Rollback in this architecture is never deletion of history (governance
principle 5, `kg_contracts.curation`): every `CurationOperation` carries
`reversal_data` — "whatever is needed to undo this operation" — and undoing it
is itself a compensating operation applied through the same executor and the
same `GraphMutationStore`. The `Compensator` reads a committed `CurationPlan`
and emits a new `CurationPlan` whose operations reverse the originals, in
reverse order, so the last thing applied is the first thing undone.

Four facts make this honest rather than aspirational:

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
- **A compensating plan asserts the state it expects to find NOW, always
  (ADR-0018).** A precondition is an optimistic-concurrency guard — "is the
  world still as it was when I computed this?" — and the compensation was
  computed against the world the *original plan produced*. Carrying the source
  plan's snapshot guard over made it false by construction (the original
  plan's own commit is what invalidated it), so every compensation was
  rejected `STALE` before it reached a store. `compensate` therefore takes a
  required, **non-optional** `against_snapshot`: the epoch the original plan
  committed at (`ExecutionRecord.new_epoch`). Every compensating plan carries
  **exactly one** snapshot precondition expecting it — rebased from the source
  plan's guard when it had one, synthesized on the plan's first candidate id
  when it did not. There is no way to obtain an unguarded compensating plan
  from this module: no `None`, no "the source plan had no guard so neither
  does this one", no silent fail-open. `against_snapshot` is validated as an
  epoch (a non-negative integer, or its decimal string); anything else raises
  `ValueError` rather than becoming an `expected` no epoch can equal.
- **A compensating `ATTACH_ASSERTION` is an upsert by `assertion_id`, not an
  append (ADR-0018).** Restoring a record that was retracted re-attaches the
  *same* `assertion_id`. A store that appends ends up holding two rows for one
  id, and the next status change picks one of them arbitrarily — measured, a
  compensation of a compensation left two contradicting assertions both
  `ACTIVE` on the same subject and predicate. An `assertion_id` identifies a
  record; attaching it twice is the same record, and an adapter MUST replace
  in place. This is a requirement on `GraphMutationStore` implementations, not
  something the `Compensator` can enforce from here.

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

    There is deliberately no `snapshot_guarded` flag. An earlier revision of
    ADR-0018 carried one, because `against_snapshot=None` could produce an
    unguarded plan. That was a disclosed fail-open path with no production
    consumer — the executor only ever sees a `CurationPlan`, never this result
    — and disclosing a safety gap instead of closing it is the exact pattern
    this ADR exists to condemn. `plan` is now guarded by construction or does
    not exist, so the flag would be a constant.
    """

    plan: CurationPlan | None
    non_compensable: tuple[CurationOperation, ...]

    @property
    def fully_compensable(self) -> bool:
        """True iff every source operation had an inverse."""
        return not self.non_compensable



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
        self, plan: CurationPlan, *, against_snapshot: str | int
    ) -> CompensationResult:
        """Compute the compensation for `plan`, guarded against `against_snapshot`.

        Operations are reversed in LIFO order. Each compensable operation
        becomes its inverse; each operation with no v1 inverse is collected in
        `non_compensable` and contributes nothing to the compensating plan.

        `against_snapshot` is the snapshot the compensation expects to find —
        normally the epoch the *original* plan committed at, which
        `PlanExecutor.execute` returns as `ExecutionRecord.new_epoch`. The
        returned plan carries **exactly one** snapshot precondition expecting
        it, and its `snapshot_version` is stamped with it. Carrying the
        *source* plan's expectation instead would be stale by construction —
        the original plan's commit is precisely what invalidated it (ADR-0018,
        superseding ADR candidate 0016).

        The keyword is required and non-optional. There is no way to ask this
        method for an unguarded compensating plan, and a source plan that
        carried no snapshot guard does not yield one either: the guard is
        synthesized on the plan's first candidate id. Per-subject
        `entity_version=0` guards are always dropped (they guarded creation,
        not reversal, and an identity that now exists would fail them
        forever).

        Raises `ValueError` if `against_snapshot` is not an epoch — a
        non-negative integer or its decimal string. A free-form string would
        otherwise be accepted and become an `expected` value no epoch can ever
        equal, which is the defect this ADR fixes wearing a different hat.

        **What this guard does and does not say.** The executor compares it
        against one graph-global epoch and ignores `subject`, so it asserts
        "nothing at all has committed since", not "the records I am about to
        undo are still as I left them". It is a safe over-approximation: it
        never permits an unsafe rollback, but any unrelated commit refuses a
        valid one, and — because nothing here re-derives a compensation
        against a newer epoch — that refusal is permanent for that plan. The
        target state is per-subject guards over the records the compensation
        actually touches (ADR candidate 0003); see ADR-0018 §Consequences.
        Treat this as a waypoint, not the destination.
        """
        snapshot = _validated_epoch(against_snapshot)

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

        compensating = CurationPlan(
            plan_id=self._ids.plan_id(f"{plan.plan_id}:compensate"),
            candidate_ids=plan.candidate_ids,
            snapshot_version=snapshot,
            operations=tuple(inverse_ops),
            preconditions=self._snapshot_preconditions(plan, snapshot),
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

    def _snapshot_preconditions(
        self, plan: CurationPlan, snapshot: str
    ) -> tuple[Precondition, ...]:
        """Exactly one snapshot guard expecting `snapshot`.

        The source plan's snapshot guards are re-expected against `snapshot`
        (their `subject` is carried for provenance — the executor ignores it
        today, see `compensate`). A source plan with no snapshot guard still
        gets one, synthesized on its first candidate id: `candidate_ids` is
        non-empty by contract, and "the source plan had no guard" is not a
        reason to hand back a rollback that applies against anything.
        """
        rebased = tuple(
            Precondition(kind=p.kind, subject=p.subject, expected=snapshot)
            for p in plan.preconditions
            if p.kind == SNAPSHOT_PRECONDITION_KIND
        )
        if rebased:
            return rebased
        return (
            Precondition(
                kind=SNAPSHOT_PRECONDITION_KIND,
                subject=plan.candidate_ids[0],
                expected=snapshot,
            ),
        )


def _validated_epoch(value: str | int) -> str:
    """`value` as a decimal epoch string, or `ValueError`.

    A precondition's `expected` is compared to `str(reader.current_epoch())`.
    Anything that is not an epoch can never equal one, so accepting it would
    mint a guard that always fails — a fresh instance of the defect ADR-0018
    fixes. Rejected at the seam instead.
    """
    if isinstance(value, bool):  # bool is an int subtype; not an epoch
        raise ValueError(f"against_snapshot must be an epoch, not {value!r}")
    if isinstance(value, int):
        epoch = value
    else:
        try:
            epoch = int(value)
        except (TypeError, ValueError):
            raise ValueError(
                f"against_snapshot must be an epoch (a non-negative integer or its "
                f"decimal string), not {value!r}"
            ) from None
    if epoch < 0:
        raise ValueError(f"against_snapshot must be a non-negative epoch, not {value!r}")
    return str(epoch)


__all__ = [
    "INVERSE_OPERATION",
    "INVERSE_PAYLOAD_KEY",
    "CompensationResult",
    "Compensator",
]
