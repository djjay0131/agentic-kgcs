"""Wave 5 / §9 law 12: ontology promotion cannot skip PROPOSED→APPROVED→OBSERVED.

Proposals enter PROPOSED (never auto-APPROVED); illegal transitions are refused;
a term that skipped approval is unconstructable; a PROMOTE_ONTOLOGY_TERM plan is
refused for any non-APPROVED term and is (correctly) declared non-compensable."""

import pytest
from kg_contracts.curation import CurationOperationType
from kg_contracts.testing.factories import make_scores
from pydantic import ValidationError

from kgcs.executor.compensate import INVERSE_OPERATION, Compensator
from kgcs.recuration import (
    CurationTrigger,
    IllegalOntologyTransition,
    OntologyLifecycle,
    OntologyPromotionRefused,
    OntologyTerm,
    OntologyTermState,
    TriggerKind,
    is_legal_transition,
)
from kg_contracts.candidates import OntologyCandidate


def _lifecycle() -> OntologyLifecycle:
    return OntologyLifecycle()


def _trigger() -> CurationTrigger:
    return CurationTrigger.of(
        kind=TriggerKind.ONTOLOGY_OR_POLICY_VERSION_CHANGED,
        concept_keys=("Material",),
        evidence_ids=("ev_1", "ev_2", "ev_3"),
        trace_id="trace-1",
    )


def _proposed() -> OntologyTerm:
    return _lifecycle().propose(
        graph_id="g1",
        term="Material",
        term_kind="entity_type",
        proposed_by="ontology_evolution/1",
        evidence_ids=("ev_1",),
    )


# --- proposals start PROPOSED, never auto-approved ---------------------------


def test_propose_creates_proposed_term_not_approved() -> None:
    term = _proposed()
    assert term.state is OntologyTermState.PROPOSED
    assert term.history == (OntologyTermState.PROPOSED,)


def test_propose_from_candidate_is_proposed() -> None:
    candidate = OntologyCandidate(
        graph_id="g1",
        producer="ontology_evolution/1",
        producer_run_id="run-1",
        ontology_version="1",
        source_coordinates={"source_type": "test", "locator": "row-1"},  # type: ignore[arg-type]
        semantic_key="ontology/Material",
        scores=make_scores(),
        term_kind="entity_type",
        term="Material",
    )
    term = _lifecycle().propose_from_candidate(candidate, trigger=_trigger())
    assert term.state is OntologyTermState.PROPOSED
    assert term.trigger_id is not None


# --- legal / illegal transitions --------------------------------------------


def test_legal_forward_chain() -> None:
    lifecycle = _lifecycle()
    term = lifecycle.approve(_proposed())
    assert term.state is OntologyTermState.APPROVED
    term = lifecycle.observe(term)
    assert term.state is OntologyTermState.OBSERVED
    term = lifecycle.deprecate(term)
    assert term.state is OntologyTermState.DEPRECATED
    assert term.history == (
        OntologyTermState.PROPOSED,
        OntologyTermState.APPROVED,
        OntologyTermState.OBSERVED,
        OntologyTermState.DEPRECATED,
    )


def test_cannot_skip_approval_transition() -> None:
    assert is_legal_transition(OntologyTermState.PROPOSED, OntologyTermState.OBSERVED) is False
    with pytest.raises(IllegalOntologyTransition):
        _lifecycle().transition(_proposed(), OntologyTermState.OBSERVED)


def test_deprecated_is_terminal() -> None:
    lifecycle = _lifecycle()
    term = lifecycle.deprecate(_proposed())  # PROPOSED → DEPRECATED (rejection) is legal
    with pytest.raises(IllegalOntologyTransition):
        lifecycle.transition(term, OntologyTermState.APPROVED)


# --- a skipped-approval term is unconstructable -----------------------------


def test_term_with_illegal_history_cannot_be_constructed() -> None:
    with pytest.raises(ValidationError):
        OntologyTerm(
            term_id="ot_x",
            graph_id="g1",
            term="Material",
            term_kind="entity_type",
            state=OntologyTermState.OBSERVED,
            history=(OntologyTermState.PROPOSED, OntologyTermState.OBSERVED),  # skipped APPROVED
            proposed_by="tester",
        )


def test_term_history_must_start_proposed_and_end_at_state() -> None:
    with pytest.raises(ValidationError):
        OntologyTerm(
            term_id="ot_x",
            graph_id="g1",
            term="Material",
            term_kind="entity_type",
            state=OntologyTermState.APPROVED,
            history=(OntologyTermState.APPROVED,),  # does not start at PROPOSED
            proposed_by="tester",
        )


# --- promotion is gated on APPROVED (law 12) --------------------------------


def test_promotion_refused_for_proposed_term() -> None:
    with pytest.raises(OntologyPromotionRefused):
        _lifecycle().plan_promotion(_proposed(), trigger=_trigger())


def test_promotion_refused_for_observed_term() -> None:
    lifecycle = _lifecycle()
    observed = lifecycle.observe(lifecycle.approve(_proposed()))
    with pytest.raises(OntologyPromotionRefused):
        lifecycle.plan_promotion(observed, trigger=_trigger())


def test_promotion_plan_for_approved_term() -> None:
    lifecycle = _lifecycle()
    trigger = _trigger()
    approved = lifecycle.approve(_proposed())
    plan = lifecycle.plan_promotion(approved, trigger=trigger)
    (op,) = plan.operations
    assert op.type is CurationOperationType.PROMOTE_ONTOLOGY_TERM
    assert op.payload["term_id"] == approved.term_id
    # Trace-linked provenance on the operation.
    assert op.reversal_data["trigger_id"] == trigger.trigger_id
    # Evidence cited on the plan (trigger evidence + the term's own).
    assert "ev_1" in plan.evidence_ids


def test_promotion_is_declared_non_compensable() -> None:
    lifecycle = _lifecycle()
    plan = lifecycle.plan_promotion(lifecycle.approve(_proposed()), trigger=_trigger())
    assert INVERSE_OPERATION[CurationOperationType.PROMOTE_ONTOLOGY_TERM] is None
    comp = Compensator().compensate(plan)
    assert comp.fully_compensable is False
    assert comp.non_compensable[0].type is CurationOperationType.PROMOTE_ONTOLOGY_TERM


def test_promotion_plan_is_deterministic() -> None:
    lifecycle = _lifecycle()
    trigger = _trigger()
    approved = lifecycle.approve(_proposed())
    a = lifecycle.plan_promotion(approved, trigger=trigger)
    b = lifecycle.plan_promotion(approved, trigger=trigger)
    assert a.model_dump_json() == b.model_dump_json()
