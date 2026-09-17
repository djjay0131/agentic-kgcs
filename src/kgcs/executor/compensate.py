"""Compensating-plan generation: how a committed plan is rolled back.

Rollback in this architecture is never deletion of history (governance
principle 5, `kg_contracts.curation`): every `CurationOperation` carries
`reversal_data` — "whatever is needed to undo this operation" — and undoing it
is itself a compensating operation applied through the same executor and the
same `GraphMutationStore`. The `Compensator` reads a committed `CurationPlan`
and emits a new `CurationPlan` whose operations reverse the originals, in
reverse order, so the last thing applied is the first thing undone.

Two facts make this honest rather than aspirational:

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
  guess.** Each inverse operation's payload is the original operation's
  `reversal_data` (which the planner populated with exactly what a reversal
  needs — e.g. an `ATTACH_ASSERTION` records the `assertion_id` a
  `RETRACT_ASSERTION` must target), and the inverse's own `reversal_data`
  carries the original type and payload so the compensation is itself
  reversible.

The `Compensator` is pure and deterministic: inverse operation ids are derived
from the originals, so a replayed compensation is byte-identical. It generates
plans; it never applies them — that is `PlanExecutor`'s job, and on the Plan-1
reference store a `RETRACT_ASSERTION`/`SPLIT_IDENTITY` compensation currently
returns `UNSUPPORTED_OPERATION` (those op types land in later waves), which the
executor reports explicitly rather than crashing.
"""

from dataclasses import dataclass

from kg_contracts.curation import (
    CurationOperation,
    CurationOperationType,
    CurationPlan,
    Precondition,
)

from kgcs.ids import DerivedIdFactory, IdFactory
from kgcs.planner import DEFAULT_POLICY_VERSION, SNAPSHOT_PRECONDITION_KIND
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

    def compensate(self, plan: CurationPlan) -> CompensationResult:
        """Compute the compensation for `plan`.

        Operations are reversed in LIFO order. Each compensable operation
        becomes its inverse; each operation with no v1 inverse is collected in
        `non_compensable` and contributes nothing to the compensating plan.
        The plan-level snapshot precondition (if any) is carried over as
        provenance; per-subject `entity_version=0` guards are dropped (they
        guarded creation, not reversal).
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

        compensating = CurationPlan(
            plan_id=self._ids.plan_id(f"{plan.plan_id}:compensate"),
            candidate_ids=plan.candidate_ids,
            snapshot_version=self._snapshot_version,
            operations=tuple(inverse_ops),
            preconditions=self._carry_snapshot_precondition(plan),
            evidence_ids=plan.evidence_ids,
            policy_version=self._policy_version,
        )
        return CompensationResult(plan=compensating, non_compensable=tuple(non_compensable))

    def _invert(
        self, op: CurationOperation, inverse_type: CurationOperationType
    ) -> CurationOperation:
        """Build the inverse of one operation.

        The inverse's payload is the original's `reversal_data` (the contract's
        "what is needed to undo this"), and the inverse's own `reversal_data`
        records the original type and payload so the compensation is itself
        reversible and traceable back to what it undid.
        """
        return CurationOperation(
            operation_id=self._ids.operation_id(f"{op.operation_id}:compensate"),
            type=inverse_type,
            payload=dict(op.reversal_data),
            reversal_data={
                "compensates_operation_id": op.operation_id,
                "original_type": op.type.value,
                "original_payload": op.payload,
            },
        )

    def _carry_snapshot_precondition(self, plan: CurationPlan) -> tuple[Precondition, ...]:
        return tuple(
            p for p in plan.preconditions if p.kind == SNAPSHOT_PRECONDITION_KIND
        )
