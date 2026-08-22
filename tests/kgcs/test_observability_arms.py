"""Named comparison arms + the no-anecdotal-promotion gate (Wave 7).

Proves the Wave-7 exit criteria: the deterministic baseline and a
matcher(+adviser) arm can be scored on the *same* named metrics over the same
labeled set and compared; the experimental `MULTI_AGENT_DEBATE` arm is OFF
unless explicitly enabled; and `should_raise_threshold` returns "insufficient
evidence" on thin data — no threshold is ever raised on anecdotal success.
"""

from kgcs.er.blocking import CandidatePair
from kgcs.er.features import PairFeatures
from kgcs.er.matcher import (
    CalibrationEntry,
    CalibrationKey,
    CalibrationMetrics,
    CalibratedMatcher,
    CalibrationModel,
    DeterministicRuleMatcher,
    GoldenSet,
    LabeledPair,
)
from kgcs.er.normalize import FeatureAgreement
from kgcs.observability import (
    DEFAULT_ENABLED_ARMS,
    ArmSpec,
    ComparisonArm,
    is_experimental,
    production_arms,
    run_arms,
    should_raise_threshold,
)


def _key() -> CalibrationKey:
    return CalibrationKey.of(
        graph_id="g1",
        entity_type="Paper",
        source_pair=("s_a", "s_b"),
        matcher_version="rules/1",
        consequence_class="standard",
    )


def _labeled(left: str, right: str, *, agree: bool, label: bool) -> LabeledPair:
    features = PairFeatures(
        identifier_agreement=FeatureAgreement.AGREE if agree else FeatureAgreement.CONTRADICT,
        name_similarity=1.0 if agree else 0.0,
    )
    return LabeledPair(pair=CandidatePair.of(left, right), features=features, label=label, key=_key())


def _golden(n: int = 4) -> GoldenSet:
    pairs = []
    for i in range(n):
        match = i % 2 == 0
        pairs.append(_labeled(f"m{i}a", f"m{i}b", agree=match, label=match))
    return GoldenSet(pairs=tuple(pairs))


def _calibrated_matcher() -> CalibratedMatcher:
    weights = {"identifier_agreement": 6.0, "name_similarity": 3.0}
    model = CalibrationModel(
        version="calib/1",
        default_weights=weights,
        default_intercept=-2.5,
        entries=(CalibrationEntry(key=_key(), weights=weights, intercept=-2.5),),
    )
    return CalibratedMatcher(model)


class TestNamedArms:
    def test_baseline_and_calibrated_arms_scored_on_same_metrics(self) -> None:
        specs = [
            ArmSpec(arm=ComparisonArm.DETERMINISTIC_BASELINE, matcher=DeterministicRuleMatcher()),
            ArmSpec(arm=ComparisonArm.CALIBRATED_MATCHER, matcher=_calibrated_matcher()),
        ]
        results = run_arms(_golden(), specs)
        arms = {r.arm for r in results}
        assert arms == {ComparisonArm.DETERMINISTIC_BASELINE, ComparisonArm.CALIBRATED_MATCHER}
        # every arm carries the same named metric shape, comparable directly
        for result in results:
            assert isinstance(result.metrics, CalibrationMetrics)
            assert result.metrics.count == 4

    def test_matcher_plus_llm_arm_is_enabled_by_default(self) -> None:
        specs = [ArmSpec(arm=ComparisonArm.MATCHER_PLUS_LLM_ADVISER, matcher=DeterministicRuleMatcher())]
        results = run_arms(_golden(), specs)
        assert [r.arm for r in results] == [ComparisonArm.MATCHER_PLUS_LLM_ADVISER]

    def test_empty_golden_set_yields_honest_null_per_arm(self) -> None:
        specs = [ArmSpec(arm=ComparisonArm.DETERMINISTIC_BASELINE, matcher=DeterministicRuleMatcher())]
        results = run_arms(GoldenSet(), specs)
        assert results[0].metrics.count == 0
        assert results[0].metrics.precision is None  # not a fabricated 1.0


class TestMultiAgentDebateOff:
    def test_debate_is_off_unless_explicitly_enabled(self) -> None:
        specs = [ArmSpec(arm=ComparisonArm.MULTI_AGENT_DEBATE, matcher=DeterministicRuleMatcher())]
        # default enabled set excludes it → it does not run
        assert run_arms(_golden(), specs) == ()

    def test_debate_runs_only_when_explicitly_enabled(self) -> None:
        specs = [ArmSpec(arm=ComparisonArm.MULTI_AGENT_DEBATE, matcher=DeterministicRuleMatcher())]
        results = run_arms(_golden(), specs, enabled=frozenset({ComparisonArm.MULTI_AGENT_DEBATE}))
        assert [r.arm for r in results] == [ComparisonArm.MULTI_AGENT_DEBATE]

    def test_debate_is_never_a_production_arm(self) -> None:
        assert is_experimental(ComparisonArm.MULTI_AGENT_DEBATE) is True
        assert ComparisonArm.MULTI_AGENT_DEBATE not in DEFAULT_ENABLED_ARMS
        assert ComparisonArm.MULTI_AGENT_DEBATE not in production_arms()
        assert ComparisonArm.DETERMINISTIC_BASELINE in production_arms()


class TestNoAnecdotalPromotion:
    def _metrics(self, *, count: int, precision: float, false_merge: float) -> CalibrationMetrics:
        return CalibrationMetrics(
            count=count,
            threshold=0.5,
            precision=precision,
            recall=0.9,
            false_merge_rate=false_merge,
            false_split_rate=0.1,
            calibration_error=0.1,
        )

    def test_thin_data_is_insufficient_evidence_not_a_raise(self) -> None:
        baseline = self._metrics(count=5, precision=0.80, false_merge=0.05)
        candidate = self._metrics(count=5, precision=0.99, false_merge=0.0)  # looks great, tiny sample
        verdict = should_raise_threshold(baseline, candidate, min_samples=30)
        assert verdict.raise_threshold is False
        assert verdict.sufficient_evidence is False
        assert "insufficient evidence" in verdict.reason

    def test_null_metric_blocks_promotion(self) -> None:
        baseline = self._metrics(count=50, precision=0.80, false_merge=0.05)
        candidate = CalibrationMetrics(
            count=50, threshold=0.5, precision=None, recall=None,
            false_merge_rate=None, false_split_rate=None, calibration_error=None,
        )
        verdict = should_raise_threshold(baseline, candidate, min_samples=30)
        assert verdict.raise_threshold is False
        assert verdict.sufficient_evidence is False

    def test_sufficient_and_improved_permits_a_raise(self) -> None:
        baseline = self._metrics(count=100, precision=0.80, false_merge=0.05)
        candidate = self._metrics(count=100, precision=0.90, false_merge=0.03)
        verdict = should_raise_threshold(baseline, candidate, min_samples=30)
        assert verdict.raise_threshold is True
        assert verdict.sufficient_evidence is True

    def test_sufficient_but_worse_false_merge_declines(self) -> None:
        baseline = self._metrics(count=100, precision=0.80, false_merge=0.02)
        candidate = self._metrics(count=100, precision=0.95, false_merge=0.10)  # precision up, but merges worse
        verdict = should_raise_threshold(baseline, candidate, min_samples=30)
        assert verdict.raise_threshold is False
        assert verdict.sufficient_evidence is True  # we had the data, just declined

    def test_thin_baseline_also_blocks_promotion(self) -> None:
        # A noisy thin baseline must not let a well-sampled candidate look better
        # than it is — BOTH arms must clear min_samples.
        baseline = self._metrics(count=5, precision=0.50, false_merge=0.20)
        candidate = self._metrics(count=100, precision=0.95, false_merge=0.01)
        verdict = should_raise_threshold(baseline, candidate, min_samples=30)
        assert verdict.raise_threshold is False
        assert verdict.sufficient_evidence is False
        assert "baseline" in verdict.reason
