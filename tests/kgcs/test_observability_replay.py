"""Replay: a recorded decision reproduces byte-identically (§9 law 17).

Proves the Wave-7 replay exit criterion. A `SemanticAuditRecord` carries the
exact `ReplayInputs`, so `replay` re-runs the decision through the same
orchestrator and confirms the final `ErDecision` reproduces byte-for-byte —
including the LLM arm, replayed through a `RecordedCompletionClient`. A
divergence (e.g. replaying an adviser-influenced decision without the matching
fixtures) is detected and reported, never hidden.
"""

import json

from kgcs.advisers.completion import CompletionResponse, RecordedCompletionClient
from kgcs.advisers.orchestrator import CurationOrchestrator, OrchestrationResult
from kgcs.advisers.specialists import IdentityAdviser
from kgcs.er.blocking import CandidatePair
from kgcs.er.matcher import CalibrationKey, MatchResult
from kgcs.er.resolution import ErAction
from kgcs.observability import ReplayInputs, SemanticAuditBuilder, replay
from kgcs.profiles import default_profile

_TRACE = "trace-replay"
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
        feature_vector={"mutually_exclusive": 0.0},
        calibration_key=_KEY,
    )


def _recorded_adviser(mr: MatchResult, payload: dict[str, object]) -> IdentityAdviser:
    from kgcs.advisers.orchestrator import _identity_question

    question = _identity_question(mr, evidence_ids=("ev_1",), trace_id=_TRACE)
    request = IdentityAdviser(port=RecordedCompletionClient({})).build_request(question)
    response = CompletionResponse(text=json.dumps(payload), model_id="recorded/echo", model_version="1")
    return IdentityAdviser(port=RecordedCompletionClient({request.request_hash: response}))


def _record_and_adviser(payload: dict[str, object]):  # type: ignore[no-untyped-def]
    mr = _mr()
    adviser = _recorded_adviser(mr, payload)
    result: OrchestrationResult = CurationOrchestrator(identity_adviser=adviser).resolve(
        mr, profile=default_profile(), evidence_ids=("ev_1",), trace_id=_TRACE
    )
    replay_inputs = ReplayInputs(
        match_result=mr, profile=default_profile(), evidence_ids=("ev_1",), trace_id=_TRACE
    )
    record = SemanticAuditBuilder().build(result, replay_inputs)
    return record, adviser


class TestReplayReproduces:
    def test_deterministic_baseline_decision_replays_identically(self) -> None:
        # A confident pair auto-links with no adviser consulted; replay with no
        # adviser must reproduce it byte-for-byte.
        mr = _mr(0.999)
        result = CurationOrchestrator().resolve(mr, profile=default_profile(), trace_id=_TRACE)
        record = SemanticAuditBuilder().build(
            result, ReplayInputs(match_result=mr, profile=default_profile(), trace_id=_TRACE)
        )
        outcome = replay(record)
        assert outcome.reproduced is True
        assert outcome.divergence is None
        assert outcome.replayed_final_action == ErAction.AUTO_LINK.value

    def test_llm_arm_replays_identically_with_recorded_client(self) -> None:
        record, adviser = _record_and_adviser({"recommendation": "same", "evidence_ids": ["ev_1"]})
        assert record.final.action is ErAction.AUTO_LINK  # advice was folded in
        outcome = replay(record, identity_adviser=adviser)
        assert outcome.reproduced is True
        assert outcome.divergence is None
        assert outcome.consulted_adviser is True

    def test_replayed_decision_is_byte_identical_to_recorded(self) -> None:
        record, adviser = _record_and_adviser({"recommendation": "different"})
        outcome = replay(record, identity_adviser=adviser)
        assert outcome.reproduced is True
        assert record.final.action is ErAction.RETAIN_SEPARATE


class TestDivergenceDetected:
    def test_missing_adviser_on_an_influenced_decision_is_reported_not_hidden(self) -> None:
        # The recorded decision folded in advice; replaying WITHOUT the adviser
        # yields the bare baseline (LLM_ASSESS), a genuine divergence that must
        # be surfaced rather than silently accepted.
        record, _ = _record_and_adviser({"recommendation": "same", "evidence_ids": ["ev_1"]})
        outcome = replay(record)  # no adviser supplied
        assert outcome.reproduced is False
        assert outcome.divergence is not None
        assert outcome.recorded_final_action == ErAction.AUTO_LINK.value
        assert outcome.replayed_final_action == ErAction.LLM_ASSESS.value

    def test_replay_is_pure_two_runs_agree(self) -> None:
        record, adviser = _record_and_adviser({"recommendation": "same", "evidence_ids": ["ev_1"]})
        first = replay(record, identity_adviser=adviser)
        second = replay(record, identity_adviser=adviser)
        assert first.model_dump_json() == second.model_dump_json()
