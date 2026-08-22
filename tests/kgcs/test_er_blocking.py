"""ER stage 2: blocking is recall-oriented, dedupes pairs, records channel
provenance, and treats the embedding channel as strictly optional."""

import pytest
from kg_contracts.identity import EntityRef
from kg_contracts.testing.factories import make_entity_candidate

from kgcs.er.blocking import (
    BlockingPipeline,
    CandidatePair,
    EmbeddingChannel,
    ExactIdentifierChannel,
    NormalizedNameChannel,
    SourceKeyChannel,
    VectorIndex,
)
from kgcs.er.normalize import DefaultNormalizer, NormalizedEntity


def _paper(*, key: str, doi: str | None = None, name: str | None = None):
    aliases = [EntityRef(entity_type="Paper", namespace="src", key=key)]
    if doi is not None:
        aliases.append(EntityRef(entity_type="Paper", namespace="doi", key=doi))
    return make_entity_candidate(
        entity_type="Paper",
        key=key,
        semantic_key=f"paper/src/{key}",
        aliases=tuple(aliases),
        display_name=name,
    )


def _normalized(*papers) -> list[NormalizedEntity]:
    normalizer = DefaultNormalizer()
    return [normalizer.normalize(p) for p in papers]


def test_candidate_pair_canonical_order_and_dedup() -> None:
    assert CandidatePair.of("b", "a").key == ("a", "b")
    assert CandidatePair.of("a", "b") == CandidatePair.of("b", "a")


def test_candidate_pair_rejects_self_link() -> None:
    with pytest.raises(ValueError):
        CandidatePair.of("a", "a")


def test_candidate_pair_rejects_non_canonical_construction() -> None:
    with pytest.raises(ValueError):
        CandidatePair(left="b", right="a")


def test_exact_identifier_channel_blocks_on_shared_doi() -> None:
    entities = _normalized(
        _paper(key="p1", doi="10.1/ABC"),
        _paper(key="p2", doi="10.1/ABC"),
        _paper(key="p3", doi="10.1/OTHER"),
    )
    pairs = BlockingPipeline([ExactIdentifierChannel()]).block(entities)
    keys = {p.key for p in pairs}
    assert ("paper/src/p1", "paper/src/p2") in keys
    assert all("p3" not in "".join(k) for k in keys)


def test_name_channel_is_recall_oriented() -> None:
    # Different source keys, no shared identifier, but the same name — the name
    # channel must still surface them (recall), so the matcher can judge them.
    entities = _normalized(
        _paper(key="p1", name="Deep Learning"),
        _paper(key="p2", name="Deep Learning"),
    )
    pairs = BlockingPipeline([NormalizedNameChannel()]).block(entities)
    assert ("paper/src/p1", "paper/src/p2") in {p.key for p in pairs}


def test_pairs_dedupe_and_record_all_channel_provenance() -> None:
    entities = _normalized(
        _paper(key="p1", doi="10.1/ABC", name="Deep Learning"),
        _paper(key="p2", doi="10.1/ABC", name="Deep Learning"),
    )
    pipeline = BlockingPipeline([ExactIdentifierChannel(), NormalizedNameChannel()])
    pairs = pipeline.block(entities)
    assert len(pairs) == 1  # one pair, produced by two channels, not duplicated
    assert pairs[0].channels == ("exact_identifier", "normalized_name")


def test_blocking_is_deterministic() -> None:
    entities = _normalized(
        _paper(key="p1", doi="10.1/ABC"),
        _paper(key="p2", doi="10.1/ABC"),
        _paper(key="p3", name="X Y"),
        _paper(key="p4", name="X Y"),
    )
    pipeline = BlockingPipeline([ExactIdentifierChannel(), NormalizedNameChannel(), SourceKeyChannel()])
    assert pipeline.block(entities) == pipeline.block(entities)


def test_embedding_channel_is_optional_with_no_index() -> None:
    entities = _normalized(_paper(key="p1"), _paper(key="p2"))
    # No vector index injected: the channel contributes nothing, and the whole
    # pipeline still runs and returns results from the other channels.
    pipeline = BlockingPipeline(
        [SourceKeyChannel()], pair_producers=[EmbeddingChannel(index=None)]
    )
    pairs = pipeline.block(entities)
    assert all("embedding" not in p.channels for p in pairs)


class _FakeIndex:
    """A trivial in-memory `VectorIndex`: everyone is everyone's neighbour."""

    def neighbors(self, entity: NormalizedEntity, *, k: int) -> tuple[str, ...]:
        return ("paper/src/p1", "paper/src/p2")


def test_embedding_channel_contributes_pairs_when_index_injected() -> None:
    index: VectorIndex = _FakeIndex()
    entities = _normalized(_paper(key="p1"), _paper(key="p2"))
    pipeline = BlockingPipeline([], pair_producers=[EmbeddingChannel(index=index, k=5)])
    pairs = pipeline.block(entities)
    assert ("paper/src/p1", "paper/src/p2") in {p.key for p in pairs}
    assert pairs[0].channels == ("embedding",)
