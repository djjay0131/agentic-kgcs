"""Wave 6 / §9 law 14: a human review decision converges on the SAME typed
plan/executor/audit pipeline as an automated decision.

A reviewer's `APPROVE` re-runs the proposed shape through the same
`ConceptEvolutionPlanner` the auto path uses, yielding a byte-identical
`CurationPlan`; override actions (SPLIT/RELABEL/…) route through the same planner
too; and the contract's `EDIT`-requires-`edited_payload` validator is respected.
"""

from datetime import UTC, datetime

import pytest
from kg_contracts.assertions import Assertion
from kg_contracts.curation import CurationOperationType, ReviewAction, ReviewDecision
from kg_contracts.evidence import EvidenceRef, EvidenceRelationship
from kg_contracts.identity import new_identity_id
from kg_contracts.testing.factories import make_assertion
from pydantic import ValidationError

from kgcs.recuration import ConceptEvolutionPlanner, CurationTrigger, EvolutionKind, TriggerKind
from kgcs.review.operations import (
    ReviewOperationError,
    ReviewOutcomeStatus,
    ReviewProposal,
    ReviewRouter,
)

NOW = datetime(2026, 8, 21, tzinfo=UTC)
MEMBERS = ("kg://g1/identity/BBB", "kg://g1/identity/AAA")


def _planner() -> ConceptEvolutionPlanner:
    return ConceptEvolutionPlanner(
        matcher_version="rules/1", adviser_version="concept_evolution/1"
    )


def _trigger(**overrides: object) -> CurationTrigger:
    kwargs: dict[str, object] = {
        "kind": TriggerKind.NEW_EVIDENCE,
        "evidence_ids": ("ev_new",),
        "trace_id": "trace-1",
    }
    kwargs.update(overrides)
    return CurationTrigger.of(**kwargs)  # type: ignore[arg-type]


def _decision(action: ReviewAction, **kw: object) -> ReviewDecision:
    fields: dict[str, object] = {
        "item_id": "rv_1",
        "action": action,
        "actor": "reviewer",
        "decided_at": NOW,
    }
    fields.update(kw)
    return ReviewDecision(**fields)  # type: ignore[arg-type]


# -- APPROVE converges byte-for-byte on the auto plan ------------------------


def test_human_approve_yields_byte_identical_plan_to_auto_merge() -> None:
    planner = _planner()
    trigger = _trigger(identity_ids=MEMBERS)

    # The automated re-curation path: the evolution planner produces the plan.
    auto = planner.plan_merge(members=MEMBERS, trigger=trigger)

    # The human path: the SAME planner, fed the proposed inputs on APPROVE.
    router = ReviewRouter(planner=planner)
    proposal = ReviewProposal(
        trigger=trigger, proposed_kind=EvolutionKind.MERGE, merge_members=MEMBERS
    )
    outcome = router.route(_decision(ReviewAction.APPROVE), proposal)

    assert outcome.status is ReviewOutcomeStatus.PLANNED
    assert outcome.plan is not None
    # Structurally identical AND byte-identical via the executor JSON seam.
    assert outcome.plan == auto.plan
    assert outcome.plan.model_dump_json() == auto.plan.model_dump_json()


def test_approve_passthrough_uses_prebuilt_plan_when_no_kind() -> None:
    planner = _planner()
    trigger = _trigger(identity_ids=MEMBERS)
    auto = planner.plan_merge(members=MEMBERS, trigger=trigger)
    router = ReviewRouter(planner=planner)
    proposal = ReviewProposal(trigger=trigger, proposed_plan=auto.plan)
    outcome = router.route(_decision(ReviewAction.APPROVE), proposal)
    assert outcome.plan is auto.plan


# -- override actions route through the same evolution planner ----------------


def test_split_routes_through_evolution_planner() -> None:
    planner = _planner()
    trigger = _trigger(identity_ids=("kg://g1/identity/SRC",))
    auto = planner.plan_split(
        source_identity="kg://g1/identity/SRC",
        into=("kg://g1/identity/T1",),
        reassignments=(),
        trigger=trigger,
    )
    router = ReviewRouter(planner=planner)
    proposal = ReviewProposal(
        trigger=trigger,
        split_source="kg://g1/identity/SRC",
        split_into=("kg://g1/identity/T1",),
    )
    outcome = router.route(_decision(ReviewAction.SPLIT), proposal)
    assert outcome.kind is EvolutionKind.SPLIT
    assert outcome.plan == auto.plan
    assert CurationOperationType.SPLIT_IDENTITY in {op.type for op in outcome.plan.operations}


def test_relabel_routes_through_evolution_planner() -> None:
    planner = _planner()
    trigger = _trigger()
    label = _label_assertion(new_identity_id("g1"))
    auto = planner.plan_relabel(label_assertion=label, trigger=trigger)
    router = ReviewRouter(planner=planner)
    proposal = ReviewProposal(trigger=trigger, label_assertion=label)
    outcome = router.route(_decision(ReviewAction.RELABEL), proposal)
    assert outcome.kind is EvolutionKind.RELABEL
    assert outcome.plan == auto.plan


def test_link_and_same_concept_map_to_merge_and_relabel() -> None:
    planner = _planner()
    router = ReviewRouter(planner=planner)
    trigger = _trigger(identity_ids=MEMBERS)
    link = router.route(
        _decision(ReviewAction.LINK),
        ReviewProposal(trigger=trigger, merge_members=MEMBERS),
    )
    assert link.kind is EvolutionKind.MERGE

    label = _label_assertion(new_identity_id("g1"))
    scope = router.route(
        _decision(ReviewAction.SAME_CONCEPT_DIFFERENT_SCOPE),
        ReviewProposal(trigger=_trigger(), label_assertion=label),
    )
    assert scope.kind is EvolutionKind.RELABEL


def test_merge_elsewhere_uses_edited_target_members() -> None:
    planner = _planner()
    router = ReviewRouter(planner=planner)
    trigger = _trigger()
    other = ("kg://g1/identity/CCC", "kg://g1/identity/DDD")
    auto = planner.plan_merge(members=other, trigger=trigger)
    outcome = router.route(
        _decision(ReviewAction.MERGE_ELSEWHERE, edited_payload={"members": list(other)}),
        ReviewProposal(trigger=trigger, merge_members=MEMBERS),
    )
    assert outcome.plan == auto.plan


# -- REJECT records no plan; EDIT respects the contract validator -------------


def test_reject_records_no_plan() -> None:
    router = ReviewRouter(planner=_planner())
    outcome = router.route(
        _decision(ReviewAction.REJECT),
        ReviewProposal(trigger=_trigger(), proposed_kind=EvolutionKind.MERGE, merge_members=MEMBERS),
    )
    assert outcome.status is ReviewOutcomeStatus.RECORDED_NO_PLAN
    assert outcome.plan is None


def test_edit_without_edited_payload_rejected_by_contract() -> None:
    # The frozen ReviewDecision validator rejects an EDIT with no edited_payload.
    with pytest.raises(ValidationError):
        _decision(ReviewAction.EDIT)


def test_edit_with_payload_reruns_proposed_shape() -> None:
    planner = _planner()
    trigger = _trigger()
    narrowed = ("kg://g1/identity/AAA", "kg://g1/identity/BBB")
    auto = planner.plan_merge(members=narrowed, trigger=trigger)
    router = ReviewRouter(planner=planner)
    proposal = ReviewProposal(
        trigger=trigger,
        proposed_kind=EvolutionKind.MERGE,
        merge_members=("kg://g1/identity/AAA", "kg://g1/identity/BBB", "kg://g1/identity/ZZZ"),
    )
    outcome = router.route(
        _decision(ReviewAction.EDIT, edited_payload={"members": list(narrowed)}), proposal
    )
    assert outcome.plan == auto.plan


def test_missing_typed_input_raises_not_silent() -> None:
    router = ReviewRouter(planner=_planner())
    with pytest.raises(ReviewOperationError):
        router.route(
            _decision(ReviewAction.SPLIT), ReviewProposal(trigger=_trigger())
        )


def _label_assertion(subject: str) -> Assertion:
    refs = (EvidenceRef(evidence_id="ev_new", relationship=EvidenceRelationship.SUPPORTS),)
    return make_assertion(subject_identity=subject, evidence_refs=refs)
