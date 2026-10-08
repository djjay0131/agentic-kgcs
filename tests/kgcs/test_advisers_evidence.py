"""Evidence *text* reaches adviser prompts (KGPS U7, KGCS issue #49).

The defect these tests pin: advisers rendered `evidence_ids: a, b` and nothing
else, and the identity question builder never filled `context`, so the LLM was
asked to judge support without seeing any evidence. The fix injects an optional
`EvidenceLookup` and renders each cited evidence — PRESENT text (bounded), an
explicit ABSENT/ERROR marker with its reason, and an explicit UNKNOWN marker for
an id the lookup does not hold.

Three things must hold, and each is tested here:

1. the evidence text actually reaches `build_request().prompt`;
2. the render is deterministic (a replayed fixture yields a byte-identical
   assessment) and is *recorded* on `AdviserAssessment` provenance;
3. with no lookup, the prompt is byte-identical to the pre-fix id-only render,
   so existing behaviour and recorded fixtures are untouched.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

from kg_contracts.evidence import (
    AbsenceReason,
    Evidence,
    EvidenceAvailability,
    EvidenceRef,
    EvidenceRelationship,
    Provenance,
    absent_evidence,
    error_evidence,
    present_evidence,
)
from kgis.evidence import SqliteEvidenceRegistry

from kgcs.advisers.base import AdviserQuestion
from kgcs.advisers.completion import (
    CompletionResponse,
    FailingCompletionClient,
    RecordedCompletionClient,
)
from kgcs.advisers.evidence import EvidenceLookup, render_evidence
from kgcs.advisers.orchestrator import CurationOrchestrator, _identity_question
from kgcs.advisers.specialists import IdentityAdviser
from kgcs.er.blocking import CandidatePair
from kgcs.er.matcher import CalibrationKey, MatchResult
from kgcs.er.resolution import ErAction, ErResolutionPolicy
from kgcs.profiles import default_profile

_NOW = datetime(2026, 10, 7, tzinfo=UTC)
_PROV = Provenance(source="test", actor="test")


class _DictLookup:
    """A minimal `EvidenceLookup` over an in-memory dict."""

    def __init__(self, by_id: dict[str, Evidence]) -> None:
        self._by_id = by_id

    def get(self, evidence_id: str) -> Evidence | None:
        return self._by_id.get(evidence_id)


def _lookup() -> _DictLookup:
    return _DictLookup(
        {
            "ev_present": present_evidence(
                evidence_id="ev_present",
                source_type="paper",
                source_locator="paper#1",
                observed_at=_NOW,
                provenance=_PROV,
                content="the paper reports an unprecedented effect",
            ),
            "ev_absent": absent_evidence(
                evidence_id="ev_absent",
                source_type="paper",
                source_locator="paper#2",
                observed_at=_NOW,
                reason=AbsenceReason.SOURCE_OMITTED,
                provenance=_PROV,
            ),
            "ev_error": error_evidence(
                evidence_id="ev_error",
                source_type="paper",
                source_locator="paper#3",
                observed_at=_NOW,
                error="connection refused",
                provenance=_PROV,
            ),
        }
    )


def _question(*evidence_ids: str) -> AdviserQuestion:
    return AdviserQuestion(
        kind="identity",
        subject="paper/a",
        other="paper/b",
        evidence_ids=evidence_ids,
        trace_id="t",
    )


# --- the lookup is the KGIS registry, structurally --------------------------


def test_sqlite_evidence_registry_satisfies_the_lookup_protocol() -> None:
    registry = SqliteEvidenceRegistry(":memory:")
    assert isinstance(registry, EvidenceLookup)


# --- the renderer -----------------------------------------------------------


class TestRenderEvidence:
    def test_present_renders_bounded_text(self) -> None:
        render = render_evidence(("ev_present",), lookup=_lookup(), max_chars=64)
        assert render.lines == (
            '[ev_present] relationship=UNKNOWN, availability=PRESENT: '
            '"the paper reports an unprecedented effect"',
        )
        assert render.rendered_ids == ("ev_present",)
        assert render.truncated is False

    def test_absent_and_error_are_explicit_markers(self) -> None:
        render = render_evidence(("ev_absent", "ev_error"), lookup=_lookup())
        assert "availability=ABSENT: reason=SOURCE_OMITTED" in render.lines[0]
        assert 'availability=ERROR: error="connection refused"' in render.lines[1]

    def test_missing_id_is_unknown_never_silently_dropped(self) -> None:
        render = render_evidence(("ev_missing",), lookup=_lookup())
        assert render.lines == (
            "[ev_missing] relationship=UNKNOWN, "
            "availability=UNKNOWN: not found in evidence lookup",
        )
        # It was rendered (an explicit marker), so provenance records it.
        assert render.rendered_ids == ("ev_missing",)

    def test_relationship_is_rendered_when_supplied(self) -> None:
        render = render_evidence(
            ("ev_present",),
            lookup=_lookup(),
            relationships={"ev_present": EvidenceRelationship.SUPPORTS.value},
        )
        assert "relationship=SUPPORTS" in render.lines[0]

    def test_truncation_is_explicit_and_budget_is_configurable(self) -> None:
        render = render_evidence(("ev_present",), lookup=_lookup(), max_chars=10)
        assert render.lines == (
            '[ev_present] relationship=UNKNOWN, availability=PRESENT: "the paper ..."',
        )
        assert render.truncated is True

    def test_span_quote_is_preferred_over_content(self) -> None:
        # `Evidence.span` lands with KGIS issue #56; the renderer reads it
        # defensively, so a duck-typed span proves the preference today.
        spanned = cast(
            Evidence,
            SimpleNamespace(
                availability=EvidenceAvailability.PRESENT,
                content="a much longer chunk of source text",
                payload_hash=None,
                span=SimpleNamespace(quote="the exact quoted sentence"),
            ),
        )
        render = render_evidence(("ev_span",), lookup=_DictLookup({"ev_span": spanned}))
        assert "the exact quoted sentence" in render.lines[0]
        assert "much longer chunk" not in render.lines[0]

    def test_render_is_deterministic(self) -> None:
        first = render_evidence(("ev_present", "ev_absent"), lookup=_lookup())
        second = render_evidence(("ev_present", "ev_absent"), lookup=_lookup())
        assert first == second


# --- the adviser pipeline ---------------------------------------------------


class TestAdviserRendersEvidenceText:
    def test_present_text_reaches_the_prompt(self) -> None:
        adviser = IdentityAdviser(port=RecordedCompletionClient({}), evidence_lookup=_lookup())
        prompt = adviser.build_request(_question("ev_present")).prompt
        assert "the paper reports an unprecedented effect" in prompt
        # The id-only line is retained; the detail line is added.
        assert "evidence_ids: ev_present" in prompt
        assert "[ev_present] relationship=UNKNOWN" in prompt

    def test_absent_marker_reaches_the_prompt(self) -> None:
        adviser = IdentityAdviser(port=RecordedCompletionClient({}), evidence_lookup=_lookup())
        prompt = adviser.build_request(_question("ev_absent")).prompt
        assert "availability=ABSENT: reason=SOURCE_OMITTED" in prompt

    def test_without_a_lookup_the_prompt_is_unchanged(self) -> None:
        adviser = IdentityAdviser(port=RecordedCompletionClient({}))
        prompt = adviser.build_request(_question("ev_present")).prompt
        assert "evidence_ids: ev_present" in prompt
        assert "availability=" not in prompt
        assert "[ev_present]" not in prompt

    def test_provenance_records_rendered_ids_and_truncation(self) -> None:
        question = _question("ev_present", "ev_absent")
        probe = IdentityAdviser(
            port=RecordedCompletionClient({}), evidence_lookup=_lookup(), evidence_max_chars=10
        )
        request = probe.build_request(question)
        response = CompletionResponse(
            text=json.dumps({"recommendation": "same", "evidence_ids": ["ev_present"]}),
            model_id="recorded/echo",
            model_version="1",
        )
        adviser = IdentityAdviser(
            port=RecordedCompletionClient({request.request_hash: response}),
            evidence_lookup=_lookup(),
            evidence_max_chars=10,
        )
        assessment = adviser.assess(question)
        assert assessment.rendered_evidence_ids == ("ev_present", "ev_absent")
        assert assessment.evidence_truncated is True

    def test_no_lookup_records_empty_rendered_ids(self) -> None:
        # No detail line was rendered, so provenance honestly records none.
        assessment = IdentityAdviser(port=FailingCompletionClient()).assess(
            _question("ev_present")
        )
        assert assessment.rendered_evidence_ids == ()
        assert assessment.evidence_truncated is False

    def test_abstain_still_records_rendered_evidence(self) -> None:
        assessment = IdentityAdviser(
            port=FailingCompletionClient(), evidence_lookup=_lookup()
        ).assess(_question("ev_present"))
        assert assessment.abstained is True
        assert assessment.rendered_evidence_ids == ("ev_present",)


# --- the orchestrator path --------------------------------------------------

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


def _recorded_adviser(match: MatchResult, *, evidence_ids: tuple[str, ...]) -> IdentityAdviser:
    lookup = _lookup()
    question = _identity_question(
        match, evidence_ids=evidence_ids, trace_id="t", evidence_lookup=lookup
    )
    probe = IdentityAdviser(port=RecordedCompletionClient({}), evidence_lookup=lookup)
    request = probe.build_request(question)
    response = CompletionResponse(
        text=json.dumps({"recommendation": "same", "evidence_ids": list(evidence_ids)}),
        model_id="recorded/echo",
        model_version="1",
    )
    return IdentityAdviser(
        port=RecordedCompletionClient({request.request_hash: response}), evidence_lookup=lookup
    )


class TestOrchestratorPassesEvidenceToTheModel:
    def test_identity_question_fills_evidence_text(self) -> None:
        question = _identity_question(
            _mr(), evidence_ids=("ev_present",), trace_id="t", evidence_lookup=_lookup()
        )
        assert "the paper reports an unprecedented effect" in "\n".join(question.evidence_context)
        assert question.rendered_evidence_ids == ("ev_present",)

    def test_orchestrator_lookup_reaches_the_prompt_and_folds_the_advice(self) -> None:
        match = _mr()
        # The adviser is recorded with the lookup-enabled prompt the orchestrator
        # will also build, so the fixture key matches exactly.
        adviser = _recorded_adviser(match, evidence_ids=("ev_present",))
        orchestrator = CurationOrchestrator(identity_adviser=adviser, evidence_lookup=_lookup())
        result = orchestrator.resolve(
            match, profile=default_profile(), evidence_ids=("ev_present",), trace_id="t"
        )
        assert result.consulted is True
        assert result.decision.action is ErAction.AUTO_LINK
        assert result.assessments[0].rendered_evidence_ids == ("ev_present",)

    def test_adviser_only_lookup_reaches_the_prompt(self) -> None:
        # The orchestrator has no lookup, but the adviser does: it renders the
        # evidence in `assess`/`build_request` and the fixture still matches.
        match = _mr()
        adviser = _recorded_adviser(match, evidence_ids=("ev_present",))
        orchestrator = CurationOrchestrator(identity_adviser=adviser)
        result = orchestrator.resolve(
            match, profile=default_profile(), evidence_ids=("ev_present",), trace_id="t"
        )
        assert result.decision.action is ErAction.AUTO_LINK
        assert result.assessments[0].rendered_evidence_ids == ("ev_present",)

    def test_lookup_on_both_renders_evidence_exactly_once(self) -> None:
        lookup = _lookup()
        question = _identity_question(
            _mr(), evidence_ids=("ev_present",), trace_id="t", evidence_lookup=lookup
        )
        adviser = IdentityAdviser(port=RecordedCompletionClient({}), evidence_lookup=lookup)
        prompt = adviser.build_request(question).prompt
        assert prompt.count("availability=PRESENT") == 1

    def test_evidence_relationships_are_threaded_from_refs(self) -> None:
        refs = (
            EvidenceRef(evidence_id="ev_present", relationship=EvidenceRelationship.SUPPORTS),
        )
        question = _identity_question(
            _mr(),
            evidence_ids=("ev_present",),
            trace_id="t",
            evidence_refs=refs,
            evidence_lookup=_lookup(),
        )
        assert "relationship=SUPPORTS" in "\n".join(question.evidence_context)


class TestReplayDeterminismWithEvidence:
    def test_recorded_fixture_replays_byte_identically(self) -> None:
        match = _mr()

        def run() -> str:
            adviser = _recorded_adviser(match, evidence_ids=("ev_present", "ev_absent"))
            orchestrator = CurationOrchestrator(identity_adviser=adviser, evidence_lookup=_lookup())
            result = orchestrator.resolve(
                match,
                profile=default_profile(),
                evidence_ids=("ev_present", "ev_absent"),
                trace_id="t",
            )
            return result.assessments[0].model_dump_json()

        assert run() == run()

    def test_baseline_is_untouched_when_the_lookup_is_absent(self) -> None:
        # Backwards-compatibility pin: the deterministic baseline is unchanged
        # when no evidence lookup is injected.
        match = _mr()
        baseline = ErResolutionPolicy().decide(match, profile=default_profile())
        result = CurationOrchestrator(identity_adviser=IdentityAdviser(port=FailingCompletionClient())).resolve(
            match, profile=default_profile(), evidence_ids=("ev_present",), trace_id="t"
        )
        assert result.decision.model_dump_json() == baseline.model_dump_json()
