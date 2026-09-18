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
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from kg_contracts.assertions import CanonicalEntity
from kg_contracts.candidates import EntityCandidate, Representation
from kg_contracts.identity import parse_identity_id
from pydantic import BaseModel, ConfigDict, Field, field_validator

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

def normalize_entity_type(entity_type: str) -> str:
    """Casefold an entity type and drop separators, for tolerant type matching.

    `"Journal"`, `"journal"` and `"Publication_Journal"`-style variants all
    reduce to the same key. Public, and exported from `kgcs.er`, so a consumer
    reading `DEFAULT_SCOPED_NAMESPACES` normalizes entity types the same way
    the rule does — the `subjects` in that mapping are already stored in this
    form, so a lookup needs no re-derivation.
    """
    return "".join(ch for ch in entity_type.casefold() if ch.isalnum())


class NamespaceScope(BaseModel):
    """What a namespace identifies, and whether disagreement in it is decisive.

    Strength is not a property of a namespace alone. It is a property of the
    **(namespace, entity type)** pair — an identifier is identity evidence only
    for the kind of thing it names — and of whether the issuing registry
    intends *one* value per subject.

    - `subjects` — the entity types this namespace identifies, stored
      normalized by `normalize_entity_type`. `SharedStrongIdentifierRule`
      treats the namespace as strong for a pair only when *both* sides carry
      one of them.
    - `contradicts` — whether disjoint values are positive evidence the two
      entities differ. True where the registry *intends* one value per subject
      (an ORCID per person). False where one subject legitimately carries
      several (a journal has a print *and* an electronic ISSN — which is why
      ISSN-L exists; a book has an ISBN-10 and an ISBN-13, and another pair per
      format). For those, agreement is still conclusive while disagreement
      means nothing, so the rule emits `AGREE` or stays honest with `UNKNOWN`
      but never manufactures a `CONTRADICT`.

    `contradicts=True` claims registry *intent*, not observed uniqueness.
    Duplicate ORCID iDs for one researcher do occur — ORCID itself publishes a
    duplicate-record merge process, and a deprecated iD redirects to a primary
    — so two disjoint ORCIDs are strong but not infallible evidence of two
    people. An adopter whose corpus is duplicate-heavy, or who resolves
    deprecated iDs after ER rather than before, can set `contradicts=False` for
    `orcid` and keep the (unaffected) `AGREE` direction.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subjects: frozenset[str]
    contradicts: bool = True

    @field_validator("subjects", mode="after")
    @classmethod
    def _normalize_subjects(cls, value: frozenset[str]) -> frozenset[str]:
        return frozenset(normalize_entity_type(subject) for subject in value)

    def names(self, left_type: str, right_type: str) -> bool:
        """True iff this namespace identifies *both* entities' types."""
        return (
            normalize_entity_type(left_type) in self.subjects
            and normalize_entity_type(right_type) in self.subjects
        )


DEFAULT_STRONG_NAMESPACES: frozenset[str] = frozenset({"doi", "vin"})
"""Namespaces strong for *whatever* entity carries them, near-uniquely.

Sharing one is powerful positive evidence; carrying disjoint ones is a
contradiction. Membership turns on one question — **does a value in this
namespace name one individual thing, the thing carrying it, whatever type that
thing is?** A DOI names one citable work (paper, dataset, chapter, software);
a VIN names one vehicle. Neither has a subject type narrow enough to be worth
enumerating, so they are unconditionally strong.

A namespace that names *one kind* of thing does not belong here — it belongs in
`DEFAULT_SCOPED_NAMESPACES`, which is where `orcid`, `issn` and `isbn` live.
Putting a scoped namespace in this set is how the false-merge defect ADR-0017
fixes was introduced.

This is a *default* — pass your own set to `SharedStrongIdentifierRule` per
domain.
"""

DEFAULT_SCOPED_NAMESPACES: Mapping[str, NamespaceScope] = MappingProxyType(
    {
        # An ORCID names a *person*. Bibliographic ingest routinely copies an
        # author's ORCID onto the work record, where a shared value means
        # "same author", not "same paper". The subject list is deliberately
        # broad: every plausible name for a person-shaped entity, including
        # role-flavoured ones, because a shared ORCID between two of *any* of
        # them still means one human. Narrowing it costs a correct merge;
        # widening it costs nothing, since none of these names a work or a
        # container. `contradicts` stays True — ORCID issues one iD per person
        # — though see the docstring for the duplicate-iD caveat.
        "orcid": NamespaceScope(
            subjects=frozenset(
                {
                    "person",
                    "people",
                    "human",
                    "individual",
                    "agent",
                    "author",
                    "coauthor",
                    "creator",
                    "contributor",
                    "researcher",
                    "scholar",
                    "academic",
                    "scientist",
                    "investigator",
                    "editor",
                    "reviewer",
                    "inventor",
                }
            ),
        ),
        # An ISSN names a *serial*, not anything published in it. Deliberately
        # NOT `venue`/`conference`: a proceedings *series* carries one ISSN
        # across unrelated conferences (LNCS 0302-9743, CEUR-WS 1613-0073,
        # PMLR 2640-3498), so promoting it for a venue type rebuilds the very
        # false merge this scoping exists to prevent.
        "issn": NamespaceScope(
            subjects=frozenset({"journal", "serial", "periodical"}),
            contradicts=False,
        ),
        # An ISBN names a *book*, not a chapter in it. One book carries an
        # ISBN-10 and an ISBN-13 (a checksum re-encoding of each other) plus a
        # distinct ISBN per format, so disagreement proves nothing.
        "isbn": NamespaceScope(
            subjects=frozenset({"book", "monograph", "bookedition"}),
            contradicts=False,
        ),
    }
)
"""Namespaces that are identity evidence only for the entity type they name.

An identifier attached to something it does not name is metadata, not identity:
an ISSN on a `Paper` says where it appeared, an ORCID on a `Paper` says who
wrote it. Neither says *which* paper it is. So an ISSN shared by two `Journal`
entities is exactly as conclusive as a shared DOI is between two papers, while
the same ISSN shared by two `Paper` entities is no evidence of identity at all.

`SharedStrongIdentifierRule` promotes a namespace listed here to strong for one
pair only when *both* sides carry one of its `subjects`. Otherwise it emits an
explicit `UNKNOWN` signal recording that the identifier was seen and
deliberately not weighed, so the suppression is auditable rather than
invisible. `NamespaceScope.contradicts` additionally governs whether disjoint
values may ever be read as positive evidence of difference.

The vocabulary is a *default*, not authority (ADR candidate 0005: the contract
carries no strong-identifier taxonomy). An adopter whose types are named
differently passes its own mapping; `{}` disables scoping entirely.
"""


@runtime_checkable
class IdentityRule(Protocol):
    """Emits explainable `IdentitySignal`s about a pair — it never merges."""

    name: str

    def evaluate(
        self, left: NormalizedEntity, right: NormalizedEntity
    ) -> tuple[IdentitySignal, ...]: ...


class SharedStrongIdentifierRule:
    """A shared strong identifier agrees; disjoint ones may contradict.

    For each namespace strong for this pair and present on *both* sides:
    `AGREE` if their value sets intersect, else `CONTRADICT` (both claim a
    value and they disagree — e.g. two different DOIs). A namespace absent from
    either side yields no signal at all (silence, not `UNKNOWN` noise). The
    rule returns evidence for the matcher; it decides nothing.

    **Strength is relative to the entity type** (ADR-0017). `strong_namespaces`
    holds namespaces strong for whatever carries them (DOI, VIN);
    `scoped_namespaces` maps a namespace to the entity types it actually names
    — an ORCID names a person, an ISSN a serial, an ISBN a book — and it is
    strong for a pair only when *both* sides are one of those types. Seen on
    any other type it yields an explicit `UNKNOWN` signal: auditable evidence
    that the identifier was observed and deliberately not weighed, which the
    feature extractor counts as neither agreement nor contradiction.

    **Disagreement is decisive only where the registry issues one value per
    subject.** A `NamespaceScope` with `contradicts=False` (ISSN, ISBN) never
    yields `CONTRADICT`: a journal legitimately carries a print *and* an
    electronic ISSN, and a book an ISBN-10 *and* an ISBN-13, so two records
    holding different values are routinely the same thing. There, agreement
    still proves identity while disagreement proves nothing.

    **Precedence (MINOR-E).** Explicit membership in `strong_namespaces` always
    wins over a scope for the same namespace, so scoping a namespace that is
    also in the strong set is a deliberate no-op rather than an error. That is
    what makes the restore-old-behaviour hatch a single argument:
    `strong_namespaces=DEFAULT_STRONG_NAMESPACES | {"issn", "isbn", "orcid"}`
    needs no matching edit to `scoped_namespaces`. It also means an adopter
    *narrowing* a namespace must remove it from `strong_namespaces`, not merely
    add a scope for it.

    **`scoped_namespaces={}` disables scoping entirely, including its knock-on
    protections (MINOR-C).** With no scope, an off-subject identifier produces
    no signal at all rather than an `UNKNOWN` one — and because
    `DefaultFeatureExtractor._attribute_rarity` keys its suppression off that
    `UNKNOWN`, a shared ISSN between two papers becomes rarity-eligible again
    (measured p=0.918754 with a `rarity_index` injected, versus 0.604806 under
    the defaults). This is the literal meaning of "no scoping" and the only way
    to express it, but it is a wider opt-out than it looks. To *narrow* the
    vocabulary, pass a mapping containing the namespaces you want rather than
    an empty one.
    """

    name = "shared_strong_identifier"

    def __init__(
        self,
        *,
        strong_namespaces: frozenset[str] = DEFAULT_STRONG_NAMESPACES,
        scoped_namespaces: Mapping[str, NamespaceScope] = DEFAULT_SCOPED_NAMESPACES,
    ) -> None:
        self._strong = strong_namespaces
        # An explicitly strong namespace outranks a scope for it.
        self._scoped = {
            namespace: scope
            for namespace, scope in scoped_namespaces.items()
            if namespace not in strong_namespaces
        }

    def _suppressed(
        self, namespace: str, scope: NamespaceScope, left: NormalizedEntity, right: NormalizedEntity
    ) -> IdentitySignal:
        """An auditable `UNKNOWN`: seen, and deliberately not weighed."""
        return IdentitySignal(
            rule=self.name,
            namespace=namespace,
            agreement=FeatureAgreement.UNKNOWN,
            detail=(
                f"{namespace} names {sorted(scope.subjects)!r}, not "
                f"{left.entity_type!r}/{right.entity_type!r}; "
                "not weighed as identity evidence"
            ),
        )

    def evaluate(
        self, left: NormalizedEntity, right: NormalizedEntity
    ) -> tuple[IdentitySignal, ...]:
        signals: list[IdentitySignal] = []
        for namespace in sorted(self._strong | set(self._scoped)):
            left_values = set(left.identifiers.get(namespace, ()))
            right_values = set(right.identifiers.get(namespace, ()))
            if not left_values or not right_values:
                continue
            scope = self._scoped.get(namespace)
            if scope is not None and not scope.names(left.entity_type, right.entity_type):
                signals.append(self._suppressed(namespace, scope, left, right))
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
            elif scope is not None and not scope.contradicts:
                # Multi-valued by design: one subject legitimately carries
                # several values (print/electronic ISSN, ISBN-10/ISBN-13), so
                # disjointness is not evidence that the subjects differ.
                signals.append(
                    IdentitySignal(
                        rule=self.name,
                        namespace=namespace,
                        agreement=FeatureAgreement.UNKNOWN,
                        detail=(
                            f"disjoint {namespace}: {sorted(left_values)!r} vs "
                            f"{sorted(right_values)!r}; one {left.entity_type!r} may carry "
                            f"several {namespace} values, so this is not a contradiction"
                        ),
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
