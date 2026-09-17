"""E2E scenario 6 — client-authoritative / reject-only identity (Issue #2, law 13).

A `CLIENT_AUTHORITATIVE` profile must never silently repair or merge identity.
End to end:

- two client-provided identities are committed as SEPARATE canonical entities;
- ER over that pair — even a confident, adviser-endorsed "same" — is clamped to
  at most `PROPOSE_LINK` (`POSSIBLY_SAME_AS`), never `AUTO_LINK`, so no merge plan
  is ever generated and the two identities stay distinct in the graph;
- a malformed reference is `REJECT`ed outright, never auto-repaired.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from kg_contracts.identity import IdentityLinkKind
from kg_contracts.testing.factories import make_entity_candidate, make_scores
from kg_contracts.testing.memory import MemoryGraphStore

from e2e_harness import PAPER_SHAPE, REGISTRY_CONFIDENCE_POLICY, entity_candidate
from kgcs import (
    CurationEngine,
    ExecutionOutcome,
    FixedClock,
    PlanExecutor,
)
from kgcs.advisers.completion import CompletionResponse, RecordedCompletionClient
from kgcs.advisers.orchestrator import CurationOrchestrator, _identity_question
from kgcs.advisers.specialists import IdentityAdviser
from kgcs.er.blocking import CandidatePair
from kgcs.er.matcher import CalibrationKey, MatchResult
from kgcs.er.resolution import ErAction, ErResolutionPolicy
from kgcs.profiles import client_authoritative_profile, default_profile

GRAPH = "client"
_CLOCK = FixedClock(datetime(2026, 8, 22, tzinfo=UTC))
_PAIR = CandidatePair.of("client/id-a", "client/id-b")
_KEY = CalibrationKey.of(
    graph_id=GRAPH,
    entity_type="Client",
    source_pair=("client_a", "client_b"),
    matcher_version="rules/1",
    consequence_class="identity_critical",
)


def _match(probability: float) -> MatchResult:
    return MatchResult(
        pair=_PAIR,
        probability=probability,
        matcher_version="rules/1",
        feature_vector={"identifier_agreement": 1.0, "mutually_exclusive": 0.0},
        calibration_key=_KEY,
    )


def _recorded_same_adviser(match: MatchResult) -> IdentityAdviser:
    question = _identity_question(match, evidence_ids=("ev_1",), trace_id="t")
    request = IdentityAdviser(port=RecordedCompletionClient({})).build_request(question)
    response = CompletionResponse(
        text=json.dumps({"recommendation": "same", "evidence_ids": ["ev_1"], "confidence": 0.99}),
        model_id="recorded/echo",
        model_version="1",
    )
    return IdentityAdviser(port=RecordedCompletionClient({request.request_hash: response}))


class TestNoSilentRepairOrMerge:
    def test_confident_same_is_clamped_to_a_proposal_never_auto_link(self) -> None:
        # §9 law 13: a probability that AUTO_LINKs under the open profile...
        match = _match(0.95)
        assert (
            ErResolutionPolicy().decide(match, profile=default_profile()).action
            is ErAction.LLM_ASSESS
        )

        # ...and an adviser that says "same" with high confidence...
        orch = CurationOrchestrator(identity_adviser=_recorded_same_adviser(match))
        result = orch.resolve(
            match, profile=client_authoritative_profile(), evidence_ids=("ev_1",), trace_id="t"
        )

        # ...still cannot merge under CLIENT_AUTHORITATIVE: at most a proposal.
        assert result.decision.action is ErAction.PROPOSE_LINK
        assert result.decision.action is not ErAction.AUTO_LINK
        assert result.decision.link_kind is IdentityLinkKind.POSSIBLY_SAME_AS

    def test_malformed_reference_is_rejected_not_repaired(self) -> None:
        decision = ErResolutionPolicy().decide(
            _match(0.99), profile=client_authoritative_profile(), malformed=True
        )
        assert decision.action is ErAction.REJECT
        assert decision.link_kind is None  # nothing linked, nothing repaired

    def test_two_client_identities_stay_separate_in_the_graph(self) -> None:
        # The two client identities are committed as distinct entities...
        store = MemoryGraphStore()
        executor = PlanExecutor(store, clock=_CLOCK)

        a = entity_candidate(PAPER_SHAPE, graph_id=GRAPH)
        b = make_entity_candidate(
            graph_id=GRAPH,
            key="client-b",
            entity_type=PAPER_SHAPE.entity_type,
            scores=make_scores(extraction_confidence=1.0, source_reliability=0.99),
        ).model_copy(update={"candidate_id": "cand_client_b", "trace_id": "trace_b"})
        for candidate in (a, b):
            engine = CurationEngine.create(
                graph_id=GRAPH,
                confidence_policy=REGISTRY_CONFIDENCE_POLICY,
                clock=_CLOCK,
                snapshot_version=str(store.current_epoch()),
            )
            plan = engine.curate([candidate]).plan
            assert plan is not None
            assert executor.execute(plan).outcome is ExecutionOutcome.COMMITTED

        assert len(store.find_entities(entity_type=PAPER_SHAPE.entity_type)) == 2

        # ...and ER proposes a link but never merges them, so both remain.
        result = CurationOrchestrator(
            identity_adviser=_recorded_same_adviser(_match(0.95))
        ).resolve(
            _match(0.95),
            profile=client_authoritative_profile(),
            evidence_ids=("ev_1",),
            trace_id="t",
        )
        assert result.decision.action is not ErAction.AUTO_LINK
        # No merge plan exists to execute, so the graph is unchanged: two entities.
        assert len(store.find_entities(entity_type=PAPER_SHAPE.entity_type)) == 2
        assert store.current_epoch() == 2  # exactly the two client CREATE_IDENTITYs
