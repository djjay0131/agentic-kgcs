"""Compensating-plan generation: inverse map, ordering, non-compensable ops.

Proves invariant 8: every operation is either compensable (an inverse is
emitted) or explicitly declared non-compensable, and a compensation executed
against the reference store fails explicitly rather than crashing.

`TestSnapshotGuard` and `TestInversePayloadIsWellFormed` pin ADR-0018: a
compensating plan asserts the state it expects to find *now*, and its inverse
operations carry payloads a store can actually apply. Before that ADR the
compensating plan carried the *source* plan's snapshot guard — false by
construction, because the source plan's own commit is what invalidated it — so
every compensation was rejected `STALE` before its payload was ever looked at.
"""

from datetime import UTC, datetime

import pytest
from kg_contracts.assertions import Assertion, CurationStatus
from kg_contracts.candidates import CandidateScores
from kg_contracts.curation import (
    CurationOperation,
    CurationOperationType,
    CurationPlan,
    Precondition,
)
from kg_contracts.testing.factories import (
    make_assertion,
    make_attribute_candidate,
    make_entity_candidate,
    new_identity_id,
)
from kg_contracts.testing.memory import MemoryGraphStore

from helpers import GRAPH_ID
from kgcs import (
    INVERSE_PAYLOAD_KEY,
    Compensator,
    CurationEngine,
    ExecutionOutcome,
    FixedClock,
    PlanExecutor,
)
from kgcs.planner import SNAPSHOT_PRECONDITION_KIND
from kgcs.recuration.evolution import AssertionReassignment, ConceptEvolutionPlanner
from kgcs.recuration.triggers import CurationTrigger, TriggerKind

#: Every op type, so a compensation is not short-circuited as unsupported
#: before its preconditions and payloads are reached.
ALL_OPERATIONS = frozenset(CurationOperationType)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 8, 22, tzinfo=UTC))


@pytest.fixture
def engine(clock: FixedClock) -> CurationEngine:
    return CurationEngine.create(graph_id=GRAPH_ID, clock=clock)


def _handbuilt(op_type: CurationOperationType) -> CurationPlan:
    return CurationPlan(
        plan_id=f"pl_{op_type.value.lower()}",
        candidate_ids=("cand_1",),
        snapshot_version="0",
        operations=(
            CurationOperation(
                operation_id=f"op_{op_type.value.lower()}",
                type=op_type,
                payload={"forward": "payload"},
                reversal_data={"undo": "data"},
            ),
        ),
        preconditions=(),
        evidence_ids=(),
        policy_version="1",
    )


class TestInverseMap:
    def test_attach_assertion_inverts_to_retract(
        self, engine: CurationEngine, auto_scores: CandidateScores
    ) -> None:
        plan = engine.curate(
            [make_attribute_candidate(graph_id=GRAPH_ID, scores=auto_scores)]
        ).plan
        assert plan is not None
        result = Compensator().compensate(plan, against_snapshot=1)

        assert result.fully_compensable
        assert result.plan is not None
        (op,) = result.plan.operations
        assert op.type is CurationOperationType.RETRACT_ASSERTION
        # payload is the original attach's inverse payload, which names the
        # assertion a retract must target
        assert "assertion_id" in op.payload
        assert op.reversal_data["original_type"] == "ATTACH_ASSERTION"

    def test_create_identity_is_non_compensable(
        self, engine: CurationEngine, auto_scores: CandidateScores
    ) -> None:
        plan = engine.curate(
            [make_entity_candidate(graph_id=GRAPH_ID, scores=auto_scores)]
        ).plan
        assert plan is not None
        result = Compensator().compensate(plan, against_snapshot=1)

        assert not result.fully_compensable
        assert result.plan is None  # the only op had no inverse
        assert len(result.non_compensable) == 1
        assert result.non_compensable[0].type is CurationOperationType.CREATE_IDENTITY

    @pytest.mark.parametrize(
        ("forward", "inverse"),
        [
            (CurationOperationType.ATTACH_ASSERTION, CurationOperationType.RETRACT_ASSERTION),
            (CurationOperationType.RETRACT_ASSERTION, CurationOperationType.ATTACH_ASSERTION),
            (CurationOperationType.MERGE_IDENTITIES, CurationOperationType.SPLIT_IDENTITY),
            (CurationOperationType.SPLIT_IDENTITY, CurationOperationType.MERGE_IDENTITIES),
            (CurationOperationType.REASSIGN_ASSERTION, CurationOperationType.REASSIGN_ASSERTION),
        ],
    )
    def test_compensable_types_map_to_their_inverse(
        self, forward: CurationOperationType, inverse: CurationOperationType
    ) -> None:
        result = Compensator().compensate(_handbuilt(forward), against_snapshot=1)
        assert result.plan is not None
        (op,) = result.plan.operations
        assert op.type is inverse
        # An op whose producer predates INVERSE_PAYLOAD_KEY still compensates:
        # the whole reversal_data is used as the inverse payload (back-compat).
        assert op.payload == {"undo": "data"}
        assert op.reversal_data["original_payload"] == {"forward": "payload"}

    def test_inverse_payload_key_wins_over_the_rest_of_reversal_data(self) -> None:
        # The lineage/provenance that shares reversal_data must never reach the
        # inverse operation's payload (ADR-0018).
        plan = CurationPlan(
            plan_id="pl_keyed",
            candidate_ids=("cand_1",),
            snapshot_version="0",
            operations=(
                CurationOperation(
                    operation_id="op_keyed",
                    type=CurationOperationType.MERGE_IDENTITIES,
                    payload={"survivor_identity": "kg://g1/identity/AAA"},
                    reversal_data={
                        INVERSE_PAYLOAD_KEY: {"premerge_members": ["a", "b"]},
                        "trigger_id": "trg_1",
                        "evidence_ids": ["ev_1"],
                    },
                ),
            ),
            preconditions=(),
            evidence_ids=(),
            policy_version="1",
        )
        result = Compensator().compensate(plan, against_snapshot=1)
        assert result.plan is not None
        (op,) = result.plan.operations
        assert op.payload == {"premerge_members": ["a", "b"]}
        assert "trigger_id" not in op.payload
        assert "evidence_ids" not in op.payload

    def test_compensating_a_compensation_returns_the_original_payload(self) -> None:
        # The inverse's own reversal_data offers the forward payload as *its*
        # inverse payload, so a rollback is itself rollback-able.
        first = Compensator().compensate(
            _handbuilt(CurationOperationType.ATTACH_ASSERTION), against_snapshot=1
        )
        assert first.plan is not None
        second = Compensator().compensate(first.plan, against_snapshot=1)
        assert second.plan is not None
        (op,) = second.plan.operations
        assert op.type is CurationOperationType.ATTACH_ASSERTION
        assert op.payload == {"forward": "payload"}

    @pytest.mark.parametrize(
        "non_inverse",
        [CurationOperationType.CREATE_IDENTITY, CurationOperationType.PROMOTE_ONTOLOGY_TERM],
    )
    def test_non_compensable_types(self, non_inverse: CurationOperationType) -> None:
        result = Compensator().compensate(_handbuilt(non_inverse), against_snapshot=1)
        assert result.plan is None
        assert result.non_compensable[0].type is non_inverse


class TestSnapshotGuard:
    """ADR-0018: the guard names the state the compensation expects NOW."""

    def _committed(
        self, engine: CurationEngine, auto_scores: CandidateScores, clock: FixedClock
    ) -> tuple[MemoryGraphStore, PlanExecutor, CurationPlan, int]:
        store = MemoryGraphStore()
        executor = PlanExecutor(store, clock=clock, supported_operations=ALL_OPERATIONS)
        plan = engine.curate(
            [make_attribute_candidate(graph_id=GRAPH_ID, scores=auto_scores)]
        ).plan
        assert plan is not None
        record = executor.execute(plan)
        assert record.outcome is ExecutionOutcome.COMMITTED
        assert record.new_epoch is not None
        return store, executor, plan, record.new_epoch

    def test_guard_is_rebased_onto_the_epoch_the_source_plan_committed_at(
        self, engine: CurationEngine, auto_scores: CandidateScores, clock: FixedClock
    ) -> None:
        store, _executor, plan, new_epoch = self._committed(engine, auto_scores, clock)
        # The source plan was computed against epoch 0 and committed at 1.
        assert [p.expected for p in plan.preconditions] == ["0"]
        assert new_epoch == 1

        result = Compensator().compensate(plan, against_snapshot=new_epoch)
        assert result.plan is not None
        snapshot_guards = [
            p for p in result.plan.preconditions if p.kind == SNAPSHOT_PRECONDITION_KIND
        ]
        # Same kind, same subject — a *new* expectation.
        assert [(p.subject, p.expected) for p in snapshot_guards] == [
            (plan.preconditions[0].subject, "1")
        ]
        assert result.plan.snapshot_version == "1"
        assert store.current_epoch() == 1

    def test_compensation_commits_against_the_state_the_source_plan_produced(
        self, engine: CurationEngine, auto_scores: CandidateScores, clock: FixedClock
    ) -> None:
        # The regression this whole change exists for: before ADR-0018 the
        # carried guard expected epoch 0 while the graph sat at 1, so this was
        # STALE and the store was never touched.
        _store, executor, plan, new_epoch = self._committed(engine, auto_scores, clock)
        result = Compensator().compensate(plan, against_snapshot=new_epoch)
        assert result.plan is not None

        record = executor.execute(result.plan, is_compensation=True)
        # The reference store cannot *apply* a RETRACT (ADR candidate 0015),
        # but it gets that far: the precondition no longer rejects it first.
        assert record.outcome is not ExecutionOutcome.STALE
        assert record.failed_preconditions == ()

    def test_compensation_is_still_stale_when_something_else_committed_first(
        self, engine: CurationEngine, auto_scores: CandidateScores, clock: FixedClock
    ) -> None:
        # The other direction: the guard is real, not decorative.
        store, executor, plan, new_epoch = self._committed(engine, auto_scores, clock)
        result = Compensator().compensate(plan, against_snapshot=new_epoch)
        assert result.plan is not None

        # A third party advances the graph past the epoch the rollback expects.
        other = CurationEngine.create(graph_id=GRAPH_ID, clock=clock, snapshot_version="1").curate(
            [make_entity_candidate(graph_id=GRAPH_ID, scores=auto_scores)]
        ).plan
        assert other is not None
        assert executor.execute(other).outcome is ExecutionOutcome.COMMITTED
        assert store.current_epoch() == 2

        record = executor.execute(result.plan, is_compensation=True)
        assert record.outcome is ExecutionOutcome.STALE
        assert any(
            p.kind == SNAPSHOT_PRECONDITION_KIND for p in record.failed_preconditions
        )

    def test_there_is_no_unguarded_path_out_of_compensate(
        self, engine: CurationEngine, auto_scores: CandidateScores
    ) -> None:
        # F-2: an earlier revision accepted `against_snapshot=None` and handed
        # back a plan with no guard, which COMMITS against moved state. The
        # keyword is now non-optional: no argument produces an unguarded plan.
        plan = engine.curate(
            [make_attribute_candidate(graph_id=GRAPH_ID, scores=auto_scores)]
        ).plan
        assert plan is not None
        with pytest.raises((TypeError, ValueError)):
            Compensator().compensate(plan, against_snapshot=None)  # type: ignore[arg-type]

    def test_the_keyword_cannot_be_omitted(
        self, engine: CurationEngine, auto_scores: CandidateScores
    ) -> None:
        plan = engine.curate(
            [make_attribute_candidate(graph_id=GRAPH_ID, scores=auto_scores)]
        ).plan
        assert plan is not None
        with pytest.raises(TypeError, match="against_snapshot"):
            Compensator().compensate(plan)  # type: ignore[call-arg]

    def test_a_source_plan_without_a_guard_still_yields_a_guarded_compensation(
        self,
    ) -> None:
        # F-2, the undocumented half: supplying a real epoch used to leave
        # `preconditions ()` when the source plan carried no snapshot guard —
        # `snapshot_version` stamped but nothing enforced. The guard is now
        # synthesized on the plan's first candidate id.
        plan = CurationPlan(
            plan_id="pl_unguarded_source",
            candidate_ids=("cand_first", "cand_second"),
            snapshot_version="0",
            operations=(
                CurationOperation(
                    operation_id="op_1",
                    type=CurationOperationType.ATTACH_ASSERTION,
                    payload={},
                    reversal_data={"assertion_id": "as_1"},
                ),
            ),
            preconditions=(
                Precondition(kind="entity_version", subject="kg://g1/identity/AAA", expected="0"),
            ),
            evidence_ids=(),
            policy_version="1",
        )
        result = Compensator().compensate(plan, against_snapshot=7)
        assert result.plan is not None
        assert [
            (p.kind, p.subject, p.expected) for p in result.plan.preconditions
        ] == [(SNAPSHOT_PRECONDITION_KIND, "cand_first", "7")]
        assert result.plan.snapshot_version == "7"

    def test_every_compensating_plan_carries_exactly_one_snapshot_guard(
        self, engine: CurationEngine, auto_scores: CandidateScores
    ) -> None:
        plan = engine.curate(
            [
                make_entity_candidate(graph_id=GRAPH_ID, scores=auto_scores),
                make_attribute_candidate(graph_id=GRAPH_ID, scores=auto_scores),
            ]
        ).plan
        assert plan is not None
        result = Compensator().compensate(plan, against_snapshot=1)
        assert result.plan is not None
        assert len(result.plan.preconditions) == 1
        assert result.plan.preconditions[0].kind == SNAPSHOT_PRECONDITION_KIND

    @pytest.mark.parametrize(
        "bad", ["banana", "", "1.5", "-1", -1, True, None, "0x1", " 1 x"]
    )
    def test_a_non_epoch_snapshot_is_refused(
        self, engine: CurationEngine, auto_scores: CandidateScores, bad: object
    ) -> None:
        # F-3: a free-form string was accepted and became an `expected` no
        # epoch can ever equal — this ADR's own defect in a new costume.
        plan = engine.curate(
            [make_attribute_candidate(graph_id=GRAPH_ID, scores=auto_scores)]
        ).plan
        assert plan is not None
        with pytest.raises((TypeError, ValueError)):
            Compensator().compensate(plan, against_snapshot=bad)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("given", "expected"), [(0, "0"), ("0", "0"), (12, "12"), ("12", "12")]
    )
    def test_epoch_accepts_int_and_decimal_string_alike(
        self,
        engine: CurationEngine,
        auto_scores: CandidateScores,
        given: str | int,
        expected: str,
    ) -> None:
        plan = engine.curate(
            [make_attribute_candidate(graph_id=GRAPH_ID, scores=auto_scores)]
        ).plan
        assert plan is not None
        result = Compensator().compensate(plan, against_snapshot=given)
        assert result.plan is not None
        assert result.plan.preconditions[0].expected == expected

    def test_entity_version_guards_are_not_carried(
        self, engine: CurationEngine, auto_scores: CandidateScores
    ) -> None:
        # A CREATE_IDENTITY's entity_version=0 guard guarded creation, not
        # reversal, and an identity that now exists would fail it forever.
        plan = engine.curate(
            [
                make_entity_candidate(graph_id=GRAPH_ID, scores=auto_scores),
                make_attribute_candidate(graph_id=GRAPH_ID, scores=auto_scores),
            ]
        ).plan
        assert plan is not None
        assert any(p.kind == "entity_version" for p in plan.preconditions)
        result = Compensator().compensate(plan, against_snapshot=1)
        assert result.plan is not None
        assert all(p.kind == SNAPSHOT_PRECONDITION_KIND for p in result.plan.preconditions)


class TestInversePayloadIsWellFormed:
    """ADR-0018: an inverse a store can actually apply, not a provenance block."""

    def test_attach_inverse_is_a_complete_retract_payload(
        self, engine: CurationEngine, auto_scores: CandidateScores
    ) -> None:
        plan = engine.curate(
            [make_attribute_candidate(graph_id=GRAPH_ID, scores=auto_scores)]
        ).plan
        assert plan is not None
        attach = plan.operations[0]
        assert attach.type is CurationOperationType.ATTACH_ASSERTION

        result = Compensator().compensate(plan, against_snapshot=1)
        assert result.plan is not None
        (retract,) = result.plan.operations
        # A retract is defined by *what status, as of when* — not just by which
        # assertion. Dropping those made the inverse lossy and forced stores to
        # invent a value.
        assert set(retract.payload) == {
            "assertion_id",
            "subject_identity",
            "new_status",
            "superseded_at",
        }
        assert retract.payload["new_status"] == CurationStatus.SUPERSEDED.value
        assert retract.payload["assertion_id"] == attach.payload["assertion_id"]
        # The instant the rolled-back record's transaction interval closes is
        # the instant it opened: as of any query time it was never validly
        # live. (Compared as instants — a plan's ATTACH payload is
        # `model_dump(mode="json")` and a RETRACT's is `isoformat()`, so "Z"
        # and "+00:00" both appear and only the value is load-bearing.)
        assert datetime.fromisoformat(str(retract.payload["superseded_at"])) == (
            datetime.fromisoformat(str(attach.payload["recorded_at"]))
        )
        # ...and no lineage leaked in.
        assert "candidate_id" not in retract.payload

    def test_retract_inverse_validates_as_an_assertion(self) -> None:
        # The RETRACT→ATTACH direction: an ATTACH payload IS an `Assertion`,
        # so anything less than a complete one cannot be applied at all.
        subject = new_identity_id(GRAPH_ID)
        old = make_assertion(
            graph_id=GRAPH_ID, subject_identity=subject, predicate="year", object_value=2015
        )
        new = make_assertion(
            graph_id=GRAPH_ID, subject_identity=subject, predicate="year", object_value=2014
        )
        trigger = CurationTrigger.of(
            kind=TriggerKind.NEW_EVIDENCE, evidence_ids=("ev_1",), trace_id="trace-1"
        )
        source = ConceptEvolutionPlanner(matcher_version="rules/1").plan_supersession(
            old_assertion=old, new_assertion=new, trigger=trigger
        )
        assert source.plan is not None

        result = Compensator().compensate(source.plan, against_snapshot=1)
        assert result.plan is not None
        (attach_inverse,) = [
            op
            for op in result.plan.operations
            if op.type is CurationOperationType.ATTACH_ASSERTION
            and op.reversal_data["original_type"] == "RETRACT_ASSERTION"
        ]
        restored = Assertion.model_validate({**attach_inverse.payload, "curation_epoch": 2})
        # Undoing "mark SUPERSEDED" restores the record as it stood.
        assert restored.assertion_id == old.assertion_id
        assert restored.predicate == old.predicate
        assert restored.object_value == old.object_value
        assert restored.status is old.status

    def test_no_provenance_leaks_into_any_inverse_payload(self) -> None:
        trigger = CurationTrigger.of(
            kind=TriggerKind.NEW_EVIDENCE, evidence_ids=("ev_1",), trace_id="trace-1"
        )
        planner = ConceptEvolutionPlanner(matcher_version="rules/1")
        source = new_identity_id(GRAPH_ID)
        into = (new_identity_id(GRAPH_ID), new_identity_id(GRAPH_ID))
        split = planner.plan_split(
            source_identity=source,
            into=into,
            reassignments=(AssertionReassignment(assertion_id="as_1", to_identity=into[0]),),
            trigger=trigger,
        )
        assert split.plan is not None
        result = Compensator().compensate(split.plan, against_snapshot=1)
        assert result.plan is not None
        for op in result.plan.operations:
            assert "trigger_id" not in op.payload
            assert "evidence_ids" not in op.payload
            assert "matcher_version" not in op.payload
            assert "policy_version" not in op.payload
        # The provenance is still there — on reversal_data, where it belongs.
        assert split.plan.operations[0].reversal_data["trigger_id"] == trigger.trigger_id


class TestOrderingAndMixing:
    def test_operations_are_reversed_lifo(self) -> None:
        plan = CurationPlan(
            plan_id="pl_two_attach",
            candidate_ids=("cand_1", "cand_2"),
            snapshot_version="0",
            operations=(
                CurationOperation(
                    operation_id="op_first",
                    type=CurationOperationType.ATTACH_ASSERTION,
                    payload={},
                    reversal_data={"assertion_id": "as_first"},
                ),
                CurationOperation(
                    operation_id="op_second",
                    type=CurationOperationType.ATTACH_ASSERTION,
                    payload={},
                    reversal_data={"assertion_id": "as_second"},
                ),
            ),
            preconditions=(),
            evidence_ids=(),
            policy_version="1",
        )
        result = Compensator().compensate(plan, against_snapshot=1)
        assert result.plan is not None
        undone = [op.reversal_data["compensates_operation_id"] for op in result.plan.operations]
        assert undone == ["op_second", "op_first"]  # last applied, first undone

    def test_mixed_plan_compensates_assertion_reports_create(
        self, engine: CurationEngine, auto_scores: CandidateScores
    ) -> None:
        plan = engine.curate(
            [
                make_entity_candidate(graph_id=GRAPH_ID, scores=auto_scores),
                make_attribute_candidate(graph_id=GRAPH_ID, scores=auto_scores),
            ]
        ).plan
        assert plan is not None
        result = Compensator().compensate(plan, against_snapshot=1)
        assert not result.fully_compensable
        assert result.plan is not None
        assert {op.type for op in result.plan.operations} == {
            CurationOperationType.RETRACT_ASSERTION
        }
        assert {op.type for op in result.non_compensable} == {
            CurationOperationType.CREATE_IDENTITY
        }


class TestDeterminismAndExecution:
    def test_compensation_is_deterministic_and_serializable(
        self, engine: CurationEngine, auto_scores: CandidateScores
    ) -> None:
        plan = engine.curate(
            [make_attribute_candidate(graph_id=GRAPH_ID, scores=auto_scores)]
        ).plan
        assert plan is not None
        a = Compensator().compensate(plan, against_snapshot=1).plan
        b = Compensator().compensate(plan, against_snapshot=1).plan
        assert a is not None and b is not None
        assert a.model_dump_json() == b.model_dump_json()
        # round-trips through the executor seam
        assert CurationPlan.model_validate_json(a.model_dump_json()) == a

    def test_a_different_snapshot_is_the_only_difference(
        self, engine: CurationEngine, auto_scores: CandidateScores
    ) -> None:
        plan = engine.curate(
            [make_attribute_candidate(graph_id=GRAPH_ID, scores=auto_scores)]
        ).plan
        assert plan is not None
        at_one = Compensator().compensate(plan, against_snapshot=1).plan
        at_two = Compensator().compensate(plan, against_snapshot=2).plan
        assert at_one is not None and at_two is not None
        assert at_one.plan_id == at_two.plan_id  # ids stay derived, not epoch-keyed
        assert at_one.operations == at_two.operations
        assert at_one.preconditions != at_two.preconditions

    def test_executing_compensation_on_reference_store_is_unsupported(
        self, engine: CurationEngine, auto_scores: CandidateScores, clock: FixedClock
    ) -> None:
        # The reference store does not implement RETRACT_ASSERTION (ADR
        # candidate 0015), so a compensation is generated but explicitly
        # reported unsupported rather than crashing.
        plan = engine.curate(
            [make_attribute_candidate(graph_id=GRAPH_ID, scores=auto_scores)]
        ).plan
        assert plan is not None
        compensation = Compensator().compensate(plan, against_snapshot=1).plan
        assert compensation is not None
        store = MemoryGraphStore()
        record = PlanExecutor(store, clock=clock).execute(compensation, is_compensation=True)
        assert record.outcome is ExecutionOutcome.UNSUPPORTED_OPERATION
        assert record.is_compensation
        assert "RETRACT_ASSERTION" in record.unsupported_types
        assert store.current_epoch() == 0
