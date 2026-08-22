"""Stage 2 of entity resolution: multi-channel, recall-oriented blocking.

Spec §7.4 (ADR-0007), step 2. Comparing every entity against every other is
quadratic and wasteful; blocking cheaply proposes the *candidate pairs* worth
scoring. The governing bias is **recall**: a pair blocking misses can never be
matched, so channels over-generate and the matcher (a later stage) is trusted
to reject false pairs with precision. Missing a true pair here is unrecoverable;
proposing a false one is merely more work.

Blocking is *multi-channel* on purpose. No single key catches every duplicate:
records that share a DOI may have wildly different names, and records with the
same name may carry no shared identifier. Each `BlockingChannel` maps an entity
to zero or more block keys; two entities sharing a key in *any* channel become a
`CandidatePair`, and the pair records *which* channels produced it (provenance),
so a later audit can see whether a pair rests on a shared identifier, a shared
name, or only an embedding neighbourhood.

Embeddings appear here as **one optional channel, never a mandatory backend**
(the core §7.4 correction). `EmbeddingChannel` retrieves ANN neighbours through
an injected `VectorIndex`; with no index it contributes nothing and ER still
runs end to end. That is the whole point of superseding the embedding-threshold
funnel: an embedding is a recall aid, not the decision.
"""

import warnings
from collections.abc import Iterable, Sequence
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from kgcs.er.normalize import NormalizedEntity


class CandidatePair(BaseModel):
    """An unordered pair of entity keys proposed for matching, with provenance.

    Order is canonicalized (`left <= right`) so `(a, b)` and `(b, a)` are the
    same value and de-duplicate; a pair never links an entity to itself.
    `channels` is the sorted, de-duplicated set of channel names that produced
    the pair — its blocking provenance.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    left: str = Field(min_length=1)
    right: str = Field(min_length=1)
    channels: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check_canonical(self) -> "CandidatePair":
        if self.left == self.right:
            raise ValueError(f"a candidate pair cannot link a key to itself: {self.left!r}")
        if self.left > self.right:
            raise ValueError(
                f"candidate pair must be canonically ordered (left <= right): "
                f"got left={self.left!r}, right={self.right!r} — use CandidatePair.of()"
            )
        return self

    @classmethod
    def of(cls, a: str, b: str, channels: Iterable[str] = ()) -> "CandidatePair":
        """Build a canonically ordered pair from two keys in any order."""
        left, right = (a, b) if a <= b else (b, a)
        return cls(left=left, right=right, channels=tuple(sorted(set(channels))))

    @property
    def key(self) -> tuple[str, str]:
        """The endpoint identity of the pair, ignoring channel provenance."""
        return (self.left, self.right)


@runtime_checkable
class BlockingChannel(Protocol):
    """Maps an entity to zero or more block keys; shared keys make a pair."""

    name: str

    def block_keys(self, entity: NormalizedEntity) -> tuple[str, ...]: ...


@runtime_checkable
class VectorIndex(Protocol):
    """An injected ANN capability over the entity population.

    Deliberately narrow: given an entity, return the `source_key`s of its
    nearest neighbours. Any backend (in-memory brute force for tests, a real
    ANN index in production) satisfies this without ER depending on it.
    """

    def neighbors(self, entity: NormalizedEntity, *, k: int) -> tuple[str, ...]: ...


@runtime_checkable
class PairProducer(Protocol):
    """A channel that yields pairs directly rather than via shared block keys.

    ANN retrieval is inherently pairwise (a query returns *its* neighbours),
    not key-bucketed, so `EmbeddingChannel` implements this instead of
    `BlockingChannel`. Both kinds feed the same `BlockingPipeline`.
    """

    name: str

    def pairs(self, entities: Sequence[NormalizedEntity]) -> Iterable[tuple[str, str]]: ...


class ExactIdentifierChannel:
    """Block on `namespace:value` of every identifier an entity carries.

    Two entities sharing any normalized identifier value in the same namespace
    land in the same block. High precision, high recall for record pairs that
    carry authoritative ids.
    """

    name = "exact_identifier"

    def block_keys(self, entity: NormalizedEntity) -> tuple[str, ...]:
        keys: list[str] = []
        for namespace in sorted(entity.identifiers):
            for value in entity.identifiers[namespace]:
                keys.append(f"{namespace}:{value}")
        return tuple(keys)


class NormalizedNameChannel:
    """Block on the full normalized name *and* on recall-oriented sub-keys.

    A full-name block is precise; a token block and a prefix block widen recall
    for names that differ by word order, a middle name, or a typo in the tail.
    All three are emitted so a pair caught by any of them survives to scoring.
    """

    name = "normalized_name"

    def __init__(self, *, prefix_length: int = 4, emit_tokens: bool = True) -> None:
        self._prefix_length = prefix_length
        self._emit_tokens = emit_tokens

    def block_keys(self, entity: NormalizedEntity) -> tuple[str, ...]:
        keys: set[str] = set()
        for name in entity.normalized_names:
            if not name:
                continue
            keys.add(f"name:{name}")
            if len(name) >= self._prefix_length:
                keys.add(f"pfx:{name[: self._prefix_length]}")
            if self._emit_tokens:
                for token in name.split():
                    keys.add(f"tok:{token}")
        return tuple(sorted(keys))


class SourceKeyChannel:
    """Block on a prefix of the source key (its semantic namespace).

    A `semantic_key` like `player/usssa/123` shares the prefix `player/usssa`
    with its siblings; blocking on the prefix groups records from the same
    source region cheaply. The prefix is everything up to the last `/`
    (falling back to the whole key when there is no separator).
    """

    name = "source_key"

    def __init__(self, *, separator: str = "/") -> None:
        self._separator = separator

    def block_keys(self, entity: NormalizedEntity) -> tuple[str, ...]:
        key = entity.source_key
        head, sep, _tail = key.rpartition(self._separator)
        prefix = head if sep else key
        return (f"src:{prefix}",)


class EmbeddingChannel:
    """Optional ANN retrieval channel — contributes nothing without an index.

    `index=None` is the honest default: no embedding backend is required, and
    the channel yields no pairs, yet the whole pipeline still runs. When an
    index is injected, it returns each entity's `k` nearest neighbours as
    pairs. Even then, an embedding neighbourhood is only *provenance for a
    candidate pair* — it never decides a match (there is no cosine threshold
    here at all; the matcher weighs embedding similarity as one feature among
    many).
    """

    name = "embedding"

    def __init__(self, *, index: VectorIndex | None = None, k: int = 10) -> None:
        self._index = index
        self._k = k

    def pairs(self, entities: Sequence[NormalizedEntity]) -> Iterable[tuple[str, str]]:
        if self._index is None:
            return ()
        found: list[tuple[str, str]] = []
        known = {entity.source_key for entity in entities}
        for entity in entities:
            for neighbor in self._index.neighbors(entity, k=self._k):
                if neighbor != entity.source_key and neighbor in known:
                    found.append((entity.source_key, neighbor))
        return found


class BlockingPipeline:
    """Runs channels over an entity set and returns deduped, ordered pairs.

    Standard blocking within each `BlockingChannel` (entities sharing a block
    key pair up), plus any `PairProducer`s, unioned across channels. A pair
    produced by several channels is emitted once, carrying *all* their names as
    provenance. Output is sorted by endpoint so the result is byte-reproducible.

    `max_block_size` optionally drops a block whose membership exceeds it: an
    over-generic key (a stop-word token, an empty prefix) pairs the whole
    corpus without adding real recall. Left `None` (no cap) by default.
    """

    def __init__(
        self,
        channels: Sequence[BlockingChannel],
        *,
        pair_producers: Sequence[PairProducer] = (),
        max_block_size: int | None = None,
    ) -> None:
        self._channels = tuple(channels)
        self._pair_producers = tuple(pair_producers)
        self._max_block_size = max_block_size

    def block(self, entities: Sequence[NormalizedEntity]) -> tuple[CandidatePair, ...]:
        """Return the recall-oriented candidate pairs for `entities`."""
        provenance: dict[tuple[str, str], set[str]] = {}

        def add(a: str, b: str, channel: str) -> None:
            if a == b:
                return
            left, right = (a, b) if a <= b else (b, a)
            provenance.setdefault((left, right), set()).add(channel)

        for channel in self._channels:
            buckets: dict[str, list[str]] = {}
            for entity in entities:
                for block_key in channel.block_keys(entity):
                    buckets.setdefault(block_key, []).append(entity.source_key)
            for block_key, members in buckets.items():
                if self._max_block_size is not None and len(members) > self._max_block_size:
                    # Recall is unrecoverable, so a dropped block is never
                    # silent: capping an over-generic key can discard true
                    # pairs, and that must be visible, not swallowed (build
                    # plan §"No silent caps").
                    warnings.warn(
                        f"blocking channel {channel.name!r} dropped over-sized "
                        f"block {block_key!r}: {len(members)} members exceed "
                        f"max_block_size={self._max_block_size}",
                        stacklevel=2,
                    )
                    continue
                unique_members = sorted(set(members))
                for i in range(len(unique_members)):
                    for j in range(i + 1, len(unique_members)):
                        add(unique_members[i], unique_members[j], channel.name)

        for producer in self._pair_producers:
            for a, b in producer.pairs(entities):
                add(a, b, producer.name)

        pairs = [
            CandidatePair(left=left, right=right, channels=tuple(sorted(channels)))
            for (left, right), channels in provenance.items()
        ]
        pairs.sort(key=lambda p: p.key)
        return tuple(pairs)
