"""ER stage 5: cluster validation — pairwise similarity cannot commit an invalid
cluster (§9 law 7), each constraint blocks a bad membership, survivor selection
is deterministic, and a stale snapshot invalidates rather than races."""

from datetime import UTC, datetime

from kgcs.er.blocking import CandidatePair
from kgcs.er.cluster import (
    Cluster,
    ClusterSnapshot,
    ClusterValidator,
    IdentityAuthorityConstraint,
    MutuallyExclusiveAttributeConstraint,
    TemporalConsistencyConstraint,
    TenantBoundaryConstraint,
    UniqueSourceConstraint,
    default_cluster_validator,
    select_survivor,
)
from kgcs.er.matcher import CalibrationKey, MatchResult
from kgcs.er.normalize import NormalizedEntity

_KEY = CalibrationKey.of(
    graph_id="g1",
    entity_type="Paper",
    source_pair=("source_a", "source_b"),
    matcher_version="rules/1",
    consequence_class="standard",
)


def _ne(
    source_key: str,
    *,
    graph_id: str = "g1",
    entity_type: str = "Paper",
    identifiers: dict[str, tuple[str, ...]] | None = None,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
    source_reliability: float | None = None,
) -> NormalizedEntity:
    return NormalizedEntity(
        source_key=source_key,
        graph_id=graph_id,
        entity_type=entity_type,
        identifiers=identifiers or {},
        valid_from=valid_from,
        valid_to=valid_to,
        source_reliability=source_reliability,
    )


def _match(a: str, b: str, *, probability: float, mutually_exclusive: bool = False) -> MatchResult:
    pair = CandidatePair.of(a, b)
    return MatchResult(
        pair=pair,
        probability=probability,
        matcher_version="rules/1",
        feature_vector={"mutually_exclusive": 1.0 if mutually_exclusive else 0.0},
        calibration_key=_KEY,
    )


def _cluster(*members: str, version: int = 0) -> Cluster:
    return Cluster(
        cluster_id="c1", version=version, graph_id="g1", entity_type="Paper", members=members
    )


# --- §9 law 7: invalid transitive closure ---------------------------------


class TestTransitiveClosure:
    def test_pairwise_matches_do_not_commit_an_invalid_cluster(self) -> None:
        """A~B and B~C, but A⊥C: the closure {A,B,C} must be rejected."""
        validator = ClusterValidator()  # pairwise-relation check alone suffices here
        entities = {k: _ne(k) for k in ("A", "B", "C")}
        results = [
            _match("A", "B", probability=0.98),
            _match("B", "C", probability=0.98),
            _match("A", "C", probability=0.01, mutually_exclusive=True),  # A ⊥ C
        ]
        verdict = validator.validate(
            _cluster("A", "B", "C"), entities=entities, match_results=results
        )
        assert verdict.valid is False
        assert any(v.constraint == "pairwise_mutual_exclusion" for v in verdict.violations)
        assert verdict.checked_pairs == 3

    def test_the_two_consistent_subclusters_are_each_valid(self) -> None:
        validator = ClusterValidator()
        entities = {k: _ne(k) for k in ("A", "B", "C")}
        ab = _match("A", "B", probability=0.98)
        bc = _match("B", "C", probability=0.98)
        assert validator.validate(_cluster("A", "B"), entities=entities, match_results=[ab]).valid
        assert validator.validate(_cluster("B", "C"), entities=entities, match_results=[bc]).valid


# --- each constraint individually blocks an invalid membership -------------


class TestConstraintsIndividually:
    def test_temporal_consistency_blocks_disjoint_periods(self) -> None:
        entities = {
            "A": _ne("A", valid_to=datetime(2000, 1, 1, tzinfo=UTC)),
            "B": _ne("B", valid_from=datetime(2010, 1, 1, tzinfo=UTC)),
        }
        result = TemporalConsistencyConstraint().check(_cluster("A", "B"), entities)
        assert result.ok is False
        assert set(result.offending) == {"A", "B"}

    def test_unique_source_blocks_two_members_from_one_source(self) -> None:
        entities = {"src/1": _ne("src/1"), "src/2": _ne("src/2")}
        result = UniqueSourceConstraint().check(_cluster("src/1", "src/2"), entities)
        assert result.ok is False

    def test_unique_source_allows_allowlisted_source(self) -> None:
        entities = {"src/1": _ne("src/1"), "src/2": _ne("src/2")}
        result = UniqueSourceConstraint(allow_multiple=frozenset({"src"})).check(
            _cluster("src/1", "src/2"), entities
        )
        assert result.ok is True

    def test_mutually_exclusive_attribute_blocks_contradicting_strong_ids(self) -> None:
        entities = {
            "A": _ne("A", identifiers={"doi": ("10.1/a",)}),
            "B": _ne("B", identifiers={"doi": ("10.1/b",)}),  # different DOI
        }
        result = MutuallyExclusiveAttributeConstraint().check(_cluster("A", "B"), entities)
        assert result.ok is False
        assert set(result.offending) == {"A", "B"}

    def test_tenant_boundary_blocks_cross_graph_member(self) -> None:
        entities = {"A": _ne("A"), "B": _ne("B", graph_id="g2")}
        result = TenantBoundaryConstraint().check(_cluster("A", "B"), entities)
        assert result.ok is False
        assert result.offending == ("B",)

    def test_identity_authority_blocks_silent_merge_of_client_authoritative(self) -> None:
        entities = {"A": _ne("A"), "B": _ne("B")}
        constraint = IdentityAuthorityConstraint(
            is_client_authoritative=lambda e: e.source_key == "B"
        )
        result = constraint.check(_cluster("A", "B"), entities)
        assert result.ok is False
        assert result.offending == ("B",)

    def test_identity_authority_permits_singleton(self) -> None:
        """A client-authoritative entity on its own is not a merge — allowed."""
        entities = {"B": _ne("B")}
        constraint = IdentityAuthorityConstraint(is_client_authoritative=lambda e: True)
        assert constraint.check(_cluster("B"), entities).ok is True


class TestDefaultValidator:
    def test_clean_membership_is_valid(self) -> None:
        validator = default_cluster_validator()
        entities = {"a/1": _ne("a/1"), "b/1": _ne("b/1")}
        ab = _match("a/1", "b/1", probability=0.97)
        verdict = validator.validate(_cluster("a/1", "b/1"), entities=entities, match_results=[ab])
        assert verdict.valid is True
        assert "tenant_boundary" in verdict.checked_constraints

    def test_validate_addition_builds_the_prospective_membership(self) -> None:
        validator = default_cluster_validator()
        entities = {"a/1": _ne("a/1"), "b/1": _ne("b/1", graph_id="g2")}
        verdict = validator.validate_addition(_cluster("a/1"), "b/1", entities=entities)
        assert verdict.valid is False  # b/1 crosses the tenant boundary


# --- survivor selection ----------------------------------------------------


class TestSurvivorSelection:
    def test_highest_reliability_wins(self) -> None:
        entities = {
            "a": _ne("a", source_reliability=0.5),
            "b": _ne("b", source_reliability=0.9),
        }
        assert select_survivor(["a", "b"], entities=entities) == "b"

    def test_tie_breaks_on_more_identifiers_then_lexicographic(self) -> None:
        entities = {
            "a": _ne("a", source_reliability=0.9, identifiers={"doi": ("1", "2")}),
            "b": _ne("b", source_reliability=0.9, identifiers={"doi": ("1",)}),
        }
        assert select_survivor(["b", "a"], entities=entities) == "a"  # a has more identifiers

    def test_full_tie_falls_back_to_smallest_key(self) -> None:
        entities = {"a": _ne("a", source_reliability=0.9), "b": _ne("b", source_reliability=0.9)}
        assert select_survivor(["b", "a"], entities=entities) == "a"

    def test_is_deterministic_regardless_of_input_order(self) -> None:
        entities = {
            "a": _ne("a", source_reliability=0.7),
            "b": _ne("b", source_reliability=0.9),
            "c": _ne("c", source_reliability=0.9),
        }
        assert (
            select_survivor(["a", "b", "c"], entities=entities)
            == select_survivor(["c", "b", "a"], entities=entities)
            == "b"
        )

    def test_without_entities_uses_the_smallest_key(self) -> None:
        assert select_survivor(["c", "a", "b"]) == "a"


# --- optimistic concurrency: stale snapshot --------------------------------


class TestClusterSnapshot:
    def test_snapshot_matches_its_cluster(self) -> None:
        cluster = _cluster("a", "b", version=17)
        assert cluster.snapshot().is_stale(cluster) is False

    def test_version_mismatch_is_stale(self) -> None:
        snapshot = ClusterSnapshot(cluster_id="c1", version=17)
        moved = _cluster("a", "b", "x", version=18)
        assert snapshot.is_stale(moved) is True

    def test_different_cluster_id_is_stale(self) -> None:
        snapshot = ClusterSnapshot(cluster_id="c1", version=17)
        other = Cluster(cluster_id="c2", version=17, graph_id="g1", entity_type="Paper")
        assert snapshot.is_stale(other) is True


class TestPairwiseCompleteness:
    def test_incomplete_pairwise_coverage_is_visible(self) -> None:
        """Coverage is reported, not silently trusted: a 3-member cluster has 3
        internal pairs, so supplying only 2 MatchResults marks it incomplete."""
        validator = ClusterValidator()
        cluster = _cluster("a", "b", "c")
        entities = {k: _ne(k) for k in ("a", "b", "c")}
        result = validator.validate(
            cluster,
            entities=entities,
            match_results=[
                _match("a", "b", probability=0.9),
                _match("b", "c", probability=0.9),
            ],  # a-c omitted
        )
        assert result.expected_pairs == 3
        assert result.checked_pairs == 2
        assert result.pairwise_complete is False

    def test_full_pairwise_coverage_is_complete(self) -> None:
        validator = ClusterValidator()
        cluster = _cluster("a", "b")
        entities = {k: _ne(k) for k in ("a", "b")}
        result = validator.validate(
            cluster, entities=entities, match_results=[_match("a", "b", probability=0.9)]
        )
        assert result.expected_pairs == 1
        assert result.checked_pairs == 1
        assert result.pairwise_complete is True
