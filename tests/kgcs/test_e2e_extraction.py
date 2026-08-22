"""E2E scenario 2 — document + LLM extraction (recorded), ER adviser path.

    document → (KGIS LLM extraction, represented by its contract output) →
    candidate ledger → KGCS ER orchestrator (a bounded adviser reasoning over
    cited evidence via a RecordedCompletionClient) → canonical graph.

Real `kgis` is structured-mode only in v1 (no LLM extraction stage), so the
KGIS *LLM-extraction* side is represented here by its contract output — the
`Candidate`s a document extractor would emit — which is exactly the seam the
architecture defines. The LLM reasoning that IS exercised is KGCS's own bounded
identity adviser, driven deterministically by a `RecordedCompletionClient`.

Also encodes §9 laws 6 and 17 end to end: the recorded decision replays
byte-identically (`observability.replay`), and the semantic audit record
explains which baseline, adviser, evidence, policy, and versions produced it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from e2e_harness import REGISTRY_CONFIDENCE_POLICY, entity_candidate
from kg_contracts.testing.memory import MemoryCandidateSink, MemoryGraphStore
from kgcs.advisers.completion import (
    CompletionResponse,
    FailingCompletionClient,
    RecordedCompletionClient,
)
from kgcs.advisers.orchestrator import CurationOrchestrator, _identity_question
from kgcs.advisers.specialists import IdentityAdviser
from kgcs.er.blocking import CandidatePair
from kgcs.er.matcher import CalibrationKey, MatchResult
from kgcs.er.resolution import ErAction, ErResolutionPolicy
from kgcs.observability import ReplayInputs, SemanticAuditBuilder, replay
from kgcs.profiles import default_profile

from kgcs import (
    CurationEngine,
    ExecutionOutcome,
    FixedClock,
    PlanExecutor,
)
from e2e_harness import PAPER_SHAPE

GRAPH = "papers"
_TRACE = "trace-doc-extraction"
_CLOCK = FixedClock(datetime(2026, 8, 22, tzinfo=UTC))

# Two document mentions the extractor produced for one real concept.
_PAIR = CandidatePair.of("concept/mention-a", "concept/mention-b")
_KEY = CalibrationKey.of(
    graph_id=GRAPH,
    entity_type="Concept",
    source_pair=("doc_a", "doc_b"),
    matcher_version="rules/1",
    consequence_class="standard",
)


def _match(probability: float = 0.90) -> MatchResult:
    return MatchResult(
        pair=_PAIR,
        probability=probability,
        matcher_version="rules/1",
        feature_vector={"identifier_agreement": 1.0, "mutually_exclusive": 0.0},
        calibration_key=_KEY,
    )


def _recorded_identity_adviser(match: MatchResult, payload: dict[str, object]) -> IdentityAdviser:
    """An `IdentityAdviser` whose one recorded response answers this exact pair."""
    question = _identity_question(match, evidence_ids=("ev_doc",), trace_id=_TRACE)
    request = IdentityAdviser(port=RecordedCompletionClient({})).build_request(question)
    response = CompletionResponse(
        text=json.dumps(payload), model_id="recorded/echo", model_version="1"
    )
    return IdentityAdviser(port=RecordedCompletionClient({request.request_hash: response}))


class TestAdviserReasonsThenCommits:
    def test_recorded_adviser_confirms_same_and_entity_reaches_canonical_graph(self) -> None:
        match = _match()
        adviser = _recorded_identity_adviser(
            match, {"recommendation": "same", "evidence_ids": ["ev_doc"], "confidence": 0.95}
        )
        orch = CurationOrchestrator(identity_adviser=adviser)

        # The bounded adviser reasons over the cited evidence; the deterministic
        # gate folds it in and upgrades LLM_ASSESS → AUTO_LINK.
        result = orch.resolve(
            match, profile=default_profile(), evidence_ids=("ev_doc",), trace_id=_TRACE
        )
        assert result.consulted is True
        assert result.baseline.action is ErAction.LLM_ASSESS
        assert result.decision.action is ErAction.AUTO_LINK
        assert result.decision.evidence_ids == ("ev_doc",)  # cites evidence (§9 law 9)

        # ER concluded the two mentions are ONE concept → one canonical identity
        # is committed through the plan/executor path (never by the adviser).
        ledger = MemoryCandidateSink()
        store = MemoryGraphStore()
        executor = PlanExecutor(store, clock=_CLOCK)
        entity = entity_candidate(PAPER_SHAPE, graph_id=GRAPH)
        ledger.submit([entity])
        engine = CurationEngine.create(
            graph_id=GRAPH, confidence_policy=REGISTRY_CONFIDENCE_POLICY, clock=_CLOCK
        )
        curated = engine.curate([entity])
        assert curated.plan is not None
        assert executor.execute(curated.plan).outcome is ExecutionOutcome.COMMITTED
        assert store.current_epoch() == 1
        assert store.find_entities(entity_type="Concept")

    def test_adviser_holds_no_graph_write_surface(self) -> None:
        # §9 law 16: the adviser's public output is an inert decision, not a
        # GraphMutationBatch — it has no method to construct or apply one.
        match = _match()
        adviser = _recorded_identity_adviser(match, {"recommendation": "same"})
        result = CurationOrchestrator(identity_adviser=adviser).resolve(
            match, profile=default_profile(), evidence_ids=("ev_doc",), trace_id=_TRACE
        )
        assert not hasattr(result.decision, "apply")
        assert not hasattr(adviser, "apply")


class TestDeterministicReplayAndAudit:
    def _build_record(self):  # type: ignore[no-untyped-def]
        match = _match()
        adviser = _recorded_identity_adviser(
            match, {"recommendation": "same", "evidence_ids": ["ev_doc"]}
        )
        result = CurationOrchestrator(identity_adviser=adviser).resolve(
            match, profile=default_profile(), evidence_ids=("ev_doc",), trace_id=_TRACE
        )
        replay_inputs = ReplayInputs(
            match_result=match,
            profile=default_profile(),
            evidence_ids=("ev_doc",),
            trace_id=_TRACE,
        )
        record = SemanticAuditBuilder().build(result, replay_inputs)
        return record, adviser

    def test_recorded_decision_replays_byte_identically(self) -> None:
        # §9 law 6: same recorded fixture + same inputs → same assessment/decision.
        record, adviser = self._build_record()
        outcome = replay(record, identity_adviser=adviser)
        assert outcome.reproduced is True
        assert outcome.divergence is None
        assert outcome.consulted_adviser is True
        assert outcome.replayed_final_action == ErAction.AUTO_LINK.value

    def test_semantic_audit_explains_the_decision(self) -> None:
        # §9 law 17: the audit names baseline, adviser, evidence, policy, versions.
        record, _ = self._build_record()
        assert record.baseline.action is ErAction.LLM_ASSESS
        assert record.final.action is ErAction.AUTO_LINK
        assert record.consulted_adviser is True
        assert record.assessments[0].evidence_ids == ("ev_doc",)
        assert record.assessments[0].baseline_action_before == ErAction.LLM_ASSESS.value
        assert record.assessments[0].final_action_after == ErAction.AUTO_LINK.value
        assert record.versions.matcher_version == "rules/1"
        assert record.versions.model_id == "recorded/echo"
        assert record.versions.policy_version == "1"
        assert record.trace_id == _TRACE

    def test_llm_failure_leaves_the_deterministic_baseline_intact(self) -> None:
        # §9 law 1: swap in a failing client — the decision is exactly the
        # deterministic baseline, and replay without the (failed) adviser
        # reproduces it. The baseline never weakens.
        match = _match()
        orch = CurationOrchestrator(
            identity_adviser=IdentityAdviser(port=FailingCompletionClient())
        )
        result = orch.resolve(
            match, profile=default_profile(), evidence_ids=("ev_doc",), trace_id=_TRACE
        )
        baseline = ErResolutionPolicy().decide(match, profile=default_profile())
        assert result.decision.model_dump_json() == baseline.model_dump_json()
        assert result.assessments[0].abstained is True
