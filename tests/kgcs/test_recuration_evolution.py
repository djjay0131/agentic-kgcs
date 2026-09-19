"""Wave 5 / DG-3: concept evolution produces compensable, bitemporal
`CurationPlan`s that never rewrite history.

Covers §9 law 8 (compensable or explicitly non-compensable), law 10
(supersession/conflict preserve history — old marked SUPERSEDED, never deleted;
competing assertions preserved), traceability (every op carries trigger_id +
evidence_ids + versions), idempotency (same trigger → same plan), and the
motivating scenario end to end."""

from kg_contracts.assertions import Assertion, ConflictStatus, CurationStatus
from kg_contracts.curation import CurationOperationType, CurationPlan
from kg_contracts.evidence import EvidenceRef, EvidenceRelationship
from kg_contracts.identity import new_identity_id
from kg_contracts.testing.factories import make_assertion, make_entity_candidate

from kgcs.executor.compensate import (
    INVERSE_OPERATION,
    INVERSE_PAYLOAD_KEY,
    Compensator,
)
from kgcs.recuration import (
    AssertionReassignment,
    ConceptEvolutionPlanner,
    CurationTrigger,
    EvolutionKind,
    InMemoryDependencyIndex,
    TriggerKind,
)

GRAPH = "g1"


def _planner() -> ConceptEvolutionPlanner:
    return ConceptEvolutionPlanner(matcher_version="rules/1", adviser_version="concept_evolution/1")


def _trigger(**overrides: object) -> CurationTrigger:
    kwargs: dict[str, object] = {
        "kind": TriggerKind.NEW_EVIDENCE,
        "evidence_ids": ("ev_new",),
        "trace_id": "trace-1",
    }
    kwargs.update(overrides)
    return CurationTrigger.of(**kwargs)  # type: ignore[arg-type]


def _assertion(assertion_id: str, *, subject: str, evidence: tuple[str, ...] = ()) -> Assertion:
    refs = tuple(
        EvidenceRef(evidence_id=e, relationship=EvidenceRelationship.SUPPORTS) for e in evidence
    )
    return make_assertion(subject_identity=subject, evidence_refs=refs).model_copy(
        update={"assertion_id": assertion_id}
    )


def _all_op_types(plan: CurationPlan) -> set[CurationOperationType]:
    return {op.type for op in plan.operations}


# --- promotion --------------------------------------------------------------


def test_promotion_creates_identity_and_is_declared_non_compensable() -> None:
    candidate = make_entity_candidate()
    result = _planner().plan_promotion(candidate=candidate, trigger=_trigger())
    assert result.kind is EvolutionKind.PROMOTION
    assert result.plan is not None
    assert _all_op_types(result.plan) == {CurationOperationType.CREATE_IDENTITY}
    # CREATE_IDENTITY has no inverse — explicitly declared non-compensable (law 8).
    assert INVERSE_OPERATION[CurationOperationType.CREATE_IDENTITY] is None
    comp = Compensator().compensate(result.plan, against_snapshot=None)
    assert comp.fully_compensable is False
    assert len(comp.non_compensable) == 1


# --- merge ------------------------------------------------------------------


def test_merge_picks_deterministic_survivor_and_is_compensable() -> None:
    members = ("kg://g1/identity/BBB", "kg://g1/identity/AAA")
    result = _planner().plan_merge(members=members, trigger=_trigger())
    assert result.plan is not None
    (op,) = result.plan.operations
    assert op.type is CurationOperationType.MERGE_IDENTITIES
    # select_survivor with no entities → lexicographically smallest key.
    assert op.payload["survivor_identity"] == "kg://g1/identity/AAA"
    # The pre-merge membership the inverse SPLIT needs lives under
    # INVERSE_PAYLOAD_KEY, separate from the provenance block (ADR-0018).
    assert op.reversal_data[INVERSE_PAYLOAD_KEY]["premerge_members"] == [  # type: ignore[index]
        "kg://g1/identity/AAA",
        "kg://g1/identity/BBB",
    ]
    # MERGE ↔ SPLIT — fully compensable (law 8).
    assert INVERSE_OPERATION[CurationOperationType.MERGE_IDENTITIES] is CurationOperationType.SPLIT_IDENTITY
    comp = Compensator().compensate(result.plan, against_snapshot=None)
    assert comp.fully_compensable is True
    assert comp.plan is not None
    assert comp.plan.operations[0].type is CurationOperationType.SPLIT_IDENTITY


# --- split ------------------------------------------------------------------


def test_split_reassigns_assertions_and_is_compensable() -> None:
    source = new_identity_id(GRAPH)
    into = (new_identity_id(GRAPH), new_identity_id(GRAPH))
    reassignments = (
        AssertionReassignment(assertion_id="as_1", to_identity=into[0]),
        AssertionReassignment(assertion_id="as_2", to_identity=into[1]),
    )
    result = _planner().plan_split(
        source_identity=source, into=into, reassignments=reassignments, trigger=_trigger()
    )
    assert result.plan is not None
    types = [op.type for op in result.plan.operations]
    assert types == [
        CurationOperationType.SPLIT_IDENTITY,
        CurationOperationType.REASSIGN_ASSERTION,
        CurationOperationType.REASSIGN_ASSERTION,
    ]
    # REASSIGN reversal swaps from/to (it is self-inverse).
    reassign_op = result.plan.operations[1]
    assert reassign_op.payload["from_identity"] == source
    assert reassign_op.reversal_data[INVERSE_PAYLOAD_KEY]["to_identity"] == source  # type: ignore[index]
    comp = Compensator().compensate(result.plan, against_snapshot=None)
    assert comp.fully_compensable is True


# --- corroboration ----------------------------------------------------------


def test_corroboration_attaches_new_and_leaves_old_active() -> None:
    subject = new_identity_id(GRAPH)
    new = _assertion("as_new", subject=subject, evidence=("ev_new",))
    result = _planner().plan_corroboration(
        corroborating_assertion=new, corroborated_assertion_id="as_old", trigger=_trigger()
    )
    assert result.kind is EvolutionKind.CORROBORATION
    assert result.plan is not None
    assert _all_op_types(result.plan) == {CurationOperationType.ATTACH_ASSERTION}
    assert result.superseded_assertions == ()  # nothing superseded
    assert "ev_new" in result.evidence_ids


# --- supersession preserves history (law 10) --------------------------------


def test_supersession_attaches_new_and_marks_old_superseded_never_deleted() -> None:
    subject = new_identity_id(GRAPH)
    old = _assertion("as_old", subject=subject, evidence=("ev_old",))
    new = _assertion("as_new", subject=subject, evidence=("ev_new",))
    result = _planner().plan_supersession(old_assertion=old, new_assertion=new, trigger=_trigger())

    assert result.plan is not None
    types = [op.type for op in result.plan.operations]
    assert types == [
        CurationOperationType.ATTACH_ASSERTION,
        CurationOperationType.RETRACT_ASSERTION,
    ]
    # The old record is preserved, marked SUPERSEDED, bitemporally queryable —
    # NOT deleted and NOT revoked.
    (superseded,) = result.superseded_assertions
    assert superseded.assertion_id == "as_old"
    assert superseded.status is CurationStatus.SUPERSEDED
    assert superseded.superseded_at == new.recorded_at
    # The retract op is a status change to SUPERSEDED (not a delete).
    retract = result.plan.operations[1]
    assert retract.payload["new_status"] == CurationStatus.SUPERSEDED.value
    assert retract.payload["superseded_by"] == "as_new"


def test_supersession_is_fully_compensable() -> None:
    subject = new_identity_id(GRAPH)
    old = _assertion("as_old", subject=subject)
    new = _assertion("as_new", subject=subject, evidence=("ev_new",))
    result = _planner().plan_supersession(old_assertion=old, new_assertion=new, trigger=_trigger())
    assert result.plan is not None
    comp = Compensator().compensate(result.plan, against_snapshot=None)
    assert comp.fully_compensable is True


# --- unresolved conflict preserves both (law 10) ----------------------------


def test_conflict_preserves_both_and_picks_no_winner() -> None:
    subject = new_identity_id(GRAPH)
    existing = _assertion("as_existing", subject=subject, evidence=("ev_old",))
    competing = _assertion("as_competing", subject=subject, evidence=("ev_new",))
    result = _planner().plan_conflict(
        subject_assertion=existing, competing_assertion=competing, trigger=_trigger()
    )
    assert result.kind is EvolutionKind.CONFLICT
    assert result.review_required is True
    assert result.conflict_record is not None
    assert result.conflict_record.status is ConflictStatus.UNRESOLVED
    assert result.conflict_record.preferred_assertion_id is None  # no winner
    assert set(result.conflict_record.assertion_ids) == {"as_existing", "as_competing"}
    # Both survive: the competing assertion is attached as an ordinary record,
    # neither is superseded.
    assert result.plan is not None
    assert _all_op_types(result.plan) == {CurationOperationType.ATTACH_ASSERTION}
    assert result.superseded_assertions == ()


# --- relabel is additive, never destructive ---------------------------------


def test_relabel_is_additive_attach_not_rename() -> None:
    subject = new_identity_id(GRAPH)
    label = _assertion("as_label", subject=subject, evidence=("ev_new",))
    result = _planner().plan_relabel(label_assertion=label, trigger=_trigger())
    assert result.plan is not None
    assert _all_op_types(result.plan) == {CurationOperationType.ATTACH_ASSERTION}


# --- traceability (every op → trigger + evidence + versions) ----------------


def test_every_operation_carries_trigger_evidence_and_versions() -> None:
    subject = new_identity_id(GRAPH)
    old = _assertion("as_old", subject=subject)
    new = _assertion("as_new", subject=subject, evidence=("ev_new",))
    trigger = _trigger()
    result = _planner().plan_supersession(old_assertion=old, new_assertion=new, trigger=trigger)
    assert result.plan is not None
    for op in result.plan.operations:
        assert op.reversal_data["trigger_id"] == trigger.trigger_id
        assert "ev_new" in op.reversal_data["evidence_ids"]  # type: ignore[operator]
        assert op.reversal_data["matcher_version"] == "rules/1"
        assert op.reversal_data["adviser_version"] == "concept_evolution/1"
        assert op.reversal_data["policy_version"] == "1"


def test_plan_evidence_ids_cite_the_trigger_evidence() -> None:
    subject = new_identity_id(GRAPH)
    new = _assertion("as_new", subject=subject, evidence=("ev_new",))
    result = _planner().plan_corroboration(
        corroborating_assertion=new, corroborated_assertion_id="as_old", trigger=_trigger()
    )
    assert result.plan is not None
    assert "ev_new" in result.plan.evidence_ids


# --- every emitted op type is in the compensability map (law 8) -------------


def test_all_emitted_op_types_are_declared_in_inverse_operation() -> None:
    planner = _planner()
    subject = new_identity_id(GRAPH)
    plans = [
        planner.plan_promotion(candidate=make_entity_candidate(), trigger=_trigger()).plan,
        planner.plan_merge(members=(new_identity_id(GRAPH), new_identity_id(GRAPH)), trigger=_trigger()).plan,
        planner.plan_split(
            source_identity=subject,
            into=(new_identity_id(GRAPH),),
            reassignments=(AssertionReassignment(assertion_id="as_1", to_identity=new_identity_id(GRAPH)),),
            trigger=_trigger(),
        ).plan,
        planner.plan_supersession(
            old_assertion=_assertion("as_old", subject=subject),
            new_assertion=_assertion("as_new", subject=subject, evidence=("ev_new",)),
            trigger=_trigger(),
        ).plan,
    ]
    for plan in plans:
        assert plan is not None
        for op in plan.operations:
            assert op.type in INVERSE_OPERATION  # compensable or declared non-compensable


# --- idempotency + determinism ----------------------------------------------


def test_same_trigger_and_inputs_yield_identical_plan() -> None:
    subject = new_identity_id(GRAPH)
    old = _assertion("as_old", subject=subject)
    new = _assertion("as_new", subject=subject, evidence=("ev_new",))
    trigger = _trigger()
    first = _planner().plan_supersession(old_assertion=old, new_assertion=new, trigger=trigger)
    second = _planner().plan_supersession(old_assertion=old, new_assertion=new, trigger=trigger)
    assert first.plan is not None and second.plan is not None
    # Re-handling the same trigger produces a byte-identical plan — no double apply.
    assert first.plan.model_dump_json() == second.plan.model_dump_json()


def test_plan_round_trips_through_json() -> None:
    result = _planner().plan_merge(
        members=(new_identity_id(GRAPH), new_identity_id(GRAPH)), trigger=_trigger()
    )
    assert result.plan is not None
    restored = CurationPlan.model_validate_json(result.plan.model_dump_json())
    assert restored == result.plan


# --- the motivating scenario in miniature -----------------------------------


def test_new_evidence_scenario_targets_then_supersedes_preserving_history() -> None:
    # An existing preferred assertion on a known identity.
    subject = new_identity_id(GRAPH)
    existing = _assertion("as_existing", subject=subject, evidence=("ev_old",))

    # 1. A NEW_EVIDENCE trigger arrives for the new evidence.
    trigger = CurationTrigger.of(
        kind=TriggerKind.NEW_EVIDENCE,
        evidence_ids=("ev_new",),
        trace_id="paper-2",
        reason="second paper contradicts the preferred assertion",
    )

    # 2. Targeting finds the affected assertion WITHOUT scanning the graph.
    index = InMemoryDependencyIndex(
        evidence_to_assertions={"ev_new": ("as_existing",)},
        assertion_to_identity={"as_existing": subject},
    )
    affected = index.affected_by_trigger(trigger)
    assert "as_existing" in affected
    assert subject in affected

    # 3. Evolution produces a supersession plan citing the new evidence; the old
    #    assertion is preserved (SUPERSEDED, still queryable), never deleted.
    superseding = _assertion("as_superseding", subject=subject, evidence=("ev_new",))
    result = _planner().plan_supersession(
        old_assertion=existing, new_assertion=superseding, trigger=trigger
    )
    assert result.plan is not None
    assert "ev_new" in result.plan.evidence_ids
    (preserved,) = result.superseded_assertions
    assert preserved.status is CurationStatus.SUPERSEDED
    assert preserved.assertion_id == "as_existing"
    # Fully reversible, and every change traces back to the trigger.
    assert Compensator().compensate(result.plan, against_snapshot=None).fully_compensable is True
    for op in result.plan.operations:
        assert op.reversal_data["trigger_id"] == trigger.trigger_id
