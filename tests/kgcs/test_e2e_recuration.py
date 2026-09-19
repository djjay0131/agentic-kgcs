"""E2E scenario 3 — FLAGSHIP evidence-driven re-curation (Wave 8).

    source A → curate → epoch N (a preferred assertion is committed)
    source B provides contradicting evidence → CurationTrigger → targeting finds
    the affected assertion → the bounded adviser reasons over the CITED evidence
    → policy selects an allowed action → a re-curation CurationPlan supersedes the
    old assertion → epoch N+1, with the OLD assertion still queryable (SUPERSEDED,
    bitemporal) and every change traceable to the trigger and its evidence.

The flow is parametrized over two source *shapes* — a research PAPER (a concept's
claimed year corrected by a second paper) and a NON-paper SENSOR (a device status
reading superseded by a later observation) — so the architecture is proven, not
the vocabulary (domain-neutral, DG-5).

Also proven end to end here:
- §9 law 1  — an LLM failure falls back to the deterministic baseline, which
  still commits (preserving both assertions, routing to review).
- §9 law 10 — supersession preserves history; conflict preserves both.
- §9 law 11 — targeting revisits only the affected assertion/identity.
- §9 law 14 — a human review decision converges on the same plan/executor path.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from kg_contracts.assertions import Assertion, ConflictStatus, CurationStatus
from kg_contracts.curation import CurationOperationType, ReviewAction, ReviewDecision
from kg_contracts.evidence import EvidenceRef, EvidenceRelationship
from kg_contracts.identity import new_identity_id
from kg_contracts.stores import GraphReadOptions
from kg_contracts.testing.factories import make_assertion

from e2e_harness import (
    PAPER_SHAPE,
    REGISTRY_CONFIDENCE_POLICY,
    SENSOR_SHAPE,
    SUPPORTED_WITH_RETRACT,
    E2EGraphStore,
    SourceShape,
    attribute_candidate,
    entity_candidate,
    new_assertion,
)
from kgcs import (
    Compensator,
    CurationEngine,
    ExecutionOutcome,
    FixedClock,
    InMemoryEpochPublisher,
    PlanExecutor,
)
from kgcs.advisers.base import AdviserAssessment, AdviserQuestion
from kgcs.advisers.completion import (
    CompletionResponse,
    FailingCompletionClient,
    RecordedCompletionClient,
)
from kgcs.advisers.specialists import AssertionAdviser, AssertionRecommendation
from kgcs.recuration import (
    ConceptEvolutionPlanner,
    CurationTrigger,
    EvolutionKind,
    EvolutionRouter,
    InMemoryDependencyIndex,
    TriggerKind,
)
from kgcs.review.operations import ReviewOutcomeStatus, ReviewProposal, ReviewRouter

_CLOCK = FixedClock(datetime(2026, 8, 22, tzinfo=UTC))
_SHAPES = [PAPER_SHAPE, SENSOR_SHAPE]
_SHAPE_IDS = [s.name for s in _SHAPES]


def _engine(graph_id: str, snapshot_version: str) -> CurationEngine:
    return CurationEngine.create(
        graph_id=graph_id,
        confidence_policy=REGISTRY_CONFIDENCE_POLICY,
        clock=_CLOCK,
        snapshot_version=snapshot_version,
    )


def _commit_source_a(shape: SourceShape, graph_id: str, store: E2EGraphStore) -> tuple[str, Assertion]:
    """Curate + execute source A: create the concept, attach its assertion.

    Returns the minted identity id and the committed (preferred) assertion.
    """
    executor = PlanExecutor(store, clock=_CLOCK)

    # Concept identity (epoch 1).
    entity = entity_candidate(shape, graph_id=graph_id)
    entity_plan = _engine(graph_id, "0").curate([entity]).plan
    assert entity_plan is not None
    assert executor.execute(entity_plan).outcome is ExecutionOutcome.COMMITTED
    identity_id = str(entity_plan.operations[0].reversal_data["identity_id"])

    # Preferred assertion A (epoch 2).
    attribute = attribute_candidate(
        shape,
        graph_id=graph_id,
        subject=identity_id,
        value=shape.value_a,
        evidence_id=shape.ev_a,
        cid=f"cand_{shape.name}_attr_a",
        trace=f"trace_{shape.name}_a",
    )
    attr_plan = _engine(graph_id, str(store.current_epoch())).curate([attribute]).plan
    assert attr_plan is not None
    assert executor.execute(attr_plan).outcome is ExecutionOutcome.COMMITTED

    (committed_a,) = store.assertions_for(identity_id)
    return identity_id, committed_a


def _recorded_assertion_adviser(
    question: AdviserQuestion, payload: dict[str, object]
) -> AssertionAdviser:
    probe = AssertionAdviser(port=RecordedCompletionClient({}))
    request = probe.build_request(question)
    response = CompletionResponse(
        text=json.dumps(payload), model_id="recorded/echo", model_version="1"
    )
    return AssertionAdviser(port=RecordedCompletionClient({request.request_hash: response}))


class TestFlagshipSupersession:
    @pytest.mark.parametrize("shape", _SHAPES, ids=_SHAPE_IDS)
    def test_new_evidence_supersedes_preserving_bitemporal_history(
        self, shape: SourceShape
    ) -> None:
        graph_id = f"flagship-{shape.name}"
        store = E2EGraphStore()

        # --- Source A: reach epoch N with a preferred assertion committed. ----
        identity_id, assertion_a = _commit_source_a(shape, graph_id, store)
        epoch_n = store.current_epoch()
        assert assertion_a.object_value == shape.value_a
        assert assertion_a.status is CurationStatus.ACTIVE

        # --- Source B: contradicting evidence arrives. ------------------------
        assertion_b = new_assertion(
            subject=identity_id,
            predicate=shape.attribute,
            value=shape.value_b,
            evidence_id=shape.ev_b,
            relationship=EvidenceRelationship.CONTRADICTS,
            assertion_id=f"as_{shape.name}_b",
            recorded_at=assertion_a.recorded_at + timedelta(days=1),
        )
        trigger = CurationTrigger.of(
            kind=TriggerKind.NEW_EVIDENCE,
            identity_ids=(identity_id,),
            assertion_ids=(assertion_a.assertion_id,),
            evidence_ids=(shape.ev_b,),
            trace_id=f"{shape.name}-source-b",
            reason="second source contradicts the preferred assertion",
        )

        # --- Targeting: find ONLY the affected assertion/identity (law 11). ---
        # Seed an unrelated identity/assertion in the index so the negative is
        # real: re-curation must not revisit knowledge the new evidence doesn't
        # touch, even though that knowledge is present in the index.
        unrelated_identity = new_identity_id(graph_id)
        index = InMemoryDependencyIndex(
            evidence_to_assertions={
                shape.ev_b: (assertion_a.assertion_id,),
                "ev_unrelated": ("as_unrelated",),
            },
            assertion_to_identity={
                assertion_a.assertion_id: identity_id,
                "as_unrelated": unrelated_identity,
            },
        )
        affected = index.affected_by_trigger(trigger)
        assert assertion_a.assertion_id in affected
        assert identity_id in affected
        assert "as_unrelated" not in affected  # present in the index, but untouched
        assert unrelated_identity not in affected

        # --- Adviser reasons over the CITED evidence → supersedes. ------------
        question = AdviserQuestion(
            kind="assertion",
            subject=assertion_a.assertion_id,
            other=assertion_b.assertion_id,
            evidence_ids=(shape.ev_a, shape.ev_b),
            trace_id=trigger.trace_id,
        )
        adviser = _recorded_assertion_adviser(
            question,
            {"recommendation": "supersedes", "evidence_ids": [shape.ev_b], "confidence": 0.9},
        )
        assessment = adviser.assess(question)
        assert assessment.recommendation == AssertionRecommendation.SUPERSEDES
        assert shape.ev_b in assessment.evidence_ids  # reasoned over cited evidence
        assert assessment.abstained is False

        # --- The adviser's recommendation CAUSALLY selects the action. --------
        # Product code, not test control flow: EvolutionRouter (the auto-path
        # analogue of the human ReviewRouter) maps the assessment's
        # recommendation to the evolution plan through a conservative gate. Had
        # the recorded response said "contradicts"/"insufficient", this would
        # produce a preserved conflict instead — the recommendation drives it.
        planner = ConceptEvolutionPlanner(
            snapshot_version=str(epoch_n),
            matcher_version="rules/1",
            adviser_version="assertion/1",
        )
        router = EvolutionRouter(planner=planner)
        result = router.route_assertion(
            recommendation=assessment.recommendation,
            old_assertion=assertion_a,
            new_assertion=assertion_b,
            trigger=trigger,
        )
        assert result.kind is EvolutionKind.SUPERSESSION
        assert result.plan is not None

        # --- Execute the re-curation plan → epoch N+1. ------------------------
        publisher = InMemoryEpochPublisher()
        executor = PlanExecutor(
            store,
            clock=_CLOCK,
            supported_operations=SUPPORTED_WITH_RETRACT,
            epoch_publisher=publisher,
        )
        record = executor.execute(result.plan)
        assert record.outcome is ExecutionOutcome.COMMITTED
        assert store.current_epoch() == epoch_n + 1
        assert publisher.published_epoch() == epoch_n + 1

        # New assertion B is the live preferred value.
        (live,) = store.assertions_for(identity_id)
        assert live.assertion_id == assertion_b.assertion_id
        assert live.object_value == shape.value_b
        assert live.status is CurationStatus.ACTIVE

        # Old assertion A is STILL queryable — SUPERSEDED, never deleted (law 10).
        with_history = store.assertions_for(
            identity_id, GraphReadOptions(include_superseded=True)
        )
        superseded = {a.assertion_id: a for a in with_history}
        assert assertion_a.assertion_id in superseded
        assert superseded[assertion_a.assertion_id].status is CurationStatus.SUPERSEDED
        assert superseded[assertion_a.assertion_id].superseded_at == assertion_b.recorded_at

        # Bitemporal: as of A's transaction time, the graph still shows A's value.
        as_of_a = store.assertions_for(
            identity_id,
            GraphReadOptions(transaction_at=assertion_a.recorded_at, include_superseded=True),
        )
        assert [a.object_value for a in as_of_a] == [shape.value_a]

        # The re-curation is fully reversible and every op traces to the trigger.
        assert Compensator().compensate(result.plan, against_snapshot=1).fully_compensable is True
        for op in result.plan.operations:
            assert op.reversal_data["trigger_id"] == trigger.trigger_id
            assert shape.ev_b in op.reversal_data["evidence_ids"]  # type: ignore[operator]

    @pytest.mark.parametrize("shape", _SHAPES, ids=_SHAPE_IDS)
    def test_every_committed_mutation_came_from_a_plan(self, shape: SourceShape) -> None:
        # §9 law 3, end to end: the store only ever advances inside execute().
        graph_id = f"law3-{shape.name}"
        store = E2EGraphStore()
        assert store.current_epoch() == 0
        _commit_source_a(shape, graph_id, store)
        assert store.current_epoch() == 2  # one epoch per executed plan, nothing else


class TestLlmFailureFallsBackToDeterministicBaseline:
    def test_failing_adviser_still_commits_preserving_both(self) -> None:
        # §9 laws 1 + 10: the adviser fails, so the deterministic baseline takes
        # the conservative CONFLICT route — both assertions preserved, routed to
        # review — and it STILL commits and advances the epoch.
        shape = PAPER_SHAPE
        graph_id = "law1-fallback"
        store = E2EGraphStore()
        identity_id, assertion_a = _commit_source_a(shape, graph_id, store)
        epoch_n = store.current_epoch()

        assertion_b = new_assertion(
            subject=identity_id,
            predicate=shape.attribute,
            value=shape.value_b,
            evidence_id=shape.ev_b,
            relationship=EvidenceRelationship.CONTRADICTS,
            assertion_id="as_paper_b_fallback",
            recorded_at=assertion_a.recorded_at + timedelta(days=1),
        )
        trigger = CurationTrigger.of(
            kind=TriggerKind.CONTRADICTION_DETECTED,
            identity_ids=(identity_id,),
            evidence_ids=(shape.ev_b,),
            trace_id="paper-b-fallback",
        )

        # The adviser is unavailable.
        question = AdviserQuestion(
            kind="assertion",
            subject=assertion_a.assertion_id,
            other=assertion_b.assertion_id,
            evidence_ids=(shape.ev_a, shape.ev_b),
            trace_id=trigger.trace_id,
        )
        assessment: AdviserAssessment = AssertionAdviser(
            port=FailingCompletionClient()
        ).assess(question)
        assert assessment.abstained is True  # no usable advice

        # Deterministic baseline: preserve BOTH, route to review — never guess.
        planner = ConceptEvolutionPlanner(
            snapshot_version=str(epoch_n), matcher_version="rules/1", adviser_version=None
        )
        result = planner.plan_conflict(
            subject_assertion=assertion_a, competing_assertion=assertion_b, trigger=trigger
        )
        assert result.review_required is True
        assert result.conflict_record is not None
        assert result.conflict_record.status is ConflictStatus.UNRESOLVED
        assert result.plan is not None

        executor = PlanExecutor(store, clock=_CLOCK, supported_operations=SUPPORTED_WITH_RETRACT)
        assert executor.execute(result.plan).outcome is ExecutionOutcome.COMMITTED
        assert store.current_epoch() == epoch_n + 1

        # Both assertions survive as ACTIVE — no overwrite destroyed evidence.
        live = store.assertions_for(identity_id)
        values = sorted(str(a.object_value) for a in live)
        assert values == sorted([str(shape.value_a), str(shape.value_b)])
        assert all(a.status is CurationStatus.ACTIVE for a in live)


class TestHumanReviewConvergesOnSamePipeline:
    def test_human_relabel_uses_the_same_planner_and_executor(self) -> None:
        # §9 law 14: a human review decision produces the SAME typed plan the auto
        # path would, and it commits through the SAME executor.
        shape = SENSOR_SHAPE
        graph_id = "law14"
        store = E2EGraphStore()
        identity_id, _ = _commit_source_a(shape, graph_id, store)
        epoch_n = store.current_epoch()

        planner = ConceptEvolutionPlanner(
            snapshot_version=str(epoch_n), matcher_version="rules/1", adviser_version="review/1"
        )
        label = make_assertion(
            subject_identity=identity_id,
            predicate="alias",
            object_value="rooftop-unit-42",
            evidence_refs=(
                EvidenceRef(evidence_id="ev_review", relationship=EvidenceRelationship.SUPPORTS),
            ),
        ).model_copy(update={"assertion_id": "as_review_label"})
        trigger = CurationTrigger.of(
            kind=TriggerKind.OPERATOR_REQUEST,
            identity_ids=(identity_id,),
            evidence_ids=("ev_review",),
            trace_id="operator-1",
        )

        # Auto path plan.
        auto = planner.plan_relabel(label_assertion=label, trigger=trigger)

        # Human path: a RELABEL review decision routed through the same planner.
        router = ReviewRouter(planner=planner)
        decision = ReviewDecision(
            item_id="rv_1",
            action=ReviewAction.RELABEL,
            actor="reviewer",
            decided_at=datetime(2026, 8, 21, tzinfo=UTC),
        )
        outcome = router.route(decision, ReviewProposal(trigger=trigger, label_assertion=label))
        assert outcome.status is ReviewOutcomeStatus.PLANNED
        assert outcome.kind is EvolutionKind.RELABEL
        assert outcome.plan is not None

        # Byte-identical to the auto plan, and it commits through the executor.
        assert outcome.plan == auto.plan
        assert outcome.plan.model_dump_json() == auto.plan.model_dump_json()
        executor = PlanExecutor(store, clock=_CLOCK, supported_operations=SUPPORTED_WITH_RETRACT)
        assert executor.execute(outcome.plan).outcome is ExecutionOutcome.COMMITTED
        assert store.current_epoch() == epoch_n + 1
        assert CurationOperationType.ATTACH_ASSERTION in {
            op.type for op in outcome.plan.operations
        }
