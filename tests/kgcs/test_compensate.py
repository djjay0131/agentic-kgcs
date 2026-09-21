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
    INVERSE_OPERATION_TYPES,
    CurationOperation,
    CurationOperationType,
    CurationPlan,
    Precondition,
)
from kg_contracts.stores import GraphReadOptions
from kg_contracts.testing.factories import (
    make_assertion,
    make_attribute_candidate,
    make_entity_candidate,
    new_identity_id,
)
from kg_contracts.testing.memory import MemoryGraphStore

from helpers import GRAPH_ID
from kgcs import (
    DEFAULT_SUPPORTED_OPERATIONS,
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

    def test_create_identity_inverts_to_a_well_formed_revoke(
        self, engine: CurationEngine, auto_scores: CandidateScores
    ) -> None:
        # KGIS ADR-0025 gave CREATE_IDENTITY an inverse. Before it, this same
        # plan compensated to nothing and was reported non-compensable.
        plan = engine.curate(
            [make_entity_candidate(graph_id=GRAPH_ID, scores=auto_scores)]
        ).plan
        assert plan is not None
        identity_id = str(plan.operations[0].payload["identity_id"])
        result = Compensator().compensate(plan, against_snapshot=1)

        assert result.fully_compensable
        assert result.non_compensable == ()
        assert result.plan is not None
        (op,) = result.plan.operations
        assert op.type is CurationOperationType.REVOKE_IDENTITY
        # An identity reference, never an entity dump (ADR-0025): a stale copy
        # carried in the plan must not be able to overwrite the live entity.
        assert op.payload["identity_id"] == identity_id
        assert set(op.payload) == {"identity_id", "reason"}
        # ...and the pre-revoke entity travels in reversal_data, which is what
        # makes the revoke itself compensable by a CREATE_IDENTITY.
        assert op.reversal_data[INVERSE_PAYLOAD_KEY] == dict(plan.operations[0].payload)

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
        # PROMOTE_ONTOLOGY_TERM is the only type the contract omits from
        # INVERSE_OPERATION_TYPES (KGIS issue #45).
        [CurationOperationType.PROMOTE_ONTOLOGY_TERM],
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
        "bad",
        [
            "banana", "", "1.5", "-1", -1, True, None, "0x1", " 1 x",
            # Non-integral numbers are rejected, NOT coerced. `int(1.5)` is 1
            # and `int(-0.4)` is 0 — and 0 is a real epoch, so that one slips
            # past a non-negative check and yields a guard that is wrong but
            # *meetable*, which is worse than an unmeetable one. `inf`/`nan`
            # also made `int()` raise OverflowError rather than ValueError.
            1.5, -0.4, 2.0, float("inf"), float("nan"),
        ],
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
        ("given", "expected"),
        [
            (0, "0"), ("0", "0"), (12, "12"), ("12", "12"),
            # Decimal strings normalize — these are unambiguous, not sloppy.
            (" 7 ", "7"), ("+7", "7"), ("007", "7"),
        ],
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

    def test_a_create_then_attach_plan_undoes_the_attach_before_the_identity(
        self, engine: CurationEngine, auto_scores: CandidateScores
    ) -> None:
        # The ordering rule that matters now that CREATE_IDENTITY is
        # compensable: an ATTACH that followed a CREATE must be retracted
        # BEFORE the identity it hangs on is revoked. Asserted as a sequence,
        # because order is the observable — a set would pass either way.
        plan = engine.curate(
            [
                make_entity_candidate(graph_id=GRAPH_ID, scores=auto_scores),
                make_attribute_candidate(graph_id=GRAPH_ID, scores=auto_scores),
            ]
        ).plan
        assert plan is not None
        assert [op.type for op in plan.operations] == [
            CurationOperationType.CREATE_IDENTITY,
            CurationOperationType.ATTACH_ASSERTION,
        ]
        result = Compensator().compensate(plan, against_snapshot=1)

        assert result.fully_compensable
        assert result.plan is not None
        assert [op.type for op in result.plan.operations] == [
            CurationOperationType.RETRACT_ASSERTION,
            CurationOperationType.REVOKE_IDENTITY,
        ]


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


class TestRollbackIsDemonstrated:
    """KGIS ADR-0025 item 7: the rollback is *executed*, not asserted.

    A document claiming compensation works is insufficient — that was exactly
    the state ADR-0018 found (`Compensator` produced plans nothing had ever
    applied). These tests run a real committed batch through a real compensator
    into a real store and then *read the graph back*.
    """

    RUN_SIZE = 8

    def _committed_run(
        self, clock: FixedClock, scores: CandidateScores
    ) -> tuple[MemoryGraphStore, PlanExecutor, CurationPlan, tuple[str, ...], int]:
        """Commit `RUN_SIZE` CREATE_IDENTITY operations and return the handles."""
        engine = CurationEngine.create(graph_id=GRAPH_ID, clock=clock, snapshot_version="0")
        candidates = [
            make_entity_candidate(graph_id=GRAPH_ID, key=f"e{n}", scores=scores)
            for n in range(self.RUN_SIZE)
        ]
        plan = engine.curate(candidates).plan
        assert plan is not None
        # Non-vacuity: a rollback demonstration over an empty run proves nothing.
        assert [op.type for op in plan.operations] == [
            CurationOperationType.CREATE_IDENTITY
        ] * self.RUN_SIZE
        identity_ids = tuple(str(op.payload["identity_id"]) for op in plan.operations)
        assert len(set(identity_ids)) == self.RUN_SIZE

        store = MemoryGraphStore()
        executor = PlanExecutor(store, clock=clock)
        record = executor.execute(plan)
        assert record.outcome is ExecutionOutcome.COMMITTED
        assert record.new_epoch is not None
        return store, executor, plan, identity_ids, record.new_epoch

    def test_revoke_identity_reverses_a_committed_create_identity_run(
        self, clock: FixedClock, auto_scores: CandidateScores
    ) -> None:
        store, executor, plan, identity_ids, created_at_epoch = self._committed_run(
            clock, auto_scores
        )

        # The graph really does hold them first — otherwise "returns none of
        # them" afterwards would be true of an empty graph too.
        assert [store.get_entity(i) is not None for i in identity_ids] == [True] * self.RUN_SIZE
        creation_epochs = {
            i: store.get_entity(i).curation_epoch  # type: ignore[union-attr]
            for i in identity_ids
        }
        assert set(creation_epochs.values()) == {created_at_epoch}

        result = Compensator().compensate(plan, against_snapshot=created_at_epoch)
        assert result.fully_compensable
        assert result.plan is not None
        assert [op.type for op in result.plan.operations] == [
            CurationOperationType.REVOKE_IDENTITY
        ] * self.RUN_SIZE
        # Payload shape, asserted here and not only in TestInverseMap. Found by
        # mutation: dropping INVERSE_PAYLOAD_KEY from the planner left this
        # demonstration passing, because `_invert`'s back-compat fallback then
        # used the whole reversal_data and the resulting
        # {"identity_id", "candidate_id"} still revoked successfully — with
        # `candidate_id`, lineage, leaking into an operation payload. The
        # rollback "worked" while carrying the defect ADR-0018 fixed for
        # RETRACT_ASSERTION. A demonstration that cannot see that is too weak.
        for op in result.plan.operations:
            assert set(op.payload) == {"identity_id", "reason"}

        rollback = executor.execute(result.plan, is_compensation=True)
        assert rollback.outcome is ExecutionOutcome.COMMITTED, (
            f"rollback did not execute: {rollback.outcome} "
            f"{rollback.error} {rollback.unsupported_types}"
        )
        assert rollback.is_compensation

        # 1. The canonical read stops returning them.
        assert [store.get_entity(i) for i in identity_ids] == [None] * self.RUN_SIZE
        assert store.find_entities(entity_type="TestEntity") == []

        # 2. include_revoked=True returns all of them, REVOKED, at their
        #    CREATION epoch — the revoke is a tombstone, not a deletion and not
        #    a re-stamp. Asserted per identity, not as a count.
        history = GraphReadOptions(include_revoked=True)
        for identity_id in identity_ids:
            entity = store.get_entity(identity_id, history)
            assert entity is not None, f"{identity_id} was erased, not revoked"
            assert entity.status is CurationStatus.REVOKED
            assert entity.curation_epoch == creation_epochs[identity_id]
        assert len(store.find_entities(entity_type="TestEntity", options=history)) == self.RUN_SIZE

        # 3. ...including under an epoch-scoped read of the epoch that created
        #    them, which is the read a revoke must not break.
        at_creation = GraphReadOptions(
            curation_epoch=created_at_epoch, include_revoked=True
        )
        assert (
            len(store.find_entities(entity_type="TestEntity", options=at_creation))
            == self.RUN_SIZE
        )

    def test_include_superseded_does_not_reveal_a_revoked_identity(
        self, clock: FixedClock, auto_scores: CandidateScores
    ) -> None:
        # The two history switches are independent (ADR-0025). A consumer
        # asking to see superseded records must not thereby be shown
        # retractions it did not ask for.
        store, executor, plan, identity_ids, epoch = self._committed_run(clock, auto_scores)
        result = Compensator().compensate(plan, against_snapshot=epoch)
        assert result.plan is not None
        assert executor.execute(result.plan, is_compensation=True).committed

        superseded_only = GraphReadOptions(include_superseded=True)
        assert store.find_entities(entity_type="TestEntity", options=superseded_only) == []
        assert store.get_entity(identity_ids[0], superseded_only) is None
        # ...while the right switch does reveal it.
        assert (
            store.get_entity(identity_ids[0], GraphReadOptions(include_revoked=True))
            is not None
        )

    def test_a_revoke_names_an_unknown_identity_and_does_not_commit(
        self, clock: FixedClock
    ) -> None:
        # ADR-0025's fail-closed clause, exercised rather than trusted: a
        # rollback aimed at an identity that is not there must not report
        # success, and must leave the store untouched.
        store = MemoryGraphStore()
        plan = CurationPlan(
            plan_id="pl_revoke_ghost",
            candidate_ids=("cand_ghost",),
            snapshot_version="0",
            operations=(
                CurationOperation(
                    operation_id="op_revoke_ghost",
                    type=CurationOperationType.REVOKE_IDENTITY,
                    payload={"identity_id": "kg://g1/identity/NEVERCREATEDNEVERCREATE"},
                ),
            ),
            preconditions=(),
            evidence_ids=(),
            policy_version="1",
        )
        record = PlanExecutor(store, clock=clock).execute(plan)

        assert record.outcome is not ExecutionOutcome.COMMITTED
        assert not record.committed
        assert store.current_epoch() == 0

    def test_the_revoke_carries_the_pre_revoke_active_entity_for_its_own_inverse(
        self, clock: FixedClock, auto_scores: CandidateScores
    ) -> None:
        # KGIS ADR-0025 §6: `reversal_data` must hold the entity as it was
        # BEFORE the revoke — i.e. ACTIVE. Compensating from a post-revoke copy
        # would "restore" the identity still REVOKED, which restores nothing.
        # `Compensator._invert` gets this right generically, by carrying the
        # forward operation's own payload (written at plan time, status ACTIVE)
        # rather than reading the graph back; this pins that it stays true.
        store, executor, plan, identity_ids, epoch = self._committed_run(clock, auto_scores)
        result = Compensator().compensate(plan, against_snapshot=epoch)
        assert result.plan is not None

        # Paired against `reversed(...)`: the compensation is LIFO, so the
        # first revoke undoes the LAST create. Zipping them forward silently
        # compares mismatched identities — which is how this assertion first
        # failed, and is itself a check on the ordering.
        for forward, revoke in zip(reversed(plan.operations), result.plan.operations):
            carried = revoke.reversal_data[INVERSE_PAYLOAD_KEY]
            assert carried["status"] == CurationStatus.ACTIVE.value
            assert dict(carried) == dict(forward.payload)

        # And it survives the revoke actually happening: the graph says REVOKED,
        # the carried dump still says ACTIVE.
        assert executor.execute(result.plan, is_compensation=True).committed
        live = store.get_entity(identity_ids[0], GraphReadOptions(include_revoked=True))
        assert live is not None and live.status is CurationStatus.REVOKED
        assert (
            result.plan.operations[-1].reversal_data[INVERSE_PAYLOAD_KEY]["status"]
            == CurationStatus.ACTIVE.value
        )

    def test_the_round_trip_restores_the_identity_but_not_its_creation_epoch(
        self, clock: FixedClock, auto_scores: CandidateScores
    ) -> None:
        # The stated bound, pinned so it is a known limit rather than a
        # surprise. REVOKE_IDENTITY inverts to CREATE_IDENTITY, which restores
        # the entity ACTIVE — but `curation_epoch` is assigned by the executor
        # at apply time, so the restored record carries the epoch of the batch
        # that re-created it, not the one that originally created it.
        # Deliberately NOT repaired in place: KGIS mutant B1' showed that making
        # CREATE_IDENTITY honour an epoch in its payload corrupts the forward
        # leg's own guarantee. The fix is a distinct RESTORE_IDENTITY operation
        # — agentic-kgis issue #51.
        store, executor, plan, identity_ids, created_epoch = self._committed_run(
            clock, auto_scores
        )
        identity_id = identity_ids[0]
        assert store.get_entity(identity_id).curation_epoch == created_epoch  # type: ignore[union-attr]

        revoke = Compensator().compensate(plan, against_snapshot=created_epoch)
        assert revoke.plan is not None
        revoked_at = executor.execute(revoke.plan, is_compensation=True)
        assert revoked_at.committed and revoked_at.new_epoch is not None
        assert store.get_entity(identity_id) is None

        # Compensating the compensation is a CREATE_IDENTITY.
        restore = Compensator().compensate(revoke.plan, against_snapshot=revoked_at.new_epoch)
        assert restore.plan is not None
        assert [op.type for op in restore.plan.operations] == [
            CurationOperationType.CREATE_IDENTITY
        ] * self.RUN_SIZE
        restored_at = executor.execute(restore.plan, is_compensation=True)
        assert restored_at.committed and restored_at.new_epoch is not None

        back = store.get_entity(identity_id)
        assert back is not None
        assert back.status is CurationStatus.ACTIVE  # the identity IS restored
        # ...and the epoch is not. Asserted as the relationship, not a literal:
        # the restored record takes the epoch of the batch that re-created it.
        assert back.curation_epoch == restored_at.new_epoch
        assert back.curation_epoch != created_epoch

    def test_a_named_inverse_does_not_mean_an_executable_rollback(
        self, clock: FixedClock, auto_scores: CandidateScores
    ) -> None:
        # `INVERSE_OPERATION_TYPES` answers "what type reverses this type" — a
        # vocabulary statement — NOT "can this plan be rolled back today".
        # A caller must consult both it and the executor's supported set.
        # Measured on the merged contract: 7 types have a named inverse, the
        # reference store executes 3.
        named_inverse = {t for t in INVERSE_OPERATION_TYPES}
        assert CurationOperationType.RETRACT_ASSERTION in named_inverse
        assert CurationOperationType.RETRACT_ASSERTION not in DEFAULT_SUPPORTED_OPERATIONS
        assert named_inverse - DEFAULT_SUPPORTED_OPERATIONS  # the gap is non-empty

        # The gap is not theoretical: an attach plan reports fully_compensable
        # and its rollback still cannot execute.
        engine = CurationEngine.create(graph_id=GRAPH_ID, clock=clock, snapshot_version="0")
        plan = engine.curate(
            [make_attribute_candidate(graph_id=GRAPH_ID, scores=auto_scores)]
        ).plan
        assert plan is not None
        result = Compensator().compensate(plan, against_snapshot=1)
        assert result.fully_compensable is True  # vocabulary says yes...
        assert result.plan is not None

        store = MemoryGraphStore()
        record = PlanExecutor(store, clock=clock).execute(result.plan, is_compensation=True)
        assert record.outcome is ExecutionOutcome.UNSUPPORTED_OPERATION  # ...execution says no
        assert "RETRACT_ASSERTION" in record.unsupported_types
        assert store.current_epoch() == 0
