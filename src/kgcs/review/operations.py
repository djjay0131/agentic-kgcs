"""Reviewer action → the SAME typed decision/plan path as the auto path (§9 law 14).

The load-bearing rule of the whole review wave: a human decision must converge
on the *same* `CurationPlan` → executor → audit pipeline as an automated
decision. A reviewer never edits canonical state through a side channel; a
reviewer's `ReviewAction` is folded through the very same planners the automated
path uses — `recuration.evolution.ConceptEvolutionPlanner` — so the plan a human
`APPROVE` yields is byte-identical to the plan the auto path would have applied.

`ReviewProposal` is the typed bridge. When the automated path routes a decision
to review instead of applying it, it packages the planner inputs it *would* have
used into a proposal. `ReviewRouter.route(decision, proposal)` then maps each
`ReviewAction` back onto those planners:

- `APPROVE` — re-run the proposed shape's planner with the proposed inputs. Same
  planner, same inputs, deterministic ⇒ byte-identical to the auto plan. (If the
  proposal carries no shape, its pre-built `proposed_plan` passes through, or —
  for a deterministic retain/no-op proposal — no plan is produced.)
- `REJECT` — no plan; the decision is recorded (history), nothing is applied.
- `EDIT` — the contract already requires `edited_payload`; the recognized edits
  (e.g. a narrowed member set) override the proposed inputs, then the proposed
  shape's planner runs → an *adjusted* plan through the same pipeline.
- `SPLIT` / `RELABEL` / `LINK` / `MERGE_ELSEWHERE` / `SAME_CONCEPT_DIFFERENT_SCOPE`
  — the reviewer overrides the proposed shape; each maps to the corresponding
  `ConceptEvolutionPlanner` method, producing the matching compensable plan.

A missing typed input surfaces as a `ReviewOperationError` (never a silent
no-op) — the review path obeys the same "no silent drop" discipline as the queue
(§9 law 15).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum

from kg_contracts.assertions import Assertion
from kg_contracts.candidates import EntityCandidate
from kg_contracts.curation import CurationPlan, ReviewAction, ReviewDecision

from kgcs.er.normalize import NormalizedEntity
from kgcs.recuration.evolution import (
    AssertionReassignment,
    ConceptEvolutionPlanner,
    EvolutionKind,
    EvolutionResult,
)
from kgcs.recuration.triggers import CurationTrigger


class ReviewOperationError(Exception):
    """A reviewer action lacked the typed input needed to build its plan.

    Raised — never swallowed — so a review action that cannot be realized as a
    plan is surfaced, not silently dropped (§9 law 15).
    """


class ReviewOutcomeStatus(StrEnum):
    """Whether a routed decision produced a plan or was recorded without one."""

    PLANNED = "PLANNED"
    RECORDED_NO_PLAN = "RECORDED_NO_PLAN"


# Reviewer override actions → the concept-evolution shape they route through.
_ACTION_TO_KIND: dict[ReviewAction, EvolutionKind] = {
    ReviewAction.SPLIT: EvolutionKind.SPLIT,
    ReviewAction.RELABEL: EvolutionKind.RELABEL,
    ReviewAction.LINK: EvolutionKind.MERGE,
    ReviewAction.MERGE_ELSEWHERE: EvolutionKind.MERGE,
    ReviewAction.SAME_CONCEPT_DIFFERENT_SCOPE: EvolutionKind.RELABEL,
}


@dataclass(frozen=True)
class ReviewProposal:
    """The typed inputs the auto path *would* have fed a planner (§9 law 14).

    Carried alongside a `ReviewItem` so a reviewer's decision can be folded
    through the same planners. `proposed_kind` is the shape the automated path
    proposed; the shape-specific fields are honest-null and only the ones a
    shape needs must be present. `proposed_plan` is the concrete plan the auto
    path built (an `APPROVE` with no `proposed_kind` passes it through).
    """

    trigger: CurationTrigger
    proposed_kind: EvolutionKind | None = None
    proposed_plan: CurationPlan | None = None
    # merge / link inputs
    merge_members: tuple[str, ...] = ()
    merge_entities: Mapping[str, NormalizedEntity] | None = None
    # split inputs
    split_source: str | None = None
    split_into: tuple[str, ...] = ()
    reassignments: tuple[AssertionReassignment, ...] = field(default_factory=tuple)
    # relabel / scope inputs
    label_assertion: Assertion | None = None
    # promotion inputs
    promotion_candidate: EntityCandidate | None = None


@dataclass(frozen=True)
class ReviewOutcome:
    """The result of folding one `ReviewDecision` back through the pipeline.

    `plan` is the compensable `CurationPlan` (present iff `status` is `PLANNED`) —
    the *same* artifact the executor/audit path consumes for an automated
    decision. `evolution` carries the full `EvolutionResult` when the action
    routed through the concept-evolution planner (e.g. a `SUPERSESSION`'s
    preserved old assertions). `decision` is echoed so a caller can record it.
    """

    decision: ReviewDecision
    action: ReviewAction
    status: ReviewOutcomeStatus
    kind: EvolutionKind | None
    plan: CurationPlan | None
    evolution: EvolutionResult | None
    rationale: str


class ReviewRouter:
    """Maps a reviewer's `ReviewAction` onto the automated planners (§9 law 14).

    Injected with the *same* `ConceptEvolutionPlanner` the automated re-curation
    path uses; nothing here mutates state or edits the graph — it only produces
    the plan the executor would apply, exactly as the auto path does.
    """

    def __init__(self, *, planner: ConceptEvolutionPlanner) -> None:
        self._planner = planner

    def route(self, decision: ReviewDecision, proposal: ReviewProposal) -> ReviewOutcome:
        """Fold `decision` through the pipeline against `proposal`'s inputs."""
        action = decision.action

        if action is ReviewAction.REJECT:
            return self._recorded(
                decision, "rejected by reviewer; no plan applied, decision recorded"
            )

        if action is ReviewAction.APPROVE:
            return self._approve(decision, proposal)

        if action is ReviewAction.EDIT:
            return self._edit(decision, proposal)

        # SPLIT / RELABEL / LINK / MERGE_ELSEWHERE / SAME_CONCEPT_DIFFERENT_SCOPE
        kind = _ACTION_TO_KIND.get(action)
        if kind is None:  # pragma: no cover — every ReviewAction is mapped above
            raise ReviewOperationError(f"unroutable review action: {action}")
        evolution = self._plan_for_kind(kind, proposal, decision)
        return self._planned(decision, kind, evolution, f"reviewer {action.value} routed to {kind.value}")

    # -- action handlers -----------------------------------------------------

    def _approve(self, decision: ReviewDecision, proposal: ReviewProposal) -> ReviewOutcome:
        """Accept the proposal as-is, converging on the auto path's plan."""
        if proposal.proposed_kind is not None:
            evolution = self._plan_for_kind(proposal.proposed_kind, proposal, decision)
            return self._planned(
                decision,
                proposal.proposed_kind,
                evolution,
                "approved: re-ran the proposed plan through the same planner",
            )
        if proposal.proposed_plan is not None:
            return self._planned(
                decision,
                None,
                None,
                "approved: applying the pre-built proposed plan",
                plan=proposal.proposed_plan,
            )
        return self._recorded(
            decision, "approved: proposal carries no plan (deterministic retain/no-op)"
        )

    def _edit(self, decision: ReviewDecision, proposal: ReviewProposal) -> ReviewOutcome:
        """Apply the recognized edits, then re-run the proposed shape's planner.

        The contract's `ReviewDecision` validator already guarantees an `EDIT`
        carries an `edited_payload`; here that payload's recognized fields (e.g.
        `members`, `into`) override the proposed inputs so the resulting plan is
        an *adjusted* version reached through the same pipeline.
        """
        kind = proposal.proposed_kind
        if kind is None:
            raise ReviewOperationError("cannot EDIT a proposal without a proposed_kind")
        edited = self._apply_edits(proposal, decision.edited_payload or {})
        evolution = self._plan_for_kind(kind, edited, decision)
        return self._planned(
            decision,
            kind,
            evolution,
            "edited: adjusted inputs re-run through the proposed planner",
        )

    # -- planner dispatch ----------------------------------------------------

    def _plan_for_kind(
        self, kind: EvolutionKind, proposal: ReviewProposal, decision: ReviewDecision
    ) -> EvolutionResult:
        """Invoke the concept-evolution planner for `kind` with `proposal`'s inputs."""
        if kind is EvolutionKind.MERGE:
            members = self._members_for(proposal, decision)
            return self._planner.plan_merge(
                members=members, trigger=proposal.trigger, entities=proposal.merge_entities
            )
        if kind is EvolutionKind.SPLIT:
            if proposal.split_source is None or not proposal.split_into:
                raise ReviewOperationError("SPLIT requires split_source and split_into")
            return self._planner.plan_split(
                source_identity=proposal.split_source,
                into=proposal.split_into,
                reassignments=proposal.reassignments,
                trigger=proposal.trigger,
            )
        if kind is EvolutionKind.RELABEL:
            if proposal.label_assertion is None:
                raise ReviewOperationError("RELABEL requires a label_assertion")
            return self._planner.plan_relabel(
                label_assertion=proposal.label_assertion, trigger=proposal.trigger
            )
        if kind is EvolutionKind.PROMOTION:
            if proposal.promotion_candidate is None:
                raise ReviewOperationError("PROMOTION requires a promotion_candidate")
            return self._planner.plan_promotion(
                candidate=proposal.promotion_candidate, trigger=proposal.trigger
            )
        raise ReviewOperationError(f"no review routing for evolution kind {kind}")

    def _members_for(self, proposal: ReviewProposal, decision: ReviewDecision) -> tuple[str, ...]:
        """Merge members: an `edited_payload["members"]` override, else the proposal's.

        `MERGE_ELSEWHERE` and `EDIT` name a different target member set via the
        decision's edited payload; other merge actions use the proposed set.
        """
        override = _str_tuple(decision.edited_payload, "members")
        members = override if override is not None else proposal.merge_members
        if len(members) < 2:
            raise ReviewOperationError("MERGE requires at least two members")
        return members

    def _apply_edits(
        self, proposal: ReviewProposal, edited_payload: Mapping[str, object]
    ) -> ReviewProposal:
        """Return a proposal with recognized simple overrides from `edited_payload`."""
        members = _str_tuple(edited_payload, "members")
        into = _str_tuple(edited_payload, "into")
        if members is None and into is None:
            return proposal
        return replace(
            proposal,
            merge_members=members if members is not None else proposal.merge_members,
            split_into=into if into is not None else proposal.split_into,
        )

    # -- outcome constructors ------------------------------------------------

    def _planned(
        self,
        decision: ReviewDecision,
        kind: EvolutionKind | None,
        evolution: EvolutionResult | None,
        rationale: str,
        *,
        plan: CurationPlan | None = None,
    ) -> ReviewOutcome:
        resolved_plan = plan if plan is not None else (evolution.plan if evolution else None)
        status = (
            ReviewOutcomeStatus.PLANNED
            if resolved_plan is not None
            else ReviewOutcomeStatus.RECORDED_NO_PLAN
        )
        return ReviewOutcome(
            decision=decision,
            action=decision.action,
            status=status,
            kind=kind,
            plan=resolved_plan,
            evolution=evolution,
            rationale=rationale,
        )

    def _recorded(self, decision: ReviewDecision, rationale: str) -> ReviewOutcome:
        return ReviewOutcome(
            decision=decision,
            action=decision.action,
            status=ReviewOutcomeStatus.RECORDED_NO_PLAN,
            kind=None,
            plan=None,
            evolution=None,
            rationale=rationale,
        )


def _str_tuple(payload: Mapping[str, object] | None, key: str) -> tuple[str, ...] | None:
    """Read `payload[key]` as a tuple of strings, or `None` if absent/ill-typed."""
    if not payload:
        return None
    value = payload.get(key)
    if not isinstance(value, (list, tuple)):
        return None
    return tuple(item for item in value if isinstance(item, str))
