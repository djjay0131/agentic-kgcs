"""Stage 6 of entity resolution: the deterministic policy gate (spec §7.4 step 6).

The matcher returns a *calibrated probability* and cluster validation returns a
*verdict*; neither decides what to do. This gate does — deterministically, and
**without any LLM**. That is §9 law 1 and a Wave-3 exit criterion: removing the
LLM leaves a complete baseline. `LLM_ASSESS` is merely one action the gate may
*select* (routing the pair to a later adviser), never a step the gate needs to
reach a decision.

`AdjudicationRoute` (`AUTO | LLM_ASSESS | HUMAN`) is too coarse for ER, so the
gate emits a richer `ErAction`. It routes on **calibrated error risk × the
false-merge consequence class × cluster validity × the source profile** — not a
fixed similarity band. The false-merge risk of an auto-link is `1 - probability`;
a higher `FalseMergeCostClass` shrinks the risk budget, so the *same* probability
routes more conservatively at `HIGH` than at `STANDARD`. There is deliberately no
`if prob > 0.9: link` anywhere here.

Three hard gates sit above the risk routing and can only make it *more*
conservative — never less:

- cluster validation failed ⇒ never `AUTO_LINK` (retain separate, or review);
- `IdentityAuthorityMode.CLIENT_AUTHORITATIVE`/`ADVISORY` ⇒ never `AUTO_LINK`;
  the most ER may do is `PROPOSE_LINK` (a `POSSIBLY_SAME_AS` proposal) — this is
  where Issue #2 / §9 law 13 is enforced in code;
- `ErMode.INERT` ⇒ retain separate; a stale cluster snapshot ⇒ abstain.

An `ErDecision` logs the driving `MatchResult` (probability + matcher version +
feature vector), the cluster-validity summary, the profile id/version, and a
rationale — so the decision is reproducible and auditable, and survives a
`model_dump_json` round trip byte-for-byte.
"""

from enum import StrEnum

from kg_contracts.identity import IdentityLinkKind
from kg_contracts.policy import AdjudicationRoute
from pydantic import BaseModel, ConfigDict, Field

from kgcs.er.blocking import CandidatePair
from kgcs.er.cluster import ClusterValidation
from kgcs.er.matcher import MatchResult
from kgcs.profiles import (
    CurationProfile,
    ErMode,
    FalseMergeCostClass,
    IdentityAuthorityMode,
)


class ErAction(StrEnum):
    """The deterministic ER outcome — finer-grained than `AdjudicationRoute`.

    `LLM_ASSESS` and `HUMAN_REVIEW` are *routes* the gate may choose, not steps
    it requires: the gate always returns one of these without ever calling an
    LLM.
    """

    AUTO_LINK = "AUTO_LINK"
    RETAIN_SEPARATE = "RETAIN_SEPARATE"
    GATHER_MORE_EVIDENCE = "GATHER_MORE_EVIDENCE"
    PROPOSE_LINK = "PROPOSE_LINK"
    LLM_ASSESS = "LLM_ASSESS"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    ABSTAIN = "ABSTAIN"
    REJECT = "REJECT"


class ErRoutingThresholds(BaseModel):
    """Risk budgets for the gate — *data*, not code (mirrors `ConfidencePolicy`).

    Budgets are on the **false-merge risk** of an auto-link (`1 - probability`),
    keyed by `FalseMergeCostClass`. A pair auto-links only when its risk is within
    the (small) `auto_link_budget` for its cost class; a wider `assess_budget`
    admits `LLM_ASSESS`; a risk at or above `separate_min_false_merge_risk` (i.e.
    the matcher is confident they differ) retains them separate; anything left in
    the uncertain middle gathers more evidence. Because the budget shrinks with
    the cost class, the same probability routes more conservatively at `HIGH`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = "1"
    auto_link_budget: dict[str, float] = Field(
        default_factory=lambda: {"LOW": 0.10, "STANDARD": 0.02, "HIGH": 0.002}
    )
    assess_budget: dict[str, float] = Field(
        default_factory=lambda: {"LOW": 0.40, "STANDARD": 0.25, "HIGH": 0.10}
    )
    separate_min_false_merge_risk: float = 0.90

    def auto_budget_for(self, cost: FalseMergeCostClass) -> float:
        """The auto-link false-merge-risk budget for `cost` (STANDARD fallback)."""
        return self.auto_link_budget.get(cost.value, self.auto_link_budget["STANDARD"])

    def assess_budget_for(self, cost: FalseMergeCostClass) -> float:
        """The LLM-assess false-merge-risk budget for `cost` (STANDARD fallback)."""
        return self.assess_budget.get(cost.value, self.assess_budget["STANDARD"])


class ErDecision(BaseModel):
    """A reproducible, auditable ER decision for one candidate pair.

    Carries everything needed to replay the decision: the driving `MatchResult`
    (probability, matcher version, feature vector), the cluster-validity summary
    it was gated on, the profile that shaped it, and a human-readable rationale.
    `link_kind` is the contract link this action would assert — `SAME_AS` for an
    auto-link, `POSSIBLY_SAME_AS` for a proposal, `None` when nothing is linked.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    pair: CandidatePair
    action: ErAction
    match_result: MatchResult
    cluster_validation: ClusterValidation | None = None
    profile_id: str
    profile_version: str
    identity_authority_mode: IdentityAuthorityMode
    er_mode: ErMode
    false_merge_cost_class: FalseMergeCostClass
    link_kind: IdentityLinkKind | None = None
    rationale: str
    evidence_ids: tuple[str, ...] = ()

    def to_route(self) -> AdjudicationRoute:
        """Project this ER action onto the coarse contract `AdjudicationRoute`.

        Shows how an `ErDecision` composes with the existing candidate-level
        `kgcs.policy.ResolutionPolicy` without rewriting it: an auto-applicable
        outcome (`AUTO_LINK`, or a deterministic `RETAIN_SEPARATE`) maps to
        `AUTO`; anything needing an adviser or a proposal maps to `LLM_ASSESS`;
        an abstain/reject/human outcome maps to `HUMAN`.
        """
        if self.action in (ErAction.AUTO_LINK, ErAction.RETAIN_SEPARATE):
            return AdjudicationRoute.AUTO
        if self.action in (
            ErAction.LLM_ASSESS,
            ErAction.PROPOSE_LINK,
            ErAction.GATHER_MORE_EVIDENCE,
        ):
            return AdjudicationRoute.LLM_ASSESS
        return AdjudicationRoute.HUMAN


def _link_kind_for(action: ErAction) -> IdentityLinkKind | None:
    """The contract identity link an action asserts (or `None` for no link)."""
    if action is ErAction.AUTO_LINK:
        return IdentityLinkKind.SAME_AS
    if action is ErAction.PROPOSE_LINK:
        return IdentityLinkKind.POSSIBLY_SAME_AS
    return None


class ErResolutionPolicy:
    """The deterministic ER policy gate (spec §7.4 step 6).

    Injected only with risk-budget *data* (`ErRoutingThresholds`); holds no
    mutable state and calls no LLM, so `decide` on the same inputs always yields
    an equal `ErDecision`. It composes with — never replaces — the candidate-level
    `kgcs.policy.ResolutionPolicy`: that stage routes a candidate's identity
    disposition, this stage routes a *pair*'s ER action, and `ErDecision.to_route`
    bridges the two.
    """

    def __init__(self, *, thresholds: ErRoutingThresholds | None = None) -> None:
        self._thresholds = thresholds or ErRoutingThresholds()

    def decide(
        self,
        match_result: MatchResult,
        *,
        profile: CurationProfile,
        cluster_validation: ClusterValidation | None = None,
        snapshot_stale: bool = False,
        evidence_count: int | None = None,
        malformed: bool = False,
    ) -> ErDecision:
        """Choose an `ErAction` for one scored pair. Always returns; never calls an LLM."""
        action, rationale = self._choose(
            match_result,
            profile=profile,
            cluster_validation=cluster_validation,
            snapshot_stale=snapshot_stale,
            evidence_count=evidence_count,
            malformed=malformed,
        )
        return ErDecision(
            pair=match_result.pair,
            action=action,
            match_result=match_result,
            cluster_validation=cluster_validation,
            profile_id=profile.profile_id,
            profile_version=profile.version,
            identity_authority_mode=profile.identity_authority_mode,
            er_mode=profile.er_mode,
            false_merge_cost_class=profile.false_merge_cost_class,
            link_kind=_link_kind_for(action),
            rationale=rationale,
        )

    def _choose(
        self,
        match_result: MatchResult,
        *,
        profile: CurationProfile,
        cluster_validation: ClusterValidation | None,
        snapshot_stale: bool,
        evidence_count: int | None,
        malformed: bool,
    ) -> tuple[ErAction, str]:
        # 1. The reject-only gate rejects a malformed reference outright — it
        #    never repairs it (Issue #2).
        if malformed:
            return ErAction.REJECT, "reference is malformed; rejected without repair"

        # 2. Inert ER does not link at all.
        if profile.er_mode is ErMode.INERT:
            return ErAction.RETAIN_SEPARATE, "er_mode is INERT; entities retained separate"

        # 3. A stale cluster snapshot invalidates the decision rather than racing
        #    it onto a cluster that has moved (spec §7.1).
        if snapshot_stale:
            return (
                ErAction.ABSTAIN,
                "cluster snapshot is stale; abstaining pending re-resolution",
            )

        # 4. Cluster validity gate — a *proven* contradiction (a constraint
        #    violation or a mutually-exclusive pair) means these entities cannot
        #    be the same, regardless of probability or evidence. It can never
        #    authorise a link and it is pointless to defer a proven
        #    contradiction to an adviser or to gathering more of the same
        #    evidence, so it short-circuits to a conservative deterministic
        #    action before the evidence/risk routes run (§9 law 7).
        if cluster_validation is not None and not cluster_validation.valid:
            names = ", ".join(v.constraint for v in cluster_validation.violations)
            if profile.false_merge_cost_class is FalseMergeCostClass.HIGH:
                return (
                    ErAction.HUMAN_REVIEW,
                    f"cluster invalid ({names}) at HIGH cost; escalated to human review",
                )
            return (
                ErAction.RETAIN_SEPARATE,
                f"cluster invalid ({names}); entities retained separate",
            )

        # 5. Not enough evidence to act — gather more before deciding.
        if evidence_count is not None and evidence_count < profile.required_evidence_count:
            return (
                ErAction.GATHER_MORE_EVIDENCE,
                (
                    f"evidence_count={evidence_count} below required "
                    f"{profile.required_evidence_count}"
                ),
            )

        # 6. Base risk route — calibrated error risk × consequence class.
        base, rationale = self._risk_route(match_result, profile.false_merge_cost_class)

        # 7. Authority gate — CLIENT_AUTHORITATIVE / ADVISORY never auto-link;
        #    an auto-link they cannot take degrades to a proposal (§9 law 13).
        if base is ErAction.AUTO_LINK and not self._auto_link_permitted(profile):
            if profile.er_mode in (ErMode.ADVISORY, ErMode.ACTIVE):
                return (
                    ErAction.PROPOSE_LINK,
                    (
                        f"{rationale}; auto-link withheld under "
                        f"{profile.identity_authority_mode.value} — proposing POSSIBLY_SAME_AS"
                    ),
                )
            return (
                ErAction.RETAIN_SEPARATE,
                f"{rationale}; auto-link not permitted for this profile",
            )

        return base, rationale

    def _auto_link_permitted(self, profile: CurationProfile) -> bool:
        """Auto-link needs OPEN authority, ACTIVE ER, and AUTO_LINK allowlisted."""
        return (
            profile.identity_authority_mode is IdentityAuthorityMode.OPEN
            and profile.er_mode is ErMode.ACTIVE
            and ErAction.AUTO_LINK.value in profile.allowable_auto_actions
        )

    def _risk_route(
        self, match_result: MatchResult, cost: FalseMergeCostClass
    ) -> tuple[ErAction, str]:
        """Route on false-merge risk (`1 - p`) against the cost class's budgets."""
        probability = match_result.probability
        false_merge_risk = 1.0 - probability
        auto_budget = self._thresholds.auto_budget_for(cost)
        assess_budget = self._thresholds.assess_budget_for(cost)

        if false_merge_risk <= auto_budget:
            return (
                ErAction.AUTO_LINK,
                (
                    f"false-merge risk {false_merge_risk:.4f} within auto budget "
                    f"{auto_budget:.4f} for {cost.value}"
                ),
            )
        if false_merge_risk <= assess_budget:
            return (
                ErAction.LLM_ASSESS,
                (
                    f"false-merge risk {false_merge_risk:.4f} within assess budget "
                    f"{assess_budget:.4f} for {cost.value}; routing to adviser"
                ),
            )
        if false_merge_risk >= self._thresholds.separate_min_false_merge_risk:
            return (
                ErAction.RETAIN_SEPARATE,
                (
                    f"false-merge risk {false_merge_risk:.4f} at/above "
                    f"{self._thresholds.separate_min_false_merge_risk:.4f}; "
                    "matcher is confident they differ"
                ),
            )
        return (
            ErAction.GATHER_MORE_EVIDENCE,
            (
                f"false-merge risk {false_merge_risk:.4f} is in the uncertain middle "
                f"for {cost.value}; gathering more evidence"
            ),
        )
