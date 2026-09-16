"""Deterministic candidate validation: rules, severity, decision invariants."""

from kg_contracts.candidates import CandidateScores
from kg_contracts.curation import FailureKind
from kg_contracts.testing.factories import (
    make_attribute_candidate,
    make_entity_candidate,
    make_scores,
)
from kg_contracts.versioning import CONTRACT_VERSION

from kgcs import CandidateValidator, RuleBasedValidator, default_validator
from kgcs.testing import CandidateValidatorContract

GRAPH_ID = "g1"


class TestSupportedKind:
    def test_implemented_kind_passes(self) -> None:
        decision = default_validator().validate(make_entity_candidate(graph_id=GRAPH_ID))
        assert decision.valid

    def test_unimplemented_kind_is_unsupported_ontology(self) -> None:
        from kg_contracts.candidates import PlanCandidate, SourceCoordinates

        candidate = PlanCandidate(
            graph_id=GRAPH_ID,
            producer="p",
            producer_run_id="r",
            ontology_version="1",
            source_coordinates=SourceCoordinates(source_type="t", locator="l"),
            semantic_key="k",
            scores=make_scores(),
            objective="do-a-thing",
        )
        decision = default_validator().validate(candidate)
        assert not decision.valid
        assert decision.failure_kind is FailureKind.UNSUPPORTED_ONTOLOGY


class TestGraphRules:
    def test_malformed_graph_id_is_bad_data(self) -> None:
        decision = default_validator().validate(make_entity_candidate(graph_id="BAD!"))
        assert not decision.valid
        assert decision.failure_kind is FailureKind.BAD_DATA

    def test_wrong_graph_scope_is_rejected_when_bound(self) -> None:
        validator = default_validator(graph_id=GRAPH_ID)
        decision = validator.validate(make_entity_candidate(graph_id="other"))
        assert not decision.valid
        assert decision.failure_kind is FailureKind.BAD_DATA

    def test_scope_not_enforced_when_unbound(self) -> None:
        decision = default_validator().validate(make_entity_candidate(graph_id="other"))
        assert decision.valid


class TestContractVersion:
    def test_matching_version_passes(self) -> None:
        decision = default_validator().validate(make_entity_candidate(graph_id=GRAPH_ID))
        assert decision.valid

    def test_mismatched_version_is_bad_data(self) -> None:
        candidate = make_entity_candidate(graph_id=GRAPH_ID).model_copy(
            update={"contract_version": CONTRACT_VERSION + "-old"}
        )
        decision = default_validator().validate(candidate)
        assert not decision.valid
        assert decision.failure_kind is FailureKind.BAD_DATA


class TestProducerPresent:
    def test_blank_producer_is_bad_data(self) -> None:
        candidate = make_entity_candidate(graph_id=GRAPH_ID).model_copy(
            update={"producer": "   "}
        )
        decision = default_validator().validate(candidate)
        assert not decision.valid
        assert decision.failure_kind is FailureKind.BAD_DATA
        assert any("producer" in r for r in decision.reasons)

    def test_present_producer_passes(self) -> None:
        decision = default_validator().validate(make_entity_candidate(graph_id=GRAPH_ID))
        assert decision.valid


class TestSeverityAndDecisionShape:
    def test_most_permanent_kind_wins_when_several_rules_fail(self) -> None:
        # Bound to g1 so the "other" graph trips BOTH the well-formed rule
        # (well-formed 'other' passes) — use a malformed id to stack a
        # graph-scope BAD_DATA on top of an unsupported-kind failure.
        from kg_contracts.candidates import PlanCandidate, SourceCoordinates

        candidate = PlanCandidate(
            graph_id="other",
            producer="p",
            producer_run_id="r",
            ontology_version="1",
            source_coordinates=SourceCoordinates(source_type="t", locator="l"),
            semantic_key="k",
            scores=make_scores(),
            objective="x",
        )
        decision = default_validator(graph_id=GRAPH_ID).validate(candidate)
        # UNSUPPORTED_ONTOLOGY (kind) + BAD_DATA (scope) both fire; BAD_DATA wins.
        assert not decision.valid
        assert decision.failure_kind is FailureKind.BAD_DATA
        assert len(decision.reasons) >= 2

    def test_valid_decision_has_no_failure_and_no_reasons(self) -> None:
        decision = default_validator().validate(make_entity_candidate(graph_id=GRAPH_ID))
        assert decision.valid
        assert decision.failure_kind is None
        assert decision.reasons == ()

    def test_reasons_follow_rule_order(self) -> None:
        # Unsupported kind (rule 1) fires before contract-version (rule 3).
        from kg_contracts.candidates import PlanCandidate, SourceCoordinates

        candidate = PlanCandidate(
            graph_id=GRAPH_ID,
            producer="p",
            producer_run_id="r",
            ontology_version="1",
            source_coordinates=SourceCoordinates(source_type="t", locator="l"),
            semantic_key="k",
            scores=make_scores(),
            objective="x",
        ).model_copy(update={"contract_version": CONTRACT_VERSION + "-old"})
        decision = default_validator().validate(candidate)
        assert "not implemented" in decision.reasons[0]
        assert "contract_version" in decision.reasons[-1]


class TestValidatorContract(CandidateValidatorContract):
    def make_validator(self) -> CandidateValidator:
        return default_validator(graph_id=GRAPH_ID)


def test_attribute_candidate_validates() -> None:
    scores: CandidateScores = make_scores()
    candidate = make_attribute_candidate(graph_id=GRAPH_ID, scores=scores)
    assert isinstance(default_validator(), RuleBasedValidator)
    assert default_validator(graph_id=GRAPH_ID).validate(candidate).valid
