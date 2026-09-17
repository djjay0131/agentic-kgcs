"""Wave 5: targeting is incremental — given new evidence, only the affected
identities/assertions are returned, unrelated knowledge is neither returned nor
read, and nothing is mutated (§9 law 11)."""

from kg_contracts.evidence import EvidenceRef, EvidenceRelationship
from kg_contracts.identity import EntityRef
from kg_contracts.testing.factories import make_attribute_candidate, make_entity_candidate

from kgcs.recuration import (
    CurationTrigger,
    InMemoryDependencyIndex,
    TriggerKind,
)

# A tiny fixed dependency graph. Only these ids are "known"; everything else is
# unrelated knowledge that a correct, incremental lookup must never touch.
_INDEX = InMemoryDependencyIndex(
    evidence_to_assertions={"ev_1": ("as_1",), "ev_2": ("as_1", "as_2")},
    assertion_to_identity={"as_1": "id_A", "as_2": "id_B", "as_unrelated": "id_Z"},
    alias_to_identity={
        "TestEntity:test:e1": "id_A",
        "testentity/e1": "id_A",
        "testentity/e1/height_cm": "id_A",
    },
)


def test_affected_by_evidence_returns_only_dependent_ids() -> None:
    assert _INDEX.affected_by_evidence(("ev_1",)) == ("as_1", "id_A")


def test_affected_by_evidence_is_incremental_not_a_full_scan() -> None:
    # ev_2 touches as_1/as_2 (id_A/id_B). The unrelated as_unrelated / id_Z are
    # never returned — the lookup visits only what the evidence maps to.
    affected = _INDEX.affected_by_evidence(("ev_2",))
    assert set(affected) == {"as_1", "as_2", "id_A", "id_B"}
    assert "as_unrelated" not in affected
    assert "id_Z" not in affected


def test_unknown_evidence_yields_nothing() -> None:
    assert _INDEX.affected_by_evidence(("ev_missing",)) == ()


def test_affected_by_candidate_via_semantic_key_and_aliases() -> None:
    candidate = make_entity_candidate(
        aliases=(EntityRef(entity_type="TestEntity", namespace="test", key="e1"),),
    )
    assert _INDEX.affected_by_candidate(candidate) == ("id_A",)


def test_affected_by_candidate_combines_alias_and_evidence() -> None:
    candidate = make_attribute_candidate().model_copy(
        update={
            "evidence_refs": (
                EvidenceRef(evidence_id="ev_1", relationship=EvidenceRelationship.SUPPORTS),
            )
        }
    )
    # semantic_key "testentity/e1/height_cm" → id_A; evidence ev_1 → as_1, id_A.
    affected = _INDEX.affected_by_candidate(candidate)
    assert set(affected) == {"id_A", "as_1"}


def test_affected_by_entity_resolves_alias() -> None:
    assert _INDEX.affected_by_entity("TestEntity:test:e1") == ("id_A",)


def test_affected_by_entity_recognizes_a_known_identity_id() -> None:
    assert _INDEX.affected_by_entity("id_A") == ("id_A",)


def test_affected_by_entity_unknown_ref_yields_nothing() -> None:
    assert _INDEX.affected_by_entity("kg://g1/identity/UNSEEN") == ()


def test_affected_by_trigger_combines_refs_and_evidence() -> None:
    trigger = CurationTrigger.of(
        kind=TriggerKind.NEW_EVIDENCE,
        assertion_ids=("as_2",),
        evidence_ids=("ev_1",),
    )
    affected = _INDEX.affected_by_trigger(trigger)
    assert set(affected) == {"as_2", "as_1", "id_A"}
    assert "id_Z" not in affected


def test_lookups_are_read_only() -> None:
    ev_map = {"ev_1": ("as_1",)}
    ident_map = {"as_1": "id_A"}
    alias_map = {"testentity/e1": "id_A"}
    index = InMemoryDependencyIndex(
        evidence_to_assertions=ev_map,
        assertion_to_identity=ident_map,
        alias_to_identity=alias_map,
    )
    index.affected_by_evidence(("ev_1",))
    index.affected_by_candidate(make_entity_candidate())
    index.affected_by_entity("id_A")
    assert ev_map == {"ev_1": ("as_1",)}
    assert ident_map == {"as_1": "id_A"}
    assert alias_map == {"testentity/e1": "id_A"}
