"""The auto-path router: a bounded adviser recommendation → an evolution plan.

The human review path has `kgcs.review.ReviewRouter`, which turns a reviewer's
`ReviewAction` into the same `CurationPlan` the automated path would produce
(law 14). `EvolutionRouter` is its **automated** counterpart: it turns an
`AssertionAdviser` *recommendation* into an `EvolutionResult`, so that on the
auto re-curation path the adviser's advice **causally selects** the evolution
action — through a deterministic gate, not by the adviser choosing a plan
itself.

The bounding discipline is the same as everywhere else: the adviser's
recommendation is *evidence*, and this deterministic router decides what may
happen. It is **conservative and never destroys evidence**:

- `SUPPORTS` → corroboration (attach the new assertion; the old stays `ACTIVE`).
- `SUPERSEDES` → supersession, but **only when the profile permits it**
  (`supersession_allowed`); otherwise the two assertions are preserved as an
  unresolved conflict routed to review.
- `CONTRADICTS`, `INSUFFICIENT`, and any `SUPERSEDES` the router is not
  permitted to act on → preserve BOTH assertions as an `UNRESOLVED`
  `ConflictRecord` and route to review/gather-more-evidence (§9 law 10).

So a wrong or over-eager adviser can, at worst, cause a conflict to be
preserved and reviewed — it can never supersede without permission, and it can
never delete a competing assertion. The router holds no store and executes
nothing; it only produces a plan (`ConceptEvolutionPlanner` output).
"""

from dataclasses import dataclass

from kg_contracts.assertions import Assertion

from kgcs.advisers.specialists import AssertionRecommendation
from kgcs.recuration.evolution import ConceptEvolutionPlanner, EvolutionResult
from kgcs.recuration.triggers import CurationTrigger


@dataclass(frozen=True)
class EvolutionRouter:
    """Maps a bounded `AssertionAdviser` recommendation to an `EvolutionResult`.

    Injected with the same `ConceptEvolutionPlanner` the deterministic
    re-curation path uses, so a human decision (`ReviewRouter`) and an
    adviser-driven auto decision converge on the identical plan machinery.
    Pure and stateless.
    """

    planner: ConceptEvolutionPlanner

    def route_assertion(
        self,
        *,
        recommendation: AssertionRecommendation,
        old_assertion: Assertion,
        new_assertion: Assertion,
        trigger: CurationTrigger,
        supersession_allowed: bool = True,
    ) -> EvolutionResult:
        """Turn an `AssertionAdviser` recommendation into an evolution plan.

        `supersession_allowed` is the deterministic policy gate (e.g. derived
        from the source's `CurationProfile` / authority): if the profile does
        not permit superseding, a `SUPERSEDES` recommendation is downgraded to a
        preserved conflict routed to review rather than acted on.
        """
        # Compare by value (StrEnum), so a recommendation stored as its string
        # form on an AdviserAssessment routes the same as the enum member.
        if recommendation == AssertionRecommendation.SUPPORTS:
            return self.planner.plan_corroboration(
                corroborating_assertion=new_assertion,
                corroborated_assertion_id=old_assertion.assertion_id,
                trigger=trigger,
            )
        if recommendation == AssertionRecommendation.SUPERSEDES and supersession_allowed:
            return self.planner.plan_supersession(
                old_assertion=old_assertion,
                new_assertion=new_assertion,
                trigger=trigger,
            )
        # CONTRADICTS, INSUFFICIENT, or SUPERSEDES-without-permission: preserve
        # both, pick no winner, route to review (never destroy evidence).
        return self.planner.plan_conflict(
            subject_assertion=old_assertion,
            competing_assertion=new_assertion,
            trigger=trigger,
        )
