from kg_contracts import (
    AdjudicationRoute,
    CandidateScores,
    ConfidencePolicy,
    LedgerReader,
)
from kg_contracts.stores import CandidateSink, GraphMutationStore
from kg_contracts.testing.contract import (
    CandidateSinkContract,
    GraphMutationStoreContract,
    LedgerReaderContract,
)
from kg_contracts.testing.memory import MemoryCandidateSink, MemoryGraphStore


class TestSinkUsableFromKgcs(CandidateSinkContract):
    def make_sink(self) -> CandidateSink:
        return MemoryCandidateSink()


class TestStoreUsableFromKgcs(GraphMutationStoreContract):
    def make_store(self) -> GraphMutationStore:
        return MemoryGraphStore()


class TestLedgerReaderUsableFromKgcs(LedgerReaderContract):
    def make_ledger(self) -> CandidateSink:
        return MemoryCandidateSink()


def test_ledger_read_surface_is_separate_from_canonical() -> None:
    # ADR-0011: the ledger read surface is reachable cross-repo and is a
    # distinct protocol from the canonical GraphMutationStore/GraphReader.
    assert isinstance(MemoryCandidateSink(), LedgerReader)
    assert not isinstance(MemoryGraphStore(), LedgerReader)


def test_policy_available() -> None:
    scores = CandidateScores(extraction_confidence=0.99,
                             source_reliability=0.99,
                             identity_confidence=0.99)
    assert ConfidencePolicy().route(scores) is AdjudicationRoute.AUTO
