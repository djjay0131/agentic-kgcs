"""The semantic curation audit: the third audit object, joined by trace_id.

Proves Wave-7 exit criteria: a `SemanticAuditRecord` captures the deterministic
baseline + adviser assessments + review + final action + resulting plan + every
version, all carrying the universal `trace_id`; it is a *distinct type* from
`kg_contracts.curation.AuditRecord` (operation-scoped) and
`kgcs.executor.ExecutionRecord` (execution-scoped), linked to them by
trace/plan/candidate ids rather than conflated; it is immutable and survives a
JSON round trip; and building it twice from the same artifacts is byte-identical
(determinism, §9 law 17).
"""

import json
from datetime import UTC, datetime

from kg_contracts.curation import AuditRecord, CurationPlan, ReviewAction, ReviewDecision

from kgcs.advisers.completion import CompletionResponse, RecordedCompletionClient
from kgcs.advisers.orchestrator import CurationOrchestrator, OrchestrationResult
from kgcs.advisers.specialists import IdentityAdviser
from kgcs.er.blocking import CandidatePair
from kgcs.er.matcher import CalibrationKey, MatchResult
from kgcs.er.resolution import ErAction
from kgcs.executor import ExecutionOutcome, ExecutionRecord
from kgcs.observability import (
    ExecutionRef,
    ReplayInputs,
    ReviewSummary,
    SemanticAuditBuilder,
    SemanticAuditRecord,
)
from kgcs.profiles import default_profile
from kgcs.recuration.triggers import CurationTrigger, TriggerKind
from kgcs.review.operations import ReviewOutcome, ReviewOutcomeStatus

_TRACE = "trace-xyz"
_PAIR = CandidatePair.of("paper/a", "paper/b")
_KEY = CalibrationKey.of(
    graph_id="g1",
    entity_type="Paper",
    source_pair=("source_a", "source_b"),
    matcher_version="rules/1",
    consequence_class="standard",
)


def _mr(probability: float = 0.90) -> MatchResult:
    return MatchResult(
        pair=_PAIR,
        probability=probability,
        matcher_version="rules/1",
        feature_vector={"mutually_exclusive": 0.0, "identifier_agreement": 1.0},
        calibration_key=_KEY,
    )


def _recorded_adviser(mr: MatchResult, payload: dict[str, object]) -> IdentityAdviser:
    from kgcs.advisers.orchestrator import _identity_question

    question = _identity_question(mr, evidence_ids=("ev_1",), trace_id=_TRACE)
    request = IdentityAdviser(port=RecordedCompletionClient({})).build_request(question)
    response = CompletionResponse(text=json.dumps(payload), model_id="recorded/echo", model_version="1")
    return IdentityAdviser(port=RecordedCompletionClient({request.request_hash: response}))


def _orchestrated() -> tuple[OrchestrationResult, ReplayInputs]:
    mr = _mr()
    adviser = _recorded_adviser(mr, {"recommendation": "same", "evidence_ids": ["ev_1"]})
    result = CurationOrchestrator(identity_adviser=adviser).resolve(
        mr, profile=default_profile(), evidence_ids=("ev_1",), trace_id=_TRACE
    )
    replay_inputs = ReplayInputs(
        match_result=mr, profile=default_profile(), evidence_ids=("ev_1",), trace_id=_TRACE
    )
    return result, replay_inputs


def _plan(plan_id: str) -> CurationPlan:
    return CurationPlan(
        plan_id=plan_id,
        candidate_ids=("cand_1",),
        snapshot_version="0",
        operations=(),
        preconditions=(),
        evidence_ids=("ev_1",),
        policy_version="1",
    )


def _review(plan_id: str) -> ReviewOutcome:
    decision = ReviewDecision(
        item_id="rv_1", action=ReviewAction.APPROVE, actor="alice", decided_at=datetime(2026, 8, 1, tzinfo=UTC)
    )
    return ReviewOutcome(
        decision=decision,
        action=ReviewAction.APPROVE,
        status=ReviewOutcomeStatus.PLANNED,
        kind=None,
        plan=_plan(plan_id),
        evolution=None,
        rationale="approved by reviewer",
    )


def _execution(plan_id: str) -> ExecutionRecord:
    return ExecutionRecord(
        execution_id="ex_1",
        plan_id=plan_id,
        batch_id="mb_1",
        outcome=ExecutionOutcome.COMMITTED,
        new_epoch=1,
        recorded_at=datetime(2026, 8, 2, tzinfo=UTC),
    )


def _trigger() -> CurationTrigger:
    return CurationTrigger.of(
        kind=TriggerKind.NEW_EVIDENCE,
        identity_ids=("id_a", "id_b"),
        evidence_ids=("ev_1",),
        trace_id=_TRACE,
    )


def _build() -> SemanticAuditRecord:
    result, replay_inputs = _orchestrated()
    return SemanticAuditBuilder().build(
        result,
        replay_inputs,
        trigger=_trigger(),
        review=_review("pl_1"),
        execution=_execution("pl_1"),
        ontology_version="ont/3",
    )


class TestCapturesFullLineage:
    def test_captures_baseline_adviser_review_final_plan_and_versions(self) -> None:
        result, _ = _orchestrated()
        record = _build()

        # baseline + final decisions preserved verbatim
        assert record.baseline.model_dump_json() == result.baseline.model_dump_json()
        assert record.final.model_dump_json() == result.decision.model_dump_json()
        assert record.final.action is ErAction.AUTO_LINK

        # adviser assessments (with DG-4 provenance) preserved
        assert record.consulted_adviser is True
        assert record.assessments[0].baseline_action_before == ErAction.LLM_ASSESS.value
        assert record.assessments[0].final_action_after == ErAction.AUTO_LINK.value

        # review + plan + execution ref
        assert record.review is not None
        assert record.review.action == ReviewAction.APPROVE.value
        assert record.plan_id == "pl_1"
        assert record.execution is not None
        assert record.execution.execution_id == "ex_1"

        # every version coordinate
        assert record.versions.matcher_version == "rules/1"
        assert record.versions.adviser_version == "identity/1"
        assert record.versions.model_id == "recorded/echo"
        assert record.versions.policy_version == "1"
        assert record.versions.ontology_version == "ont/3"
        assert record.versions.profile_id == "default"
        assert record.score_vector == {"mutually_exclusive": 0.0, "identifier_agreement": 1.0}

    def test_universal_trace_id_flows_through(self) -> None:
        record = _build()
        assert record.trace_id == _TRACE
        assert record.assessments[0].trace_id == _TRACE
        assert record.replay_inputs.trace_id == _TRACE

    def test_affected_refs_come_from_the_trigger(self) -> None:
        record = _build()
        assert record.affected_refs == ("id_a", "id_b")
        assert record.trigger_id is not None


class TestDistinctButLinked:
    def test_is_a_distinct_type_from_the_other_two_audits(self) -> None:
        record = _build()
        assert isinstance(record, SemanticAuditRecord)
        assert not isinstance(record, AuditRecord)
        assert not isinstance(record, ExecutionRecord)

    def test_links_to_execution_by_plan_id_not_by_embedding(self) -> None:
        execution = _execution("pl_1")
        result, replay_inputs = _orchestrated()
        record = SemanticAuditBuilder().build(
            result, replay_inputs, review=_review("pl_1"), execution=execution
        )
        # The semantic audit joins to the execution stream by plan_id; the
        # ExecutionRecord remains its own object, referenced only by a compact ref.
        assert isinstance(record.execution, ExecutionRef)
        assert record.execution.plan_id == execution.plan_id == record.plan_id

    def test_review_is_summarized_not_embedded(self) -> None:
        record = _build()
        assert isinstance(record.review, ReviewSummary)
        assert record.review.plan_id == "pl_1"


class TestImmutableAndSerializable:
    def test_survives_a_json_round_trip(self) -> None:
        record = _build()
        restored = SemanticAuditRecord.model_validate_json(record.model_dump_json())
        assert restored == record

    def test_is_frozen(self) -> None:
        import pytest

        record = _build()
        with pytest.raises(Exception):
            record.trace_id = "mutated"  # type: ignore[misc]


class TestDeterminism:
    def test_building_twice_is_byte_identical(self) -> None:
        first, second = _build(), _build()
        assert first.audit_id == second.audit_id
        assert first.model_dump_json() == second.model_dump_json()

    def test_audit_id_is_derived_not_random(self) -> None:
        record = _build()
        assert record.audit_id.startswith("au_")
