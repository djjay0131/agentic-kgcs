"""Replay reproduces adviser decisions rendered with evidence text (issue #56).

Cross-PR follow-up to #49/#50/#51. A decision made with an `EvidenceLookup`
renders evidence text into the adviser prompt (prompt version ``2+evidence``),
which changes the completion request hash. Before #56 `ReplayInputs` captured
only the cited ids, so replay rebuilt an id-only prompt, missed the recorded
fixture, and raised `CompletionMiss` — the decision could not be reproduced.

`ReplayInputs.rendered_evidence` now captures the exact block the adviser saw
(ids, relationship, availability marker, truncated text, `evidence_truncated`,
`unresolved_evidence_ids`), so replay rebuilds the identical prompt **without**
the live registry. Three guarantees are pinned here:

1. an evidence-rendered decision persisted to `SqliteSemanticAuditSink` and
   reopened replays byte-identically (no lookup supplied to replay);
2. a record that predates the field still loads and replays as before;
3. tampered captured evidence is reported as divergence, never hidden.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from kg_contracts.evidence import Evidence, Provenance, present_evidence

from kgcs.advisers.completion import CompletionResponse, RecordedCompletionClient
from kgcs.advisers.evidence import EvidenceLookup
from kgcs.advisers.orchestrator import CurationOrchestrator, _identity_question
from kgcs.advisers.specialists import IdentityAdviser
from kgcs.er.blocking import CandidatePair
from kgcs.er.matcher import CalibrationKey, MatchResult
from kgcs.er.resolution import ErAction
from kgcs.observability import (
    ReplayInputs,
    SemanticAuditBuilder,
    SemanticAuditRecord,
    replay,
)
from kgcs.persistence import SqliteSemanticAuditSink
from kgcs.profiles import default_profile

_TRACE = "trace-replay-evidence"
_NOW = datetime(2026, 10, 7, tzinfo=UTC)
_PAIR = CandidatePair.of("paper/a", "paper/b")
_KEY = CalibrationKey.of(
    graph_id="g1",
    entity_type="Paper",
    source_pair=("source_a", "source_b"),
    matcher_version="rules/1",
    consequence_class="standard",
)


class _DictLookup:
    """A minimal `EvidenceLookup` over an in-memory dict (the live registry)."""

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
                provenance=Provenance(source="test", actor="test"),
                content="the paper reports an unprecedented effect",
            ),
        }
    )


def _mr(probability: float = 0.90) -> MatchResult:
    return MatchResult(
        pair=_PAIR,
        probability=probability,
        matcher_version="rules/1",
        feature_vector={"mutually_exclusive": 0.0},
        calibration_key=_KEY,
    )


def _recorded_adviser(
    mr: MatchResult,
    evidence_ids: tuple[str, ...],
    *,
    lookup: EvidenceLookup | None = None,
) -> IdentityAdviser:
    """An adviser whose port replays a `same` assessment for the built prompt.

    The fixture key is the request hash of the question built with (or without)
    the lookup, so a replay that rebuilds the same prompt matches it.
    """
    question = _identity_question(
        mr, evidence_ids=evidence_ids, trace_id=_TRACE, evidence_lookup=lookup
    )
    request = IdentityAdviser(port=RecordedCompletionClient({})).build_request(question)
    response = CompletionResponse(
        text=json.dumps({"recommendation": "same", "evidence_ids": list(evidence_ids)}),
        model_id="recorded/echo",
        model_version="1",
    )
    return IdentityAdviser(port=RecordedCompletionClient({request.request_hash: response}))


def _replay_inputs(mr: MatchResult) -> ReplayInputs:
    return ReplayInputs(
        match_result=mr,
        profile=default_profile(),
        evidence_ids=("ev_present",),
        trace_id=_TRACE,
    )


def _evidence_record() -> SemanticAuditRecord:
    """A recorded AUTO_LINK decision whose prompt carried rendered evidence."""
    mr = _mr()
    adviser = _recorded_adviser(mr, ("ev_present",), lookup=_lookup())
    result = CurationOrchestrator(identity_adviser=adviser, evidence_lookup=_lookup()).resolve(
        mr, profile=default_profile(), evidence_ids=("ev_present",), trace_id=_TRACE
    )
    assert result.decision.action is ErAction.AUTO_LINK  # advice was folded in
    return SemanticAuditBuilder().build(result, _replay_inputs(mr))


def _id_only_record() -> SemanticAuditRecord:
    """A recorded AUTO_LINK decision from the pre-#56 id-only prompt path."""
    mr = _mr()
    adviser = _recorded_adviser(mr, ("ev_present",))  # no lookup → id-only prompt
    result = CurationOrchestrator(identity_adviser=adviser).resolve(
        mr, profile=default_profile(), evidence_ids=("ev_present",), trace_id=_TRACE
    )
    assert result.decision.action is ErAction.AUTO_LINK
    return SemanticAuditBuilder().build(result, _replay_inputs(mr))


class TestReplayFromDurableSink:
    def test_evidence_record_survives_reopen_and_replays_without_the_registry(
        self, tmp_path
    ) -> None:
        record = _evidence_record()
        # The block is captured verbatim, so replay needs no live lookup.
        captured = record.replay_inputs.rendered_evidence
        assert captured is not None
        assert any("availability=PRESENT" in line for line in captured.context)
        assert captured.rendered_ids == ("ev_present",)

        path = tmp_path / "evidence.sqlite3"
        conn = sqlite3.connect(path)
        SqliteSemanticAuditSink(conn).record(record)
        conn.close()

        reopened = sqlite3.connect(path)
        (restored,) = SqliteSemanticAuditSink(reopened).records()
        assert restored == record

        # No lookup anywhere on the replay path: the captured block is sufficient.
        adviser = _recorded_adviser(_mr(), ("ev_present",), lookup=_lookup())
        outcome = replay(restored, identity_adviser=adviser)
        assert outcome.reproduced is True
        assert outcome.divergence is None
        assert outcome.consulted_adviser is True
        assert outcome.replayed_final_action == ErAction.AUTO_LINK.value

    def test_replay_rebuilds_the_identical_request_hash(self) -> None:
        # A replay with an adviser fixture keyed by the *live* rendered prompt
        # matches, and the replayed decision is byte-identical to the recorded one.
        record = _evidence_record()
        adviser = _recorded_adviser(_mr(), ("ev_present",), lookup=_lookup())
        outcome = replay(record, identity_adviser=adviser)
        assert outcome.reproduced is True
        assert record.final.action is ErAction.AUTO_LINK

    def test_rebuilding_the_question_from_capture_yields_the_same_request_hash(self) -> None:
        # The explicit statement of "same request hash": the live question (built
        # with the registry) and the replay question (built from the captured
        # block, registry-free) render the identical prompt and request hash.
        record = _evidence_record()
        captured = record.replay_inputs.rendered_evidence
        assert captured is not None

        live = _identity_question(
            _mr(), evidence_ids=("ev_present",), trace_id=_TRACE, evidence_lookup=_lookup()
        )
        replayed = _identity_question(
            _mr(), evidence_ids=("ev_present",), trace_id=_TRACE, rendered_evidence=captured
        )
        adviser = IdentityAdviser(port=RecordedCompletionClient({}))
        live_request = adviser.build_request(live)
        replay_request = adviser.build_request(replayed)
        assert replay_request.prompt == live_request.prompt
        assert replay_request.request_hash == live_request.request_hash


class TestBackwardCompatibility:
    def test_an_id_only_record_without_the_field_replays_as_before(self) -> None:
        record = _id_only_record()
        assert record.replay_inputs.rendered_evidence is None  # no evidence rendered
        data = json.loads(record.model_dump_json())
        # A pre-#56 record carried no rendered-evidence capture at all.
        del data["replay_inputs"]["rendered_evidence"]
        legacy = SemanticAuditRecord.model_validate_json(json.dumps(data))
        assert legacy.replay_inputs.rendered_evidence is None

        adviser = _recorded_adviser(_mr(), ("ev_present",))
        outcome = replay(legacy, identity_adviser=adviser)
        assert outcome.reproduced is True

    def test_a_deterministic_record_without_the_field_still_replays(self) -> None:
        mr = _mr(0.999)
        result = CurationOrchestrator().resolve(mr, profile=default_profile(), trace_id=_TRACE)
        record = SemanticAuditBuilder().build(
            result, ReplayInputs(match_result=mr, profile=default_profile(), trace_id=_TRACE)
        )
        assert record.replay_inputs.rendered_evidence is None
        data = json.loads(record.model_dump_json())
        del data["replay_inputs"]["rendered_evidence"]
        legacy = SemanticAuditRecord.model_validate_json(json.dumps(data))
        assert replay(legacy).reproduced is True


class TestTamperedEvidenceIsDivergence:
    def test_altered_captured_evidence_is_reported_not_hidden(self) -> None:
        record = _evidence_record()
        adviser = _recorded_adviser(_mr(), ("ev_present",), lookup=_lookup())
        assert replay(record, identity_adviser=adviser).reproduced is True

        original = record.replay_inputs.rendered_evidence
        assert original is not None
        forged = original.model_copy(
            update={
                "context": (
                    '[ev_present] relationship=SUPPORTS, availability=PRESENT: "forged text"',
                )
            }
        )
        tampered = record.model_copy(
            update={
                "replay_inputs": record.replay_inputs.model_copy(
                    update={"rendered_evidence": forged}
                )
            }
        )
        outcome = replay(tampered, identity_adviser=adviser)
        assert outcome.reproduced is False
        assert outcome.divergence is not None
        assert outcome.replayed_final_action == ""
