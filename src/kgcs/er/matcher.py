"""Stage 4 of entity resolution: the calibrated matcher (ADR-0007).

Spec §7.4, step 4. The matcher turns a pair's typed features into a *calibrated
match probability* — and nothing more. It does not decide to merge: routing on
that probability by consequence class is the deterministic policy gate (step 6,
a later wave). Keeping the matcher to "features → probability" is what lets a
decision be reproduced from its logged inputs.

The v1 embedding-threshold funnel is superseded, and this module is where that
correction bites: **no fixed global cosine threshold decides a match.** Embedding
similarity is one weighted feature among many; a pair with sky-high embedding
similarity but contradicting strong identifiers (a `mutually_exclusive` pair)
scores *low*, because the contradiction weight overwhelms the embedding weight.
The reference `DeterministicRuleMatcher` makes this explicit and testable.

**Reproducibility.** A `MatchResult` carries the `feature_vector` it scored and
the `matcher_version`; the same vector under the same version always yields the
same probability, and a result survives a `model_dump_json()` round trip
byte-for-byte. **Calibration** is keyed by `CalibrationKey` (graph, entity type,
source pair, matcher version, consequence class) — the exact five axes §7.4
requires — so a probability learned for one context is never silently reused for
another. Calibration can be *evaluated* against a labeled `GoldenSet`, and an
empty golden set yields explicit null metrics, never a fabricated perfect score.
"""

import math
from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from kgcs.er.blocking import CandidatePair
from kgcs.er.features import PairFeatures


class CalibrationKey(BaseModel):
    """The five axes calibration is keyed by (spec §7.4 "calibration discipline").

    Thresholds and weights are maintained per graph, entity type, source pair,
    matcher version, and consequence class — never one global setting. The
    `source_pair` is canonically ordered so the pair `(a, b)` keys the same as
    `(b, a)`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    graph_id: str
    entity_type: str
    source_pair: tuple[str, str]
    matcher_version: str
    consequence_class: str

    @classmethod
    def of(
        cls,
        *,
        graph_id: str,
        entity_type: str,
        source_pair: tuple[str, str],
        matcher_version: str,
        consequence_class: str,
    ) -> "CalibrationKey":
        """Build a key with the source pair canonically ordered."""
        a, b = source_pair
        ordered = (a, b) if a <= b else (b, a)
        return cls(
            graph_id=graph_id,
            entity_type=entity_type,
            source_pair=ordered,
            matcher_version=matcher_version,
            consequence_class=consequence_class,
        )


class MatchResult(BaseModel):
    """A calibrated match probability plus everything needed to reproduce it.

    `feature_vector` is the exact `PairFeatures.to_vector()` that was scored and
    `matcher_version` the model that scored it, so recomputing from the stored
    vector — even after a JSON round trip — yields the identical probability
    (spec §7.4: "every decision logs the full score vector and model versions").
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    pair: CandidatePair
    probability: float = Field(ge=0.0, le=1.0)
    matcher_version: str
    feature_vector: dict[str, float | None]
    calibration_key: CalibrationKey


def sigmoid(z: float) -> float:
    """Numerically stable logistic squashing to (0, 1)."""
    if z >= 0.0:
        return 1.0 / (1.0 + math.exp(-z))
    exp_z = math.exp(z)
    return exp_z / (1.0 + exp_z)


def linear_score(
    vector: Mapping[str, float | None], weights: Mapping[str, float], intercept: float
) -> float:
    """Weighted sum over the *present* features only.

    A `None` feature (honest null) contributes nothing — it is neither a fake
    zero nor an error. Weights are read in sorted key order so the sum is
    associative-stable and byte-reproducible.
    """
    total = intercept
    for key in sorted(weights):
        value = vector.get(key)
        if value is not None:
            total += weights[key] * value
    return total


@runtime_checkable
class Matcher(Protocol):
    """Scores a pair's features into a reproducible `MatchResult`."""

    matcher_version: str

    def score(
        self, features: PairFeatures, *, pair: CandidatePair, key: CalibrationKey
    ) -> MatchResult: ...


# ---------------------------------------------------------------------------
# Deterministic rule baseline
# ---------------------------------------------------------------------------

# Fixed, explainable weights for the reference matcher. Strong-identifier
# agreement and mutual exclusion dominate; embedding similarity is deliberately
# a *small* weight — one feature among many, never the decision.
_RULE_WEIGHTS: dict[str, float] = {
    "identifier_agreement": 6.0,       # AGREE (+1) → strong pull up; CONTRADICT (-1) → strong pull down
    "mutually_exclusive": -8.0,        # positive evidence against a match dominates everything
    "name_similarity": 3.0,
    "shared_affiliations": 0.5,
    "attribute_rarity": 2.0,
    "neighborhood_compatibility": 1.5,
    "embedding_similarity": 1.0,       # one feature among many — never sufficient alone
    "temporal_compatible": 1.0,
    "source_reliability": 0.5,
}
_RULE_INTERCEPT = -2.5


class DeterministicRuleMatcher:
    """Explainable rule-weighted logistic baseline (`matcher_version="rules/1"`).

    A fixed linear combination of the feature vector squashed by a logistic —
    the reference every learned matcher is compared against. Strong-identifier
    `AGREE` pushes the probability high and `CONTRADICT` low; name similarity
    contributes; embedding similarity carries only a small weight, so a high
    embedding similarity *cannot on its own* force a match — and a
    `mutually_exclusive` pair is driven low regardless of every other feature.
    """

    matcher_version = "rules/1"

    def __init__(
        self,
        *,
        weights: Mapping[str, float] = _RULE_WEIGHTS,
        intercept: float = _RULE_INTERCEPT,
    ) -> None:
        self._weights = dict(weights)
        self._intercept = intercept

    def score(
        self, features: PairFeatures, *, pair: CandidatePair, key: CalibrationKey
    ) -> MatchResult:
        vector = features.to_vector()
        probability = sigmoid(linear_score(vector, self._weights, self._intercept))
        return MatchResult(
            pair=pair,
            probability=probability,
            matcher_version=self.matcher_version,
            feature_vector=vector,
            calibration_key=key,
        )


# ---------------------------------------------------------------------------
# Calibratable logistic model
# ---------------------------------------------------------------------------


class CalibrationEntry(BaseModel):
    """Per-`CalibrationKey` logistic weights + intercept."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: CalibrationKey
    weights: dict[str, float]
    intercept: float = 0.0


class CalibrationModel(BaseModel):
    """A frozen, calibratable logistic model, keyed by `CalibrationKey`.

    Pure Python — a weights dict and an intercept, per key, with a default for
    keys not yet calibrated. Deterministic and serializable: the model *is* the
    calibration, versioned by `version` (which becomes the matcher version), so
    a stored probability can always be traced to the model that produced it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    default_weights: dict[str, float] = Field(default_factory=dict)
    default_intercept: float = 0.0
    entries: tuple[CalibrationEntry, ...] = ()

    def resolve(self, key: CalibrationKey) -> tuple[dict[str, float], float]:
        """The (weights, intercept) calibrated for `key`, or the defaults."""
        for entry in self.entries:
            if entry.key == key:
                return entry.weights, entry.intercept
        return self.default_weights, self.default_intercept


class CalibratedMatcher:
    """A `Matcher` backed by a `CalibrationModel` (version = model version).

    Reproducible by construction: it scores `PairFeatures.to_vector()` with the
    weights the model resolves for the calibration key, so the same stored
    vector under the same model version always yields the same probability.
    """

    def __init__(self, model: CalibrationModel) -> None:
        self._model = model
        self.matcher_version = model.version

    def score(
        self, features: PairFeatures, *, pair: CandidatePair, key: CalibrationKey
    ) -> MatchResult:
        weights, intercept = self._model.resolve(key)
        vector = features.to_vector()
        probability = sigmoid(linear_score(vector, weights, intercept))
        return MatchResult(
            pair=pair,
            probability=probability,
            matcher_version=self.matcher_version,
            feature_vector=vector,
            calibration_key=key,
        )


# ---------------------------------------------------------------------------
# Golden-set evaluation
# ---------------------------------------------------------------------------


class LabeledPair(BaseModel):
    """A ground-truth-labeled pair for calibration evaluation.

    `label` is `True` when the two are genuinely the same entity. `features` is
    the pre-extracted `PairFeatures` (so evaluation needs no normalized
    entities), and `key` carries the calibration context the matcher scores in.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    pair: CandidatePair
    features: PairFeatures
    label: bool
    key: CalibrationKey


class GoldenSet(BaseModel):
    """A labeled evaluation set (obvious matches/non-matches, hard negatives,
    aliases, homonyms — spec §7.4 calibration discipline)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pairs: tuple[LabeledPair, ...] = ()


class CalibrationMetrics(BaseModel):
    """Evaluation metrics for a matcher against a `GoldenSet`.

    Every rate is `None` when its denominator is empty — an honest null, never a
    fabricated `1.0`. An empty golden set therefore returns all-`None` with
    `count=0`: "not measured", not "perfect". `false_merge_rate` (predicting
    same when actually different) is reported alongside `false_split_rate`
    because §7.4 weights a false merge more heavily — a merge contaminates every
    attached fact.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    count: int
    threshold: float
    precision: float | None
    recall: float | None
    false_merge_rate: float | None
    false_split_rate: float | None
    calibration_error: float | None


def evaluate(matcher: Matcher, golden: GoldenSet, *, threshold: float) -> CalibrationMetrics:
    """Evaluate `matcher` over `golden` at `threshold` (on the probability).

    `threshold` is an evaluation cut on the *calibrated probability*, not a
    cosine band — it is a property of this measurement, not a global merge rule
    baked into the matcher. An empty golden set yields all-`None` metrics.
    """
    pairs = golden.pairs
    if not pairs:
        return CalibrationMetrics(
            count=0,
            threshold=threshold,
            precision=None,
            recall=None,
            false_merge_rate=None,
            false_split_rate=None,
            calibration_error=None,
        )

    true_positive = false_positive = true_negative = false_negative = 0
    abs_error_total = 0.0
    for labeled in pairs:
        result = matcher.score(labeled.features, pair=labeled.pair, key=labeled.key)
        predicted_same = result.probability >= threshold
        abs_error_total += abs(result.probability - (1.0 if labeled.label else 0.0))
        if labeled.label and predicted_same:
            true_positive += 1
        elif labeled.label and not predicted_same:
            false_negative += 1
        elif not labeled.label and predicted_same:
            false_positive += 1
        else:
            true_negative += 1

    actual_positives = true_positive + false_negative
    actual_negatives = false_positive + true_negative
    predicted_positives = true_positive + false_positive

    return CalibrationMetrics(
        count=len(pairs),
        threshold=threshold,
        precision=_ratio(true_positive, predicted_positives),
        recall=_ratio(true_positive, actual_positives),
        false_merge_rate=_ratio(false_positive, actual_negatives),
        false_split_rate=_ratio(false_negative, actual_positives),
        calibration_error=abs_error_total / len(pairs),
    )


def _ratio(numerator: int, denominator: int) -> float | None:
    """`numerator / denominator`, or `None` when the denominator is zero
    (honest null — the rate is undefined, not zero)."""
    return numerator / denominator if denominator else None


def calibrate_logistic(
    golden: GoldenSet,
    *,
    key: CalibrationKey,
    version: str,
    feature_keys: Sequence[str],
    learning_rate: float = 0.5,
    epochs: int = 400,
    l2: float = 0.0,
) -> CalibrationModel:
    """Fit a `CalibrationModel` for `key` by pure-Python logistic regression.

    Batch gradient descent over the golden set's feature vectors (present
    features only; honest nulls are skipped, contributing no gradient). Small,
    dependency-free, and deterministic (fixed init at zero, fixed iteration
    order) — enough to demonstrate calibration end to end without a heavy ML
    dependency in the core package.
    """
    weights: dict[str, float] = {k: 0.0 for k in feature_keys}
    intercept = 0.0
    samples = [
        (lp.features.to_vector(), 1.0 if lp.label else 0.0)
        for lp in golden.pairs
    ]
    n = len(samples)
    for _ in range(epochs):
        if n == 0:
            break
        grad_w: dict[str, float] = {k: 0.0 for k in feature_keys}
        grad_b = 0.0
        for vector, target in samples:
            prediction = sigmoid(linear_score(vector, weights, intercept))
            error = prediction - target
            grad_b += error
            for k in feature_keys:
                value = vector.get(k)
                if value is not None:
                    grad_w[k] += error * value
        intercept -= learning_rate * grad_b / n
        for k in feature_keys:
            weights[k] -= learning_rate * (grad_w[k] / n + l2 * weights[k])
    return CalibrationModel(
        version=version,
        default_weights=weights,
        default_intercept=intercept,
        entries=(CalibrationEntry(key=key, weights=weights, intercept=intercept),),
    )
