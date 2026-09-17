"""Wave 5/8: the EvolutionRouter is the AUTO-path analogue of ReviewRouter.

An `AssertionAdviser` recommendation causally selects the evolution action
through a deterministic, conservative gate (product code, not test control
flow): SUPPORTS→corroboration, SUPERSEDES→supersession (only when permitted),
CONTRADICTS/INSUFFICIENT/forbidden-SUPERSEDES→preserved conflict + review. A
wrong adviser can at worst preserve a conflict — never supersede without
permission, never delete a competing assertion (§9 law 10)."""

from kg_contracts.assertions import Assertion, ConflictStatus
from kg_contracts.testing.factories import make_assertion

from kgcs.advisers.specialists import AssertionRecommendation
from kgcs.recuration import (
    ConceptEvolutionPlanner,
    CurationTrigger,
    EvolutionKind,
    EvolutionRouter,
    TriggerKind,
)

GRAPH = "g1"


def _trigger() -> CurationTrigger:
    return CurationTrigger.of(
        kind=TriggerKind.NEW_EVIDENCE, evidence_ids=("ev_new",), trace_id="t1"
    )


def _pair() -> tuple[Assertion, Assertion]:
    old = make_assertion(graph_id=GRAPH, predicate="year", object_value=2015)
    new = make_assertion(
        graph_id=GRAPH,
        subject_identity=old.subject_identity,
        predicate="year",
        object_value=2014,
    )
    return old, new


def _router() -> EvolutionRouter:
    return EvolutionRouter(planner=ConceptEvolutionPlanner(adviser_version="assertion/1"))


def test_supersedes_recommendation_drives_a_supersession_plan() -> None:
    old, new = _pair()
    result = _router().route_assertion(
        recommendation=AssertionRecommendation.SUPERSEDES,
        old_assertion=old,
        new_assertion=new,
        trigger=_trigger(),
    )
    assert result.kind is EvolutionKind.SUPERSESSION
    # the OLD assertion is preserved (marked SUPERSEDED), never deleted (law 10)
    assert any(a.assertion_id == old.assertion_id for a in result.superseded_assertions)


def test_supports_recommendation_drives_corroboration_not_supersession() -> None:
    old, new = _pair()
    result = _router().route_assertion(
        recommendation=AssertionRecommendation.SUPPORTS,
        old_assertion=old,
        new_assertion=new,
        trigger=_trigger(),
    )
    assert result.kind is EvolutionKind.CORROBORATION
    assert result.superseded_assertions == ()  # nothing superseded


def test_contradicts_preserves_both_and_routes_to_review() -> None:
    old, new = _pair()
    result = _router().route_assertion(
        recommendation=AssertionRecommendation.CONTRADICTS,
        old_assertion=old,
        new_assertion=new,
        trigger=_trigger(),
    )
    assert result.kind is EvolutionKind.CONFLICT
    assert result.review_required is True
    assert result.conflict_record is not None
    assert result.conflict_record.status is ConflictStatus.UNRESOLVED


def test_insufficient_never_supersedes() -> None:
    old, new = _pair()
    result = _router().route_assertion(
        recommendation=AssertionRecommendation.INSUFFICIENT,
        old_assertion=old,
        new_assertion=new,
        trigger=_trigger(),
    )
    assert result.kind is EvolutionKind.CONFLICT
    assert result.superseded_assertions == ()


def test_supersedes_without_permission_is_downgraded_to_conflict() -> None:
    # The policy gate: even a SUPERSEDES recommendation cannot supersede when the
    # profile forbids it — both assertions are preserved and reviewed.
    old, new = _pair()
    result = _router().route_assertion(
        recommendation=AssertionRecommendation.SUPERSEDES,
        old_assertion=old,
        new_assertion=new,
        trigger=_trigger(),
        supersession_allowed=False,
    )
    assert result.kind is EvolutionKind.CONFLICT
    assert result.review_required is True
    assert result.superseded_assertions == ()
