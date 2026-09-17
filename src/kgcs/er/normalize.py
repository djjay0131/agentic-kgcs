"""Stage 1 of entity resolution: normalization + deterministic identity rules.

Spec §7.4 (ADR-0007), step 1. The v1 embedding-threshold funnel is superseded;
the first thing ER does is *not* compare embeddings but produce a stable,
deterministic normalized view of every entity and run explainable exact rules
over strong identifiers. Two disciplines from that spec are load-bearing here:

- **Normalization never merges.** It only produces a canonical form
  (`NormalizedEntity`). Deciding two entities are the same is the matcher's
  job (§7.4 step 4) behind a policy gate (step 6, a later wave) — never a
  side effect of casefolding a name.
- **Identity rules produce explainable evidence, not silent merges.** A
  `SharedStrongIdentifierRule` emits an `IdentitySignal` (which namespace,
  agreement or contradiction, and why) that flows into the typed features and
  the matcher. A shared DOI is strong evidence *for* the matcher to weigh, not
  an authority to collapse two records.

Everything here is a pure, deterministic function of its input: the same
`EntityCandidate` always normalizes to the same `NormalizedEntity`, and
normalization is idempotent (normalizing an already-normalized name is a
no-op). No clock, no graph, no randomness, no network.

`FeatureAgreement` lives here rather than in `features` because it is the
lowest layer both an `IdentitySignal` (this module) and `PairFeatures`
(`features`) speak, and `features` imports from `normalize`, never the reverse.
"""

import unicodedata
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from kg_contracts.assertions import CanonicalEntity
from kg_contracts.candidates import EntityCandidate, Representation
from kg_contracts.identity import parse_identity_id
from pydantic import BaseModel, ConfigDict, Field

# A normalizable source is either a proposed identity (`EntityCandidate`) or an
# already-accepted one (`CanonicalEntity`); ER runs the same substrate over
# both — resolving a new candidate against the canonical graph.
NormalizableEntity = EntityCandidate | CanonicalEntity


class FeatureAgreement(StrEnum):
    """Three-valued agreement of a signal or feature (spec §7.4 step 3).

    `UNKNOWN` is the honest-null value: a feature that could not be computed
    (e.g. neither entity carries the identifier) is `UNKNOWN`, never a
    fabricated `AGREE`/`CONTRADICT`. `CONTRADICT` records positive evidence
    *against* a match (disjoint strong identifiers), not merely absence.
    """

    AGREE = "AGREE"
    CONTRADICT = "CONTRADICT"
    UNKNOWN = "UNKNOWN"


class NormalizedEntity(BaseModel):
    """A stable, comparable normalized view of one entity, built for ER.

    Keyed by `source_key` (a candidate's `semantic_key`, or a canonical
    entity's `identity_id`) — the stable handle blocking and matching carry
    instead of a whole model. Every string field is already normalized;
    `identifiers` maps a namespace to the *sorted, de-duplicated* tuple of its
    normalized values, so an entity carrying two aliases in one namespace is
    represented honestly rather than one overwriting the other. Absent data is
    an empty tuple / `None`, never a fabricated zero.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_key: str = Field(min_length=1)
    graph_id: str
    entity_type: str
    normalized_names: tuple[str, ...] = ()
    identifiers: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    affiliations: tuple[str, ...] = ()
    neighbors: tuple[str, ...] = ()
    attributes: dict[str, str] = Field(default_factory=dict)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    source_reliability: float | None = None
    embedding: tuple[float, ...] | None = None


class IdentitySignal(BaseModel):
    """One explainable identity-rule signal about a pair (spec §7.4 step 1).

    A rule *never* merges — it emits this. `namespace` and `detail` make the
    signal auditable (which identifier fired, and on what values), and
    `agreement` says whether the identifier agrees, contradicts, or is silent.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule: str = Field(min_length=1)
    namespace: str
    agreement: FeatureAgreement
    detail: str | None = None


# ---------------------------------------------------------------------------
# Deterministic string / identifier normalization primitives
# ---------------------------------------------------------------------------


def normalize_name(text: str, *, nfkc: bool = True) -> str:
    """Casefold + trim + collapse internal whitespace (optionally NFKC first).

    Idempotent: `normalize_name(normalize_name(x)) == normalize_name(x)`.
    NFKC folds compatibility variants (e.g. full-width forms) before casing so
    visually equal names normalize equal.
    """
    if nfkc:
        text = unicodedata.normalize("NFKC", text)
    # str.split() with no argument trims and collapses all runs of whitespace.
    return " ".join(text.split()).casefold()


def _normalize_doi(value: str) -> str:
    """DOIs are case-insensitive; strip the resolver prefix and a `doi:` scheme."""
    lowered = value.strip().casefold()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        if lowered.startswith(prefix):
            lowered = lowered[len(prefix):]
            break
    return lowered


def _normalize_orcid(value: str) -> str:
    """ORCID identity is its 16 digits (final char may be the checksum `X`)."""
    return "".join(ch for ch in value.strip().upper() if ch.isdigit() or ch == "X")


def _normalize_default_identifier(value: str) -> str:
    """Fallback identifier rule: casefold + trim. Never invents structure."""
    return value.strip().casefold()


_DEFAULT_IDENTIFIER_RULES: dict[str, Callable[[str], str]] = {
    "doi": _normalize_doi,
    "orcid": _normalize_orcid,
}
"""Per-namespace identifier normalization. A namespace with no entry falls
back to `_normalize_default_identifier`. Extend via the `DefaultNormalizer`
constructor rather than editing this module-level default."""


# ---------------------------------------------------------------------------
# Normalizer port + default implementation
# ---------------------------------------------------------------------------


@runtime_checkable
class Normalizer(Protocol):
    """Maps an `EntityCandidate` or `CanonicalEntity` to a `NormalizedEntity`."""

    def normalize(self, source: NormalizableEntity) -> NormalizedEntity: ...


TypeHook = Callable[[NormalizableEntity], Mapping[str, str]]
"""A per-`entity_type` hook contributing extra normalized `attributes`
(e.g. a `Person` hook that splits and normalizes a name). Pure, and it only
*adds* attributes — it can never merge two entities."""


def _as_str_tuple(value: object, *, nfkc: bool) -> tuple[str, ...]:
    """Normalize a property value into a sorted, de-duplicated string tuple."""
    if isinstance(value, (list, tuple)):
        items = [normalize_name(str(v), nfkc=nfkc) for v in value]
    elif isinstance(value, str):
        items = [normalize_name(value, nfkc=nfkc)]
    else:
        return ()
    return tuple(sorted({item for item in items if item}))


def _as_datetime(value: object) -> datetime | None:
    """Read a property as a `datetime`, or `None` if it is not one."""
    return value if isinstance(value, datetime) else None


def _as_float(value: object) -> float | None:
    """Read a property as a `float`, or `None` if it is not numeric."""
    if isinstance(value, bool):  # bool is an int subclass; never a reliability
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _first_vector(representations: Mapping[str, Representation]) -> tuple[float, ...] | None:
    """The vector of the first `kind='vector'` representation, by sorted key.

    Deterministic: representations are keyed by name, so scanning in sorted
    key order picks the same one on every run.
    """
    for name in sorted(representations):
        rep = representations[name]
        if rep.kind == "vector" and rep.vector is not None:
            return rep.vector
    return None


class DefaultNormalizer:
    """Deterministic reference `Normalizer`.

    Injected with: identifier rules (merged over the module defaults),
    per-`entity_type` hooks (a sane default is *no* extra attributes), and an
    `nfkc` flag. Holds no mutable state; `normalize` is a pure function.
    """

    def __init__(
        self,
        *,
        nfkc: bool = True,
        identifier_rules: Mapping[str, Callable[[str], str]] | None = None,
        type_hooks: Mapping[str, TypeHook] | None = None,
    ) -> None:
        self._nfkc = nfkc
        self._identifier_rules: dict[str, Callable[[str], str]] = {
            **_DEFAULT_IDENTIFIER_RULES,
            **(identifier_rules or {}),
        }
        self._type_hooks: dict[str, TypeHook] = dict(type_hooks or {})

    def _normalize_identifier(self, namespace: str, key: str) -> str:
        rule = self._identifier_rules.get(namespace, _normalize_default_identifier)
        return rule(key)

    def normalize(self, source: NormalizableEntity) -> NormalizedEntity:
        """Produce the normalized view. Pure; never merges."""
        identifiers: dict[str, list[str]] = {}
        for alias in source.aliases:
            value = self._normalize_identifier(alias.namespace, alias.key)
            if value:
                identifiers.setdefault(alias.namespace, []).append(value)
        normalized_identifiers = {
            namespace: tuple(sorted(set(values)))
            for namespace, values in sorted(identifiers.items())
        }

        names: list[str] = []
        if source.display_name:
            names.append(normalize_name(source.display_name, nfkc=self._nfkc))

        attributes: dict[str, str] = {}
        affiliations: tuple[str, ...] = ()
        neighbors: tuple[str, ...] = ()
        valid_from: datetime | None = None
        valid_to: datetime | None = None
        embedding: tuple[float, ...] | None = None
        source_reliability: float | None = None

        if isinstance(source, EntityCandidate):
            source_key = source.semantic_key
            graph_id = source.graph_id
            source_reliability = source.scores.source_reliability
            embedding = _first_vector(source.representations)
            props = source.properties
            affiliations = _as_str_tuple(props.get("affiliations"), nfkc=self._nfkc)
            neighbors = _as_str_tuple(props.get("neighbors"), nfkc=self._nfkc)
            valid_from = _as_datetime(props.get("valid_from"))
            valid_to = _as_datetime(props.get("valid_to"))
            for extra_name in ("name", "full_name"):
                extra = props.get(extra_name)
                if isinstance(extra, str) and extra.strip():
                    names.append(normalize_name(extra, nfkc=self._nfkc))
            latitude = _as_float(props.get("latitude"))
            longitude = _as_float(props.get("longitude"))
            if latitude is not None and longitude is not None:
                attributes["latitude"] = repr(latitude)
                attributes["longitude"] = repr(longitude)
        else:  # CanonicalEntity
            source_key = source.identity_id
            graph_id, _ = parse_identity_id(source.identity_id)

        hook = self._type_hooks.get(source.entity_type)
        if hook is not None:
            for hook_key, hook_value in sorted(hook(source).items()):
                attributes[hook_key] = hook_value

        return NormalizedEntity(
            source_key=source_key,
            graph_id=graph_id,
            entity_type=source.entity_type,
            normalized_names=tuple(dict.fromkeys(names)),  # dedupe, keep order
            identifiers=normalized_identifiers,
            affiliations=affiliations,
            neighbors=neighbors,
            attributes=attributes,
            valid_from=valid_from,
            valid_to=valid_to,
            source_reliability=source_reliability,
            embedding=embedding,
        )


# ---------------------------------------------------------------------------
# Identity rules
# ---------------------------------------------------------------------------

DEFAULT_STRONG_NAMESPACES: frozenset[str] = frozenset({"doi", "orcid", "isbn", "vin", "issn"})
"""Namespaces whose values are strong (near-unique) identifiers. Sharing one
is powerful positive evidence; carrying disjoint ones is a contradiction. This
is a *default* — pass your own set to `SharedStrongIdentifierRule` per domain."""


@runtime_checkable
class IdentityRule(Protocol):
    """Emits explainable `IdentitySignal`s about a pair — it never merges."""

    name: str

    def evaluate(
        self, left: NormalizedEntity, right: NormalizedEntity
    ) -> tuple[IdentitySignal, ...]: ...


class SharedStrongIdentifierRule:
    """A shared strong identifier agrees; disjoint ones in a namespace contradict.

    For each strong namespace present in *both* entities: `AGREE` if their
    value sets intersect, else `CONTRADICT` (both claim a value in that
    namespace and they disagree — e.g. two different DOIs). A namespace absent
    from either side yields no signal at all (silence, not `UNKNOWN` noise).
    The rule returns evidence for the matcher; it decides nothing.
    """

    name = "shared_strong_identifier"

    def __init__(self, *, strong_namespaces: frozenset[str] = DEFAULT_STRONG_NAMESPACES) -> None:
        self._strong = strong_namespaces

    def evaluate(
        self, left: NormalizedEntity, right: NormalizedEntity
    ) -> tuple[IdentitySignal, ...]:
        signals: list[IdentitySignal] = []
        for namespace in sorted(self._strong):
            left_values = set(left.identifiers.get(namespace, ()))
            right_values = set(right.identifiers.get(namespace, ()))
            if not left_values or not right_values:
                continue
            shared = left_values & right_values
            if shared:
                signals.append(
                    IdentitySignal(
                        rule=self.name,
                        namespace=namespace,
                        agreement=FeatureAgreement.AGREE,
                        detail=f"shared {namespace}={sorted(shared)!r}",
                    )
                )
            else:
                signals.append(
                    IdentitySignal(
                        rule=self.name,
                        namespace=namespace,
                        agreement=FeatureAgreement.CONTRADICT,
                        detail=(
                            f"disjoint {namespace}: "
                            f"{sorted(left_values)!r} vs {sorted(right_values)!r}"
                        ),
                    )
                )
        return tuple(signals)


def run_identity_rules(
    rules: Sequence[IdentityRule], left: NormalizedEntity, right: NormalizedEntity
) -> tuple[IdentitySignal, ...]:
    """Run every rule over a pair and concatenate the signals, in rule order."""
    signals: list[IdentitySignal] = []
    for rule in rules:
        signals.extend(rule.evaluate(left, right))
    return tuple(signals)
