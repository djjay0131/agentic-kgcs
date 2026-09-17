"""ER stage 3: typed features are honest-null, represent agreement AND
contradiction, and detect mutually-exclusive evidence."""

from datetime import UTC, datetime

from kgcs.er.blocking import CandidatePair
from kgcs.er.features import (
    FEATURE_KEYS,
    DefaultFeatureExtractor,
    PairFeatures,
    jaro_winkler_similarity,
)
from kgcs.er.normalize import (
    FeatureAgreement,
    IdentitySignal,
    NormalizedEntity,
    SharedStrongIdentifierRule,
    run_identity_rules,
)


def _entity(key: str, **kwargs) -> NormalizedEntity:
    return NormalizedEntity(source_key=key, graph_id="g1", entity_type="Paper", **kwargs)


_PAIR = CandidatePair.of("a", "b")


def test_jaro_winkler_bounds_and_prefix_boost() -> None:
    assert jaro_winkler_similarity("martha", "martha") == 1.0
    assert jaro_winkler_similarity("martha", "marhta") > 0.9
    assert jaro_winkler_similarity("abc", "xyz") == 0.0


def test_missing_features_are_none_not_zero() -> None:
    extractor = DefaultFeatureExtractor()
    # Neither entity has names, affiliations, embeddings, neighbours, geo, or
    # temporal data: every optional feature must be None, never 0.0.
    features = extractor.extract(_PAIR, _entity("a"), _entity("b"))
    assert features.name_similarity is None
    assert features.shared_affiliations is None
    assert features.embedding_similarity is None
    assert features.neighborhood_compatibility is None
    assert features.geographic_distance is None
    assert features.temporal_compatible is None
    assert features.identifier_agreement is FeatureAgreement.UNKNOWN


def test_shared_affiliations_measures_zero_when_data_present_but_disjoint() -> None:
    extractor = DefaultFeatureExtractor()
    left = _entity("a", affiliations=("mit",))
    right = _entity("b", affiliations=("stanford",))
    # Both have affiliation data but share none: a *measured* zero, not None.
    assert extractor.extract(_PAIR, left, right).shared_affiliations == 0


def test_identifier_agreement_is_explicit() -> None:
    extractor = DefaultFeatureExtractor()
    agree = (IdentitySignal(rule="r", namespace="doi", agreement=FeatureAgreement.AGREE),)
    contra = (IdentitySignal(rule="r", namespace="doi", agreement=FeatureAgreement.CONTRADICT),)
    a = extractor.extract(_PAIR, _entity("a"), _entity("b"), signals=agree)
    c = extractor.extract(_PAIR, _entity("a"), _entity("b"), signals=contra)
    assert a.identifier_agreement is FeatureAgreement.AGREE
    assert c.identifier_agreement is FeatureAgreement.CONTRADICT


def test_mutually_exclusive_on_contradicting_identifiers_despite_same_name() -> None:
    extractor = DefaultFeatureExtractor()
    left = _entity("a", normalized_names=("john smith",), identifiers={"orcid": ("0001",)})
    right = _entity("b", normalized_names=("john smith",), identifiers={"orcid": ("0002",)})
    signals = run_identity_rules([SharedStrongIdentifierRule(strong_namespaces=frozenset({"orcid"}))], left, right)
    features = extractor.extract(_PAIR, left, right, signals=signals)
    assert features.name_similarity == 1.0  # identical names
    assert features.identifier_agreement is FeatureAgreement.CONTRADICT
    assert features.mutually_exclusive is True


def test_mutually_exclusive_on_disjoint_time_ranges() -> None:
    extractor = DefaultFeatureExtractor()
    left = _entity(
        "a",
        valid_from=datetime(2000, 1, 1, tzinfo=UTC),
        valid_to=datetime(2005, 1, 1, tzinfo=UTC),
    )
    right = _entity(
        "b",
        valid_from=datetime(2010, 1, 1, tzinfo=UTC),
        valid_to=datetime(2015, 1, 1, tzinfo=UTC),
    )
    features = extractor.extract(_PAIR, left, right)
    assert features.temporal_compatible is False
    assert features.mutually_exclusive is True


def test_overlapping_time_ranges_are_compatible() -> None:
    extractor = DefaultFeatureExtractor()
    left = _entity("a", valid_from=datetime(2000, 1, 1, tzinfo=UTC))
    right = _entity("b", valid_to=datetime(2010, 1, 1, tzinfo=UTC))
    features = extractor.extract(_PAIR, left, right)
    assert features.temporal_compatible is True
    assert features.mutually_exclusive is False


def test_embedding_similarity_computed_when_both_present() -> None:
    extractor = DefaultFeatureExtractor()
    left = _entity("a", embedding=(1.0, 0.0))
    right = _entity("b", embedding=(1.0, 0.0))
    assert extractor.extract(_PAIR, left, right).embedding_similarity == 1.0


def test_to_vector_fixed_order_and_honest_null() -> None:
    vector = PairFeatures().to_vector()
    assert list(vector) == list(FEATURE_KEYS)
    # UNKNOWN agreement and every absent optional stay None (never 0.0).
    assert vector["identifier_agreement"] is None
    assert vector["name_similarity"] is None
    assert vector["temporal_compatible"] is None
    # mutually_exclusive default is a measured False → 0.0, not None.
    assert vector["mutually_exclusive"] == 0.0


def test_to_vector_maps_agreement_and_bools() -> None:
    vector = PairFeatures(
        identifier_agreement=FeatureAgreement.CONTRADICT,
        temporal_compatible=True,
        shared_affiliations=3,
        mutually_exclusive=True,
    ).to_vector()
    assert vector["identifier_agreement"] == -1.0
    assert vector["temporal_compatible"] == 1.0
    assert vector["shared_affiliations"] == 3.0
    assert vector["mutually_exclusive"] == 1.0


def test_mixed_tz_valid_periods_do_not_crash() -> None:
    """A naive datetime (e.g. from a producer's open `properties`) compared
    against an aware one must not raise: disjointness is simply unprovable, so
    the periods are treated as possibly-overlapping (honest null over crash)."""
    left = _entity("a", valid_to=datetime(2020, 1, 1, tzinfo=UTC))  # aware
    right = _entity("b", valid_from=datetime(2021, 1, 1))  # naive
    features = DefaultFeatureExtractor().extract(_PAIR, left, right)
    # no TypeError; unprovable disjointness => compatible (True), not a crash
    assert features.temporal_compatible is True


def test_provably_disjoint_periods_are_mutually_exclusive() -> None:
    left = _entity("a", valid_to=datetime(2020, 1, 1, tzinfo=UTC))
    right = _entity("b", valid_from=datetime(2021, 1, 1, tzinfo=UTC))
    features = DefaultFeatureExtractor().extract(_PAIR, left, right)
    assert features.temporal_compatible is False
    assert features.mutually_exclusive is True
