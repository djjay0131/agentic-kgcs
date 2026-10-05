"""Deterministic resolution: routing, identity disposition, ER escalation."""

from kg_contracts.candidates import CandidateScores
from kg_contracts.identity import EntityRef, is_identity_id
from kg_contracts.policy import AdjudicationRoute, ConfidencePolicy, IdentityDisposition
from kg_contracts.testing.factories import (
    make_attribute_candidate,
    make_entity_candidate,
    make_scores,
)

from helpers import GRAPH_ID, known_identity
from kgcs import CurationPlanner, ResolutionPolicy, ResolvedCandidate


def _policy() -> ResolutionPolicy:
    return ResolutionPolicy(snapshot_version="7")


def _new_identity_scores() -> CandidateScores:
    """Above both AUTO gates, with `identity_confidence` honestly absent."""
    return make_scores(extraction_confidence=0.99, source_reliability=0.99)


class TestRouting:
    def test_auto_scores_route_auto(self, auto_scores: CandidateScores) -> None:
        decision = _policy().resolve(make_entity_candidate(graph_id=GRAPH_ID, scores=auto_scores))
        assert decision.route is AdjudicationRoute.AUTO

    def test_assess_scores_route_llm_assess(self, assess_scores: CandidateScores) -> None:
        decision = _policy().resolve(make_entity_candidate(graph_id=GRAPH_ID, scores=assess_scores))
        assert decision.route is AdjudicationRoute.LLM_ASSESS

    def test_human_scores_route_human(self, human_scores: CandidateScores) -> None:
        decision = _policy().resolve(make_entity_candidate(graph_id=GRAPH_ID, scores=human_scores))
        assert decision.route is AdjudicationRoute.HUMAN


class TestEntityDisposition:
    def test_auto_entity_mints_new_identity(self, auto_scores: CandidateScores) -> None:
        decision = _policy().resolve(make_entity_candidate(graph_id=GRAPH_ID, scores=auto_scores))
        assert decision.create_new_identity
        assert decision.resolved_identity is not None
        assert is_identity_id(decision.resolved_identity)

    def test_non_auto_entity_defers_minting(self, human_scores: CandidateScores) -> None:
        decision = _policy().resolve(make_entity_candidate(graph_id=GRAPH_ID, scores=human_scores))
        assert not decision.create_new_identity
        assert decision.resolved_identity is None

    def test_minted_identity_is_deterministic(self, auto_scores: CandidateScores) -> None:
        candidate = make_entity_candidate(graph_id=GRAPH_ID, scores=auto_scores)
        assert _policy().resolve(candidate) == _policy().resolve(candidate)


class TestAttributeDisposition:
    def test_known_identity_subject_resolves(self, auto_scores: CandidateScores) -> None:
        subject = known_identity()
        candidate = make_attribute_candidate(
            graph_id=GRAPH_ID, subject=subject, scores=auto_scores
        )
        decision = _policy().resolve(candidate)
        assert decision.route is AdjudicationRoute.AUTO
        assert decision.resolved_identity == subject
        assert not decision.create_new_identity

    def test_entity_ref_subject_needs_er_and_escalates(self, auto_scores: CandidateScores) -> None:
        """A subject that is an alias, not an identity id, cannot auto-apply —
        the route escalates to at least LLM_ASSESS even with AUTO scores."""
        alias = EntityRef(entity_type="Player", namespace="test", key="ada")
        candidate = make_attribute_candidate(graph_id=GRAPH_ID, subject=alias, scores=auto_scores)
        decision = _policy().resolve(candidate)
        assert decision.route is AdjudicationRoute.LLM_ASSESS
        assert decision.resolved_identity is None


class TestDecisionMetadata:
    def test_matcher_version_is_honest_null(self, auto_scores: CandidateScores) -> None:
        """No matcher runs in Sprint 1, so matcher_version is None, not faked."""
        decision = _policy().resolve(make_entity_candidate(graph_id=GRAPH_ID, scores=auto_scores))
        assert decision.matcher_version is None

    def test_snapshot_version_is_carried(self, auto_scores: CandidateScores) -> None:
        decision = _policy().resolve(make_entity_candidate(graph_id=GRAPH_ID, scores=auto_scores))
        assert decision.snapshot_version == "7"

    def test_score_vector_reflects_candidate_scores(self) -> None:
        scores = make_scores(extraction_confidence=0.91, source_reliability=0.92)
        decision = _policy().resolve(make_entity_candidate(graph_id=GRAPH_ID, scores=scores))
        assert decision.score_vector["extraction_confidence"] == 0.91
        assert decision.score_vector["source_reliability"] == 0.92

    def test_trace_id_flows_from_candidate(self, auto_scores: CandidateScores) -> None:
        candidate = make_entity_candidate(graph_id=GRAPH_ID, scores=auto_scores)
        assert _policy().resolve(candidate).trace_id == candidate.trace_id


class TestDispositionIsDerivedBeforeRouting:
    """Issue #43: the disposition is a resolution fact, not a routing by-product.

    Before the fix, `ResolutionPolicy.resolve` called `route(scores)` with one
    argument, so every candidate was treated as `RESOLVED_EXISTING` and a
    brand-new identity — which by construction has no resolution score — could
    never route `AUTO`. These tests fail against that call site, not against
    `ConfidencePolicy.route` (which was always correct); the defect lived only
    in the producer.
    """

    def test_new_identity_with_absent_identity_confidence_routes_auto(self) -> None:
        candidate = make_entity_candidate(graph_id=GRAPH_ID, scores=_new_identity_scores())
        assert _policy().identity_disposition(candidate) is IdentityDisposition.NEW_IDENTITY
        decision = _policy().resolve(candidate)
        assert decision.route is AdjudicationRoute.AUTO
        assert decision.create_new_identity
        assert decision.resolved_identity is not None

    def test_new_identity_reaches_the_planner_as_create_identity(self) -> None:
        candidate = make_entity_candidate(graph_id=GRAPH_ID, scores=_new_identity_scores())
        resolution = _policy().resolve(candidate)
        plan = CurationPlanner().plan([ResolvedCandidate(candidate, resolution)]).plan
        assert plan is not None
        assert [op.type.value for op in plan.operations] == ["CREATE_IDENTITY"]

    def test_new_identity_with_stated_low_identity_confidence_is_blocked(self) -> None:
        # ADR-0024: `NEW_IDENTITY` excuses an *absent* score, never a low one.
        scores = make_scores(
            extraction_confidence=0.99, source_reliability=0.99, identity_confidence=0.5
        )
        decision = _policy().resolve(make_entity_candidate(graph_id=GRAPH_ID, scores=scores))
        assert decision.route is not AdjudicationRoute.AUTO
        assert not decision.create_new_identity

    def test_unresolved_identity_does_not_route_auto(self) -> None:
        # An alias subject means ER has not decided (an ambiguous/conflicting
        # cluster leaves it UNRESOLVED) -> `AUTO` is blocked outright.
        alias = EntityRef(entity_type="Player", namespace="test", key="ada")
        candidate = make_attribute_candidate(
            graph_id=GRAPH_ID, subject=alias, scores=_new_identity_scores()
        )
        assert _policy().identity_disposition(candidate) is IdentityDisposition.UNRESOLVED
        assert _policy().resolve(candidate).route is not AdjudicationRoute.AUTO

    def test_resolved_existing_with_absent_identity_confidence_is_unchanged(self) -> None:
        # An assertion whose subject is already a minted identity is
        # `RESOLVED_EXISTING`; a missing link confidence still blocks AUTO.
        candidate = make_attribute_candidate(
            graph_id=GRAPH_ID, subject=known_identity(), scores=_new_identity_scores()
        )
        assert _policy().identity_disposition(candidate) is IdentityDisposition.RESOLVED_EXISTING
        assert _policy().resolve(candidate).route is not AdjudicationRoute.AUTO

    def test_non_auto_entity_is_new_identity_not_unresolved(self, human_scores: CandidateScores) -> None:
        # The cycle regression: routing happens *after* disposition, so an
        # entity candidate that fails to route AUTO is not re-reported
        # UNRESOLVED just because nothing was minted.
        candidate = make_entity_candidate(graph_id=GRAPH_ID, scores=human_scores)
        assert _policy().identity_disposition(candidate) is IdentityDisposition.NEW_IDENTITY
        decision = _policy().resolve(candidate)
        assert decision.route is AdjudicationRoute.HUMAN
        assert decision.resolved_identity is None  # minting is still deferred


class TestThresholdsUnchanged:
    def test_default_confidence_thresholds_are_untouched(self) -> None:
        # The fix reads the disposition into `route`; it changes no number.
        policy = ConfidencePolicy()
        assert policy.auto_min_extraction == 0.95
        assert policy.auto_min_source_reliability == 0.90
        assert policy.auto_max_policy_risk == 0.20
        assert policy.human_min_policy_risk == 0.5
        assert policy.assess_min_extraction == 0.80
        assert policy.auto_min_identity_confidence == 0.95
        assert policy.require_identity_confidence_for_auto is True
        assert policy.allow_auto_for_new_identity is True
