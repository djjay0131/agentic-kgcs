"""Named comparison arms + the no-anecdotal-promotion gate (Wave 7).

Build plan Wave 7 exit criteria: "an LLM-enhanced path can be compared against
the deterministic baseline on named metrics", "insufficient evaluation data
yields an honest null, not a promotion claim", and "no threshold is raised based
only on anecdotal success."

A `ComparisonArm` names a scoring configuration; `run_arms` scores the *same*
labeled `GoldenSet` through each enabled arm and returns per-arm
`CalibrationMetrics` for a like-for-like comparison. `MULTI_AGENT_DEBATE` is
experimental and is **off unless explicitly enabled** — it is excluded from
`DEFAULT_ENABLED_ARMS` and never runs as a production tier (`production_arms`
asserts this).

`should_raise_threshold` is the promotion gate. It returns an
`insufficient-evidence` verdict — never a raise — unless the candidate arm was
evaluated on enough samples *and* shows a real, non-anecdotal improvement over
the baseline (better precision without a worse false-merge rate). Any `None`
metric (honest null from thin data) blocks promotion.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from kgcs.er.matcher import CalibrationMetrics, GoldenSet, Matcher, evaluate


class ComparisonArm(StrEnum):
    """The named evaluation arms (build plan Wave 7).

    `MULTI_AGENT_DEBATE` is experimental — deliberately last, excluded from the
    production defaults, and gated behind explicit enablement.
    """

    DETERMINISTIC_BASELINE = "DETERMINISTIC_BASELINE"
    CALIBRATED_MATCHER = "CALIBRATED_MATCHER"
    MATCHER_PLUS_LLM_ADVISER = "MATCHER_PLUS_LLM_ADVISER"
    MULTI_AGENT_DEBATE = "MULTI_AGENT_DEBATE"


#: The arms enabled by default — the three production-eligible tiers. The
#: experimental `MULTI_AGENT_DEBATE` is intentionally absent, so it runs only
#: when a caller explicitly opts it in.
DEFAULT_ENABLED_ARMS: frozenset[ComparisonArm] = frozenset(
    {
        ComparisonArm.DETERMINISTIC_BASELINE,
        ComparisonArm.CALIBRATED_MATCHER,
        ComparisonArm.MATCHER_PLUS_LLM_ADVISER,
    }
)


def is_experimental(arm: ComparisonArm) -> bool:
    """True for an arm that must never be a production tier (`MULTI_AGENT_DEBATE`)."""
    return arm is ComparisonArm.MULTI_AGENT_DEBATE


def production_arms() -> frozenset[ComparisonArm]:
    """The production-eligible arms — every arm that is not experimental."""
    return frozenset(arm for arm in ComparisonArm if not is_experimental(arm))


@dataclass(frozen=True)
class ArmSpec:
    """One arm's scoring configuration: which arm, which matcher, which cut.

    A `Matcher` produces the calibrated probability the arm is scored on; the
    `threshold` is the evaluation cut applied to that probability (a property of
    the measurement, not a global merge rule — see `er.matcher.evaluate`).
    """

    arm: ComparisonArm
    matcher: Matcher
    threshold: float = 0.5


class ArmResult(BaseModel):
    """Per-arm metrics on the shared eval set — comparable across arms."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    arm: ComparisonArm
    metrics: CalibrationMetrics


class ThresholdVerdict(BaseModel):
    """The promotion gate's answer — honest about insufficient evidence.

    `raise_threshold` is `True` only when `sufficient_evidence` holds *and* the
    candidate genuinely improved; `reason` always explains the call. A thin or
    unmeasurable candidate yields `sufficient_evidence=False`,
    `raise_threshold=False` — the "insufficient evidence" verdict the exit
    criteria require, never a promotion on anecdote.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    raise_threshold: bool
    sufficient_evidence: bool
    reason: str


def run_arms(
    golden: GoldenSet,
    specs: Sequence[ArmSpec],
    *,
    enabled: frozenset[ComparisonArm] = DEFAULT_ENABLED_ARMS,
) -> tuple[ArmResult, ...]:
    """Score `golden` through each *enabled* arm; return per-arm metrics.

    An arm runs only when it is in `enabled` (so `MULTI_AGENT_DEBATE` runs only
    when a caller explicitly enables it). Every arm is scored on the identical
    golden set, so the resulting `CalibrationMetrics` are directly comparable. An
    empty golden set yields all-`None` metrics per arm (honest null), never a
    fabricated perfect score.
    """
    return tuple(
        ArmResult(arm=spec.arm, metrics=evaluate(spec.matcher, golden, threshold=spec.threshold))
        for spec in specs
        if spec.arm in enabled
    )


def should_raise_threshold(
    baseline: CalibrationMetrics,
    candidate: CalibrationMetrics,
    *,
    min_samples: int = 30,
    min_precision_gain: float = 0.01,
) -> ThresholdVerdict:
    """Decide whether `candidate` earns a raised threshold over `baseline`.

    Refuses to promote on anecdote (build plan Wave 7): returns
    `insufficient_evidence` unless *both* arms were measured on at least
    `min_samples` labeled pairs and every metric it depends on is present (a
    `None` — honest null from thin data — blocks promotion). Requiring the
    baseline to clear `min_samples` too means a noisy thin baseline can't make a
    candidate look better than it is. Only then does it raise, and only when
    precision improved by at least `min_precision_gain` *without* the false-merge
    rate getting worse (§7.4 weights false merges most heavily). Otherwise it
    declines with the reason.
    """
    thin = min(candidate.count, baseline.count)
    if thin < min_samples:
        arm = "candidate" if candidate.count < baseline.count else "baseline"
        return ThresholdVerdict(
            raise_threshold=False,
            sufficient_evidence=False,
            reason=(
                f"insufficient evidence: {arm} has {thin} samples "
                f"below the {min_samples} required to raise a threshold"
            ),
        )
    if (
        candidate.precision is None
        or baseline.precision is None
        or candidate.false_merge_rate is None
        or baseline.false_merge_rate is None
    ):
        return ThresholdVerdict(
            raise_threshold=False,
            sufficient_evidence=False,
            reason="insufficient evidence: a required metric is null (not measured)",
        )

    precision_gain = candidate.precision - baseline.precision
    false_merge_worse = candidate.false_merge_rate > baseline.false_merge_rate
    if precision_gain >= min_precision_gain and not false_merge_worse:
        return ThresholdVerdict(
            raise_threshold=True,
            sufficient_evidence=True,
            reason=(
                f"precision improved by {precision_gain:.4f} "
                f"(>= {min_precision_gain:.4f}) with no worse false-merge rate"
            ),
        )
    return ThresholdVerdict(
        raise_threshold=False,
        sufficient_evidence=True,
        reason=(
            f"sufficient data but no promotable improvement: precision gain "
            f"{precision_gain:.4f}, false_merge_worse={false_merge_worse}"
        ),
    )
