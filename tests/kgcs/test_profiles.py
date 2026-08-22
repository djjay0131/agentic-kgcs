"""DG-5 curation profiles: scope resolution is deterministic, the built-in
profiles carry the Issue #2 behaviours (projection_consumer auto/inert,
client-authoritative reject-only), and profiles configure the *existing* policy
machinery rather than a new write path."""

from kg_contracts.policy import AdjudicationRoute
from kg_contracts.testing.factories import make_entity_candidate, make_scores

from kgcs import ResolutionPolicy
from kgcs.profiles import (
    CurationProfile,
    ErMode,
    FalseMergeCostClass,
    IdentityAuthorityMode,
    ProfileRegistry,
    ProfileScope,
    client_authoritative_profile,
    default_profile,
    default_registry,
    projection_consumer_profile,
)


class TestProfileScope:
    def test_wildcard_matches_everything(self) -> None:
        assert ProfileScope().matches(graph_id="g1", entity_type="Paper", source="s")

    def test_pinned_selector_must_equal(self) -> None:
        scope = ProfileScope(entity_type="Paper")
        assert scope.matches(graph_id="g1", entity_type="Paper")
        assert not scope.matches(graph_id="g1", entity_type="Player")

    def test_source_selector_does_not_match_unknown_source(self) -> None:
        scope = ProfileScope(source="projection")
        assert not scope.matches(graph_id="g1", entity_type="Paper", source=None)

    def test_specificity_counts_pinned_fields(self) -> None:
        assert ProfileScope().specificity == 0
        assert ProfileScope(graph_id="g1", source="s").specificity == 2


class TestProfileRegistry:
    def test_falls_back_to_default_when_nothing_matches(self) -> None:
        registry = ProfileRegistry(default=default_profile())
        assert registry.resolve(graph_id="g1", entity_type="Paper").profile_id == "default"

    def test_most_specific_profile_wins(self) -> None:
        broad = CurationProfile(profile_id="broad", scope=ProfileScope(graph_id="g1"))
        narrow = CurationProfile(
            profile_id="narrow", scope=ProfileScope(graph_id="g1", entity_type="Paper")
        )
        registry = ProfileRegistry(profiles=(broad, narrow))
        assert registry.resolve(graph_id="g1", entity_type="Paper").profile_id == "narrow"
        assert registry.resolve(graph_id="g1", entity_type="Player").profile_id == "broad"

    def test_resolution_is_deterministic_on_specificity_ties(self) -> None:
        a = CurationProfile(profile_id="a", scope=ProfileScope(graph_id="g1"))
        b = CurationProfile(profile_id="b", scope=ProfileScope(graph_id="g1"))
        # Order of registration must not change the answer (tie → largest id).
        forward = ProfileRegistry(profiles=(a, b)).resolve(graph_id="g1", entity_type="Paper")
        backward = ProfileRegistry(profiles=(b, a)).resolve(graph_id="g1", entity_type="Paper")
        assert forward.profile_id == backward.profile_id == "b"

    def test_default_registry_carries_projection_consumer(self) -> None:
        registry = default_registry()
        resolved = registry.resolve(graph_id="g1", entity_type="Paper", source="projection")
        assert resolved.profile_id == "projection_consumer"


class TestProjectionConsumer:
    def test_er_is_inert(self) -> None:
        assert projection_consumer_profile().er_mode is ErMode.INERT

    def test_confidence_one_routes_auto_through_the_embedded_policy(self) -> None:
        """Issue #2 item 3: a confidence-1.0 projection routes AUTO — proving the
        profile configures the *existing* ResolutionPolicy, not a new path."""
        profile = projection_consumer_profile()
        policy = ResolutionPolicy(confidence_policy=profile.confidence_policy)
        scores = make_scores(
            extraction_confidence=1.0, source_reliability=1.0, identity_confidence=1.0
        )
        decision = policy.resolve(make_entity_candidate(graph_id="g1", scores=scores))
        assert decision.route is AdjudicationRoute.AUTO


class TestClientAuthoritativeProfile:
    def test_mode_and_er_mode(self) -> None:
        profile = client_authoritative_profile()
        assert profile.identity_authority_mode is IdentityAuthorityMode.CLIENT_AUTHORITATIVE
        assert profile.er_mode is ErMode.ADVISORY

    def test_never_permits_auto_link(self) -> None:
        assert "AUTO_LINK" not in client_authoritative_profile().allowable_auto_actions

    def test_defaults_to_high_false_merge_cost(self) -> None:
        assert client_authoritative_profile().false_merge_cost_class is FalseMergeCostClass.HIGH


class TestDefaultProfile:
    def test_is_open_active_and_may_auto_link(self) -> None:
        profile = default_profile()
        assert profile.identity_authority_mode is IdentityAuthorityMode.OPEN
        assert profile.er_mode is ErMode.ACTIVE
        assert "AUTO_LINK" in profile.allowable_auto_actions
