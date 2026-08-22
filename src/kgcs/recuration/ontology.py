"""Governed ontology lifecycle: PROPOSED→APPROVED→OBSERVED→DEPRECATED (§9 law 12).

Spec §7.3 gives an ontology term a governed lifecycle, and §9 law 12 makes the
governance non-negotiable: **ontology promotion cannot skip approval.** A
`PROMOTE_ONTOLOGY_TERM` operation may only be planned for a term that has reached
`APPROVED` *via the governed path* — an `OntologyEvolutionAdviser` proposal
creates a `PROPOSED` term and nothing more; it can never auto-approve or
auto-promote (build plan §DG-2, spec §7.3).

The guarantee is enforced two ways so a single bug cannot defeat it:

- **Structurally, at the model.** An `OntologyTerm` carries the full `history`
  of states it has passed through, and a validator rejects any term whose history
  is not a legal path starting at `PROPOSED` and ending at its current `state`.
  A `PROPOSED→OBSERVED` term (approval skipped) is therefore *unconstructable* —
  you cannot even hand-build one to smuggle past the lifecycle.
- **At the gate.** `OntologyLifecycle.transition` refuses any transition not in
  the allowed map (raising `IllegalOntologyTransition`), and `plan_promotion`
  refuses to plan a `PROMOTE_ONTOLOGY_TERM` for a term that is not `APPROVED`
  (raising `OntologyPromotionRefused`).

`PROMOTE_ONTOLOGY_TERM` has no inverse in the v1 vocabulary
(`executor.compensate.INVERSE_OPERATION` maps it to `None`), so a promotion plan
is *explicitly* declared non-compensable (§9 law 8) — its `reversal_data` still
carries the trace-linked provenance for audit.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from kg_contracts.candidates import OntologyCandidate
from kg_contracts.curation import (
    CurationOperation,
    CurationOperationType,
    CurationPlan,
    Precondition,
)

from kgcs.ids import DerivedIdFactory, IdFactory
from kgcs.planner import DEFAULT_POLICY_VERSION, SNAPSHOT_PRECONDITION_KIND
from kgcs.policy import DEFAULT_SNAPSHOT_VERSION
from kgcs.recuration.triggers import CurationTrigger, merge_evidence, trigger_provenance

TermKind = Literal["entity_type", "relation_type", "attribute"]


class OntologyTermState(StrEnum):
    """The governed ontology-term lifecycle states (spec §7.3)."""

    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    OBSERVED = "OBSERVED"
    DEPRECATED = "DEPRECATED"


#: The legal successor states for each state. Forward progress must pass through
#: APPROVED (there is no PROPOSED→OBSERVED edge — that is exactly the skip §9
#: law 12 forbids); DEPRECATED is terminal. A proposal may be retired
#: (PROPOSED→DEPRECATED) or an approved/observed term deprecated, but nothing may
#: reach OBSERVED without first being APPROVED.
_ALLOWED_TRANSITIONS: dict[OntologyTermState, frozenset[OntologyTermState]] = {
    OntologyTermState.PROPOSED: frozenset(
        {OntologyTermState.APPROVED, OntologyTermState.DEPRECATED}
    ),
    OntologyTermState.APPROVED: frozenset(
        {OntologyTermState.OBSERVED, OntologyTermState.DEPRECATED}
    ),
    OntologyTermState.OBSERVED: frozenset({OntologyTermState.DEPRECATED}),
    OntologyTermState.DEPRECATED: frozenset(),
}


class IllegalOntologyTransition(ValueError):
    """A requested lifecycle transition is not permitted (§9 law 12)."""


class OntologyPromotionRefused(ValueError):
    """A `PROMOTE_ONTOLOGY_TERM` plan was requested for a non-`APPROVED` term."""


def is_legal_transition(frm: OntologyTermState, to: OntologyTermState) -> bool:
    """Whether `frm → to` is a permitted ontology lifecycle transition."""
    return to in _ALLOWED_TRANSITIONS[frm]


class OntologyTerm(BaseModel):
    """A proposed/governed ontology term with its full lifecycle `history`.

    `history` is the state path the term has actually travelled; it is validated
    to start at `PROPOSED`, end at the current `state`, and take only legal steps
    — so a term that skipped approval cannot be constructed at all (§9 law 12).
    `evidence_ids` and `trigger_id` link the proposal to what motivated it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    term_id: str = Field(min_length=1)
    graph_id: str
    term: str = Field(min_length=1)
    term_kind: TermKind
    state: OntologyTermState
    history: tuple[OntologyTermState, ...] = Field(min_length=1)
    definition: str | None = None
    proposed_by: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = ()
    trigger_id: str | None = None
    adviser_version: str | None = None
    rationale: str = ""

    @model_validator(mode="after")
    def _check_history_is_a_legal_path(self) -> "OntologyTerm":
        if self.history[0] is not OntologyTermState.PROPOSED:
            raise ValueError("ontology history must start at PROPOSED")
        if self.history[-1] is not self.state:
            raise ValueError(
                f"ontology history must end at the current state {self.state.value!r} "
                f"(ends at {self.history[-1].value!r})"
            )
        for frm, to in zip(self.history, self.history[1:]):
            if not is_legal_transition(frm, to):
                raise ValueError(
                    f"illegal ontology transition in history: "
                    f"{frm.value} → {to.value}"
                )
        return self


class OntologyLifecycle:
    """The governed lifecycle service: propose, transition, and plan promotion.

    Injected with an `IdFactory` (for the promotion op/plan ids) and the
    snapshot/policy versions to stamp. It is the only sanctioned way to advance a
    term's state, and the only place a `PROMOTE_ONTOLOGY_TERM` plan is built —
    always gated on `APPROVED` (§9 law 12).
    """

    def __init__(
        self,
        *,
        id_factory: IdFactory | None = None,
        snapshot_version: str = DEFAULT_SNAPSHOT_VERSION,
        policy_version: str = DEFAULT_POLICY_VERSION,
    ) -> None:
        self._ids = id_factory or DerivedIdFactory()
        self._snapshot_version = snapshot_version
        self._policy_version = policy_version

    def propose(
        self,
        *,
        graph_id: str,
        term: str,
        term_kind: TermKind,
        proposed_by: str,
        definition: str | None = None,
        evidence_ids: Sequence[str] = (),
        trigger: CurationTrigger | None = None,
        adviser_version: str | None = None,
        rationale: str = "",
    ) -> OntologyTerm:
        """Create a `PROPOSED` term — never `APPROVED`.

        This is where an `OntologyEvolutionAdviser` proposal (or a deterministic
        rule) enters the lifecycle. The term starts, and can only start, in
        `PROPOSED`: approval is a separate governed act (§9 law 12).
        """
        return OntologyTerm(
            term_id=_derive_term_id(graph_id, term_kind, term),
            graph_id=graph_id,
            term=term,
            term_kind=term_kind,
            state=OntologyTermState.PROPOSED,
            history=(OntologyTermState.PROPOSED,),
            definition=definition,
            proposed_by=proposed_by,
            evidence_ids=tuple(evidence_ids),
            trigger_id=trigger.trigger_id if trigger is not None else None,
            adviser_version=adviser_version,
            rationale=rationale,
        )

    def propose_from_candidate(
        self,
        candidate: OntologyCandidate,
        *,
        trigger: CurationTrigger | None = None,
        adviser_version: str | None = None,
        rationale: str = "",
    ) -> OntologyTerm:
        """Map an `OntologyCandidate` into a `PROPOSED` term (never promoted)."""
        return self.propose(
            graph_id=candidate.graph_id,
            term=candidate.term,
            term_kind=candidate.term_kind,
            proposed_by=candidate.producer,
            definition=candidate.definition,
            evidence_ids=tuple(ref.evidence_id for ref in candidate.evidence_refs),
            trigger=trigger,
            adviser_version=adviser_version,
            rationale=rationale,
        )

    def transition(self, term: OntologyTerm, to_state: OntologyTermState) -> OntologyTerm:
        """Advance `term` to `to_state`, refusing any illegal transition (§9 law 12)."""
        if not is_legal_transition(term.state, to_state):
            raise IllegalOntologyTransition(
                f"illegal ontology transition {term.state.value} → {to_state.value} "
                f"for term {term.term_id!r}"
            )
        return term.model_copy(
            update={"state": to_state, "history": (*term.history, to_state)}
        )

    def approve(self, term: OntologyTerm) -> OntologyTerm:
        """Governed approval: PROPOSED → APPROVED."""
        return self.transition(term, OntologyTermState.APPROVED)

    def observe(self, term: OntologyTerm) -> OntologyTerm:
        """APPROVED → OBSERVED (the term is now seen in use)."""
        return self.transition(term, OntologyTermState.OBSERVED)

    def deprecate(self, term: OntologyTerm) -> OntologyTerm:
        """Retire a term (→ DEPRECATED) from any non-terminal state."""
        return self.transition(term, OntologyTermState.DEPRECATED)

    def plan_promotion(
        self, term: OntologyTerm, *, trigger: CurationTrigger
    ) -> CurationPlan:
        """Build a `PROMOTE_ONTOLOGY_TERM` plan — only for an `APPROVED` term.

        Refuses (raising `OntologyPromotionRefused`) unless the term is
        `APPROVED`; because an `OntologyTerm`'s history is validated to be a legal
        path from `PROPOSED`, an `APPROVED` term necessarily reached approval
        through governance (§9 law 12). `PROMOTE_ONTOLOGY_TERM` has no inverse, so
        the plan is explicitly non-compensable; `reversal_data` carries the
        trace-linked provenance for audit (§9 law 8).
        """
        if term.state is not OntologyTermState.APPROVED:
            raise OntologyPromotionRefused(
                f"cannot promote ontology term {term.term_id!r}: state is "
                f"{term.state.value}, must be APPROVED (approval governance cannot "
                "be skipped, §9 law 12)"
            )
        evidence_ids = merge_evidence(trigger, term.evidence_ids)
        provenance = trigger_provenance(
            trigger,
            matcher_version=None,
            adviser_version=term.adviser_version,
            policy_version=self._policy_version,
        )
        operation = CurationOperation(
            operation_id=self._ids.operation_id(
                f"promote_ontology:{trigger.trigger_id}:{term.term_id}"
            ),
            type=CurationOperationType.PROMOTE_ONTOLOGY_TERM,
            payload={
                "term_id": term.term_id,
                "term": term.term,
                "term_kind": term.term_kind,
                "graph_id": term.graph_id,
                "state": term.state.value,
            },
            reversal_data={"term_id": term.term_id, **provenance},
        )
        return CurationPlan(
            plan_id=self._ids.plan_id(f"promote_ontology:{trigger.trigger_id}:{term.term_id}"),
            candidate_ids=(term.term_id,),
            snapshot_version=self._snapshot_version,
            operations=(operation,),
            preconditions=(
                Precondition(
                    kind=SNAPSHOT_PRECONDITION_KIND,
                    subject=term.term_id,
                    expected=self._snapshot_version,
                ),
            ),
            evidence_ids=evidence_ids,
            policy_version=self._policy_version,
        )


def _derive_term_id(graph_id: str, term_kind: str, term: str) -> str:
    """A stable `ot_…` content address for an ontology term."""
    seed = f"{graph_id}::{term_kind}::{term}"
    return "ot_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
