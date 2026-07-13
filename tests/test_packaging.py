def test_kgcs_imports_and_sees_contracts() -> None:
    import kg_contracts
    import kgcs

    assert kgcs.__name__ == "kgcs"
    assert kg_contracts.__name__ == "kg_contracts"
