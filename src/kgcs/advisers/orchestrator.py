"""The `CurationOrchestrator` — deterministic baseline first, advice folded in.

This is where the wave's architectural rule is made operational (build plan
Wave 4, §9 laws 1/6/16). The orchestrator:

1. **runs the deterministic Wave-3 baseline first** (`ErResolutionPolicy`) and
   captures the resulting `ErDecision` — this is the complete answer on its own;
2. **consults advisers only when the question is unresolved** — i.e. the
   baseline chose the `LLM_ASSESS` *route*. Any other baseline action (an
   auto-link, a retain-separate, a reject, an abstain, a proposal) is already
   decided; no adviser is called and the baseline is returned verbatim;
3. **selects specialists deterministically** by the routed question (the ER
   identity path selects the `IdentityAdviser` when one is configured);
4. **aggregates the structured assessments** conservatively;
5. **folds the advice back through the deterministic policy's own gates** to
   produce the final `ErDecision`; and
6. **records DG-4 provenance** (`baseline_action_before`, `final_action_after`)
   on every assessment.

The two invariants the fold guarantees:

- **The baseline stands on any LLM failure (§9 law 1).** A failing, timing-out,
  or malformed adviser yields an `abstained` assessment (never an exception), and
  the fold returns the *exact* baseline `ErDecision` — byte-identical to running
  `ErResolutionPolicy` alone. Removing the adviser entirely does the same.
- **Advice never overrides the authority gate.** A `same` recommendation is
  clamped by the very rules Wave 3 enforces: it can reach `AUTO_LINK` only for a
  profile Wave 3 would let auto-link (OPEN authority, ACTIVE ER, `AUTO_LINK`
  allowlisted); under `CLIENT_AUTHORITATIVE` the most it can do is
  `PROPOSE_LINK`. Insufficient/abstaining advice routes conservatively and never
  toward a link (§9 law 13, Issue #2).

The orchestrator never executes a mutation and never returns an operation or a
plan — its output is an `OrchestrationResult` carrying a decision and the
assessments (§9 law 16).
"""

from kg_contracts.identity import IdentityLinkKind
from pydantic import BaseModel, ConfigDict

from kgcs.advisers.base import AdviserAssessment, AdviserQuestion
from kgcs.advisers.specialists import IdentityAdviser, IdentityRecommendation
from kgcs.er.cluster import ClusterValidation
from kgcs.er.matcher import MatchResult
from kgcs.er.resolution import ErAction, ErDecision, ErResolutionPolicy
from kgcs.profiles import (
    CurationProfile,
    ErMode,
    IdentityAuthorityMode,
)


class OrchestrationResult(BaseModel):
    """The orchestrator's output: a decision, its baseline, and the assessments.

    `decision` is the final `ErDecision`; `baseline` is the deterministic Wave-3
    decision *before* any adviser input (equal to `decision` whenever no usable
    advice was folded in). `assessments` carry full DG-4 provenance. There is no
    field capable of holding a `CurationOperation`/`GraphMutationBatch`/
    `CurationPlan` — the orchestrator advises and decides, it never writes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: ErDecision
    baseline: ErDecision
    assessments: tuple[AdviserAssessment, ...] = ()
    consulted: bool = False


def _link_kind_for(action: ErAction) -> IdentityLinkKind | None:
    """The contract identity link an action asserts (mirrors `resolution`)."""
    if action is ErAction.AUTO_LINK:
        return IdentityLinkKind.SAME_AS
    if action is ErAction.PROPOSE_LINK:
        return IdentityLinkKind.POSSIBLY_SAME_AS
    return None


def _auto_link_permitted(profile: CurationProfile) -> bool:
    """Auto-link needs OPEN authority, ACTIVE ER, and AUTO_LINK allowlisted.

    Deliberately the same predicate `ErResolutionPolicy` applies, re-derived from
    the profile data so the fold clamps advice by exactly the Wave-3 rule without
    reaching into the policy's internals.
    """
    return (
        profile.identity_authority_mode is IdentityAuthorityMode.OPEN
        and profile.er_mode is ErMode.ACTIVE
        and ErAction.AUTO_LINK.value in profile.allowable_auto_actions
    )


class CurationOrchestrator:
    """Runs the deterministic baseline, then folds bounded advice into it.

    Injected with an `ErResolutionPolicy` (the Wave-3 gate) and, optionally, an
    `IdentityAdviser`. With no adviser — or with a failing one — `resolve`
    returns exactly the baseline decision, so the deterministic path is always
    complete on its own. Holds no `GraphMutationStore` and executes nothing.
    """

    def __init__(
        self,
        *,
        policy: ErResolutionPolicy | None = None,
        identity_adviser: IdentityAdviser | None = None,
    ) -> None:
        self._policy = policy or ErResolutionPolicy()
        self._identity_adviser = identity_adviser

    def resolve(
        self,
        match_result: MatchResult,
        *,
        profile: CurationProfile,
        cluster_validation: ClusterValidation | None = None,
        snapshot_stale: bool = False,
        evidence_count: int | None = None,
        malformed: bool = False,
        evidence_ids: tuple[str, ...] = (),
        trace_id: str = "",
    ) -> OrchestrationResult:
        """Decide one pair: baseline first, advice only if the baseline defers.

        `cluster_validation`/`snapshot_stale`/`evidence_count`/`malformed` are
        passed straight through to the Wave-3 policy. `evidence_ids`/`trace_id`
        thread evidence and trace provenance into the adviser question.
        """
        baseline = self._policy.decide(
            match_result,
            profile=profile,
            cluster_validation=cluster_validation,
            snapshot_stale=snapshot_stale,
            evidence_count=evidence_count,
            malformed=malformed,
        )

        advisers = self._select(baseline)
        if not advisers:
            return OrchestrationResult(decision=baseline, baseline=baseline, consulted=False)

        question = _identity_question(match_result, evidence_ids=evidence_ids, trace_id=trace_id)
        raw = tuple(adviser.assess(question) for adviser in advisers)
        final = self._fold(baseline, raw, profile=profile)
        assessments = tuple(
            assessment.model_copy(
                update={
                    "baseline_action_before": baseline.action.value,
                    "final_action_after": final.action.value,
                }
            )
            for assessment in raw
        )
        return OrchestrationResult(
            decision=final, baseline=baseline, assessments=assessments, consulted=True
        )

    def _select(self, baseline: ErDecision) -> tuple[IdentityAdviser, ...]:
        """Deterministically pick advisers: identity adviser iff routed LLM_ASSESS."""
        if baseline.action is ErAction.LLM_ASSESS and self._identity_adviser is not None:
            return (self._identity_adviser,)
        return ()

    def _fold(
        self,
        baseline: ErDecision,
        assessments: tuple[AdviserAssessment, ...],
        *,
        profile: CurationProfile,
    ) -> ErDecision:
        """Fold advice into the baseline, clamped by the deterministic gates.

        Returns the *exact* baseline when there is no usable (non-abstained)
        advice — the guarantee that a failed/malformed/absent adviser leaves the
        Wave-3 decision untouched. Otherwise maps the aggregated recommendation
        to a final action that is never less conservative than the authority gate
        allows.
        """
        usable = tuple(a for a in assessments if not a.abstained)
        if baseline.action is not ErAction.LLM_ASSESS or not usable:
            return baseline

        recommendation = _aggregate_identity(usable)
        cited = tuple(sorted({eid for a in usable for eid in a.evidence_ids}))

        if recommendation is IdentityRecommendation.DIFFERENT:
            action = ErAction.RETAIN_SEPARATE
            note = "adviser: different → retain separate"
        elif recommendation is IdentityRecommendation.SAME:
            action, note = self._same_action(profile)
        else:  # insufficient_evidence
            action = ErAction.GATHER_MORE_EVIDENCE
            note = "adviser: insufficient evidence → gather more"

        return baseline.model_copy(
            update={
                "action": action,
                "link_kind": _link_kind_for(action),
                "rationale": f"{baseline.rationale}; {note}",
                "evidence_ids": cited,
            }
        )

    def _same_action(self, profile: CurationProfile) -> tuple[ErAction, str]:
        """Map a `same` recommendation to an action, clamped by the authority gate."""
        if _auto_link_permitted(profile):
            return ErAction.AUTO_LINK, "adviser: same → auto-link (profile permits)"
        if profile.er_mode in (ErMode.ADVISORY, ErMode.ACTIVE):
            return (
                ErAction.PROPOSE_LINK,
                (
                    "adviser: same → propose POSSIBLY_SAME_AS "
                    f"(auto-link withheld under {profile.identity_authority_mode.value})"
                ),
            )
        return ErAction.RETAIN_SEPARATE, "adviser: same → retain separate (auto-link not permitted)"


def _identity_question(
    match_result: MatchResult, *, evidence_ids: tuple[str, ...], trace_id: str
) -> AdviserQuestion:
    """Build the deterministic identity question for a scored pair.

    Derived purely from the pair's endpoint keys and the supplied evidence/trace,
    so a test can reproduce the exact request (and thus the recorded fixture key)
    without any hidden state.
    """
    pair = match_result.pair
    return AdviserQuestion(
        kind="identity",
        subject=pair.left,
        other=pair.right,
        evidence_ids=evidence_ids,
        trace_id=trace_id,
    )


def _aggregate_identity(usable: tuple[AdviserAssessment, ...]) -> IdentityRecommendation:
    """Conservatively aggregate identity recommendations.

    Order of caution: any `different` dominates (never merge if a specialist says
    they differ), then any `insufficient_evidence`, and only unanimous `same`
    yields `same`. With a single adviser this is just its recommendation.
    """
    recommendations = {a.recommendation for a in usable}
    if IdentityRecommendation.DIFFERENT.value in recommendations:
        return IdentityRecommendation.DIFFERENT
    if IdentityRecommendation.INSUFFICIENT_EVIDENCE.value in recommendations:
        return IdentityRecommendation.INSUFFICIENT_EVIDENCE
    if recommendations == {IdentityRecommendation.SAME.value}:
        return IdentityRecommendation.SAME
    return IdentityRecommendation.INSUFFICIENT_EVIDENCE
