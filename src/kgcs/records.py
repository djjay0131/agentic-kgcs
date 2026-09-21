"""Fact identity vs. record identity — the two things an `assertion_id` was.

A knowledge graph has to hold *two* records of one fact. That is what
supersession **is**: the record that stood, and the record that replaced it,
both present, one current. Before ADR-0021 KGCS could not represent that,
because `assertion_id` was a pure function of `candidate_id`:

    assertion_id = as_ + sha256(f"{candidate_id}:assertion")

and an adopter's `candidate_id` is derived from `(graph_id, candidate_kind,
semantic_key)` with **evidence deliberately excluded**. So re-asserting a known
fact with new evidence minted the *same* `assertion_id`, and the three routes a
caller could take all failed:

1. re-plan at the default snapshot `"0"` → `STALE`; the new evidence never lands;
2. re-plan at the live epoch → `COMMITTED` by in-place overwrite — the prior
   record's evidence is gone and the reference store holds two rows under one
   id, one of them permanently unreachable;
3. `plan_supersession` — the *designed* path — → `COMMITTED`, and the fact
   **disappears from the live graph**, because the new record superseded
   itself.

The evidence-free candidate identity is **correct and is not the bug**: the
same fact arriving from two sources is corroboration, not two facts, and
folding evidence into `candidate_id` would turn every corroboration into a
duplicate. The bug is that one identifier was carrying two different jobs.
This module separates them.

**Fact identity** — *what is being asserted*. Stable across re-assertions,
source-independent, evidence-independent. On the canonical graph it is the
assertion slot, `(subject_identity, predicate)`: `fact_key()` derives it. It is
**derived, not stored** — `kg_contracts.assertions.Assertion` is frozen with
`extra="forbid"` and lives in another repository, so a stored fact key would be
a cross-repo contract change plus a backfill of every existing record, to hold
a value that is already a pure function of two fields every assertion carries.

**Record identity** — *this particular claim*: what it asserts, over what
period, **where it came from**, and what it cites. `assertion_id` is the record
identity, and `record_seed()` is what it is now minted from. Supersession then
operates on *records of a fact*, which is exactly what the epoch / supersession
model (`GraphReadOptions(curation_epoch=, include_superseded=, valid_at=,
transaction_at=)`) already exists to express.

## What is in the seed, and why

- **the fact** (`fact_id`) — two records collide only within one fact;
- **the asserted object** — two different claims about one slot are two
  records, not a collision (this is also what stops two genuinely different
  facts sharing a `candidate_id` from minting one id);
- **the valid period** — the same claim over a different domain-time interval
  is a different record;
- **the origin** (`provenance.source` / `provenance.source_ref`) — *where the
  claim came from*. See "the provenance component" below: without it the fix
  does not reach any producer that leaves `evidence_refs` empty, and the
  loser's provenance is silently destroyed;
- **the cited evidence**, in order — new evidence mints a new record.

Deliberately **not** in the seed:

- **`recorded_at` / any clock.** The planner is pure and clock-free and a plan
  carries no timestamp; seeding on transaction time would make "identical input
  produces an identical plan" false whenever a producer let `created_at`
  default to `now()`, and would make a replay indistinguishable from a
  re-assertion. Two records of one fact citing identical evidence from the same
  origin at different instants are a replay, not new knowledge.
- **who processed the claim** — `provenance.actor`, `provenance.model`,
  `provenance.prompt_version`, `authority`. The seed holds *what is claimed and
  where it came from*, never *which pipeline read it*. So renaming a producer,
  or re-reading one source row with a newer model, does not fork the record.

## Two producer-side obligations this identity rests on

Both are stated because an identity function that silently depends on an
accident of the current producer is the failure this module exists to prevent.

1. **`source_coordinates.locator` must be stable across re-ingests of the same
   source row.** `kg_contracts.candidates.SourceCoordinates` already requires
   this — it is documented there as "the primary idempotency anchor" (spec
   §5.8). A producer that folds a per-run token into the locator mints a fresh
   record on every run. KGIS's structured reader stamps `@snapshot=<version>`
   into the locator **by default**, so a re-sync at a new snapshot version is a
   new record of every fact it re-reads — semantically honest ("as of snapshot
   v2 the source still says this"), but it is a new record per row, and the
   remedies are to supersede or to set `include_snapshot_in_locator=False`.
   ADR-0021 §Risks.
2. **`evidence_refs` order is observable.** `['ev_A','ev_B']` and
   `['ev_B','ev_A']` are different records. A plan's `evidence_ids` are
   first-seen ordered and KGIS's extraction runner appends exactly one ref per
   candidate, so this is safe today — but it is safe by an accident of the
   current extractor, not by a guarantee. A producer that rehydrates refs from
   an unordered read (the evidence registry's own query has no `ORDER BY`) must
   order them deterministically, or a replay mints a new id and lands a
   duplicate. `CurationTrigger._derive_trigger_id` in this repo made the
   opposite choice — sorted, order-independent — and sorting here is the
   alternative if the owner prefers to remove the obligation rather than state
   it. ADR-0021 §Risks.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import date, datetime

from kg_contracts.assertions import Assertion
from kg_contracts.evidence import EvidenceRef, Provenance, ValidPeriod

from kgcs.ids import DerivedIdFactory, IdFactory

#: Separator between seed components. NUL-joined, because naive concatenation
#: collides: `("ab", "c")` and `("a", "bc")` would otherwise digest identically
#: and two different records would share an id.
_PART_SEPARATOR = "\x00"

#: Tag prefix for a value JSON cannot render natively. A *tagged* rendering,
#: never a bare string: `datetime(2015, 1, 1)` and the string
#: `"2015-01-01T00:00:00"` are different claims and must not digest alike.
_TEMPORAL_TAG = "__temporal__"

FACT_KEY_PREFIX = "fact_"


class UnstableRecordValueError(TypeError):
    """An `object_value` that cannot be rendered reproducibly.

    Raised rather than digested. `Assertion.object_value` is typed
    `object | None`, so an arbitrary object is *type*-permitted, and the
    obvious fallback — `json.dumps(..., default=str)` — digests such an
    object's **memory address**, so two runs over the same logical input mint
    different record ids. That silently breaks the guarantee this whole module
    exists to serve ("identical input produces an identical plan",
    `kgcs.ids`), and it breaks it in the one direction that cannot be noticed:
    the ids merely differ.

    Refusing loses nothing real. An operation payload must survive
    `CurationPlan.model_dump_json` (ADR-0010, the executor seam), so a value
    JSON cannot render was never applicable in the first place — this turns a
    plan that would fail later, or silently duplicate a record, into an error
    at the point the value enters.
    """


def fact_key(subject_identity: str, predicate: str) -> str:
    """The stable identity of the *fact* `(subject_identity, predicate)` names.

    The assertion slot, not the proposition: `proposed_year` corrected from
    2015 to 2014 is the **same fact** with a new record, which is why the
    asserted object is not part of the key. Two records share a fact key iff
    one may supersede the other.

    Derived rather than stored — see the module docstring. Pure: no clock, no
    randomness, stable across processes.
    """
    return FACT_KEY_PREFIX + _digest(subject_identity, predicate)


def assertion_fact_key(assertion: Assertion) -> str:
    """`fact_key` for a record already in the graph (or already built)."""
    return fact_key(assertion.subject_identity, assertion.predicate)


def record_seed(
    *,
    fact_id: str,
    object_value: object | None,
    object_identity: str | None,
    valid_period: ValidPeriod | None,
    evidence_refs: Sequence[EvidenceRef],
    provenance: Provenance | None = None,
) -> str:
    """The `IdFactory.assertion_id` seed for **one record** of fact `fact_id`.

    `fact_id` is the fact's stable identity — `fact_key(subject, predicate)` on
    both the admission and the re-curation path. Everything after it is
    record-distinguishing content; the module docstring says what is in and
    what is out, and why.

    `provenance` contributes **only** `source` and `source_ref` — where the
    claim came from, never who processed it. Those are the two fields an
    `Assertion` actually stores (the planner drops
    `source_coordinates.fragment` when it builds `Provenance`), which is what
    keeps `assertion_record_seed` over a committed row byte-identical to what
    the planner minted for it — the property the ADR-0021 migration backfill
    rests on. Two rows of one source that differ only in `fragment` therefore
    still collide; that is stated in ADR-0021 §Risks rather than papered over
    by seeding on a field the graph does not keep.

    `provenance=None` is the honest "no origin recorded" case and is rendered
    as such, not as an absent component — a record with no stated origin must
    not accidentally digest like one that has an empty-string origin.

    Raises `UnstableRecordValueError` if `object_value` cannot be rendered
    reproducibly.
    """
    period = (valid_period or ValidPeriod()).model_dump(mode="json")
    evidence = [[ref.evidence_id, str(ref.relationship.value)] for ref in evidence_refs]
    origin = (
        None if provenance is None else [provenance.source, provenance.source_ref]
    )
    return _PART_SEPARATOR.join(
        (
            fact_id,
            "record",
            _canonical_json(object_value),
            _canonical_json(object_identity),
            _canonical_json(period),
            _canonical_json(origin),
            _canonical_json(evidence),
        )
    )


def assertion_record_seed(assertion: Assertion, *, fact_id: str | None = None) -> str:
    """`record_seed` for an existing `Assertion`, defaulting to its own fact key.

    Used on the re-curation path, where a caller holds a committed record and
    needs the record id its successor would carry — and by
    `backfill_record_id`, because every component it reads is stored on the
    row itself.
    """
    return record_seed(
        fact_id=fact_id if fact_id is not None else assertion_fact_key(assertion),
        object_value=assertion.object_value,
        object_identity=assertion.object_identity,
        valid_period=assertion.valid_period,
        evidence_refs=assertion.evidence_refs,
        provenance=assertion.provenance,
    )


def backfill_record_id(
    assertion: Assertion, *, id_factory: IdFactory | None = None
) -> str:
    """The ADR-0021 record id for a row minted under the **old** seed.

    The migration helper. Every component of the new seed is stored on the
    assertion itself, so the new id is a pure function of the row: no
    candidate, no producer, no re-ingest, no clock. A backfill is therefore a
    single offline pass over the assertion table that can be dry-run and
    diffed against a copy before anything is written, with no window during
    which an ordinary read returns a duplicated fact.

    Two obligations the caller carries, both in ADR-0021 §Risks:

    - **The mapping is not injective.** Two legacy rows of one fact with the
      same object, valid period, origin and evidence had different
      `candidate_id`s and therefore different old ids, and map to **one** new
      id. They must be **merged** (or one superseded), never both renamed — a
      blind `UPDATE ... SET assertion_id = ...` would create exactly the
      two-rows-one-id corruption this change exists to prevent. Detect it by
      checking for collisions in the computed mapping *before* writing.
    - **Every reference must be rewritten, not just the key**: `superseded_by`
      in stored `RETRACT_ASSERTION` payloads, `ConflictRecord.assertion_ids`
      **and** `preferred_assertion_id` (whose membership validator will reject
      a half-done backfill), any persisted `CurationTrigger.assertion_ids`, and
      audit / execution-ledger rows.

    `id_factory` defaults to `DerivedIdFactory`, which is what the planner and
    the engine default to; pass the same factory the graph was written with if
    it was written with another.
    """
    factory = id_factory if id_factory is not None else DerivedIdFactory()
    return factory.assertion_id(assertion_record_seed(assertion))


def _canonical_json(value: object) -> str:
    """A stable JSON rendering of `value` for hashing.

    `sort_keys` so a mapping's iteration order cannot change the id.
    `datetime`/`date` are rendered as a **tagged** ISO pair so they can neither
    raise nor collide with a string of the same text. Anything else JSON
    cannot render natively raises `UnstableRecordValueError` — see that class
    for why refusing beats digesting.

    A `tuple` renders identically to the equivalent `list`, deliberately: the
    stored payload goes through `model_dump(mode="json")`, which turns a tuple
    into a list, so they *are* the same record once committed. Digesting them
    apart would mint two ids for one stored row.
    """
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    except TypeError:
        pass
    if isinstance(value, (datetime, date)):
        return json.dumps([_TEMPORAL_TAG, value.isoformat()], separators=(",", ":"))
    raise UnstableRecordValueError(
        f"object_value of type {type(value).__name__!r} cannot be rendered "
        "reproducibly, so it cannot contribute to a record id: two runs over "
        "the same logical input would mint different ids. Such a value also "
        "cannot survive CurationPlan.model_dump_json (the executor seam), so "
        "it was never applicable. Convert it to a JSON-native value first."
    )


def _digest(*parts: str) -> str:
    return hashlib.sha256(_PART_SEPARATOR.join(parts).encode("utf-8")).hexdigest()[:32]
