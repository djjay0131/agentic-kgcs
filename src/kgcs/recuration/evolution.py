"""Concept evolution → compensable `CurationPlan` (Wave 5, DG-3, §9 laws 8, 10).

Concepts change as evidence accumulates. DG-3's rule is absolute: represent that
change through the *existing* immutable operations and bitemporal assertion
history, **never by rewriting history**. This module turns a concept-evolution
decision — a `ConceptEvolutionAdviser` recommendation folded through policy, or a
deterministic rule — together with its originating `CurationTrigger`, into an
immutable `CurationPlan` of the appropriate compensable operations.

The six shapes DG-3 names, one method each:

- **promotion** — a candidate/mention becomes a canonical concept
  (`CREATE_IDENTITY`);
- **merge** — two identities judged equivalent (`MERGE_IDENTITIES`) with a
  deterministic survivor (`er.cluster.select_survivor`) and the pre-merge
  membership in `reversal_data` so a `SPLIT_IDENTITY` can restore it;
- **split** — one identity decomposed into several, with an explicit
  `REASSIGN_ASSERTION` per moved assertion;
- **relabel / scope refinement** — a *new* alias/label assertion
  (`ATTACH_ASSERTION`), never a destructive rename;
- **supersession** — a new assertion is attached and the prior one is marked
  `SUPERSEDED` (a `RETRACT_ASSERTION` status change): the old record stays
  queryable bitemporally, it is **never deleted** (§9 law 10);
- **unresolved conflict** — *both* competing assertions are preserved (a
  `ConflictRecord`, `UNRESOLVED`) and the work is routed to review /
  gather-more-evidence; no winner is picked.

Two guarantees hold for every operation emitted here:

- **Compensable or explicitly non-compensable (§9 law 8).** Every op type used
  is present in `executor.compensate.INVERSE_OPERATION`. `MERGE↔SPLIT`,
  `ATTACH↔RETRACT`, and `REASSIGN` (self-inverse) are compensable; a promotion's
  `CREATE_IDENTITY` is *declared* non-compensable (no inverse in the v1
  vocabulary) rather than papered over — a caller must block auto-execution of a
  rollback that cannot fully reverse.
- **Traceable (§9 laws 9, 11).** Each op's `reversal_data` carries the
  originating `trigger_id`, the cited `evidence_ids`, and the matcher/adviser/
  policy versions, so every changed conclusion traces to a trigger and an
  evidence set.

Like the planner and compensator, this is pure and deterministic: op and plan
ids derive from the trigger, so handling the same trigger with the same inputs
yields a byte-identical plan (re-curation is idempotent — it does not double
apply).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from kg_contracts.assertions import (
    Assertion,
    CanonicalEntity,
    ConflictRecord,
    ConflictStatus,
    CurationStatus,
)
from kg_contracts.candidates import EntityCandidate
from kg_contracts.curation import (
    CurationOperation,
    CurationOperationType,
    CurationPlan,
    Precondition,
)

from kgcs.er.cluster import select_survivor
from kgcs.er.normalize import NormalizedEntity
from kgcs.ids import DerivedIdFactory, IdFactory
from kgcs.planner import (
    DEFAULT_POLICY_VERSION,
    INVERSE_PAYLOAD_KEY,
    SNAPSHOT_PRECONDITION_KIND,
    retract_inverse_payload,
)
from kgcs.policy import DEFAULT_SNAPSHOT_VERSION
from kgcs.recuration.triggers import (
    CurationTrigger,
    merge_evidence,
    trigger_provenance,
)


class EvolutionKind(StrEnum):
    """Which DG-3 concept-evolution shape a plan realizes."""

    PROMOTION = "PROMOTION"
    MERGE = "MERGE"
    SPLIT = "SPLIT"
    RELABEL = "RELABEL"
    CORROBORATION = "CORROBORATION"
    SUPERSESSION = "SUPERSESSION"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class AssertionReassignment:
    """One assertion moving off the split source onto a target identity."""

    assertion_id: str
    to_identity: str


@dataclass(frozen=True)
class EvolutionResult:
    """The output of one concept-evolution decision.

    `plan` is the immutable, compensable `CurationPlan` (present for every shape
    that mutates the graph). `superseded_assertions` are the prior records marked
    `SUPERSEDED` — returned so a caller (and a test) can see they survive,
    bitemporally queryable, never deleted (§9 law 10). `conflict_record` is set
    only for an unresolved conflict, where both sides are preserved and
    `review_required` is True.
    """

    kind: EvolutionKind
    plan: CurationPlan | None
    trigger_id: str
    evidence_ids: tuple[str, ...]
    rationale: str
    superseded_assertions: tuple[Assertion, ...] = ()
    conflict_record: ConflictRecord | None = None
    review_required: bool = False
    reassignments: tuple[AssertionReassignment, ...] = field(default_factory=tuple)


class ConceptEvolutionPlanner:
    """Builds compensable, bitemporal `CurationPlan`s for concept evolution.

    Injected only with an `IdFactory` (deterministic by default) and the
    version context to stamp — snapshot/policy versions plus the matcher/adviser
    versions the decision was made under, which are recorded in every operation's
    `reversal_data`. Pure and stateless: each `plan_*` method reads frozen inputs
    and returns frozen outputs, mutating nothing (a decision only *proposes* a
    plan; executing merge/split against a capable store is a later concern, and
    the Wave-1 reference executor returns `UNSUPPORTED_OPERATION` for these — that
    is expected and correct).
    """

    def __init__(
        self,
        *,
        id_factory: IdFactory | None = None,
        snapshot_version: str = DEFAULT_SNAPSHOT_VERSION,
        policy_version: str = DEFAULT_POLICY_VERSION,
        matcher_version: str | None = None,
        adviser_version: str | None = None,
    ) -> None:
        self._ids = id_factory or DerivedIdFactory()
        self._snapshot_version = snapshot_version
        self._policy_version = policy_version
        self._matcher_version = matcher_version
        self._adviser_version = adviser_version

    # -- promotion -----------------------------------------------------------

    def plan_promotion(
        self,
        *,
        candidate: EntityCandidate,
        trigger: CurationTrigger,
        identity_id: str | None = None,
    ) -> EvolutionResult:
        """A candidate/mention becomes a canonical concept (`CREATE_IDENTITY`).

        The identity id is either supplied (already minted by resolution) or
        minted here deterministically from the candidate. `CREATE_IDENTITY` has
        no inverse in the v1 vocabulary, so a promotion plan is *explicitly*
        non-compensable for that step (§9 law 8) — the caller must not
        auto-execute a rollback that cannot un-create the identity.
        """
        minted = identity_id or self._ids.identity_id(
            candidate.graph_id, f"promote:{candidate.candidate_id}"
        )
        entity = CanonicalEntity(
            identity_id=minted,
            entity_type=candidate.entity_type,
            aliases=candidate.aliases,
            status=CurationStatus.ACTIVE,
            display_name=candidate.display_name,
            created_at=candidate.created_at,
            curation_epoch=0,
        )
        operation = CurationOperation(
            operation_id=self._op_id(trigger, CurationOperationType.CREATE_IDENTITY, minted),
            type=CurationOperationType.CREATE_IDENTITY,
            payload=_json_payload(entity),
            reversal_data=self._reversal(trigger, {"identity_id": minted}),
        )
        return self._result(
            EvolutionKind.PROMOTION,
            trigger,
            operations=(operation,),
            candidate_ids=(candidate.candidate_id,),
            rationale=f"promoted candidate {candidate.candidate_id} to identity {minted}",
        )

    # -- merge ---------------------------------------------------------------

    def plan_merge(
        self,
        *,
        members: Sequence[str],
        trigger: CurationTrigger,
        entities: Mapping[str, NormalizedEntity] | None = None,
    ) -> EvolutionResult:
        """Two+ identities judged equivalent → `MERGE_IDENTITIES`.

        The survivor is chosen by `er.cluster.select_survivor` (a fixed,
        documented rule, so a replayed merge picks the same survivor). The full
        pre-merge membership is recorded in `reversal_data`; the inverse
        `SPLIT_IDENTITY` uses it to restore lineage (§9 law 8).
        """
        unique = tuple(sorted(set(members)))
        survivor = select_survivor(members, entities=entities)
        merged = tuple(m for m in unique if m != survivor)
        operation = CurationOperation(
            operation_id=self._op_id(trigger, CurationOperationType.MERGE_IDENTITIES, survivor),
            type=CurationOperationType.MERGE_IDENTITIES,
            payload={"survivor_identity": survivor, "merged_identities": list(merged)},
            reversal_data=self._reversal(
                trigger,
                {"survivor_identity": survivor, "premerge_members": list(unique)},
            ),
        )
        return self._result(
            EvolutionKind.MERGE,
            trigger,
            operations=(operation,),
            candidate_ids=unique,
            rationale=f"merged {list(merged)} into survivor {survivor}",
        )

    # -- split ---------------------------------------------------------------

    def plan_split(
        self,
        *,
        source_identity: str,
        into: Sequence[str],
        reassignments: Sequence[AssertionReassignment],
        trigger: CurationTrigger,
    ) -> EvolutionResult:
        """One identity decomposed into several, reassigning assertions explicitly.

        Emits a `SPLIT_IDENTITY` (reversible to a `MERGE_IDENTITIES` via the
        pre-split membership) followed by one `REASSIGN_ASSERTION` per moved
        assertion. `REASSIGN_ASSERTION` is its own inverse, so each op's
        `reversal_data` swaps `from`/`to` to move the assertion back (§9 law 8).
        """
        into_ids = tuple(into)
        split_op = CurationOperation(
            operation_id=self._op_id(
                trigger, CurationOperationType.SPLIT_IDENTITY, source_identity
            ),
            type=CurationOperationType.SPLIT_IDENTITY,
            payload={"source_identity": source_identity, "into_identities": list(into_ids)},
            reversal_data=self._reversal(
                trigger,
                {
                    "survivor_identity": source_identity,
                    "premerge_members": [source_identity, *into_ids],
                },
            ),
        )
        operations: list[CurationOperation] = [split_op]
        for reassignment in reassignments:
            operations.append(
                CurationOperation(
                    operation_id=self._op_id(
                        trigger,
                        CurationOperationType.REASSIGN_ASSERTION,
                        reassignment.assertion_id,
                    ),
                    type=CurationOperationType.REASSIGN_ASSERTION,
                    payload={
                        "assertion_id": reassignment.assertion_id,
                        "from_identity": source_identity,
                        "to_identity": reassignment.to_identity,
                    },
                    reversal_data=self._reversal(
                        trigger,
                        {
                            "assertion_id": reassignment.assertion_id,
                            "from_identity": reassignment.to_identity,
                            "to_identity": source_identity,
                        },
                    ),
                )
            )
        return self._result(
            EvolutionKind.SPLIT,
            trigger,
            operations=tuple(operations),
            candidate_ids=(source_identity, *into_ids),
            rationale=f"split {source_identity} into {list(into_ids)}",
            reassignments=tuple(reassignments),
        )

    # -- relabel / scope refinement -----------------------------------------

    def plan_relabel(
        self,
        *,
        label_assertion: Assertion,
        trigger: CurationTrigger,
    ) -> EvolutionResult:
        """A new alias/label/scope assertion — never a destructive rename.

        Relabelling and scope refinement are additive: a new `ATTACH_ASSERTION`
        records the new label or ontology relationship while every prior record
        stays intact (§DG-3). Compensable via `RETRACT_ASSERTION`.
        """
        operation = self._attach(trigger, label_assertion)
        return self._result(
            EvolutionKind.RELABEL,
            trigger,
            operations=(operation,),
            candidate_ids=(label_assertion.subject_identity,),
            rationale=f"relabelled/refined scope via new assertion {label_assertion.assertion_id}",
        )

    # -- corroboration -------------------------------------------------------

    def plan_corroboration(
        self,
        *,
        corroborating_assertion: Assertion,
        corroborated_assertion_id: str,
        trigger: CurationTrigger,
    ) -> EvolutionResult:
        """New evidence supports an existing assertion — attach, never overwrite.

        The corroborating assertion is attached as a new `ACTIVE` record citing
        the new evidence; the existing assertion is left untouched (still
        `ACTIVE`). Nothing is superseded — corroboration only *adds* support.
        """
        operation = self._attach(trigger, corroborating_assertion)
        return self._result(
            EvolutionKind.CORROBORATION,
            trigger,
            operations=(operation,),
            candidate_ids=(corroborating_assertion.subject_identity,),
            extra_evidence=_assertion_evidence(corroborating_assertion),
            rationale=(
                f"corroborated assertion {corroborated_assertion_id} with new "
                f"assertion {corroborating_assertion.assertion_id}"
            ),
        )

    # -- supersession --------------------------------------------------------

    def plan_supersession(
        self,
        *,
        old_assertion: Assertion,
        new_assertion: Assertion,
        trigger: CurationTrigger,
    ) -> EvolutionResult:
        """Attach a new assertion and mark the prior one `SUPERSEDED` — never delete.

        Emits `ATTACH_ASSERTION(new)` then `RETRACT_ASSERTION(old)` whose payload
        is a *status change* to `SUPERSEDED` (not `REVOKED`): the old record stays
        in the graph, bitemporally queryable, with `superseded_at` set to the new
        assertion's transaction time. Supersession never rewrites history (§9 law
        10). Both ops are compensable (`ATTACH↔RETRACT`). The returned
        `superseded_assertions` carries the marked-old copy so a caller can
        confirm it is preserved, not gone.
        """
        superseded_old = old_assertion.model_copy(
            update={
                "status": CurationStatus.SUPERSEDED,
                "superseded_at": new_assertion.recorded_at,
            }
        )
        attach_new = self._attach(trigger, new_assertion)
        retract_old = CurationOperation(
            operation_id=self._op_id(
                trigger, CurationOperationType.RETRACT_ASSERTION, old_assertion.assertion_id
            ),
            type=CurationOperationType.RETRACT_ASSERTION,
            payload={
                "assertion_id": old_assertion.assertion_id,
                "subject_identity": old_assertion.subject_identity,
                "new_status": CurationStatus.SUPERSEDED.value,
                "superseded_at": new_assertion.recorded_at.isoformat(),
                "superseded_by": new_assertion.assertion_id,
            },
            reversal_data=self._reversal(
                trigger,
                # The inverse of "mark this record SUPERSEDED" is "put the
                # record back as it stood", so the inverse ATTACH's payload is
                # the full pre-retraction assertion — status included (that is
                # what `restore_status` was gesturing at before ADR-0018, in a
                # payload that could not validate as an `Assertion` at all).
                _json_payload(old_assertion),
            ),
        )
        return self._result(
            EvolutionKind.SUPERSESSION,
            trigger,
            operations=(attach_new, retract_old),
            candidate_ids=(new_assertion.subject_identity,),
            extra_evidence=_assertion_evidence(new_assertion),
            rationale=(
                f"superseded assertion {old_assertion.assertion_id} with "
                f"{new_assertion.assertion_id}; old marked SUPERSEDED (preserved)"
            ),
            superseded_assertions=(superseded_old,),
        )

    # -- unresolved conflict -------------------------------------------------

    def plan_conflict(
        self,
        *,
        subject_assertion: Assertion,
        competing_assertion: Assertion,
        trigger: CurationTrigger,
        resolution_policy: str | None = None,
    ) -> EvolutionResult:
        """Preserve BOTH competing assertions; pick no winner (§9 law 10).

        When policy does not permit choosing, both assertions survive as ordinary
        records and an `UNRESOLVED` `ConflictRecord` links them. The competing
        assertion is attached (`ACTIVE`) so both are present; neither is
        superseded. `review_required` is True — the work routes to review /
        gather-more-evidence.
        """
        conflict = ConflictRecord(
            assertion_ids=(subject_assertion.assertion_id, competing_assertion.assertion_id),
            preferred_assertion_id=None,
            resolution_policy=resolution_policy,
            status=ConflictStatus.UNRESOLVED,
        )
        operation = self._attach(trigger, competing_assertion)
        result = self._result(
            EvolutionKind.CONFLICT,
            trigger,
            operations=(operation,),
            candidate_ids=(subject_assertion.subject_identity,),
            extra_evidence=_assertion_evidence(competing_assertion),
            rationale=(
                f"unresolved conflict between {subject_assertion.assertion_id} and "
                f"{competing_assertion.assertion_id}; both preserved, routed to review"
            ),
        )
        return EvolutionResult(
            kind=result.kind,
            plan=result.plan,
            trigger_id=result.trigger_id,
            evidence_ids=result.evidence_ids,
            rationale=result.rationale,
            conflict_record=conflict,
            review_required=True,
        )

    # -- shared construction -------------------------------------------------

    def _attach(self, trigger: CurationTrigger, assertion: Assertion) -> CurationOperation:
        """An `ATTACH_ASSERTION` for `assertion` with trace-linked reversal data."""
        return CurationOperation(
            operation_id=self._op_id(
                trigger, CurationOperationType.ATTACH_ASSERTION, assertion.assertion_id
            ),
            type=CurationOperationType.ATTACH_ASSERTION,
            payload=_json_payload(assertion),
            reversal_data=self._reversal(
                trigger,
                retract_inverse_payload(assertion, assertion.subject_identity),
            ),
        )

    def _result(
        self,
        kind: EvolutionKind,
        trigger: CurationTrigger,
        *,
        operations: tuple[CurationOperation, ...],
        candidate_ids: Sequence[str],
        rationale: str,
        extra_evidence: Sequence[str] = (),
        superseded_assertions: tuple[Assertion, ...] = (),
        reassignments: tuple[AssertionReassignment, ...] = (),
    ) -> EvolutionResult:
        """Assemble the immutable `CurationPlan` and wrap it in an `EvolutionResult`."""
        evidence_ids = merge_evidence(trigger, extra_evidence)
        candidates = _dedupe(candidate_ids) or (trigger.trigger_id,)
        plan = CurationPlan(
            plan_id=self._ids.plan_id(f"recur:{trigger.trigger_id}:{kind.value}"),
            candidate_ids=candidates,
            snapshot_version=self._snapshot_version,
            operations=operations,
            preconditions=(
                Precondition(
                    kind=SNAPSHOT_PRECONDITION_KIND,
                    subject=candidates[0],
                    expected=self._snapshot_version,
                ),
            ),
            evidence_ids=evidence_ids,
            policy_version=self._policy_version,
        )
        return EvolutionResult(
            kind=kind,
            plan=plan,
            trigger_id=trigger.trigger_id,
            evidence_ids=evidence_ids,
            rationale=rationale,
            superseded_assertions=superseded_assertions,
            reassignments=reassignments,
        )

    def _op_id(
        self, trigger: CurationTrigger, op_type: CurationOperationType, target: str
    ) -> str:
        """A deterministic operation id keyed by trigger + op type + target."""
        return self._ids.operation_id(f"recur:{trigger.trigger_id}:{op_type.value}:{target}")

    def _reversal(
        self, trigger: CurationTrigger, inverse_payload: Mapping[str, object]
    ) -> dict[str, object]:
        """The inverse operation's payload plus the trace-linked provenance block.

        The two are deliberately *not* merged flat (ADR-0018). `reversal_data`
        is read by two different consumers: `kgcs.executor.compensate` takes
        `INVERSE_PAYLOAD_KEY` as the reversing operation's payload, and an
        auditor takes the provenance block. Flattening them put `trigger_id`,
        `evidence_ids` and the version stamps into every inverse *payload* —
        harmless-looking for a merge, fatal for a `RETRACT`→`ATTACH` inverse,
        whose payload must validate as an `Assertion` (`extra="forbid"`, and
        `trace_id` means two different things on the two sides).
        """
        provenance = trigger_provenance(
            trigger,
            matcher_version=self._matcher_version,
            adviser_version=self._adviser_version,
            policy_version=self._policy_version,
        )
        return {INVERSE_PAYLOAD_KEY: dict(inverse_payload), **provenance}


def _assertion_evidence(assertion: Assertion) -> tuple[str, ...]:
    """The evidence ids an assertion cites, in order."""
    return tuple(ref.evidence_id for ref in assertion.evidence_refs)


def _json_payload(model: Assertion | CanonicalEntity) -> dict[str, object]:
    """A JSON-native operation payload, minus the executor-owned `curation_epoch`.

    Mirrors the planner: `curation_epoch` is assigned atomically by the executor
    at apply time, so the plan omits it, and `mode="json"` keeps the plan's
    `model_dump_json` round trip exact.
    """
    payload = model.model_dump(mode="json")
    payload.pop("curation_epoch", None)
    return payload


def _dedupe(values: Sequence[str]) -> tuple[str, ...]:
    """First-seen-order de-duplication (deterministic)."""
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return tuple(ordered)
