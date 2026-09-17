"""Reusable contract suite for the ER `Matcher` port (spec §7.4, ADR-0007).

Subclass `MatcherContract`, implement `make_matcher()`, and the implementation
is held to the laws every calibrated matcher must obey — regardless of whether
it is the deterministic rule baseline or a learned `CalibrationModel`:

- scoring is deterministic and its probability lives in [0, 1];
- a `MatchResult` reproduces byte-for-byte across a JSON round trip, carrying
  the feature vector and matcher version that produced it;
- **no embedding threshold decides a match**: a pair with maximal embedding
  similarity but contradicting strong identifiers scores *below* 0.5, because
  the contradiction outweighs the embedding. This is the §7.4 correction to the
  superseded cosine-band funnel, asserted as a port law.
"""

from kgcs.er.blocking import CandidatePair
from kgcs.er.features import PairFeatures
from kgcs.er.matcher import CalibrationKey, Matcher
from kgcs.er.normalize import FeatureAgreement


def _sample_pair() -> CandidatePair:
    return CandidatePair.of("entity/a", "entity/b")


def _sample_key(matcher_version: str) -> CalibrationKey:
    return CalibrationKey.of(
        graph_id="g1",
        entity_type="Paper",
        source_pair=("source_a", "source_b"),
        matcher_version=matcher_version,
        consequence_class="standard",
    )


class MatcherContract:
    """Subclass and implement `make_matcher()`."""

    def make_matcher(self) -> Matcher:
        """Return a matcher whose weights respect the ER port laws."""
        raise NotImplementedError

    def test_probability_in_unit_interval(self) -> None:
        matcher = self.make_matcher()
        result = matcher.score(
            PairFeatures(name_similarity=0.9, identifier_agreement=FeatureAgreement.AGREE),
            pair=_sample_pair(),
            key=_sample_key(matcher.matcher_version),
        )
        assert 0.0 <= result.probability <= 1.0

    def test_scoring_is_deterministic(self) -> None:
        matcher = self.make_matcher()
        features = PairFeatures(name_similarity=0.8, identifier_agreement=FeatureAgreement.AGREE)
        first = matcher.score(features, pair=_sample_pair(), key=_sample_key(matcher.matcher_version))
        second = matcher.score(
            features, pair=_sample_pair(), key=_sample_key(matcher.matcher_version)
        )
        assert first.probability == second.probability
        assert first == second

    def test_result_survives_json_round_trip(self) -> None:
        matcher = self.make_matcher()
        result = matcher.score(
            PairFeatures(name_similarity=0.7, identifier_agreement=FeatureAgreement.AGREE),
            pair=_sample_pair(),
            key=_sample_key(matcher.matcher_version),
        )
        reloaded = type(result).model_validate_json(result.model_dump_json())
        assert reloaded == result
        assert reloaded.probability == result.probability
        assert reloaded.feature_vector == result.feature_vector

    def test_matcher_version_is_stamped(self) -> None:
        matcher = self.make_matcher()
        result = matcher.score(
            PairFeatures(), pair=_sample_pair(), key=_sample_key(matcher.matcher_version)
        )
        assert result.matcher_version == matcher.matcher_version

    def test_contradiction_beats_high_embedding(self) -> None:
        """Strong-id agreement scores high; contradiction beats a max embedding.

        Two halves, so a degenerate matcher that always returns a constant
        cannot satisfy the law: a pair with agreeing strong identifiers scores
        *above* 0.5, while a pair with maximal embedding similarity but
        contradicting strong ids scores *below* 0.5. No fixed cosine/embedding
        threshold decides a match — the identifier evidence is what moves it.
        """
        matcher = self.make_matcher()
        key = _sample_key(matcher.matcher_version)

        agreeing = PairFeatures(
            name_similarity=1.0,
            embedding_similarity=1.0,
            identifier_agreement=FeatureAgreement.AGREE,
        )
        agree_result = matcher.score(agreeing, pair=_sample_pair(), key=key)
        assert agree_result.probability > 0.5

        contradicting = PairFeatures(
            name_similarity=1.0,
            embedding_similarity=1.0,
            identifier_agreement=FeatureAgreement.CONTRADICT,
            mutually_exclusive=True,
        )
        contradict_result = matcher.score(contradicting, pair=_sample_pair(), key=key)
        assert contradict_result.probability < 0.5
        # and the two are ordered — the contradiction genuinely lowers the score
        assert contradict_result.probability < agree_result.probability
