"""Targeted, incremental re-curation: what could this evidence affect? (§9 law 11).

A version bump or a new document must *not* force a full sweep of the canonical
graph — Wave-5 exit criteria and §9 law 11: "new evidence can trigger targeted
re-curation without scanning or mutating unrelated knowledge." This module
answers the dependency question — *which* canonical identities and assertions
could a piece of evidence, a candidate, or an entity ref touch? — behind a port,
so re-curation stays incremental and KGCS stays uncoupled from any particular
graph query language.

`DependencyIndex` is that port: three read-only lookups returning tuples of
affected identity/assertion ids. `InMemoryDependencyIndex` is the reference
implementation, backed entirely by *injected* maps (evidence → assertions,
assertion → owning identity, alias/semantic-key → identity). It performs map
lookups, never a scan: an unrelated identity that shares no evidence, alias, or
assertion with the input is never returned and never read. Every lookup is pure
and deterministic — results are de-duplicated in first-seen order — and nothing
here mutates anything, canonical or otherwise (a trigger only ever enqueues
work; §DG-1).

Batch scheduling *may* be layered on top (feed many triggers through
`affected_by_trigger`), but no periodic sweep is required for correctness.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

from kg_contracts.candidates import (
    AttributeAssertionCandidate,
    Candidate,
    EntityCandidate,
    RelationCandidate,
)
from kg_contracts.identity import EntityRef

from kgcs.recuration.triggers import CurationTrigger


@runtime_checkable
class DependencyIndex(Protocol):
    """The dependency-lookup port: read-only, graph-language-agnostic.

    Each method returns the canonical identity/assertion ids an input *could*
    affect — the frontier re-curation must revisit. Implementations must be pure
    and must never scan the whole graph or mutate anything.
    """

    def affected_by_evidence(self, evidence_ids: Sequence[str]) -> tuple[str, ...]:
        """Assertion (and owning identity) ids that cite any of `evidence_ids`."""
        ...

    def affected_by_candidate(self, candidate: Candidate) -> tuple[str, ...]:
        """Identity/assertion ids an incoming `candidate` could touch."""
        ...

    def affected_by_entity(self, entity_ref: str) -> tuple[str, ...]:
        """Identity/assertion ids reachable from an alias or identity ref."""
        ...


class InMemoryDependencyIndex:
    """A reference `DependencyIndex` backed by injected adjacency maps.

    Injected (all optional, all read-only):

    - `evidence_to_assertions`: evidence_id → the assertion ids it supports;
    - `assertion_to_identity`: assertion_id → its subject identity id;
    - `alias_to_identity`: an alias key or `semantic_key` → the identity it
      resolved to.

    Lookups compose these: evidence resolves to assertions and then to their
    owning identities; a candidate resolves through its `semantic_key`/aliases
    (to identities) and its `evidence_refs` (to assertions). Because every step
    is a keyed lookup, only genuinely dependent ids are returned — an unrelated
    identity is never visited (§9 law 11). The maps are stored by reference and
    only ever read.
    """

    def __init__(
        self,
        *,
        evidence_to_assertions: Mapping[str, Sequence[str]] | None = None,
        assertion_to_identity: Mapping[str, str] | None = None,
        alias_to_identity: Mapping[str, str] | None = None,
    ) -> None:
        self._evidence_to_assertions = evidence_to_assertions or {}
        self._assertion_to_identity = assertion_to_identity or {}
        self._alias_to_identity = alias_to_identity or {}

    def affected_by_evidence(self, evidence_ids: Sequence[str]) -> tuple[str, ...]:
        """Assertions citing any of `evidence_ids`, plus their owning identities."""
        collected: list[str] = []
        for evidence_id in evidence_ids:
            for assertion_id in self._evidence_to_assertions.get(evidence_id, ()):
                collected.append(assertion_id)
                identity = self._assertion_to_identity.get(assertion_id)
                if identity is not None:
                    collected.append(identity)
        return _dedupe(collected)

    def affected_by_candidate(self, candidate: Candidate) -> tuple[str, ...]:
        """Everything a candidate could touch: identities via keys, assertions via evidence."""
        collected: list[str] = []
        for key in _candidate_keys(candidate):
            identity = self._alias_to_identity.get(key)
            if identity is not None:
                collected.append(identity)
        for ref in candidate.evidence_refs:
            collected.extend(self.affected_by_evidence((ref.evidence_id,)))
        return _dedupe(collected)

    def affected_by_entity(self, entity_ref: str) -> tuple[str, ...]:
        """The identity an alias/identity ref resolves to (or itself if unknown-but-mapped)."""
        collected: list[str] = []
        identity = self._alias_to_identity.get(entity_ref)
        if identity is not None:
            collected.append(identity)
        elif entity_ref in self._assertion_to_identity.values():
            # The ref is itself a known canonical identity id.
            collected.append(entity_ref)
        return _dedupe(collected)

    def affected_by_trigger(self, trigger: CurationTrigger) -> tuple[str, ...]:
        """The full affected frontier for a trigger: its refs plus its evidence.

        Combines the trigger's explicitly named target refs (already canonical
        identity/assertion ids or concept keys) with everything its evidence
        resolves to — the one call a re-curation handler needs to know exactly
        what to revisit for this trigger, and nothing more.
        """
        collected: list[str] = []
        for identity_id in trigger.identity_ids:
            collected.extend(self.affected_by_entity(identity_id) or (identity_id,))
        collected.extend(trigger.assertion_ids)
        for concept_key in trigger.concept_keys:
            collected.extend(self.affected_by_entity(concept_key) or (concept_key,))
        collected.extend(self.affected_by_evidence(trigger.evidence_ids))
        return _dedupe(collected)


def _candidate_keys(candidate: Candidate) -> tuple[str, ...]:
    """The lookup keys a candidate offers into `alias_to_identity`.

    Always its `semantic_key` (the idempotency anchor); for an entity, its
    alias keys too; for a relation/attribute, its subject (and object) refs.
    """
    keys: list[str] = [candidate.semantic_key]
    if isinstance(candidate, EntityCandidate):
        keys.extend(_ref_key(alias) for alias in candidate.aliases)
    elif isinstance(candidate, RelationCandidate):
        keys.append(_ref_key(candidate.subject))
        keys.append(_ref_key(candidate.object))
    elif isinstance(candidate, AttributeAssertionCandidate):
        keys.append(_ref_key(candidate.subject))
    return tuple(keys)


def _ref_key(ref: EntityRef | str) -> str:
    """A stable string key for an `EntityRef` (`Type:namespace:key`) or a bare id."""
    if isinstance(ref, EntityRef):
        return ref.render()
    return ref


def _dedupe(values: Sequence[str]) -> tuple[str, ...]:
    """First-seen-order de-duplication (deterministic, replay-stable)."""
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return tuple(ordered)
