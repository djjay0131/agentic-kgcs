"""Immutable `CurationPlan` generation. The core's output stage.

Stage 3 turns validated, resolved candidates into a `CurationPlan`: a frozen,
JSON-round-trippable description of *exactly what would happen* to the
canonical graph — which never executes here. The plan is the unit a later
transaction-aware executor (Plan 3) applies; Sprint 1 stops at producing it.

Only candidates the core is fully confident about become operations:

- route is `AUTO` (anything needing an LLM or a human is deferred, not
  planned), and
- the candidate maps to an operation type the v1 executor implements —
  `CREATE_IDENTITY` for an entity, `ATTACH_ASSERTION` for an attribute or
  relation. This mirrors exactly what the reference `MemoryGraphStore.apply`
  supports in Plan 1; the other five `CurationOperationType`s land in Plan 3.

`artifact` candidates are the deliberate gap: they validate and resolve, but
there is no artifact operation type in the contract, so they produce no
operation (see `llm/governance/adr/candidates/0001-artifact-has-no-operation-type.md`).

Determinism is structural, not incidental. A `CurationPlan` carries **no
timestamp** — every field is a pure function of the input candidates and the
injected `IdFactory`. So the planner needs no clock, and two plans over the
same resolved candidates are byte-for-byte equal (verified by round-tripping
through `model_dump_json`). The planner holds no mutable state and copies
nothing in place; it only reads frozen inputs and constructs frozen outputs
(the "planner never mutates state" invariant).

Two guarantees keep an emitted plan applicable:

- A `CREATE_IDENTITY` carries an `entity_version`/`expected="0"` precondition:
  the minted identity must not already exist. `MemoryGraphStore` enforces it.
- A plan-level `snapshot_version` precondition records the graph snapshot the
  plan was computed against. The Plan-1 executor does not enforce non-
  `entity_version` preconditions, but the guard is part of the plan's
  immutable provenance so a later executor can reject a stale plan.
"""

from dataclasses import dataclass
from typing import Sequence

from kg_contracts.assertions import Assertion, CanonicalEntity, CurationStatus
from kg_contracts.candidates import (
    AttributeAssertionCandidate,
    Candidate,
    EntityCandidate,
    RelationCandidate,
)
from kg_contracts.curation import (
    CurationOperation,
    CurationOperationType,
    CurationPlan,
    Precondition,
    ResolutionDecision,
)
from kg_contracts.evidence import Provenance, ValidPeriod
from kg_contracts.policy import AdjudicationRoute

from kgcs.ids import DerivedIdFactory, IdFactory
from kgcs.policy import DEFAULT_SNAPSHOT_VERSION

SNAPSHOT_PRECONDITION_KIND = "snapshot_version"
ENTITY_VERSION_PRECONDITION_KIND = "entity_version"
DEFAULT_POLICY_VERSION = "1"


@dataclass(frozen=True)
class ResolvedCandidate:
    """A validated candidate paired with its `ResolutionDecision`.

    The planner's unit of input: it needs the candidate for the operation's
    content and the decision for the identity disposition and route.
    """

    candidate: Candidate
    resolution: ResolutionDecision


@dataclass(frozen=True)
class PlannedOperation:
    """One built operation with the provenance the audit stage needs.

    Links an emitted `CurationOperation` back to the candidate and decision
    that produced it — the operation↔candidate mapping the audit recorder
    uses to attribute each audit record (the contract's `AuditRecord` keys on
    `operation_id`, not `candidate_id`).
    """

    operation: CurationOperation
    candidate: Candidate
    resolution: ResolutionDecision


@dataclass(frozen=True)
class PlanResult:
    """The planner's output: the plan plus its operation provenance.

    `plan` is `None` when no candidate produced an operation (nothing auto-
    applies), which keeps the contract's `candidate_ids`/`operations`
    non-empty invariants satisfiable — an empty plan is expressed as *no
    plan*, never a plan with zero operations.
    """

    plan: CurationPlan | None
    planned_operations: tuple[PlannedOperation, ...]


class CurationPlanner:
    """Builds an immutable `CurationPlan` from resolved candidates.

    Injected only with an `IdFactory` (deterministic by default) and the
    snapshot/policy versions to stamp. Pure and stateless: `plan()` reads
    frozen inputs and returns frozen outputs, mutating nothing.
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

    def plan(self, resolved: Sequence[ResolvedCandidate]) -> PlanResult:
        """Compile resolved candidates into a `PlanResult`.

        Preserves input order throughout: operations, `candidate_ids`,
        preconditions, and `evidence_ids` all follow the order candidates
        were supplied, so the plan is reproducible.
        """
        planned: list[PlannedOperation] = []
        for item in resolved:
            operation = self._build_operation(item.candidate, item.resolution)
            if operation is not None:
                planned.append(
                    PlannedOperation(
                        operation=operation,
                        candidate=item.candidate,
                        resolution=item.resolution,
                    )
                )

        if not planned:
            return PlanResult(plan=None, planned_operations=())

        operations = tuple(p.operation for p in planned)
        candidate_ids = tuple(p.candidate.candidate_id for p in planned)
        graph_id = planned[0].candidate.graph_id
        plan = CurationPlan(
            plan_id=self._ids.plan_id(self._plan_seed(candidate_ids)),
            candidate_ids=candidate_ids,
            snapshot_version=self._snapshot_version,
            operations=operations,
            preconditions=self._preconditions(planned, graph_id),
            evidence_ids=self._evidence_ids(planned),
            policy_version=self._policy_version,
        )
        return PlanResult(plan=plan, planned_operations=tuple(planned))

    # --- operation building ---------------------------------------------------

    def _build_operation(
        self, candidate: Candidate, resolution: ResolutionDecision
    ) -> CurationOperation | None:
        """The one operation this candidate would apply, or `None` to defer.

        Anything not routed `AUTO` is deferred (no operation). Beyond that,
        dispatch is by concrete candidate type; a kind with no v1 operation
        (artifact, or any candidate whose content cannot form a valid
        operation) yields `None`.
        """
        if resolution.route is not AdjudicationRoute.AUTO:
            return None
        if isinstance(candidate, EntityCandidate):
            return self._create_identity(candidate, resolution)
        if isinstance(candidate, AttributeAssertionCandidate):
            return self._attach_attribute(candidate, resolution)
        if isinstance(candidate, RelationCandidate):
            return self._attach_relation(candidate, resolution)
        return None

    def _create_identity(
        self, candidate: EntityCandidate, resolution: ResolutionDecision
    ) -> CurationOperation | None:
        """A `CREATE_IDENTITY` for a new entity identity.

        The identity id was minted by the resolution stage and travels here
        via `resolved_identity`; the planner never mints a second one. Entity
        `properties` are not materialized in v1 — an identity is created as a
        shell, and its properties would become attribute assertions later.
        """
        identity_id = resolution.resolved_identity
        if identity_id is None or not resolution.create_new_identity:
            return None
        entity = CanonicalEntity(
            identity_id=identity_id,
            entity_type=candidate.entity_type,
            aliases=candidate.aliases,
            status=CurationStatus.ACTIVE,
            display_name=candidate.display_name,
            created_at=candidate.created_at,
            curation_epoch=0,
        )
        return CurationOperation(
            operation_id=self._ids.operation_id(
                self._op_seed(candidate, CurationOperationType.CREATE_IDENTITY)
            ),
            type=CurationOperationType.CREATE_IDENTITY,
            payload=_entity_payload(entity),
            reversal_data={"identity_id": identity_id, "candidate_id": candidate.candidate_id},
        )

    def _attach_attribute(
        self, candidate: AttributeAssertionCandidate, resolution: ResolutionDecision
    ) -> CurationOperation | None:
        """An `ATTACH_ASSERTION` carrying an attribute value.

        Requires a known subject and a non-null value — an attribute whose
        value is `None` cannot form a valid `Assertion` (exactly one of
        object_value/object_identity must be set), so it is deferred rather
        than forced.
        """
        subject_identity = resolution.resolved_identity
        if subject_identity is None or candidate.value is None:
            return None
        assertion = self._assertion(
            candidate=candidate,
            subject_identity=subject_identity,
            predicate=candidate.attribute,
            object_value=candidate.value,
            object_identity=None,
            valid_period=candidate.valid_period,
        )
        return self._attach_operation(candidate, subject_identity, assertion)

    def _attach_relation(
        self, candidate: RelationCandidate, resolution: ResolutionDecision
    ) -> CurationOperation | None:
        """An `ATTACH_ASSERTION` linking subject to object identity.

        Both endpoints are guaranteed to be minted identity ids here: the
        resolution stage escalated the route away from `AUTO` for any
        relation whose subject or object still needed resolution, so a
        relation reaching this point with `AUTO` has both.
        """
        subject_identity = resolution.resolved_identity
        object_identity = candidate.object
        if subject_identity is None or not isinstance(object_identity, str):
            return None
        assertion = self._assertion(
            candidate=candidate,
            subject_identity=subject_identity,
            predicate=candidate.relation_type,
            object_value=None,
            object_identity=object_identity,
            valid_period=candidate.valid_period,
        )
        return self._attach_operation(candidate, subject_identity, assertion)

    def _assertion(
        self,
        *,
        candidate: Candidate,
        subject_identity: str,
        predicate: str,
        object_value: object | None,
        object_identity: str | None,
        valid_period: ValidPeriod | None,
    ) -> Assertion:
        """Build the `Assertion` an `ATTACH_ASSERTION` carries.

        Fields are derived deterministically from the candidate: `recorded_at`
        (transaction time) from the candidate's own `created_at`; `authority`
        and `provenance` from the producer and source coordinates (Sprint 1
        has no separate authority model — see the ADR candidate on authority
        provenance). `assertion_id` is pinned via the `IdFactory` so the plan
        is fully deterministic rather than defaulting to a random ULID.
        """
        return Assertion(
            assertion_id=self._ids.assertion_id(f"{candidate.candidate_id}:assertion"),
            subject_identity=subject_identity,
            predicate=predicate,
            object_value=object_value,
            object_identity=object_identity,
            status=CurationStatus.ACTIVE,
            valid_period=valid_period if valid_period is not None else ValidPeriod(),
            recorded_at=candidate.created_at,
            superseded_at=None,
            scores=candidate.scores,
            evidence_refs=candidate.evidence_refs,
            authority=candidate.producer,
            provenance=Provenance(
                source=candidate.source_coordinates.source_type,
                source_ref=candidate.source_coordinates.locator,
                actor=candidate.producer,
            ),
            derivation=None,
            curation_epoch=0,
            trace_id=candidate.trace_id,
        )

    def _attach_operation(
        self, candidate: Candidate, subject_identity: str, assertion: Assertion
    ) -> CurationOperation:
        return CurationOperation(
            operation_id=self._ids.operation_id(
                self._op_seed(candidate, CurationOperationType.ATTACH_ASSERTION)
            ),
            type=CurationOperationType.ATTACH_ASSERTION,
            payload=_assertion_payload(assertion),
            reversal_data={
                "assertion_id": assertion.assertion_id,
                "subject_identity": subject_identity,
                "candidate_id": candidate.candidate_id,
            },
        )

    # --- plan assembly --------------------------------------------------------

    def _preconditions(
        self, planned: Sequence[PlannedOperation], graph_id: str
    ) -> tuple[Precondition, ...]:
        """The plan's optimistic-concurrency guards, in a fixed order.

        The snapshot guard comes first (the whole plan was computed against
        one snapshot), then an `entity_version=0` guard per minted identity
        so a `CREATE_IDENTITY` refuses to clobber an id that already exists.
        `ATTACH_ASSERTION` emits no per-subject version guard: knowing a
        subject's current version needs a graph read, which Sprint 1 does not
        do (see the ADR candidate on snapshot-free preconditions).
        """
        preconditions: list[Precondition] = [
            Precondition(
                kind=SNAPSHOT_PRECONDITION_KIND,
                subject=graph_id,
                expected=self._snapshot_version,
            )
        ]
        for item in planned:
            if item.operation.type is CurationOperationType.CREATE_IDENTITY:
                identity_id = item.resolution.resolved_identity
                if identity_id is not None:
                    preconditions.append(
                        Precondition(
                            kind=ENTITY_VERSION_PRECONDITION_KIND,
                            subject=identity_id,
                            expected="0",
                        )
                    )
        return tuple(preconditions)

    def _evidence_ids(self, planned: Sequence[PlannedOperation]) -> tuple[str, ...]:
        """Evidence ids justifying the plan: first-seen order, deduplicated."""
        seen: set[str] = set()
        ordered: list[str] = []
        for item in planned:
            for ref in item.candidate.evidence_refs:
                if ref.evidence_id not in seen:
                    seen.add(ref.evidence_id)
                    ordered.append(ref.evidence_id)
        return tuple(ordered)

    def _plan_seed(self, candidate_ids: tuple[str, ...]) -> str:
        return "|".join(candidate_ids) + f"@{self._snapshot_version}#{self._policy_version}"

    def _op_seed(self, candidate: Candidate, op_type: CurationOperationType) -> str:
        return f"{candidate.candidate_id}:{op_type.value}"


def _entity_payload(entity: CanonicalEntity) -> dict[str, object]:
    """A JSON-native `CREATE_IDENTITY` payload, minus the executor-owned epoch.

    `curation_epoch` is assigned atomically by the executor at apply time
    (`MemoryGraphStore.apply`), so the plan omits it rather than pinning a
    placeholder. `mode="json"` gives primitive types, keeping the plan's
    `model_dump_json` round trip exact.
    """
    payload = entity.model_dump(mode="json")
    payload.pop("curation_epoch", None)
    return payload


def _assertion_payload(assertion: Assertion) -> dict[str, object]:
    """A JSON-native `ATTACH_ASSERTION` payload, minus the executor-owned epoch."""
    payload = assertion.model_dump(mode="json")
    payload.pop("curation_epoch", None)
    return payload
