# ADR-0019: `ATTACH_ASSERTION` carries an `assertion_absent` per-subject precondition

Status: Proposed
Date: 2026-09-21

## Context

`kgcs.planner` emitted exactly two guard kinds on a `CurationPlan`:

- one plan-level `snapshot_version` precondition, naming the graph snapshot the
  plan was computed against; and
- one `entity_version`/`expected="0"` precondition per `CREATE_IDENTITY`,
  naming the identity that operation would mint.

An `ATTACH_ASSERTION` carried **no** per-subject guard at all. ADR candidate
0003 recorded why: the guard assumed necessary for an attach was
`entity_version = <the subject's current version>`, and the deterministic core
does no graph reads, so it cannot know that version. Wave 1 answered candidate
0003's option 1 by having `PlanExecutor` enforce the plan-level snapshot guard
against the graph's current epoch, which makes an unmodified replay of an
already-committed plan `STALE`.

That covers one replay shape and not the other. Re-planning the *same*
candidate against the graph's **current** snapshot produces a plan whose
snapshot guard is satisfied by construction. Reproduced on `main` at 609270e (and re-verified on 14ffd0e),
with the reference `MemoryGraphStore`:

```
=== ATTACH_ASSERTION replay ===
first execute : COMMITTED epoch -> 1
preconditions : [('snapshot_version', 'g1', '0')]
replay execute: COMMITTED  failed_preconditions: ()
assertions on subject: 2
distinct assertion ids: ['as_7E170RRZ4E4AVM7BRBQW81BTEY']   <-- one id, two rows
epoch: 2

=== CREATE_IDENTITY replay (same construction) ===
preconditions : [('snapshot_version', 'g1', '0'),
                 ('entity_version', 'kg://g1/identity/2WJY…', '0')]
replay execute: STALE  failed: [('entity_version', 'kg://g1/identity/2WJY…', '0')]
epoch: 1
```

The damage is not a duplicated *count*, it is a duplicated *identity*: the
canonical graph now holds two rows carrying the same `assertion_id`.
`mark_superseded(assertion_id)` reaches only the first of them, so the duplicate
is not merely redundant — it is unreachable by the supersession path that exists
to retire it.

So replay protection was inconsistent **within one planner**: the operation type
that mints an identity guarded itself; the operation type that mints an
assertion did not.

## Decision

The planner emits, for every `ATTACH_ASSERTION`, a per-subject precondition:

```
Precondition(kind="assertion_absent",
             subject=<the assertion's subject_identity>,
             expected=<the assertion_id this operation would mint>)
```

`subject` is a canonical graph subject, exactly as it is for an
`entity_version` guard; the `kind` supplies the polarity (absence), and
`expected` names the record that must not be there. The constructor
`kgcs.planner.assertion_absent_guard` is the single place that decides which
field holds which, so producers and the enforcing executor cannot disagree.

`PlanExecutor` enforces it, alongside the `snapshot_version` guard it already
enforced, before touching the store: it reads the subject's assertions through
its `GraphReader` with `GraphReadOptions(include_superseded=True)` and reports
`STALE` if the named `assertion_id` is already present. The reference
`MemoryGraphStore` passes every precondition kind but `entity_version`, so
enforcement has to live in the executor — the same shape candidate 0003's
option 1 already took for the snapshot guard.

`include_superseded=True` is load-bearing. Supersession marks a record, it never
deletes it (governance principle 5), and the default canonical read hides
`SUPERSEDED` rows. Without the flag, re-attaching an id whose record had been
superseded would read as "absent" and the replay would be waved through.

**This needs no `kg_contracts` change.** `Precondition.kind` is a free-form
`str`, so a new kind is a KGCS-local decision; no contract type, enum, or
validator is touched. The frozen contract stays frozen.

## Rationale

The guard is the exact structural analogue of the one `CREATE_IDENTITY` already
carried: *the record this operation would mint must not already exist*. Both are
read-free at plan time, because in both cases the planner minted the id itself
(`assertion_id` is derived from the candidate via the injected `IdFactory`).
That is what dissolves candidate 0003's objection here rather than reopening it:
0003's reasoning was sound for the guard it considered (a subject **version**
match, which does need a read) and simply too narrow — it did not consider that
a per-subject guard could be an *existence* check on the minted record instead
of a version check on the subject.

It also preserves the property that matters most downstream: legitimate
re-assertion stays possible. The same fact attached again later with new
evidence arrives as a **new candidate**, which derives a **different**
`assertion_id`, whose `assertion_absent` guard names a record the graph does not
hold — so it applies, and both assertions coexist with their own evidence. Only
a byte-identical re-mint of an id already in the graph is refused. Evidence
evolution is a release-critical adopter acceptance criterion; a guard that
foreclosed it would be a worse defect than the one being fixed.

## Alternatives Considered

### A subject-version guard (`entity_version = <subject's current version>`)

Candidate 0003's option 2: have the read-capable resolution plane stamp the
subject's observed `entity_version` onto the decision so the planner can emit a
precise per-subject version guard.

Rejected on two independent grounds. First, it requires the graph read the
deterministic core is defined not to do — it is only available once the async
resolution plane has run, so attaches planned by the core alone would still be
unguarded, which is the whole defect. Second, and worse, it guards the wrong
thing: the subject's version increments on *every* attach to that subject, so
this guard refuses a plan because some unrelated fact about the same subject
landed first. That is a false conflict on the common path, and callers would
learn to retry through it — which is precisely how a guard stops being a guard.
It is a concurrency check wearing a replay check's clothes.

### An idempotency key on the plan or the batch

Give each plan (or operation) a dedup key and have the write path refuse a key
it has already seen.

Rejected: it needs a durable key registry that nothing in the contract or the
reference store provides, so it would either add a `kg_contracts` type — the
frozen contract, and out of bounds here — or invent a KGCS-local side table that
every backend adapter would then have to reimplement and keep transactionally
consistent with the graph. It also answers a weaker question. A key says "I have
seen this request before"; `assertion_absent` says "this record is in the graph
now", which stays correct across a lost key store, a new executor process, or a
record written by some other path.

### Enforce absence inside the store instead of the executor

Rejected because `GraphMutationStore.apply` implementations are adapters, and
the reference one explicitly passes every kind but `entity_version`. Putting
enforcement there makes the guarantee a per-adapter promise that silently
evaporates on any adapter that has not implemented it. In the executor it holds
for every adapter that can be read, and the executor already occupies exactly
this role for the snapshot guard.

### Deduplicate by assertion content rather than by id

Rejected: it would refuse re-assertion with new evidence — two assertions with
the same subject/predicate/value but different `evidence_refs` are the evidence
evolution case, and they must both land.

## Consequences

### Positive

- Replay protection is uniform across the operation types the v1 executor
  applies: each guards the record it mints.
- A replayed attach is refused with a named failed precondition, so the caller
  gets "re-evaluate, never blindly retry" (the contract's `CommitResult`
  semantics) instead of a silent duplicate.
- The canonical graph can no longer hold two rows under one `assertion_id` via
  this path, so `mark_superseded` keeps reaching every copy of a record.

### Negative / Tradeoffs

- One extra precondition per attach on every plan, and one `assertions_for`
  read per attach at execution time. Plans get slightly larger and execution
  does O(attaches) reads.
- The guard keys on `assertion_id`, which `DerivedIdFactory` derives from
  `candidate_id` alone. Two *different* facts submitted under one
  `candidate_id` would collide on that id — but they already did, before this
  ADR; the guard makes the collision visible (the second is refused) instead of
  silently duplicating. Widening the assertion-id seed is a separate decision
  and would change every derived id, so it is not taken here.

### Risks

- **The guard is unenforceable without a reader.** Both executor-enforced
  guards (`snapshot_version` and `assertion_absent`) need a `GraphReader`; an
  executor over a store that is neither a reader nor paired with one has
  nothing to check them against, and the reference precondition contract passes
  them. This limit is pinned by
  `test_without_a_reader_the_attach_guard_is_unenforceable` rather than left
  implicit. Adapters that are write-only need their own enforcement.
- **Scope.** This ADR covers plans from `kgcs.planner`. `kgcs.recuration.evolution`
  and `kgcs.recuration.ontology` build their own plans and still emit only the
  snapshot guard; their attaches carry caller-supplied assertions, so extending
  the guard there is a follow-up, not a mechanical repeat.

## Impacted Areas

- [ ] Product
- [x] Domain model
- [x] Data architecture
- [ ] AI architecture
- [x] Domain-specific systems (see governance delta)
- [ ] Integrations
- [ ] UX
- [ ] Security/privacy
- [x] Implementation
- [x] Documentation

## Related Documents

- `llm/governance/adr/candidates/0003-preconditions-need-snapshot-read.md` —
  narrowed by this ADR, not retired: its snapshot-read objection still stands
  for a subject-*version* guard.
- `llm/governance/governance-delta.md` §Project Principles 1, 3, 5.
- Design authority: `agentic-kgis/llm/specs/2026-07-09-kgis-kgcs-design.md`
  §7.1 (`CurationPlan`, `Precondition`).

## Related Issues / PRs

- Issue #35. Reported by the `agentic-kg` adopter wiring its curation
  pipeline; independently reproduced here against the reference
  `MemoryGraphStore`.

## Supersedes

None.

## Superseded By

None.
