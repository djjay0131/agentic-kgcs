# ADR-0021: A fact's identity and a record's identity are distinct; `assertion_id` is the record

Status: Proposed
Date: 2026-09-21

## Context

A knowledge graph must be able to hold **two records of one fact**. That is
what supersession *is*: the record that stood, the record that replaced it,
both present, one current. The whole epoch/bitemporal surface —
`GraphReadOptions(curation_epoch=, include_superseded=, valid_at=,
transaction_at=)` — exists to express exactly that.

KGCS could not represent it. `CurationPlanner._assertion` minted

```python
assertion_id = self._ids.assertion_id(f"{candidate.candidate_id}:assertion")
```

so the record id was a pure function of the `candidate_id`. And the adopter's
`candidate_id` (`kgis.ids.DeterministicIdStrategy`) is derived from
`(graph_id, candidate_kind, semantic_key)` with **evidence deliberately
excluded**. Re-asserting a known fact with new evidence therefore minted the
*same* `assertion_id` as the record already in the graph. One identifier was
carrying two different jobs, and the second job silently overwrote the first.

### Independent reproduction — all three routes

`main` @ `14ffd0e`, against a clean `agentic-kgis @ origin/main` (34b5be7),
producer shape reproduced from `DeterministicIdStrategy`, executed through
`PlanExecutor` onto `E2EGraphStore` (the harness store that applies
`RETRACT_ASSERTION`; the reference `MemoryGraphStore` does not):

```
=== PREMISE: candidate_id excludes evidence ===
  candidate_id(ev_A) == candidate_id(ev_B): True
  assertion_id(ev_A) == assertion_id(ev_B): True  (as_08AGK3T0NAPST34YEAD7DNDDCP)
  seeded graph: epoch 1, first attach COMMITTED
    live   : [('as_08AGK3T0N', 2015, 'ACTIVE', ['ev_A'])]

=== ROUTE 1: re-plan at snapshot='0' (the planner default) ===
  outcome: STALE   failed: [('snapshot_version', '0')]
    live   : [('as_08AGK3T0N', 2015, 'ACTIVE', ['ev_A'])]      <-- ev_B never lands

=== ROUTE 2: re-plan at the LIVE epoch ===
  outcome: COMMITTED  epoch -> 2
    live   : [('as_08AGK3T0N', 2015, 'ACTIVE', ['ev_B'])]      <-- ev_A GONE, id reused
  reference MemoryGraphStore: COMMITTED; rows=2 distinct ids=1 <-- two rows, one id
  after one  mark_superseded: ACTIVE rows = 1
  after a second mark_superseded: ACTIVE rows = 1              <-- duplicate unreachable

=== ROUTE 3: plan_supersession — the DESIGNED path ===
  old.assertion_id == new.assertion_id: True
  ops: ['ATTACH_ASSERTION', 'RETRACT_ASSERTION']
  outcome: COMMITTED  epoch -> 2
    live   : []                                                 <-- the fact is GONE
    history: [('as_08AGK3T0N', 2015, 'SUPERSEDED', ['ev_B'])]
  FACT VISIBLE IN LIVE GRAPH: False
```

Route 3 is the worst failure mode available: the **correct** API, a **green**
result, and **silent data loss**. The plan attaches the new record and then
marks *that same id* `SUPERSEDED` — it supersedes itself. Worse than the loss
of the live fact, the one surviving historical row has had its evidence
rewritten to `ev_B`: it claims the `ev_A` record was retired while no longer
holding `ev_A`.

Route 2's damage on the reference store is a **duplicated identity**, not a
duplicated count: `MemoryGraphStore.mark_superseded` returns after its first
match and does not skip rows it has already marked, so the second row is
permanently unreachable no matter how many calls are made.

### The constraint that decides where the fix goes

Folding evidence into `candidate_id` is the tempting fix and it is **wrong**.
An evidence-free candidate identity is *correct*: the same fact arriving from
two sources is **corroboration, not two facts**. Folding evidence in would turn
every corroboration into a duplicate candidate and inflate the graph without
bound, and it would break replay idempotency on the admission ledger, whose
whole point is that re-ingesting one source row is recognised as the same
proposed fact.

So the candidate layer is right and the fix belongs in KGCS.

## Decision

**Separate the two identities that `assertion_id` was conflating, and make
`assertion_id` the record identity.**

- **Fact identity** — *what is being asserted*. Stable across re-assertions,
  evidence-independent. On the canonical graph it is the assertion slot,
  `(subject_identity, predicate)`. New module `kgcs.records` exposes it as
  `fact_key(subject_identity, predicate)` / `assertion_fact_key(assertion)`.
  It is **derived, not stored**.
- **Record identity** — *this particular claim*: what it asserts, over what
  period, **where it came from**, and what it cites. `assertion_id` is minted
  from `records.record_seed(fact_id=…, object_value=…, object_identity=…,
  valid_period=…, evidence_refs=…, provenance=…)`. The origin contributes only
  `provenance.source` / `provenance.source_ref` — *where the claim came from*,
  never *which pipeline read it* (`actor`, `model`, `prompt_version`,
  `authority` all stay out).

Three changes realize it:

1. **`CurationPlanner._assertion`** seeds `assertion_id` from `record_seed`
   over `fact_key(subject, predicate)` plus the record-distinguishing content,
   instead of from `candidate_id` alone.
2. **`ConceptEvolutionPlanner.next_record(prior, *, evidence_refs,
   recorded_at, provenance=…, authority=…, …)`** is the minting API a
   re-assertion needs on the re-curation path, where there is no candidate: it
   returns the next record of the same fact, `ACTIVE`, `superseded_at`
   cleared, `curation_epoch=0`, with a freshly minted record id. It **raises
   `ValueError`** when nothing record-distinguishing changed — that is a
   replay, not new knowledge.
3. **`records.backfill_record_id(assertion)`** recomputes the new id from a
   committed row alone, which is what makes the migration a single offline
   pass rather than a re-ingest (§Risks).
4. **`ConceptEvolutionPlanner.plan_supersession` enforces three obligations**
   it previously assumed, each `ValueError`:
   - old and new may not share an `assertion_id` (the route-3 self-supersession);
   - both must be records of the same fact (`fact_key`);
   - the superseded record must still be `ACTIVE`.

No `kg_contracts` change. No new operation type. No new precondition kind.

## Rationale

**Why the record seed contains what it contains.** Three properties were
designed for:

- *A replay still collides.* Same fact, same object, same valid period, same
  evidence in the same order → the same record id. A genuine replay is still
  recognised as the same record, which is what keeps ADR-0019's
  `assertion_absent` guard meaningful.
- *New evidence is a new record.* Adding, removing or reordering evidence refs
  changes the id. That is the property evidence evolution needs.
- *No clock.* `recorded_at` is deliberately **not** in the seed. The planner is
  pure and clock-free and a `CurationPlan` carries no timestamp; seeding on
  transaction time would make "identical input produces an identical plan"
  false for any producer that lets `created_at` default to `now()`, and would
  make a replay indistinguishable from a re-assertion. Two records of one fact
  citing identical evidence at different instants are a replay.

**Why the origin is in the seed — and why it had to be.** The first draft of
this ADR keyed the record on the evidence alone, and that draft **did not
reach the structured/tabular producer at all.** KGIS has two producer paths.
The extraction path attaches evidence to the candidate
(`kgis/extraction/runner.py`). The structured path does **not**: it links
evidence into a side SQLite registry keyed by `candidate_id`
(`kgis/structured/evidence.py`, `link()`) and never populates
`Candidate.evidence_refs` — `evidence_refs` is assigned in exactly one place
in all of `src/kgis`, and it is the extraction runner. So for every structured
candidate the seed's evidence component was the constant `[]`, two structured
sources of one fact minted one id, and route 2's silent in-place overwrite
survived unchanged, destroying the first source's `authority`,
`provenance.source_ref` and `trace_id`. On the release-critical criterion.

Adding `provenance.source` / `source_ref` closes it, and closes it with the
**same key the producer itself uses**: `kgis.structured.evidence`'s
`source_evidence_id(coordinates, snapshot_version)` derives the registry's
evidence id from the coordinates. On that path the coordinates *are* the
evidence identity, so seeding on them is not a workaround standing in for
evidence — it is the same discriminator, read from the field the contract
does keep.

It is also the right answer independently of B1. "The same fact read from
CSV-A" and "the same fact read from CSV-B" are two distinguishable claims with
different provenance and different trust; collapsing them was never
de-duplication, it was the silent destruction of one row's traceability.

**Why `source`/`source_ref` specifically, and not the full coordinates.** The
planner drops `source_coordinates.fragment` when it builds `Provenance`, so a
seed that read `fragment` could not be recomputed from a committed record —
and that recomputation is exactly what makes the migration a single offline
backfill instead of a re-ingest. The seed reads the two fields the row stores.
The cost is that two rows of one source differing only in `fragment` still
collide; stated in §Risks.

**Why the fact identity is derived rather than stored.**
`kg_contracts.assertions.Assertion` is frozen with `extra="forbid"` and lives
in a different repository. A stored fact key would be a cross-repo contract
change *plus* a backfill of every record already in every graph, to hold a
value that is already a pure function of two fields every assertion carries.
`fact_key()` costs nothing, works on records already committed, and any adapter
can compute it.

**Why `(subject, predicate)` and not `(subject, predicate, object)` — the
slot, and it is settled.** The fact is the *slot*, not the proposition. `proposed_year` corrected from 2015 to 2014
is the same fact with a new record — that is the flagship re-curation scenario
in this repo. Keying the fact on the object would make a correction "two
unrelated facts" and there would be nothing for supersession to operate on.
The object is instead part of the *record* seed, which is where it belongs and
which is also what stops two genuinely different claims sharing one
`candidate_id` from minting one id.

Three further reasons the slot reading is the right one, recorded because this
was raised as an open question and is now closed:

- The object is not lost — it is record-distinguishing, so two different
  claims about one slot are two records.
- The acknowledged cost (a multi-valued relation such as `PLAYS_FOR` treated
  as one slot) is **contained**, because `plan_supersession` is *told* which
  record to retire and now refuses the cases where it would guess wrong.
  Nothing is inferred.
- If multi-valued relations should be first-class, the right instrument is a
  per-predicate cardinality declaration in the ontology layer — not a
  proposition-keyed fact identity. Changing the fact key to fix relation
  cardinality would break corrections in order to fix a problem corrections do
  not have.

**Why the `plan_supersession` guards are refusals, not repairs.** The planner
could have silently re-minted a colliding id. It does not: the caller supplied
two records and one of the two is not what they think it is, so the honest
outcome is a loud `ValueError` naming which of the three mistakes was made.
A refusal is recoverable. The behaviour it replaces was a `COMMITTED` plan
that removed the fact from the graph.

## Alternatives Considered

### Alternative 1 — fold evidence into `candidate_id` (rejected upstream)

Make the producer's `candidate_id` depend on the cited evidence, so a
re-assertion is simply a different candidate.

Rejected, and not this repo's decision to take: an evidence-free candidate
identity is correct. The same fact from two sources is corroboration, not two
facts, so this converts every corroboration into a duplicate and inflates the
graph without bound. It also destroys ledger replay idempotency, whose purpose
is that re-ingesting one source row is the same proposed fact.

### Alternative 2 — keep `assertion_id` as the fact id and version the record in the store

Leave the id alone. Let the record identity be the **composite**
`(assertion_id, curation_epoch)` and have each adapter keep a version chain
per `assertion_id` — the graph-native "node with history" shape. `Assertion`
already carries both halves (`assertion_id`, `curation_epoch`) and
`GraphReadOptions` is already epoch-parameterised, so **no field would need to
be added**. And it has **zero migration cost**: every existing id keeps its
meaning, which is this proposal's weakest point.

**The first draft of this ADR rejected it on a claim that is false.** It said
`assertion_id` is a singular key in five places that "all live in the frozen
contract". Checked by grep against `kg_contracts`, **two of the five are real**:

| Claimed site | In frozen `kg_contracts`? | Evidence |
|---|---|---|
| `RETRACT_ASSERTION.payload["assertion_id"]` | **no** | `curation.py` types `payload` as `FrozenDictObject`; `assertion_id` appears **0** times in that file. The key is invented by KGCS. |
| the `superseded_by` field | **no — it does not exist** | `superseded_by` appears **0** times in all of `kg_contracts`. It is a key in KGCS's own untyped RETRACT payload with **no reader in `src/`**. |
| `ConflictRecord.assertion_ids` | **yes** | `assertions.py:178`, frozen, `extra="forbid"`. |
| `CurationTrigger.assertion_ids` | **no** | KGCS-local (`kgcs/recuration/triggers.py`); **0** occurrences in `kg_contracts`. Its id hashes over *sorted* ids, so it is a set, not a record key. |
| `GraphWriter.mark_superseded(assertion_id, at)` | **yes** | `stores.py:235`. |

The sharpest error was calling `superseded_by` "the one field whose entire job
is to identify a record": it is not a field, and this ADR's own sibling text
already describes it as optional. Recording the correction here rather than
quietly deleting it, because the owner was asked to accept a migration burden
on that comparison.

**The honest rejection**, resting only on what is there:

- **The cross-repo conformance suite.** `kg_contracts/testing/contract.py` —
  the reusable suite **every adapter** (memory, Neo4j, Spanner, …) runs —
  references `assertion_id` in **21** places, comparing it by equality and by
  set membership. A composite identity changes that suite, and a change there
  propagates to every backend that must keep passing it. *This* is the
  "larger contract change" argument; the first draft did not make it.
- **`ConflictRecord.preferred_assertion_id`** (`assertions.py:179`) is a real
  record pointer in the frozen contract, with a validator enforcing
  `preferred_assertion_id in assertion_ids`. It is exactly the role the first
  draft wrongly attributed to `superseded_by`, and under a composite identity
  it could no longer name a single record.
- **Per-adapter cost.** It pushes a version chain into **every** adapter
  instead of into the one place ids are minted. A uniqueness constraint on
  `assertion_id` is something every backend already has; a correct per-id
  version chain is something each must reimplement and each can get wrong.

**This is a genuinely closer call than the first draft made it look.** Against
the three points above stands a real zero-migration benefit. The judgement
recorded here is that a record identity which is a *value* — computable by
anyone holding the row, with no store cooperation — is worth a one-time
offline backfill, and that the backfill is now a stated, testable procedure
(§Risks) rather than an open problem. **The owner may reasonably decide
otherwise, and now has correct information to decide on.**

### Alternative 3 — include the whole of `authority` / `provenance` in the seed

The first draft rejected this wholesale. That was too coarse, and B1 is what
exposed it: `provenance` answers two different questions, and they deserve
different answers.

- ***Where* the claim came from** (`provenance.source`, `source_ref`) is
  **in**. It is part of what the record claims, it is what makes the fix reach
  a producer with no `evidence_refs`, and it is what stops one source's
  traceability being silently overwritten by another's.
- ***Who* processed it** (`provenance.actor`, `model`, `prompt_version`, and
  `authority`) is **out**. Renaming a producer, or re-reading one source row
  with a newer model, must not fork the record. Two pipelines reading the same
  source row and citing the same evidence are recording the same claim.

Both halves are pinned —
`TestOriginIsInTheSeedButTheProcessorIsNot::test_a_different_source_locator_is_a_different_record`
and `::test_a_different_processor_is_the_same_record` — so moving the line is
a decision, not a drift.

## Consequences

### Positive

- Evidence evolution is representable: a fact asserted on evidence A and
  re-asserted on evidence B yields **two records**, the latest live, the prior
  reachable with `include_superseded=True`, with its own evidence intact.
- Route 3 — the designed path, previously silent data loss — is the path that
  now works.
- `plan_corroboration` is fixed by the same change: a corroborating assertion
  built from the same candidate previously carried the same `assertion_id` and
  overwrote the record it was meant to corroborate.
- The open question on the ADR-0019 PR ("two different facts under one
  `candidate_id` collide") is answered: the asserted object is in the record
  seed, so they no longer collide.
- The record id is now computable from a committed `Assertion` alone, by
  anyone, with no candidate in hand — which is what turns the migration into a
  single offline backfill (`records.backfill_record_id`, §Risks).
- Two sources of one fact no longer overwrite each other's `authority`,
  `provenance.source_ref` and `trace_id`. Before this change the second source
  destroyed the first's traceability in place, on every producer that leaves
  `evidence_refs` empty.

### Negative / Tradeoffs

- **Existing graphs carry ids minted under the old seed.** See Risks — this is
  the real cost.
- Two records of one fact citing *different* evidence but never routed through
  supersession are **both `ACTIVE`**, and an ordinary read returns both with no
  ranking. That is the honest reading of corroboration, but a caller that
  wanted "the current value" must supersede, not merely re-attach.
- Record count now grows with **distinct evidence** for a fact. Bounded by the
  evidence, not unbounded, but a fact corroborated by a thousand sources has a
  thousand records unless the caller supersedes.
- The **processor** is not in the seed (Alternative 3), so two pipelines
  reading the same source row and citing the same evidence mint one record;
  whichever lands last owns the row's `actor`/`authority`. This is a deliberate
  narrowing of what used to be a much wider collapse, not its elimination.
- Two rows of **one source** that differ only in
  `source_coordinates.fragment` still collide, because the planner does not
  store `fragment` on the `Assertion` and the seed reads only what is stored
  (§Rationale). A producer that needs those apart must fold the row key into
  the locator or cite per-row evidence.

### Risks

- **Migration — a stated procedure, not an open problem.** Every
  `assertion_id` already in a graph was minted from `candidate_id` alone.
  After this change, re-planning a candidate that is already committed mints a
  **different** id, so ADR-0019's `assertion_absent` guard does not recognise
  the existing row and the re-plan attaches a *second* `ACTIVE` record of that
  fact instead of being refused. Measured, and measured again with #36's guard
  composed in: the guard does not save you.

  The first draft said "there is no automatic remedy". That was too
  pessimistic. **Every component of the new seed is stored on the row itself**,
  so the new id is a pure function of the committed record —
  `records.backfill_record_id(assertion)` recomputes exactly what the planner
  mints, pinned by `test_backfill_reproduces_exactly_what_the_planner_mints`
  across the module boundary. A **re-id backfill is therefore a single offline
  pass** over the assertion table: dry-runnable, diffable against a copy, with
  **no window** during which an ordinary read returns a duplicated fact.

  **Recommended: the re-id backfill, not an epoch boundary.** The
  epoch-boundary option (let duplicates land, reconcile by ordinary
  `plan_supersession` — which does work, unmodified, because both rows share a
  fact key and the legacy row is `ACTIVE`) leaves that duplicate window open
  for the whole reconciliation period, on a surface whose release criterion is
  that reads are correct.

  Two things the backfill must handle, both pinned by tests:

  - **The mapping is not injective.** Two legacy rows of one fact with the
    same object, valid period, origin and evidence had different
    `candidate_id`s and therefore different old ids, and map to **one** new id.
    They must be **merged** (or one superseded), never both renamed — a blind
    `UPDATE ... SET assertion_id = ...` would create exactly the
    two-rows-one-id corruption this change exists to prevent. Collisions are
    visible in the computed mapping before anything is written
    (`test_the_backfill_mapping_is_not_injective_and_collisions_are_visible`).
  - **Every reference must be rewritten, not just the key**: `superseded_by`
    in stored `RETRACT_ASSERTION` payloads, `ConflictRecord.assertion_ids`
    **and** `preferred_assertion_id` (whose membership validator rejects a
    half-done backfill), any persisted `CurationTrigger.assertion_ids`, and
    audit / execution-ledger rows.

  **The owner still rules on whether to run it**, but the decision is now
  "run this procedure" rather than "solve this problem".

- **Producer obligation 1 — locator stability.** The record identity now reads
  `provenance.source_ref`, i.e. `source_coordinates.locator`, which
  `kg_contracts` already documents as "the primary idempotency anchor" (spec
  §5.8). A producer that folds a per-run token into the locator mints a fresh
  record on every run. **KGIS's structured reader stamps `@snapshot=<version>`
  into the locator by default**, so a re-sync at a new snapshot version is a
  new record of every fact it re-reads. That is semantically honest ("as of
  snapshot v2 the source still says this") and it is loud and recoverable
  rather than silent — but it is a new record per row, and the remedies are to
  route re-syncs through supersession or to set
  `include_snapshot_in_locator=False`, which KGIS provides for exactly this.
  **This is the second thing the owner should weigh.**

- **Producer obligation 2 — evidence order.** `['ev_A','ev_B']` and
  `['ev_B','ev_A']` are different records. A plan's `evidence_ids` are
  first-seen ordered and KGIS's extraction runner appends exactly one ref per
  candidate, so this is safe today — but by an accident of the current
  extractor, not by a guarantee, and the evidence registry's own read has no
  `ORDER BY`. A producer that rehydrates refs from an unordered read must
  order them deterministically or a replay mints a new id and lands a
  duplicate. `CurationTrigger._derive_trigger_id` in this repo made the
  opposite choice (sorted, order-independent) and documented why; **sorting
  here is the available alternative** if the owner prefers removing the
  obligation to stating it.
- The `(subject, predicate)` fact key treats a multi-valued relation
  (`PLAYS_FOR` two teams at once) as one fact slot. `plan_supersession` is
  *told* which record to retire, so nothing is decided automatically — but a
  caller that supersedes one team with another has said something stronger than
  it may mean. Out of scope here.
- The absence read that ADR-0019's guard performs is still outside the store
  transaction (ADR-0019 §Risks); unchanged by this.

### What this deliberately does NOT fix

- `MemoryGraphStore.mark_superseded` still returns after the first match and
  still does not skip already-superseded rows. That is a `kg_contracts` defect
  in a different repository, and this design does not repair it.

  **Correction to the first draft**, which said the containment came from
  record ids being unique. **They are not unique per row, by design**: a
  byte-identical replay mints the *same* id — that is the property that keeps
  ADR-0019's guard meaningful — and `MemoryGraphStore.put_assertion` appends,
  so on the reference store a replay still yields two rows under one id,
  exactly as on `main`. Measured on this branch, unmodified.

  The containment that actually exists comes from two places this ADR does not
  own: **ADR-0019's `assertion_absent` guard** (PR #36), which refuses the
  replay outright, and **adapters that upsert by `assertion_id`**, which
  `e2e_harness.py` argues every real adapter must be. What this ADR *does*
  contribute is the reachable half of the second defect: `plan_supersession`
  refuses to emit a retract against a non-`ACTIVE` record
  (`test_supersession_refuses_a_record_that_is_already_superseded`).

- **The fix reaches a producer only through fields that producer populates.**
  With the origin in the seed it now reaches KGIS's structured path (§Decision,
  §Rationale), but a producer that leaves **both** `evidence_refs` empty *and*
  the locator constant across genuinely different sources would still collapse
  two records into one. The durable fix for the structured path remains
  upstream: **KGIS should populate `Candidate.evidence_refs` from its evidence
  registry** — the registry already holds the refs and the contract field
  already exists; only the join is missing. Filed as a KGIS gap
  (`kgis/structured/evidence.py`), not absorbed here.
- `recuration.ontology` builds its own plans and is untouched.
- `candidate_id`, `kgis`, and `kg_contracts` are untouched.
- No ranking or "current record" index over several `ACTIVE` records of one
  fact.

## Impacted Areas

- [ ] Product
- [x] Domain model
- [x] Data architecture
- [ ] AI architecture
- [ ] Domain-specific systems (see governance delta)
- [ ] Integrations
- [ ] UX
- [ ] Security/privacy
- [x] Implementation
- [x] Documentation

## Related Documents

- `llm/governance/adr/0019-attach-assertion-absence-precondition.md` (PR #36,
  open): the `assertion_absent` guard this change is complementary to, and
  whose open question 2 this ADR answers.
- `llm/governance/adr/0018-compensating-plans-assert-post-application-state.md`
- `llm/governance/adr/candidates/0015-reference-store-limited-operation-coverage.md`

## Related Issues / PRs

- This PR (design proposal, not merged).
- PR #36 — ADR-0019 `assertion_absent`. **Semantic interaction**, see the PR
  body. **#36 merges first, then this PR rebases and carries the rewrite of
  three of #36's tests** — not one, as the first draft said:
  `test_reassertion_under_the_same_candidate_id_is_refused_and_drops_the_evidence`,
  `test_a_plan_that_mints_one_assertion_id_twice_is_refused_as_error`, and
  `test_the_self_conflict_check_needs_no_reader`. The latter two depend on a
  construction (two different facts forced under one `candidate_id`) that no
  longer collides under this ADR. **#36's F1 guard itself stays reachable and
  correct** — a different construction still reaches it — so the rewrite is
  mechanical, but the first draft's claim that F1 was "unaffected" was wrong.
- PR #38 — ADR-0020 `REVOKE_IDENTITY` compensation. Adjacent hunks only.

## Supersedes

None.

## Superseded By

None.
