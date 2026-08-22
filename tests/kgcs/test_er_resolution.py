"""ER stage 6: the deterministic policy gate. A complete baseline before any LLM
(§9 law 1), CLIENT_AUTHORITATIVE never merges (§9 law 13 / Issue #2), cluster
invalidity and stale snapshots can only make it more conservative, and routing
is risk/consequence-driven — not a fixed similarity band."""

from kg_contracts.identity import IdentityLinkKind
from kg_contracts.policy import AdjudicationRoute

from kgcs.er.blocking import CandidatePair
from kgcs.er.cluster import ClusterValidation, ConstraintResult
from kgcs.er.matcher import CalibrationKey, MatchResult
from kgcs.er.resolution import (
    ErAction,
    ErDecision,
    ErResolutionPolicy,
)
from kgcs.profiles import (
    CurationProfile,
    FalseMergeCostClass,
    client_authoritative_profile,
    default_profile,
    projection_consumer_profile,
)

_PAIR = CandidatePair.of("paper/a", "paper/b")
_KEY = CalibrationKey.of(
    graph_id="g1",
    entity_type="Paper",
    source_pair=("source_a", "source_b"),
    matcher_version="rules/1",
    consequence_class="standard",
)

_SWEEP = tuple(i / 20 for i in range(21))  # 0.0, 0.05, ... 1.0


def _mr(probability: float, *, mutually_exclusive: bool = False) -> MatchResult:
    return MatchResult(
        pair=_PAIR,
        probability=probability,
        matcher_version="rules/1",
        feature_vector={"mutually_exclusive": 1.0 if mutually_exclusive else 0.0},
        calibration_key=_KEY,
    )


def _invalid_cluster() -> ClusterValidation:
    return ClusterValidation(
        cluster_id="c1",
        version=3,
        valid=False,
        violations=(
            ConstraintResult(constraint="pairwise_mutual_exclusion", ok=False, offending=("a", "b")),
        ),
    )


def _high_cost(profile: CurationProfile) -> CurationProfile:
    return profile.model_copy(update={"false_merge_cost_class": FalseMergeCostClass.HIGH})


# --- §9 law 1: deterministic baseline complete before any LLM --------------


class TestDeterministicBaseline:
    def test_every_input_yields_an_action_without_an_llm(self) -> None:
        policy = ErResolutionPolicy()
        profiles = [default_profile(), client_authoritative_profile(), projection_consumer_profile()]
        for profile in profiles:
            for probability in _SWEEP:
                decision = policy.decide(_mr(probability), profile=profile)
                assert isinstance(decision.action, ErAction)

    def test_baseline_can_auto_link_without_ever_needing_llm_assess(self) -> None:
        policy = ErResolutionPolicy()
        decision = policy.decide(_mr(0.999), profile=default_profile())
        assert decision.action is ErAction.AUTO_LINK  # LLM_ASSESS is a route, not required

    def test_llm_assess_is_only_ever_one_selected_action(self) -> None:
        """The gate may *route* to LLM_ASSESS but is never blocked waiting for it."""
        policy = ErResolutionPolicy()
        decision = policy.decide(_mr(0.90), profile=_high_cost(default_profile()))
        assert decision.action is ErAction.LLM_ASSESS
        assert decision.to_route() is AdjudicationRoute.LLM_ASSESS


# --- §9 law 13 / Issue #2: client-authoritative never merges ---------------


class TestClientAuthoritativeNeverMerges:
    def test_never_auto_links_across_the_whole_probability_range(self) -> None:
        policy = ErResolutionPolicy()
        profile = client_authoritative_profile()
        for probability in _SWEEP:
            decision = policy.decide(_mr(probability), profile=profile)
            assert decision.action is not ErAction.AUTO_LINK
            assert decision.link_kind is not IdentityLinkKind.SAME_AS

    def test_the_most_it_does_is_propose_possibly_same_as(self) -> None:
        policy = ErResolutionPolicy()
        decision = policy.decide(_mr(0.9999), profile=client_authoritative_profile())
        assert decision.action is ErAction.PROPOSE_LINK
        assert decision.link_kind is IdentityLinkKind.POSSIBLY_SAME_AS

    def test_inert_er_mode_never_auto_links(self) -> None:
        policy = ErResolutionPolicy()
        profile = projection_consumer_profile()  # er_mode INERT
        for probability in _SWEEP:
            decision = policy.decide(_mr(probability), profile=profile)
            assert decision.action is ErAction.RETAIN_SEPARATE


# --- cluster validity gate -------------------------------------------------


class TestClusterValidityGate:
    def test_invalid_cluster_never_auto_links(self) -> None:
        policy = ErResolutionPolicy()
        decision = policy.decide(
            _mr(0.999), profile=default_profile(), cluster_validation=_invalid_cluster()
        )
        assert decision.action is not ErAction.AUTO_LINK
        assert decision.action is ErAction.RETAIN_SEPARATE

    def test_invalid_cluster_at_high_cost_escalates_to_human(self) -> None:
        policy = ErResolutionPolicy()
        decision = policy.decide(
            _mr(0.999),
            profile=_high_cost(default_profile()),
            cluster_validation=_invalid_cluster(),
        )
        assert decision.action is ErAction.HUMAN_REVIEW

    def test_stale_snapshot_abstains_rather_than_racing(self) -> None:
        policy = ErResolutionPolicy()
        decision = policy.decide(_mr(0.999), profile=default_profile(), snapshot_stale=True)
        assert decision.action is ErAction.ABSTAIN


# --- risk/consequence-driven routing, not a fixed band ---------------------


class TestRiskDrivenRouting:
    def test_raising_cost_class_makes_the_same_probability_more_conservative(self) -> None:
        """Same probability, only the false-merge cost class differs: STANDARD
        auto-links where HIGH refuses to — proving no fixed similarity band."""
        policy = ErResolutionPolicy()
        probability = 0.99
        standard = policy.decide(_mr(probability), profile=default_profile())
        high = policy.decide(_mr(probability), profile=_high_cost(default_profile()))
        assert standard.action is ErAction.AUTO_LINK
        assert high.action is ErAction.LLM_ASSESS  # strictly more conservative

    def test_confident_non_match_retains_separate(self) -> None:
        policy = ErResolutionPolicy()
        decision = policy.decide(_mr(0.02), profile=default_profile())
        assert decision.action is ErAction.RETAIN_SEPARATE

    def test_uncertain_middle_gathers_more_evidence(self) -> None:
        policy = ErResolutionPolicy()
        decision = policy.decide(_mr(0.5), profile=default_profile())
        assert decision.action is ErAction.GATHER_MORE_EVIDENCE


# --- other gate branches ---------------------------------------------------


class TestOtherBranches:
    def test_malformed_reference_is_rejected_without_repair(self) -> None:
        policy = ErResolutionPolicy()
        decision = policy.decide(_mr(0.999), profile=default_profile(), malformed=True)
        assert decision.action is ErAction.REJECT

    def test_insufficient_evidence_gathers_more(self) -> None:
        policy = ErResolutionPolicy()
        profile = default_profile().model_copy(update={"required_evidence_count": 3})
        decision = policy.decide(_mr(0.999), profile=profile, evidence_count=1)
        assert decision.action is ErAction.GATHER_MORE_EVIDENCE


# --- reproducibility / audit ----------------------------------------------


class TestReproducibility:
    def test_same_inputs_yield_equal_decisions(self) -> None:
        policy = ErResolutionPolicy()
        first = policy.decide(_mr(0.97), profile=default_profile())
        second = policy.decide(_mr(0.97), profile=default_profile())
        assert first == second

    def test_decision_is_byte_identical_after_json_round_trip(self) -> None:
        policy = ErResolutionPolicy()
        decision = policy.decide(
            _mr(0.97), profile=default_profile(), cluster_validation=_invalid_cluster()
        )
        dumped = decision.model_dump_json()
        reloaded = ErDecision.model_validate_json(dumped)
        assert reloaded == decision
        assert reloaded.model_dump_json() == dumped

    def test_decision_logs_the_driving_match_result(self) -> None:
        policy = ErResolutionPolicy()
        decision = policy.decide(_mr(0.97), profile=default_profile())
        assert decision.match_result.probability == 0.97
        assert decision.match_result.matcher_version == "rules/1"
        assert "mutually_exclusive" in decision.match_result.feature_vector
        assert decision.profile_id == "default"
        assert decision.rationale


class TestRouteProjection:
    def test_auto_link_maps_to_auto(self) -> None:
        policy = ErResolutionPolicy()
        assert policy.decide(_mr(0.999), profile=default_profile()).to_route() is (
            AdjudicationRoute.AUTO
        )

    def test_propose_link_maps_to_llm_assess(self) -> None:
        policy = ErResolutionPolicy()
        decision = policy.decide(_mr(0.9999), profile=client_authoritative_profile())
        assert decision.to_route() is AdjudicationRoute.LLM_ASSESS

    def test_abstain_maps_to_human(self) -> None:
        policy = ErResolutionPolicy()
        decision = policy.decide(_mr(0.999), profile=default_profile(), snapshot_stale=True)
        assert decision.to_route() is AdjudicationRoute.HUMAN


# --- Wave-3 review hardening: proven-invalid cluster is always conservative ---


class TestInvalidClusterAlwaysConservative:
    def test_invalid_cluster_forces_retain_across_full_probability_sweep(self) -> None:
        """A proven contradiction never routes toward a link OR an adviser — it
        is a deterministic RETAIN_SEPARATE at any probability (was previously
        able to fall through to LLM_ASSESS/GATHER at mid probability)."""
        policy = ErResolutionPolicy()
        profile = default_profile()
        for probability in _SWEEP:
            decision = policy.decide(
                _mr(probability), profile=profile, cluster_validation=_invalid_cluster()
            )
            assert decision.action is ErAction.RETAIN_SEPARATE

    def test_invalid_cluster_at_high_cost_escalates_to_human_at_any_probability(self) -> None:
        policy = ErResolutionPolicy()
        profile = _high_cost(default_profile())
        for probability in _SWEEP:
            decision = policy.decide(
                _mr(probability), profile=profile, cluster_validation=_invalid_cluster()
            )
            assert decision.action is ErAction.HUMAN_REVIEW

    def test_invalid_cluster_beats_insufficient_evidence(self) -> None:
        """A proven contradiction outranks 'gather more evidence' — more of the
        same evidence cannot reconcile a proven contradiction."""
        policy = ErResolutionPolicy()
        decision = policy.decide(
            _mr(0.99),
            profile=default_profile(),
            cluster_validation=_invalid_cluster(),
            evidence_count=0,
        )
        assert decision.action is ErAction.RETAIN_SEPARATE


# --- Wave-3 review: staleness seam wired end-to-end --------------------------


class TestStalenessSeam:
    def test_stale_snapshot_from_cluster_drives_abstain(self) -> None:
        from kgcs.er.cluster import Cluster, ClusterSnapshot

        pinned = ClusterSnapshot(cluster_id="c1", version=3)
        moved = Cluster(cluster_id="c1", version=4, graph_id="g1", entity_type="Paper")
        stale = pinned.is_stale(moved)
        assert stale is True
        decision = ErResolutionPolicy().decide(
            _mr(0.99), profile=default_profile(), snapshot_stale=stale
        )
        assert decision.action is ErAction.ABSTAIN


# --- Wave-3 review: reject-only holds even combined with invalid + malformed --


class TestRejectOnlyUnderCombinedConditions:
    def test_client_authoritative_never_auto_links_even_with_invalid_or_malformed(self) -> None:
        policy = ErResolutionPolicy()
        profile = client_authoritative_profile()
        for probability in _SWEEP:
            for cv in (None, _invalid_cluster()):
                for malformed in (False, True):
                    decision = policy.decide(
                        _mr(probability),
                        profile=profile,
                        cluster_validation=cv,
                        malformed=malformed,
                    )
                    assert decision.action is not ErAction.AUTO_LINK
                    assert decision.link_kind is not IdentityLinkKind.SAME_AS
