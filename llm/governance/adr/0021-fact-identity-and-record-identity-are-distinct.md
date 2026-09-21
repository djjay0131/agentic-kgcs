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
- **Record identity** — *this particular claim*: its asserted object, its valid
  period, its cited evidence. `assertion_id` is minted from
  `records.record_seed(fact_id=…, object_value=…, object_identity=…,
  valid_period=…, evidence_refs=…)`.

Three changes realize it:

1. **`CurationPlanner._assertion`** seeds `assertion_id` from `record_seed`
   over `fact_key(subject, predicate)` plus the record-distinguishing content,
   instead of from `candidate_id` alone.
2. **`ConceptEvolutionPlanner.next_record(prior, *, evidence_refs,
   recorded_at, …)`** is the minting API a re-assertion needs on the
   re-curation path, where there is no candidate: it returns the next record of
   the same fact, `ACTIVE`, `superseded_at` cleared, `curation_epoch=0`, with a
   freshly minted record id. It **raises `ValueError`** when nothing
   record-distinguishing changed — that is a replay, not new knowledge.
3. **`ConceptEvolutionPlanner.plan_supersession` enforces three obligations**
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

**Why the fact identity is derived rather than stored.**
`kg_contracts.assertions.Assertion` is frozen with `extra="forbid"` and lives
in a different repository. A stored fact key would be a cross-repo contract
change *plus* a backfill of every record already in every graph, to hold a
value that is already a pure function of two fields every assertion carries.
`fact_key()` costs nothing, works on records already committed, and any adapter
can compute it.

**Why `(subject, predicate)` and not `(subject, predicate, object)`.** The fact
is the *slot*, not the proposition. `proposed_year` corrected from 2015 to 2014
is the same fact with a new record — that is the flagship re-curation scenario
in this repo. Keying the fact on the object would make a correction "two
unrelated facts" and there would be nothing for supersession to operate on.
The object is instead part of the *record* seed, which is where it belongs and
which is also what stops two genuinely different claims sharing one
`candidate_id` from minting one id.

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
`(assertion_id, curation_epoch)`, and have each adapter keep a version chain
per `assertion_id` — the graph-database-native "node with a history" shape.
Attractive because it needs **no id change at all**: every existing id keeps
its meaning and there is zero migration burden, which is this proposal's
weakest point.

Rejected on blast radius:

- `assertion_id` is used as a **singular** key in at least five places that
  would all become ambiguous and all live in the frozen contract:
  `RETRACT_ASSERTION.payload["assertion_id"]`, the `superseded_by` field,
  `ConflictRecord.assertion_ids`, `CurationTrigger.assertion_ids`, and
  `GraphWriter.mark_superseded(assertion_id, at)`. Each would need a composite
  key, so this is a *larger* `kg_contracts` change than the one it avoids, not
  a smaller one.
- `superseded_by=<assertion_id>` could no longer name *which record* superseded
  which — the one field whose entire job is to identify a record.
- It pushes the work into **every** adapter (Neo4j, Spanner, the reference
  store) as a per-id version chain, instead of into the one place ids are
  minted. A uniqueness constraint on `assertion_id` is something every backend
  already has; a correct version chain is something each must reimplement.

The migration cost this alternative avoids is real and is recorded honestly
under Risks. It was judged smaller than making the record identity
unrepresentable in five contract fields.

### Alternative 3 — include `authority` / `provenance` in the record seed

Two authorities citing the same evidence for the same claim would then make two
records. Rejected as the default: authority and provenance describe *who
recorded* a claim, and a producer rename would fork every record. The seed
holds the claim's content. Pinned by
`test_the_authority_is_not_in_the_seed` so that changing it is a decision
rather than a drift.

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
  anyone, with no candidate in hand.

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
- `authority` is not in the seed (Alternative 3), so two producers asserting
  identical content from identical evidence mint one record.

### Risks

- **Migration.** Every `assertion_id` already in a graph was minted from
  `candidate_id` alone. After this change, re-planning a candidate that is
  already committed mints a **different** id, so ADR-0019's `assertion_absent`
  guard will not recognise the existing row and the re-plan attaches a
  *second* `ACTIVE` record of that fact instead of being refused. There is no
  automatic remedy: an existing graph needs either a one-time re-id backfill,
  or an epoch boundary after which the duplicate pair is reconciled by
  supersession. **This needs an owner ruling before the change reaches a graph
  with data in it.**
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
  in a different repository. This design **contains** it — each record id is
  now unique, so "first match" is the only match, and `plan_supersession`
  refuses to emit a retract against a non-`ACTIVE` record, which is the
  reachable half — but it does not repair it. Filed for upstream.
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
  body: `test_reassertion_under_the_same_candidate_id_is_refused_and_drops_the_evidence`
  on that branch asserts the behaviour this ADR deliberately changes.
- PR #38 — ADR-0020 `REVOKE_IDENTITY` compensation. Adjacent hunks only.

## Supersedes

None.

## Superseded By

None.
