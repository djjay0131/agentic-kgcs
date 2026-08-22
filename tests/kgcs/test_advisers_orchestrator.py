"""The orchestrator: baseline-first, advice folded through the deterministic gate.

Proves the wave's load-bearing behaviours: the deterministic baseline stands on
every LLM failure and when the adviser is removed (§9 law 1); a recorded fixture
gives an identical final decision (§9 law 6); advice never overrides the
authority gate (a `same` recommendation cannot upgrade a CLIENT_AUTHORITATIVE
pair to AUTO_LINK — §9 law 13); insufficient/abstaining advice routes
conservatively; and DG-4 before/after provenance is stamped on every assessment.
"""

import json

from kgcs.advisers.completion import (
    CompletionResponse,
    FailingCompletionClient,
    MalformedCompletionClient,
    RecordedCompletionClient,
    TimeoutCompletionClient,
)
from kgcs.advisers.orchestrator import (
    CurationOrchestrator,
    OrchestrationResult,
    _identity_question,
)
from kgcs.advisers.specialists import IdentityAdviser
from kgcs.er.blocking import CandidatePair
from kgcs.er.matcher import CalibrationKey, MatchResult
from kgcs.er.resolution import ErAction, ErDecision, ErResolutionPolicy
from kgcs.profiles import (
    CurationProfile,
    client_authoritative_profile,
    default_profile,
)

_PAIR = CandidatePair.of("paper/a", "paper/b")
_KEY = CalibrationKey.of(
    graph_id="g1",
    entity_type="Paper",
    source_pair=("source_a", "source_b"),
    matcher_version="rules/1",
    consequence_class="standard",
)


def _mr(probability: float) -> MatchResult:
    return MatchResult(
        pair=_PAIR,
        probability=probability,
        matcher_version="rules/1",
        feature_vector={"mutually_exclusive": 0.0},
        calibration_key=_KEY,
    )


# p=0.90 puts a default (STANDARD) profile in the LLM_ASSESS band; p=0.95 does
# the same for a client-authoritative (HIGH) profile.
_ASSESS_DEFAULT = 0.90
_ASSESS_CA = 0.95


def _recorded_adviser(
    match_result: MatchResult,
    payload: dict[str, object],
    *,
    evidence_ids: tuple[str, ...],
    trace_id: str,
) -> IdentityAdviser:
    question = _identity_question(match_result, evidence_ids=evidence_ids, trace_id=trace_id)
    request = IdentityAdviser(port=RecordedCompletionClient({})).build_request(question)
    response = CompletionResponse(text=json.dumps(payload), model_id="recorded/echo", model_version="1")
    return IdentityAdviser(port=RecordedCompletionClient({request.request_hash: response}))


def _baseline(match_result: MatchResult, profile: CurationProfile) -> ErDecision:
    return ErResolutionPolicy().decide(match_result, profile=profile)


# --- §9 law 1: the deterministic baseline survives every LLM failure --------


class TestBaselineSurvivesFailure:
    def test_failing_adviser_returns_exactly_the_baseline(self) -> None:
        mr = _mr(_ASSESS_DEFAULT)
        orch = CurationOrchestrator(identity_adviser=IdentityAdviser(port=FailingCompletionClient()))
        result = orch.resolve(mr, profile=default_profile(), evidence_ids=("ev_1",), trace_id="t")
        assert result.decision.model_dump_json() == _baseline(mr, default_profile()).model_dump_json()
        assert result.assessments[0].abstained is True

    def test_timeout_adviser_returns_exactly_the_baseline(self) -> None:
        mr = _mr(_ASSESS_DEFAULT)
        orch = CurationOrchestrator(identity_adviser=IdentityAdviser(port=TimeoutCompletionClient()))
        result = orch.resolve(mr, profile=default_profile(), evidence_ids=("ev_1",), trace_id="t")
        assert result.decision.model_dump_json() == _baseline(mr, default_profile()).model_dump_json()

    def test_malformed_adviser_returns_exactly_the_baseline(self) -> None:
        mr = _mr(_ASSESS_DEFAULT)
        orch = CurationOrchestrator(identity_adviser=IdentityAdviser(port=MalformedCompletionClient()))
        result = orch.resolve(mr, profile=default_profile(), evidence_ids=("ev_1",), trace_id="t")
        assert result.decision.model_dump_json() == _baseline(mr, default_profile()).model_dump_json()

    def test_no_adviser_leaves_a_valid_baseline_decision(self) -> None:
        mr = _mr(_ASSESS_DEFAULT)
        result = CurationOrchestrator().resolve(mr, profile=default_profile())
        assert result.consulted is False
        assert result.decision.model_dump_json() == _baseline(mr, default_profile()).model_dump_json()

    def test_advisers_are_not_consulted_when_baseline_does_not_defer(self) -> None:
        # A confident pair auto-links deterministically; no adviser is called.
        mr = _mr(0.999)
        orch = CurationOrchestrator(identity_adviser=IdentityAdviser(port=FailingCompletionClient()))
        result = orch.resolve(mr, profile=default_profile(), evidence_ids=("ev_1",))
        assert result.decision.action is ErAction.AUTO_LINK
        assert result.consulted is False
        assert result.assessments == ()


# --- §9 law 6: recorded fixture + same inputs → identical final decision -----


class TestDeterministicReplay:
    def test_same_fixture_yields_identical_result(self) -> None:
        mr = _mr(_ASSESS_DEFAULT)
        payload = {"recommendation": "same", "evidence_ids": ["ev_1"], "confidence": 0.95}

        def run() -> OrchestrationResult:
            orch = CurationOrchestrator(
                identity_adviser=_recorded_adviser(mr, payload, evidence_ids=("ev_1",), trace_id="t")
            )
            return orch.resolve(mr, profile=default_profile(), evidence_ids=("ev_1",), trace_id="t")

        first, second = run(), run()
        assert first.decision.model_dump_json() == second.decision.model_dump_json()
        assert first.assessments[0].model_dump_json() == second.assessments[0].model_dump_json()

    def test_same_recommendation_auto_links_under_open_profile(self) -> None:
        mr = _mr(_ASSESS_DEFAULT)
        orch = CurationOrchestrator(
            identity_adviser=_recorded_adviser(
                mr, {"recommendation": "same", "evidence_ids": ["ev_1"]}, evidence_ids=("ev_1",), trace_id="t"
            )
        )
        result = orch.resolve(mr, profile=default_profile(), evidence_ids=("ev_1",), trace_id="t")
        assert result.decision.action is ErAction.AUTO_LINK
        assert result.decision.evidence_ids == ("ev_1",)  # final action cites evidence (§9 law 9)

    def test_different_recommendation_retains_separate(self) -> None:
        mr = _mr(_ASSESS_DEFAULT)
        orch = CurationOrchestrator(
            identity_adviser=_recorded_adviser(
                mr, {"recommendation": "different"}, evidence_ids=("ev_1",), trace_id="t"
            )
        )
        result = orch.resolve(mr, profile=default_profile(), evidence_ids=("ev_1",), trace_id="t")
        assert result.decision.action is ErAction.RETAIN_SEPARATE


# --- §9 law 13 / Issue #2: advice never overrides the authority gate ---------


class TestAdviceNeverOverridesAuthority:
    def test_same_cannot_upgrade_client_authoritative_to_auto_link(self) -> None:
        mr = _mr(_ASSESS_CA)
        profile = client_authoritative_profile()
        assert _baseline(mr, profile).action is ErAction.LLM_ASSESS  # it does route to an adviser
        orch = CurationOrchestrator(
            identity_adviser=_recorded_adviser(
                mr, {"recommendation": "same", "evidence_ids": ["ev_1"], "confidence": 0.99},
                evidence_ids=("ev_1",), trace_id="t",
            )
        )
        result = orch.resolve(mr, profile=profile, evidence_ids=("ev_1",), trace_id="t")
        assert result.decision.action is ErAction.PROPOSE_LINK  # clamped — never AUTO_LINK
        assert result.decision.action is not ErAction.AUTO_LINK


# --- conservative on insufficient / abstain ---------------------------------


class TestConservativeRouting:
    def test_explicit_insufficient_gathers_more_evidence(self) -> None:
        mr = _mr(_ASSESS_DEFAULT)
        orch = CurationOrchestrator(
            identity_adviser=_recorded_adviser(
                mr, {"recommendation": "insufficient_evidence"}, evidence_ids=("ev_1",), trace_id="t"
            )
        )
        result = orch.resolve(mr, profile=default_profile(), evidence_ids=("ev_1",), trace_id="t")
        assert result.decision.action is ErAction.GATHER_MORE_EVIDENCE

    def test_abstain_never_routes_toward_a_link(self) -> None:
        mr = _mr(_ASSESS_DEFAULT)
        orch = CurationOrchestrator(identity_adviser=IdentityAdviser(port=FailingCompletionClient()))
        result = orch.resolve(mr, profile=default_profile(), evidence_ids=("ev_1",), trace_id="t")
        assert result.decision.action not in (ErAction.AUTO_LINK, ErAction.PROPOSE_LINK)


# --- DG-4 provenance --------------------------------------------------------


class TestProvenance:
    def test_before_and_after_actions_are_stamped(self) -> None:
        mr = _mr(_ASSESS_DEFAULT)
        orch = CurationOrchestrator(
            identity_adviser=_recorded_adviser(
                mr, {"recommendation": "same", "evidence_ids": ["ev_1"]}, evidence_ids=("ev_1",), trace_id="tr"
            )
        )
        assessment = orch.resolve(mr, profile=default_profile(), evidence_ids=("ev_1",), trace_id="tr").assessments[0]
        assert assessment.baseline_action_before == ErAction.LLM_ASSESS.value
        assert assessment.final_action_after == ErAction.AUTO_LINK.value
        assert assessment.trace_id == "tr"
        assert assessment.evidence_ids == ("ev_1",)
