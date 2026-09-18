"""Stage 3 of entity resolution: typed pairwise features, honest-null.

Spec §7.4 (ADR-0007), step 3. Given a `CandidatePair` and its two
`NormalizedEntity` views, produce a *typed* `PairFeatures` record the matcher
can score. Two disciplines are load-bearing:

- **Honest null.** A feature that cannot be computed is `None` (or the
  `UNKNOWN` agreement), never a fabricated `0.0`. "We could not compare their
  affiliations" and "they share zero affiliations" are different facts, and a
  fake zero would let the matcher read absence as measured disagreement. Every
  optional field here defaults to absent.
- **Agreement *and* contradiction are explicit** (`FeatureAgreement`). The
  superseded v1 funnel could only say "similar / not similar"; ER must be able
  to record positive evidence *against* a match — two athletes with similar
  names on different teams at the same instant are `mutually_exclusive`, and no
  amount of name or embedding similarity should override that.

`to_vector()` flattens the features into a fixed-key-order `dict[str, float |
None]` — the exact input the matcher scores and the exact thing a decision logs,
so a stored decision is reproducible (spec §7.4: "every decision logs the full
score vector"). The mapping of the three-valued and boolean fields onto floats
is defined once, here, so matcher input and audit log can never disagree.

Name similarity is Jaro-Winkler, implemented in pure Python (no dependency):
the core package takes no heavy ER libraries (ADR-0007 keeps Splink/dedupe as
benchmarks, not dependencies).
"""

import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from kgcs.er.blocking import CandidatePair
from kgcs.er.normalize import FeatureAgreement, IdentitySignal, NormalizedEntity

# The fixed key order of `to_vector()`. Declared once so the vector is
# byte-reproducible and the matcher/audit share one schema.
FEATURE_KEYS: tuple[str, ...] = (
    "name_similarity",
    "identifier_agreement",
    "temporal_compatible",
    "geographic_distance",
    "shared_affiliations",
    "attribute_rarity",
    "source_reliability",
    "embedding_similarity",
    "neighborhood_compatibility",
    "mutually_exclusive",
)


class PairFeatures(BaseModel):
    """Typed features for one candidate pair (spec §7.4 step 3).

    Optional fields are `None` when not computable — honest null, never a fake
    zero. `identifier_agreement` is three-valued. `mutually_exclusive` is a
    plain bool because "we found positive evidence they cannot be the same" is
    always answerable (its default, `False`, means "no such evidence found",
    not "unknown").
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name_similarity: float | None = None
    identifier_agreement: FeatureAgreement = FeatureAgreement.UNKNOWN
    temporal_compatible: bool | None = None
    geographic_distance: float | None = None
    shared_affiliations: int | None = None
    attribute_rarity: float | None = None
    source_reliability: float | None = None
    embedding_similarity: float | None = None
    neighborhood_compatibility: float | None = None
    mutually_exclusive: bool = False

    def to_vector(self) -> dict[str, float | None]:
        """Flatten to a fixed-key-order `dict[str, float | None]`.

        `UNKNOWN` agreement and any absent optional stay `None` (honest null);
        `AGREE`→+1.0, `CONTRADICT`→-1.0; booleans map to 1.0/0.0. The key order
        is `FEATURE_KEYS`, so equal features always flatten to an equal,
        equally ordered dict — the reproducible matcher input and audit record.
        """
        return {
            "name_similarity": self.name_similarity,
            "identifier_agreement": _agreement_to_float(self.identifier_agreement),
            "temporal_compatible": _bool_to_float(self.temporal_compatible),
            "geographic_distance": self.geographic_distance,
            "shared_affiliations": (
                None if self.shared_affiliations is None else float(self.shared_affiliations)
            ),
            "attribute_rarity": self.attribute_rarity,
            "source_reliability": self.source_reliability,
            "embedding_similarity": self.embedding_similarity,
            "neighborhood_compatibility": self.neighborhood_compatibility,
            "mutually_exclusive": 1.0 if self.mutually_exclusive else 0.0,
        }


def _agreement_to_float(agreement: FeatureAgreement) -> float | None:
    if agreement is FeatureAgreement.AGREE:
        return 1.0
    if agreement is FeatureAgreement.CONTRADICT:
        return -1.0
    return None


def _bool_to_float(value: bool | None) -> float | None:
    return None if value is None else (1.0 if value else 0.0)


# ---------------------------------------------------------------------------
# Pure-Python similarity metrics (no dependencies)
# ---------------------------------------------------------------------------


def jaro_similarity(s1: str, s2: str) -> float:
    """Jaro similarity in [0, 1]. Two empty strings are identical (1.0)."""
    if s1 == s2:
        return 1.0
    len1, len2 = len(s1), len(s2)
    if len1 == 0 or len2 == 0:
        return 0.0
    match_distance = max(len1, len2) // 2 - 1
    s1_matches = [False] * len1
    s2_matches = [False] * len2
    matches = 0
    for i in range(len1):
        start = max(0, i - match_distance)
        end = min(i + match_distance + 1, len2)
        for j in range(start, end):
            if s2_matches[j] or s1[i] != s2[j]:
                continue
            s1_matches[i] = True
            s2_matches[j] = True
            matches += 1
            break
    if matches == 0:
        return 0.0
    transpositions = 0
    k = 0
    for i in range(len1):
        if not s1_matches[i]:
            continue
        while not s2_matches[k]:
            k += 1
        if s1[i] != s2[k]:
            transpositions += 1
        k += 1
    transpositions //= 2
    return (
        matches / len1 + matches / len2 + (matches - transpositions) / matches
    ) / 3.0


def jaro_winkler_similarity(s1: str, s2: str, *, prefix_weight: float = 0.1) -> float:
    """Jaro-Winkler: Jaro boosted for a shared prefix (up to 4 chars)."""
    jaro = jaro_similarity(s1, s2)
    prefix = 0
    for c1, c2 in zip(s1, s2):
        if c1 != c2:
            break
        prefix += 1
        if prefix == 4:
            break
    return jaro + prefix * prefix_weight * (1.0 - jaro)


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float | None:
    """Cosine similarity in [-1, 1], or `None` if lengths differ or one is zero.

    A zero-norm or mismatched-length vector is *not comparable*, so the honest
    answer is `None`, not `0.0`.
    """
    if len(a) != len(b) or not a:
        return None
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return None
    return dot / (norm_a * norm_b)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres between two lat/lon points."""
    radius = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(a)))


def _best_name_similarity(left: NormalizedEntity, right: NormalizedEntity) -> float | None:
    """Max Jaro-Winkler over the cross product of names, or `None` if either
    has no name (not computable → honest null, never 0.0)."""
    if not left.normalized_names or not right.normalized_names:
        return None
    return max(
        jaro_winkler_similarity(a, b)
        for a in left.normalized_names
        for b in right.normalized_names
    )


def _strictly_before(a: datetime | None, b: datetime | None) -> bool:
    """`a < b`, but only when the comparison is actually well-defined.

    Returns `False` (disjointness *unproven*) when either bound is absent or
    when the two datetimes disagree on tz-awareness — comparing a naive and an
    aware datetime raises `TypeError` in Python, and `valid_from`/`valid_to`
    can arrive naive from a producer's open `properties` dict. An unprovable
    disjointness must not crash; it simply is not proven, so the period is
    treated as possibly-overlapping (honest null over exception, §9 law 9).
    """
    if a is None or b is None:
        return False
    if (a.tzinfo is None) != (b.tzinfo is None):
        return False
    return a < b


def _temporal_compatible(left: NormalizedEntity, right: NormalizedEntity) -> bool | None:
    """`False` if the two valid periods are provably disjoint, `True` if they
    can overlap, `None` if either side carries no temporal data.

    Open ends are treated as unbounded: a disjointness can only be *proven*
    when both bounds it needs are present and comparable (see `_strictly_before`
    for the naive/aware guard).
    """
    if left.valid_from is None and left.valid_to is None:
        return None
    if right.valid_from is None and right.valid_to is None:
        return None
    left_before_right = _strictly_before(left.valid_to, right.valid_from)
    right_before_left = _strictly_before(right.valid_to, left.valid_from)
    return not (left_before_right or right_before_left)


def _geographic_distance(left: NormalizedEntity, right: NormalizedEntity) -> float | None:
    """Haversine km if both carry lat/lon attributes, else `None`."""
    try:
        lat1 = float(left.attributes["latitude"])
        lon1 = float(left.attributes["longitude"])
        lat2 = float(right.attributes["latitude"])
        lon2 = float(right.attributes["longitude"])
    except (KeyError, ValueError):
        return None
    return _haversine_km(lat1, lon1, lat2, lon2)


# ---------------------------------------------------------------------------
# Feature extractor port + default implementation
# ---------------------------------------------------------------------------


@runtime_checkable
class FeatureExtractor(Protocol):
    """Turns a pair + its two normalized entities (+ signals) into features."""

    def extract(
        self,
        pair: CandidatePair,
        left: NormalizedEntity,
        right: NormalizedEntity,
        *,
        signals: Sequence[IdentitySignal] = (),
    ) -> PairFeatures: ...


def _identifier_agreement(signals: Sequence[IdentitySignal]) -> FeatureAgreement:
    """Aggregate identity signals into one agreement value.

    `CONTRADICT` dominates `AGREE`: disjoint strong identifiers are decisive
    negative evidence (they make the pair mutually exclusive) and must not be
    laundered away by a coincidental agreement elsewhere. No signals → UNKNOWN.
    """
    seen_agree = False
    for signal in signals:
        if signal.agreement is FeatureAgreement.CONTRADICT:
            return FeatureAgreement.CONTRADICT
        if signal.agreement is FeatureAgreement.AGREE:
            seen_agree = True
    return FeatureAgreement.AGREE if seen_agree else FeatureAgreement.UNKNOWN


class DefaultFeatureExtractor:
    """Deterministic reference `FeatureExtractor`.

    Computes each feature from the normalized pair, leaving any it cannot
    compute as `None`/`UNKNOWN`. `mutually_exclusive` fires on *positive*
    evidence against a match — contradicting strong identifiers or provably
    disjoint valid periods — regardless of how similar the names or embeddings
    are (the §7.4 "two athletes, different teams, same time" case).

    `rarity_index` optionally maps a normalized identifier value to its corpus
    frequency; a *shared rare* identifier then yields a high `attribute_rarity`
    (1/frequency). Without it, `attribute_rarity` stays `None` — honest null.
    Namespaces the identity rules suppressed as not naming this entity type
    (an `UNKNOWN` signal) are excluded from rarity too, so an identifier
    refused as identity evidence cannot re-enter the score through this
    channel.
    """

    def __init__(self, *, rarity_index: Mapping[str, int] | None = None) -> None:
        self._rarity_index = rarity_index

    def _attribute_rarity(
        self,
        left: NormalizedEntity,
        right: NormalizedEntity,
        signals: Sequence[IdentitySignal] = (),
    ) -> float | None:
        """Rarity of the rarest *identity-bearing* value the pair shares.

        A namespace the identity rules explicitly suppressed — an `UNKNOWN`
        signal, meaning it does not name this kind of entity (ADR-0017) — is
        excluded. Otherwise a shared ISSN between two papers would re-enter the
        score through this feature's `+2.0` weight after having been refused
        entry as identity evidence, which is the same false merge by a second
        route. Namespaces the rules say nothing about are still counted: rarity
        is precisely the channel for a shared *weak* identifier.
        """
        if self._rarity_index is None:
            return None
        suppressed = {
            signal.namespace
            for signal in signals
            if signal.agreement is FeatureAgreement.UNKNOWN
        }
        best: float | None = None
        for namespace in left.identifiers.keys() & right.identifiers.keys():
            if namespace in suppressed:
                continue
            shared = set(left.identifiers[namespace]) & set(right.identifiers[namespace])
            for value in shared:
                freq = self._rarity_index.get(value)
                if freq is not None and freq > 0:
                    rarity = 1.0 / freq
                    best = rarity if best is None else max(best, rarity)
        return best

    def _shared_affiliations(
        self, left: NormalizedEntity, right: NormalizedEntity
    ) -> int | None:
        if not left.affiliations or not right.affiliations:
            return None
        return len(set(left.affiliations) & set(right.affiliations))

    def _neighborhood_compatibility(
        self, left: NormalizedEntity, right: NormalizedEntity
    ) -> float | None:
        if not left.neighbors or not right.neighbors:
            return None
        left_set, right_set = set(left.neighbors), set(right.neighbors)
        union = left_set | right_set
        return len(left_set & right_set) / len(union) if union else None

    def _embedding_similarity(
        self, left: NormalizedEntity, right: NormalizedEntity
    ) -> float | None:
        if left.embedding is None or right.embedding is None:
            return None
        return cosine_similarity(left.embedding, right.embedding)

    def _source_reliability(
        self, left: NormalizedEntity, right: NormalizedEntity
    ) -> float | None:
        if left.source_reliability is None or right.source_reliability is None:
            return None
        return min(left.source_reliability, right.source_reliability)

    def extract(
        self,
        pair: CandidatePair,
        left: NormalizedEntity,
        right: NormalizedEntity,
        *,
        signals: Sequence[IdentitySignal] = (),
    ) -> PairFeatures:
        """Produce the typed features for `pair`. Pure and deterministic."""
        agreement = _identifier_agreement(signals)
        temporal = _temporal_compatible(left, right)
        mutually_exclusive = (
            agreement is FeatureAgreement.CONTRADICT or temporal is False
        )
        return PairFeatures(
            name_similarity=_best_name_similarity(left, right),
            identifier_agreement=agreement,
            temporal_compatible=temporal,
            geographic_distance=_geographic_distance(left, right),
            shared_affiliations=self._shared_affiliations(left, right),
            attribute_rarity=self._attribute_rarity(left, right, signals),
            source_reliability=self._source_reliability(left, right),
            embedding_similarity=self._embedding_similarity(left, right),
            neighborhood_compatibility=self._neighborhood_compatibility(left, right),
            mutually_exclusive=mutually_exclusive,
        )
