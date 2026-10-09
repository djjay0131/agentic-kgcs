"""Deterministic candidate validation: rules, severity, decision invariants."""

import pytest
from kg_contracts.candidates import Candidate, CandidateScores
from kg_contracts.curation import FailureKind
from kg_contracts.testing.factories import (
    make_attribute_candidate,
    make_entity_candidate,
    make_scores,
)
from kg_contracts.versioning import CONTRACT_VERSION

from kgcs import (
    CandidateValidator,
    ContractVersionMode,
    ContractVersionRule,
    RuleBasedValidator,
    default_validator,
)
from kgcs.testing import CandidateValidatorContract

GRAPH_ID = "g1"


def _entity_with_version(version: str) -> Candidate:
    return make_entity_candidate(graph_id=GRAPH_ID).model_copy(
        update={"contract_version": version}
    )


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


class TestContractVersionCompatibility:
    """Compatible-minor acceptance, and the exact-match escape hatch.

    A fixed installed version (`2.2.0`) is used so this matrix does not drift
    with `CONTRACT_VERSION`; the default validator's use of the real installed
    version is covered by `TestContractVersion`.
    """

    INSTALLED = "2.2.0"

    def _rule(self, *, mode: ContractVersionMode | None = None) -> ContractVersionRule:
        if mode is None:
            return ContractVersionRule(self.INSTALLED)
        return ContractVersionRule(self.INSTALLED, mode=mode)

    def test_default_mode_is_compatible_minor(self) -> None:
        assert self._rule().mode is ContractVersionMode.COMPATIBLE_MINOR

    def test_same_version_passes(self) -> None:
        assert self._rule().check(_entity_with_version("2.2.0")) == ()

    def test_older_minor_passes(self) -> None:
        # Rows persisted at an older 2.x (before a minor bump) stay admissible.
        assert self._rule().check(_entity_with_version("2.1.0")) == ()
        assert self._rule().check(_entity_with_version("2.0.0")) == ()

    def test_patch_is_ignored(self) -> None:
        assert self._rule().check(_entity_with_version("2.2.7")) == ()
        assert self._rule().check(_entity_with_version("2.1.99")) == ()

    def test_newer_minor_is_rejected(self) -> None:
        violations = self._rule().check(_entity_with_version("2.3.0"))
        assert len(violations) == 1
        assert violations[0].failure_kind is FailureKind.BAD_DATA
        # The reason names both versions, so a reader can see the mismatch.
        assert self.INSTALLED in violations[0].reason
        assert "2.3.0" in violations[0].reason

    def test_different_major_is_rejected(self) -> None:
        for version in ("1.9.9", "3.0.0"):
            violations = self._rule().check(_entity_with_version(version))
            assert len(violations) == 1
            assert violations[0].failure_kind is FailureKind.BAD_DATA
            assert version in violations[0].reason
            assert self.INSTALLED in violations[0].reason

    def test_malformed_versions_are_rejected(self) -> None:
        for version in ("2.2", "2.2.0.1", "2", "abc", "2.x.0", "", "v2.2.0", "2.2.0-rc1"):
            violations = self._rule().check(_entity_with_version(version))
            assert len(violations) == 1, version
            assert violations[0].failure_kind is FailureKind.BAD_DATA

    def test_non_canonical_semver_is_rejected(self) -> None:
        # Leading zeros are not canonical semver; strict parsing refuses them
        # rather than reading "2.02.0" as "2.2.0".
        for version in ("02.2.0", "2.02.0", "2.2.00"):
            violations = self._rule().check(_entity_with_version(version))
            assert len(violations) == 1, version
            assert violations[0].failure_kind is FailureKind.BAD_DATA

    def test_exact_mode_accepts_only_the_exact_version(self) -> None:
        rule = self._rule(mode=ContractVersionMode.EXACT)
        assert rule.check(_entity_with_version("2.2.0")) == ()
        # A compatible older minor is refused in exact mode.
        violations = rule.check(_entity_with_version("2.1.0"))
        assert len(violations) == 1
        assert violations[0].failure_kind is FailureKind.BAD_DATA

    def test_unparseable_expected_is_rejected_up_front(self) -> None:
        with pytest.raises(ValueError):
            ContractVersionRule("not-semver")

    def test_exact_mode_tolerates_an_unparseable_expected(self) -> None:
        rule = ContractVersionRule("not-semver", mode=ContractVersionMode.EXACT)
        assert rule.check(_entity_with_version("not-semver")) == ()
        assert len(rule.check(_entity_with_version("2.2.0"))) == 1

    def test_default_validator_honours_the_mode(self) -> None:
        compatible = default_validator(
            graph_id=GRAPH_ID, contract_version=self.INSTALLED
        )
        assert compatible.validate(_entity_with_version("2.0.0")).valid

        exact = default_validator(
            graph_id=GRAPH_ID,
            contract_version=self.INSTALLED,
            contract_version_mode=ContractVersionMode.EXACT,
        )
        decision = exact.validate(_entity_with_version("2.0.0"))
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
