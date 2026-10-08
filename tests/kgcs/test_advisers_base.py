"""The shared adviser machinery: abstain-never-raise, provenance, determinism.

Proves the core disciplines that make an adviser a *bounded* adviser: any
completion failure or malformed output becomes an abstain (never an exception
reaching the caller), a `CompletionMiss` still propagates loudly, every
assessment carries DG-4 provenance, a recorded fixture replays byte-identically
(§9 law 6), and an adviser can only cite evidence it was actually given.
"""

import json

import pytest

from kgcs.advisers.base import AdviserAssessment, AdviserQuestion
from kgcs.advisers.completion import (
    CompletionMiss,
    CompletionResponse,
    FailingCompletionClient,
    MalformedCompletionClient,
    RecordedCompletionClient,
    TimeoutCompletionClient,
)
from kgcs.advisers.specialists import IdentityAdviser, IdentityRecommendation


def _question(evidence: tuple[str, ...] = ("ev_1", "ev_2")) -> AdviserQuestion:
    return AdviserQuestion(
        kind="identity", subject="a", other="b", evidence_ids=evidence, trace_id="trace-1"
    )


def _recorded(adviser: IdentityAdviser, question: AdviserQuestion, payload: dict[str, object]) -> IdentityAdviser:
    """A fresh adviser whose port replays `payload` for `question`."""
    request = adviser.build_request(question)
    response = CompletionResponse(
        text=json.dumps(payload), model_id="recorded/echo", model_version="2024.1"
    )
    return IdentityAdviser(port=RecordedCompletionClient({request.request_hash: response}))


class TestAbstainNeverRaises:
    def test_port_failure_abstains(self) -> None:
        assessment = IdentityAdviser(port=FailingCompletionClient()).assess(_question())
        assert assessment.abstained is True
        assert assessment.recommendation == IdentityRecommendation.INSUFFICIENT_EVIDENCE
        assert assessment.confidence is None

    def test_timeout_abstains(self) -> None:
        assessment = IdentityAdviser(port=TimeoutCompletionClient()).assess(_question())
        assert assessment.abstained is True

    def test_malformed_output_abstains(self) -> None:
        assessment = IdentityAdviser(port=MalformedCompletionClient()).assess(_question())
        assert assessment.abstained is True
        # A parse failure still records the model that produced the bad output.
        assert assessment.model_id == "malformed"

    def test_unpermitted_recommendation_abstains(self) -> None:
        question = _question()
        adviser = _recorded(
            IdentityAdviser(port=FailingCompletionClient()),
            question,
            {"recommendation": "merge_them_now"},  # not in the identity vocabulary
        )
        assert adviser.assess(question).abstained is True

    def test_completion_miss_propagates(self) -> None:
        # A missing fixture is a wiring error, surfaced loudly — not an abstain.
        adviser = IdentityAdviser(port=RecordedCompletionClient({}))
        with pytest.raises(CompletionMiss):
            adviser.assess(_question())


class TestProvenance:
    def test_every_assessment_carries_dg4_provenance(self) -> None:
        question = _question()
        adviser = _recorded(
            IdentityAdviser(port=FailingCompletionClient()),
            question,
            {"recommendation": "same", "evidence_ids": ["ev_1"], "confidence": 0.9},
        )
        assessment = adviser.assess(question)
        assert assessment.adviser_type == "identity"
        assert assessment.adviser_version == "identity/1"
        assert assessment.model_id == "recorded/echo"
        assert assessment.model_version == "2024.1"
        assert assessment.prompt_version == "1"
        assert assessment.trace_id == "trace-1"
        assert assessment.recommendation == IdentityRecommendation.SAME

    def test_abstain_carries_supplied_evidence_and_null_model(self) -> None:
        assessment = IdentityAdviser(port=FailingCompletionClient()).assess(_question())
        assert assessment.evidence_ids == ("ev_1", "ev_2")  # supplied set
        assert assessment.model_id == "unavailable"
        assert assessment.model_version == "unavailable"


class TestEvidenceCitationIsBounded:
    def test_cited_evidence_is_intersected_with_supplied(self) -> None:
        question = _question(evidence=("ev_1", "ev_2"))
        adviser = _recorded(
            IdentityAdviser(port=FailingCompletionClient()),
            question,
            {"recommendation": "same", "evidence_ids": ["ev_1", "ev_9999"]},
        )
        # ev_9999 was never supplied, so the adviser cannot cite it.
        assert adviser.assess(question).evidence_ids == ("ev_1",)


class TestDeterminism:
    def test_same_fixture_and_inputs_yield_byte_identical_assessment(self) -> None:
        question = _question()
        payload = {
            "recommendation": "same",
            "evidence_ids": ["ev_1"],
            "contradictions": ["c1"],
            "confidence": 0.8,
            "rationale": "shared identifier",
        }
        first = _recorded(IdentityAdviser(port=FailingCompletionClient()), question, payload).assess(question)
        second = _recorded(IdentityAdviser(port=FailingCompletionClient()), question, payload).assess(question)
        assert first.model_dump_json() == second.model_dump_json()

    def test_confidence_is_clamped(self) -> None:
        question = _question()
        adviser = _recorded(
            IdentityAdviser(port=FailingCompletionClient()),
            question,
            {"recommendation": "same", "confidence": 5.0},
        )
        assert adviser.assess(question).confidence == 1.0


class TestAssessmentIsInert:
    def test_assessment_fields_are_only_primitives(self) -> None:
        # There is no field capable of holding an operation/plan/batch.
        assessment = AdviserAssessment(
            adviser_type="identity",
            adviser_version="identity/1",
            model_id="m",
            model_version="1",
            prompt_version="1",
            recommendation="same",
            trace_id="t",
        )
        for value in assessment.model_dump().values():
            assert value is None or isinstance(value, (str, bool, int, float, tuple, list))


class TestParseFailureIsStructurallyGuarded:
    def test_a_raising_parse_abstains_rather_than_escaping(self) -> None:
        """Law 1 is structural, not incidental: even if an adviser's `_parse`
        raises, `assess` abstains instead of letting the exception reach the
        orchestrator and disturb the deterministic baseline."""

        class _BrokenParseAdviser(IdentityAdviser):
            def _parse(self, response, question):  # type: ignore[no-untyped-def]
                raise RuntimeError("boom in parse")

        response = CompletionResponse(
            text='{"recommendation": "same"}', model_id="m", model_version="1"
        )
        question = _question()
        # A recorded response keyed to THIS adviser's request, so the port
        # succeeds and control reaches the (raising) _parse.
        probe = _BrokenParseAdviser(port=FailingCompletionClient())
        request = probe.build_request(question)
        adviser = _BrokenParseAdviser(
            port=RecordedCompletionClient({request.request_hash: response})
        )
        assessment = adviser.assess(question)
        assert assessment.abstained is True
        assert "parse error" in (assessment.rationale or "")
