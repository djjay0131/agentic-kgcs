from kg_contracts import AdjudicationRoute, CandidateScores, ConfidencePolicy
from kg_contracts.stores import CandidateSink, GraphMutationStore
from kg_contracts.testing.contract import (
    CandidateSinkContract,
    GraphMutationStoreContract,
)
from kg_contracts.testing.memory import MemoryCandidateSink, MemoryGraphStore


class TestSinkUsableFromKgcs(CandidateSinkContract):
    def make_sink(self) -> CandidateSink:
        return MemoryCandidateSink()


class TestStoreUsableFromKgcs(GraphMutationStoreContract):
    def make_store(self) -> GraphMutationStore:
        return MemoryGraphStore()


def test_policy_available() -> None:
    scores = CandidateScores(extraction_confidence=0.99,
                             source_reliability=0.99,
                             identity_confidence=0.99)
    assert ConfidencePolicy().route(scores) is AdjudicationRoute.AUTO
