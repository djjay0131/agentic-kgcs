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
evidence-independent. On the canonical graph it is the assertion slot,
`(subject_identity, predicate)`: `fact_key()` derives it. It is **derived, not
stored** — `kg_contracts.assertions.Assertion` is frozen with `extra="forbid"`
and lives in another repository, so a stored fact key would be a cross-repo
contract change plus a backfill of every existing record, to hold a value that
is already a pure function of two fields every assertion carries.

**Record identity** — *this particular claim*: its evidence, its asserted
object, its valid period. `assertion_id` is the record identity, and
`record_seed()` is what it is now minted from. Supersession then operates on
*records of a fact*, which is exactly what the epoch / supersession model
(`GraphReadOptions(curation_epoch=, include_superseded=, valid_at=,
transaction_at=)`) already exists to express.

Three properties the seed is built to have:

- **A replay still collides.** Same fact, same object, same valid period, same
  evidence → the same record id, so a genuine replay is still recognised as
  the same record (and still refused by ADR-0019's `assertion_absent` guard,
  which a re-assertion under a *new* record id correctly passes).
- **New evidence is a new record.** Adding, removing, or reordering evidence
  refs changes the id, which is what makes evidence evolution representable.
- **No clock.** `recorded_at` is deliberately **not** in the seed. The planner
  is pure and clock-free and a plan carries no timestamp; seeding on
  transaction time would make "identical input produces an identical plan"
  false whenever a producer let `created_at` default to `now()`, and would
  make a replay indistinguishable from a re-assertion. Two records of one
  fact citing identical evidence at different instants are a replay, not new
  knowledge.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from kg_contracts.assertions import Assertion
from kg_contracts.evidence import EvidenceRef, ValidPeriod

#: Separator between seed components. NUL-joined, because naive concatenation
#: collides: `("ab", "c")` and `("a", "bc")` would otherwise digest identically
#: and two different records would share an id.
_PART_SEPARATOR = "\x00"

FACT_KEY_PREFIX = "fact_"


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
) -> str:
    """The `IdFactory.assertion_id` seed for **one record** of fact `fact_id`.

    `fact_id` is whatever the caller uses as the fact's stable identity — the
    `candidate_id` on the admission path (which is exactly the evidence-free
    fact identity the adopter already mints), or a `fact_key()` on the
    re-curation path, where there is no candidate. Everything after it is
    record-distinguishing content:

    - the asserted object (`object_value` or `object_identity`), so two
      different claims about one slot are two records rather than a collision —
      this is also what stops two genuinely different facts sharing a
      `candidate_id` from minting one id;
    - the valid period, so the same claim over a different domain-time interval
      is a different record;
    - the cited evidence, **in order**, so new evidence mints a new record.

    Evidence order is preserved rather than sorted: a plan's `evidence_ids` are
    already first-seen ordered and a reordered citation list is a different
    citation list. Comparing them as sets here would make the seed blind to an
    observable difference.
    """
    period = (valid_period or ValidPeriod()).model_dump(mode="json")
    evidence = [
        [ref.evidence_id, str(ref.relationship.value)] for ref in evidence_refs
    ]
    return _PART_SEPARATOR.join(
        (
            fact_id,
            "record",
            _canonical_json(object_value),
            _canonical_json(object_identity),
            _canonical_json(period),
            _canonical_json(evidence),
        )
    )


def assertion_record_seed(assertion: Assertion, *, fact_id: str | None = None) -> str:
    """`record_seed` for an existing `Assertion`, defaulting to its own fact key.

    Used on the re-curation path, where a caller holds a committed record and
    needs the record id its successor would carry.
    """
    return record_seed(
        fact_id=fact_id if fact_id is not None else assertion_fact_key(assertion),
        object_value=assertion.object_value,
        object_identity=assertion.object_identity,
        valid_period=assertion.valid_period,
        evidence_refs=assertion.evidence_refs,
    )


def _canonical_json(value: object) -> str:
    """A stable JSON rendering of `value` for hashing.

    `sort_keys` so a mapping's iteration order cannot change the id;
    `default=str` so a value the planner has not already serialized (a
    `datetime`, a `Decimal`) still yields a stable string rather than raising.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(*parts: str) -> str:
    return hashlib.sha256(
        _PART_SEPARATOR.join(parts).encode("utf-8")
    ).hexdigest()[:32]
