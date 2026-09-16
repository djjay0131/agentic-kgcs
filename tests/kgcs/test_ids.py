"""Determinism and well-formedness of the ID factories."""

from kg_contracts.identity import is_identity_id

from kgcs import DerivedIdFactory, UlidIdFactory, is_well_formed_graph_id


class TestDerivedIdFactory:
    def test_same_seed_same_id(self) -> None:
        factory = DerivedIdFactory()
        assert factory.operation_id("s") == factory.operation_id("s")
        assert factory.plan_id("s") == factory.plan_id("s")
        assert factory.audit_id("s") == factory.audit_id("s")
        assert factory.assertion_id("s") == factory.assertion_id("s")

    def test_two_instances_agree(self) -> None:
        """Determinism is not per-instance state — a fresh factory replays the same ids."""
        assert DerivedIdFactory().plan_id("x") == DerivedIdFactory().plan_id("x")

    def test_different_seeds_differ(self) -> None:
        factory = DerivedIdFactory()
        assert factory.operation_id("a") != factory.operation_id("b")

    def test_prefixes_match_contract_defaults(self) -> None:
        factory = DerivedIdFactory()
        assert factory.operation_id("s").startswith("op_")
        assert factory.assertion_id("s").startswith("as_")
        assert factory.plan_id("s").startswith("pl_")
        assert factory.audit_id("s").startswith("au_")

    def test_minted_identity_is_well_formed(self) -> None:
        identity = DerivedIdFactory().identity_id("g1", "seed")
        assert is_identity_id(identity)

    def test_minted_identity_is_deterministic(self) -> None:
        factory = DerivedIdFactory()
        assert factory.identity_id("g1", "seed") == factory.identity_id("g1", "seed")

    def test_bad_graph_id_is_rejected(self) -> None:
        import pytest

        with pytest.raises(ValueError):
            DerivedIdFactory().identity_id("Bad Graph!", "seed")


class TestUlidIdFactory:
    def test_ids_are_unique_across_calls(self) -> None:
        factory = UlidIdFactory()
        assert factory.operation_id("s") != factory.operation_id("s")

    def test_minted_identity_is_well_formed(self) -> None:
        assert is_identity_id(UlidIdFactory().identity_id("g1", "ignored"))


class TestGraphIdWellFormed:
    def test_accepts_lowercase(self) -> None:
        assert is_well_formed_graph_id("baseball-2026")

    def test_rejects_uppercase_and_spaces(self) -> None:
        assert not is_well_formed_graph_id("Baseball")
        assert not is_well_formed_graph_id("has space")
