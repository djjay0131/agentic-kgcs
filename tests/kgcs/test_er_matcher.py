"""ER stage 4: the calibrated matcher — reproducible probabilities, no cosine
threshold as the decision, and golden-set evaluation with honest-null metrics."""

from kgcs.er.blocking import CandidatePair
from kgcs.er.features import PairFeatures
from kgcs.er.matcher import (
    CalibratedMatcher,
    CalibrationKey,
    CalibrationModel,
    DeterministicRuleMatcher,
    GoldenSet,
    LabeledPair,
    Matcher,
    MatchResult,
    calibrate_logistic,
    evaluate,
    linear_score,
    sigmoid,
)
from kgcs.er.normalize import FeatureAgreement
from kgcs.testing.er import MatcherContract

_KEY = CalibrationKey.of(
    graph_id="g1",
    entity_type="Paper",
    source_pair=("source_b", "source_a"),  # deliberately unordered
    matcher_version="rules/1",
    consequence_class="standard",
)
_PAIR = CandidatePair.of("paper/a", "paper/b")


# Calibration weights that respect the ER port laws (contradiction dominates
# embedding), used for the CalibratedMatcher contract + reproducibility tests.
_CALIBRATED_WEIGHTS = {
    "identifier_agreement": 5.0,
    "mutually_exclusive": -8.0,
    "name_similarity": 3.0,
    "embedding_similarity": 1.0,
}


def _calibrated_model(version: str = "calibrated/test") -> CalibrationModel:
    return CalibrationModel(
        version=version, default_weights=_CALIBRATED_WEIGHTS, default_intercept=-2.0
    )


def test_calibration_key_canonicalizes_source_pair() -> None:
    assert _KEY.source_pair == ("source_a", "source_b")


class TestRuleMatcherContract(MatcherContract):
    def make_matcher(self) -> Matcher:
        return DeterministicRuleMatcher()


class TestCalibratedMatcherContract(MatcherContract):
    def make_matcher(self) -> Matcher:
        return CalibratedMatcher(_calibrated_model())


def test_probability_reproducible_from_stored_vector_and_version() -> None:
    matcher = DeterministicRuleMatcher()
    features = PairFeatures(name_similarity=0.9, identifier_agreement=FeatureAgreement.AGREE)
    result = matcher.score(features, pair=_PAIR, key=_KEY)
    # Recompute the probability from ONLY the stored feature vector + the fixed
    # rule weights of that matcher version — no access to the original features.
    recomputed = sigmoid(linear_score(result.feature_vector, matcher._weights, matcher._intercept))
    assert recomputed == result.probability


def test_result_is_byte_identical_after_json_round_trip() -> None:
    matcher = DeterministicRuleMatcher()
    features = PairFeatures(name_similarity=0.75, identifier_agreement=FeatureAgreement.AGREE)
    result = matcher.score(features, pair=_PAIR, key=_KEY)
    dumped = result.model_dump_json()
    reloaded = MatchResult.model_validate_json(dumped)
    assert reloaded == result
    assert reloaded.model_dump_json() == dumped
    assert reloaded.probability == result.probability


def test_high_embedding_alone_does_not_force_match_when_ids_contradict() -> None:
    matcher = DeterministicRuleMatcher()
    contradicting = PairFeatures(
        name_similarity=1.0,
        embedding_similarity=1.0,
        identifier_agreement=FeatureAgreement.CONTRADICT,
        mutually_exclusive=True,
    )
    agreeing = PairFeatures(
        name_similarity=1.0,
        embedding_similarity=1.0,
        identifier_agreement=FeatureAgreement.AGREE,
    )
    low = matcher.score(contradicting, pair=_PAIR, key=_KEY).probability
    high = matcher.score(agreeing, pair=_PAIR, key=_KEY).probability
    assert low < 0.5 < high  # embedding=1.0 both times; the identifiers decide


def test_calibrated_matcher_version_from_model() -> None:
    matcher = CalibratedMatcher(_calibrated_model(version="calibrated/7"))
    result = matcher.score(PairFeatures(), pair=_PAIR, key=_KEY)
    assert result.matcher_version == "calibrated/7"


# --- golden-set evaluation -------------------------------------------------


def _labeled(features: PairFeatures, label: bool) -> LabeledPair:
    return LabeledPair(pair=_PAIR, features=features, label=label, key=_KEY)


def _golden() -> GoldenSet:
    return GoldenSet(
        pairs=(
            # obvious match: shared identifier + identical name
            _labeled(
                PairFeatures(name_similarity=1.0, identifier_agreement=FeatureAgreement.AGREE),
                True,
            ),
            # alias positive: same entity, different surface name, shared id
            _labeled(
                PairFeatures(name_similarity=0.6, identifier_agreement=FeatureAgreement.AGREE),
                True,
            ),
            # obvious non-match: nothing in common
            _labeled(PairFeatures(name_similarity=0.1), False),
            # hard homonym negative: identical name but contradicting ids
            _labeled(
                PairFeatures(
                    name_similarity=1.0,
                    identifier_agreement=FeatureAgreement.CONTRADICT,
                    mutually_exclusive=True,
                ),
                False,
            ),
        )
    )


def test_golden_set_evaluation_separates_matches_from_homonyms() -> None:
    metrics = evaluate(DeterministicRuleMatcher(), _golden(), threshold=0.5)
    assert metrics.count == 4
    # The hard homonym must NOT be predicted a match — no false merge.
    assert metrics.false_merge_rate == 0.0
    assert metrics.precision == 1.0
    assert metrics.recall == 1.0
    assert metrics.calibration_error is not None


def test_empty_golden_set_returns_honest_null_metrics() -> None:
    metrics = evaluate(DeterministicRuleMatcher(), GoldenSet(), threshold=0.5)
    assert metrics.count == 0
    assert metrics.precision is None
    assert metrics.recall is None
    assert metrics.false_merge_rate is None
    assert metrics.false_split_rate is None
    assert metrics.calibration_error is None


def test_calibrate_logistic_learns_to_separate() -> None:
    model = calibrate_logistic(
        _golden(),
        key=_KEY,
        version="calibrated/fit",
        feature_keys=("name_similarity", "identifier_agreement", "mutually_exclusive"),
    )
    matcher = CalibratedMatcher(model)
    positive = matcher.score(
        PairFeatures(name_similarity=1.0, identifier_agreement=FeatureAgreement.AGREE),
        pair=_PAIR,
        key=_KEY,
    ).probability
    homonym = matcher.score(
        PairFeatures(
            name_similarity=1.0,
            identifier_agreement=FeatureAgreement.CONTRADICT,
            mutually_exclusive=True,
        ),
        pair=_PAIR,
        key=_KEY,
    ).probability
    assert positive > homonym
