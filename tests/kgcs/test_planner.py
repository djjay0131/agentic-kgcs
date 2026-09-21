"""Immutable, deterministic, executable curation plans."""

from typing import Sequence

import pytest
from kg_contracts.assertions import Assertion, CanonicalEntity
from kg_contracts.candidates import (
    Candidate,
    CandidateScores,
    RelationCandidate,
    SourceCoordinates,
)
from kg_contracts.curation import CurationOperationType, CurationPlan, Precondition
from kg_contracts.stores import GraphMutationBatch
from kg_contracts.testing.factories import (
    make_attribute_candidate,
    make_entity_candidate,
)
from kg_contracts.testing.memory import MemoryGraphStore

from helpers import GRAPH_ID, known_identity
from kgcs import CurationPlanner, ResolutionPolicy, ResolvedCandidate


def _resolve(candidates: Sequence[Candidate]) -> list[ResolvedCandidate]:
    policy = ResolutionPolicy()
    return [ResolvedCandidate(candidate=c, resolution=policy.resolve(c)) for c in candidates]


def _plan(candidates: Sequence[Candidate]) -> CurationPlan | None:
    return CurationPlanner().plan(_resolve(candidates)).plan


def _relation(subject: str, obj: str, scores: CandidateScores) -> RelationCandidate:
    return RelationCandidate(
        graph_id=GRAPH_ID,
        producer="p",
        producer_run_id="r",
        ontology_version="1",
        source_coordinates=SourceCoordinates(source_type="t", locator="l"),
        semantic_key="rel/1",
        scores=scores,
        relation_type="PLAYS_FOR",
        subject=subject,
        object=obj,
    )


class TestOperationEmission:
    def test_auto_entity_yields_create_identity(self, auto_scores: CandidateScores) -> None:
        plan = _plan([make_entity_candidate(graph_id=GRAPH_ID, scores=auto_scores)])
        assert plan is not None
        assert [op.type for op in plan.operations] == [CurationOperationType.CREATE_IDENTITY]

    def test_auto_attribute_yields_attach_assertion(self, auto_scores: CandidateScores) -> None:
        candidate = make_attribute_candidate(
            graph_id=GRAPH_ID, subject=known_identity(), scores=auto_scores
        )
        plan = _plan([candidate])
        assert plan is not None
        assert [op.type for op in plan.operations] == [CurationOperationType.ATTACH_ASSERTION]

    def test_auto_relation_yields_attach_assertion(self, auto_scores: CandidateScores) -> None:
        plan = _plan([_relation(known_identity(), known_identity(), auto_scores)])
        assert plan is not None
        assert [op.type for op in plan.operations] == [CurationOperationType.ATTACH_ASSERTION]

    def test_non_auto_candidate_yields_no_plan(self, human_scores: CandidateScores) -> None:
        assert _plan([make_entity_candidate(graph_id=GRAPH_ID, scores=human_scores)]) is None

    def test_artifact_yields_no_operation(self, auto_scores: CandidateScores) -> None:
        from kg_contracts.candidates import ArtifactCandidate

        artifact = ArtifactCandidate(
            graph_id=GRAPH_ID,
            producer="p",
            producer_run_id="r",
            ontology_version="1",
            source_coordinates=SourceCoordinates(source_type="t", locator="l"),
            semantic_key="art/1",
            scores=auto_scores,
            artifact_type="cutlist",
            artifact_hash="abc",
            source_uri="file://x",
        )
        assert _plan([artifact]) is None


class TestPayloadsAreExecutable:
    """The plan is a description, but its payloads must be real: applying them
    to the reference store proves they are valid contract records, not just
    well-typed dicts."""

    def test_create_identity_payload_is_a_valid_entity(
        self, auto_scores: CandidateScores
    ) -> None:
        plan = _plan([make_entity_candidate(graph_id=GRAPH_ID, scores=auto_scores)])
        assert plan is not None
        payload = plan.operations[0].payload
        entity = CanonicalEntity.model_validate({**payload, "curation_epoch": 1})
        assert "curation_epoch" not in payload  # executor assigns it
        assert entity.curation_epoch == 1

    def test_attach_payload_is_a_valid_assertion(self, auto_scores: CandidateScores) -> None:
        candidate = make_attribute_candidate(
            graph_id=GRAPH_ID, subject=known_identity(), scores=auto_scores
        )
        plan = _plan([candidate])
        assert plan is not None
        assertion = Assertion.model_validate({**plan.operations[0].payload, "curation_epoch": 1})
        assert assertion.subject_identity == candidate.subject

    def test_plan_applies_cleanly_to_memory_store(self, auto_scores: CandidateScores) -> None:
        entity = make_entity_candidate(graph_id=GRAPH_ID, scores=auto_scores)
        attribute = make_attribute_candidate(
            graph_id=GRAPH_ID, subject=known_identity(), scores=auto_scores
        )
        plan = _plan([entity, attribute])
        assert plan is not None
        store = MemoryGraphStore()
        batch = GraphMutationBatch(plan_id=plan.plan_id, operations=plan.operations)
        result = store.apply(batch, plan.preconditions)
        assert result.committed
        assert result.new_epoch == 1


class TestPreconditionsAndEvidence:
    def test_snapshot_precondition_comes_first(self, auto_scores: CandidateScores) -> None:
        plan = _plan([make_entity_candidate(graph_id=GRAPH_ID, scores=auto_scores)])
        assert plan is not None
        assert plan.preconditions[0].kind == "snapshot_version"

    def test_create_identity_guards_new_id_at_version_zero(
        self, auto_scores: CandidateScores
    ) -> None:
        plan = _plan([make_entity_candidate(graph_id=GRAPH_ID, scores=auto_scores)])
        assert plan is not None
        version_guards = [p for p in plan.preconditions if p.kind == "entity_version"]
        assert len(version_guards) == 1
        assert version_guards[0].expected == "0"

    def test_attach_assertion_guards_its_minted_assertion_id(
        self, auto_scores: CandidateScores
    ) -> None:
        # ADR-0019: the symmetric counterpart of entity_version=0. Read-free,
        # because the planner minted the assertion id itself.
        subject = known_identity()
        plan = _plan(
            [make_attribute_candidate(graph_id=GRAPH_ID, subject=subject, scores=auto_scores)]
        )
        assert plan is not None
        assert plan.operations[0].type is CurationOperationType.ATTACH_ASSERTION
        guards = [p for p in plan.preconditions if p.kind == "assertion_absent"]
        assert len(guards) == 1
        assert guards[0].subject == subject
        assert guards[0].expected == plan.operations[0].payload["assertion_id"]

    def test_the_guard_round_trips_through_its_own_reader(self) -> None:
        # ADR-0019 claims one constructor and one reader are the only places
        # that decide which field holds which. That is only true if the reader
        # exists and refuses everything else — the executor calls it, so a
        # reader that silently accepted a snapshot guard would hand the
        # executor a graph id where an assertion id belongs.
        from kgcs import assertion_absent_guard, read_assertion_absent_guard

        guard = assertion_absent_guard("kg://g1/identity/ABC", "as_XYZ")
        assert read_assertion_absent_guard(guard) == ("kg://g1/identity/ABC", "as_XYZ")
        for wrong in (
            Precondition(kind="snapshot_version", subject="g1", expected="0"),
            Precondition(kind="entity_version", subject="kg://g1/identity/ABC", expected="0"),
        ):
            with pytest.raises(ValueError, match="not an assertion_absent precondition"):
                read_assertion_absent_guard(wrong)

    def test_every_attach_operation_gets_its_own_guard(
        self, auto_scores: CandidateScores
    ) -> None:
        # One guard per attach, not one per plan: a two-attach plan replayed
        # after only one of its assertions landed must still be refused.
        candidates = [
            make_attribute_candidate(
                graph_id=GRAPH_ID, subject=known_identity(), attribute=name, scores=auto_scores
            )
            for name in ("height_cm", "width_cm")
        ]
        plan = _plan(candidates)
        assert plan is not None
        attach_ops = [
            op for op in plan.operations if op.type is CurationOperationType.ATTACH_ASSERTION
        ]
        assert len(attach_ops) == 2
        guarded = [p.expected for p in plan.preconditions if p.kind == "assertion_absent"]
        assert guarded == [str(op.payload["assertion_id"]) for op in attach_ops]

    def test_evidence_ids_are_deduplicated_in_order(self, auto_scores: CandidateScores) -> None:
        from kg_contracts.evidence import EvidenceRef, EvidenceRelationship

        refs = (
            EvidenceRef(evidence_id="ev1", relationship=EvidenceRelationship.SUPPORTS),
            EvidenceRef(evidence_id="ev2", relationship=EvidenceRelationship.SUPPORTS),
        )
        c1 = make_entity_candidate(graph_id=GRAPH_ID, key="a", scores=auto_scores).model_copy(
            update={"evidence_refs": refs}
        )
        c2 = make_entity_candidate(graph_id=GRAPH_ID, key="b", scores=auto_scores).model_copy(
            update={"evidence_refs": (refs[0],)}
        )
        plan = _plan([c1, c2])
        assert plan is not None
        assert plan.evidence_ids == ("ev1", "ev2")


class TestDeterminismAndPurity:
    def test_identical_input_yields_identical_plan(self, auto_scores: CandidateScores) -> None:
        candidates = [make_entity_candidate(graph_id=GRAPH_ID, scores=auto_scores)]
        assert _plan(candidates) == _plan(candidates)

    def test_plan_round_trips_byte_for_byte(self, auto_scores: CandidateScores) -> None:
        plan = _plan([make_entity_candidate(graph_id=GRAPH_ID, scores=auto_scores)])
        assert plan is not None
        raw = plan.model_dump_json()
        assert CurationPlan.model_validate_json(raw).model_dump_json() == raw

    def test_planning_does_not_mutate_inputs(self, auto_scores: CandidateScores) -> None:
        candidate = make_entity_candidate(graph_id=GRAPH_ID, scores=auto_scores)
        before = candidate.model_dump_json()
        resolved = _resolve([candidate])
        planner = CurationPlanner()
        planner.plan(resolved)
        planner.plan(resolved)  # a second call must see identical inputs
        assert candidate.model_dump_json() == before
