"""Deterministic resolution: routing, identity disposition, ER escalation."""

from kg_contracts.candidates import CandidateScores
from kg_contracts.identity import EntityRef, is_identity_id
from kg_contracts.policy import AdjudicationRoute
from kg_contracts.testing.factories import (
    make_attribute_candidate,
    make_entity_candidate,
    make_scores,
)

from kgcs import ResolutionPolicy

from helpers import GRAPH_ID, known_identity


def _policy() -> ResolutionPolicy:
    return ResolutionPolicy(snapshot_version="7")


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
