# ADR-0018: A compensating plan asserts post-application state, and carries a payload a store can apply

Status: Proposed
Date: 2026-09-19

## Context

Rollback in KGCS is never deletion. A committed `CurationPlan` is undone by a
*compensating* `CurationPlan` of inverse operations, applied through the same
`PlanExecutor` and the same `GraphMutationStore` (§9 law 8, governance
principle 5). `kgcs.executor.compensate.Compensator` builds that plan: it maps
each operation through `INVERSE_OPERATION`, reverses the order (LIFO), and
takes each inverse's payload from the forward operation's `reversal_data` —
the contract's own "whatever is needed to undo this operation".

Three defects in that path were found while an adopter
(`agentic-kg`) built a Neo4j `GraphMutationStore`, and all three reproduce
against KGCS's own reference store. They are stated separately below because
they were found separately, but **they are one defect with three faces**, and
the shared cause is the more useful finding than any of the three.

### Reproduction A — the guard is false by construction

`Compensator` carried the *source* plan's snapshot precondition into the
compensating plan (`_carry_snapshot_precondition`). The executor enforces
plan-level snapshot guards itself against the graph's current epoch
(`executor.py`, `_stale_snapshot_preconditions`, ADR candidate 0003), using the
store as its `GraphReader` when the store implements one — the reference
`MemoryGraphStore` does.

Measured on `v1.0.0` against `kg_contracts.testing.memory.MemoryGraphStore`,
one `ATTACH_ASSERTION` plan:

```
source plan preconditions: [('snapshot_version', 'g1', '0')]
forward execute       -> COMMITTED   new_epoch = 1
store.current_epoch()  = 1
compensating plan preconditions: [('snapshot_version', 'g1', '0')]   <-- carried
compensation execute  -> STALE
failed_preconditions:   [('snapshot_version', 'g1', '0')]
store untouched, epoch still 1
```

Two blockers are stacked here, which is why the inner one survived long enough
to be ratified. On the **stock** reference store a compensating
`RETRACT_ASSERTION` is rejected `UNSUPPORTED_OPERATION` first (the executor
pre-checks operation types before preconditions), which *masks* the `STALE`
entirely. The trace above widens `supported_operations` to reach the
precondition at all. An adopter whose store does support `RETRACT` — the Neo4j
adapter — removes the outer blocker and meets the inner one immediately.

The original plan's own commit is what advanced the epoch past the guard the
compensation inherited. The guard therefore **cannot hold, ever**, against any
store the executor can read. This is not adapter-specific: on the shipped
product, compensation has never been executable against a readable store. It
reached no payload, no batch, and no `store.apply`.

### Reproduction B — the `RETRACT`→`ATTACH` inverse is not an `Assertion`

An `ATTACH_ASSERTION`'s payload *is* an `Assertion` (that is how
`MemoryGraphStore.apply` and every adapter consume it: `Assertion.model_validate`
with `extra="forbid"`). The `RETRACT_ASSERTION` emitted by
`ConceptEvolutionPlanner.plan_supersession` recorded a `reversal_data` of
`{assertion_id, subject_identity, restore_status}` merged with the trace
provenance block. Inverting it produced this payload:

```
inverse ATTACH payload keys: ['adviser_version', 'assertion_id', 'evidence_ids',
  'matcher_version', 'policy_version', 'restore_status', 'subject_identity',
  'trace_id', 'trigger_id', 'trigger_kind']
Assertion.model_validate(...) -> 17 validation errors
  10 missing: predicate, object_value, object_identity, status, valid_period,
              recorded_at, scores, evidence_refs, authority, provenance
   7 extra_forbidden: adviser_version, evidence_ids, matcher_version,
              policy_version, restore_status, trigger_id, trigger_kind
```

`predicate` is the headline, but the shape is wrong in both directions at once:
required `Assertion` fields absent, and the provenance block present where the
model forbids extras. `trace_id` appears on both sides meaning two different
things.

### Reproduction C — the `ATTACH`→`RETRACT` inverse is lossy

A `RETRACT_ASSERTION` is defined by *which record, to what status, as of when*.
The forward supersession emits all of it. The inverse of an `ATTACH` emitted
only the identifiers:

```
forward RETRACT payload keys: ['assertion_id', 'new_status', 'subject_identity',
                               'superseded_at', 'superseded_by']
inverse RETRACT payload keys: ['adviser_version', 'assertion_id', 'evidence_ids',
                               'matcher_version', 'policy_version',
                               'subject_identity', 'trace_id', 'trigger_id',
                               'trigger_kind']
missing: ['new_status', 'superseded_at', 'superseded_by']
```

The same omission held at the second producer, `CurationPlanner._attach_operation`.

### The shared cause

Two mistakes, and defect A hid the other one:

1. **`reversal_data` was overloaded** — it was simultaneously the inverse
   operation's payload and the forward operation's lineage/provenance, and
   `Compensator._invert` consumed the whole dict as the payload
   (`payload=dict(op.reversal_data)`). B and C are both what that produces:
   a payload that is the wrong *shape* (B) or the right shape with fields
   dropped (C), plus provenance leaking into every inverse payload including
   `MERGE`/`SPLIT`/`REASSIGN`.
2. **Nothing ever executed an inverse end to end**, because A rejected every
   compensation `STALE` before its payload was looked at. The one place a
   compensating `RETRACT` did reach a store — the E2E harness — reached it only
   after the test hand-rebuilt the precondition, and applied only because the
   harness substituted a fixed instant for the `superseded_at` the inverse had
   dropped. The `RETRACT`→`ATTACH` direction was never executed anywhere.

The suite was green throughout, and two artifacts recorded the broken behaviour
as intended:

- `tests/kgcs/test_e2e_concurrency.py::test_executed_attach_is_rolled_back_by_its_compensation`
  asserted `executor.execute(comp.plan).outcome is ExecutionOutcome.STALE` under
  the comment *"a rollback carries the original snapshot guard, so it must be
  re-evaluated, never raced"* — pinning A as correct, then working around it.
- **ADR candidate 0016** described A precisely and, at v1 completion
  (2026-09-17), promoted it to an *Accepted* durable KGCS-local decision, with
  the hand re-stamp recorded as the "local workaround". The defect was
  ratified, not fixed.

Candidate 0016's own "possible future contract improvement" proposed the
signature this ADR adopts. It was right about the remedy and wrong about the
grading: this is a defect, not a missing convenience.

## Decision

**1. A compensating plan asserts the state it expects to find *now*.**
`Compensator.compensate` takes a **required keyword-only** `against_snapshot`:
the snapshot the rollback expects, normally the epoch the original plan
committed at, which `PlanExecutor.execute` already returns as
`ExecutionRecord.new_epoch`. The source plan's snapshot guards are *rebased*
onto it — same `kind`, same `subject`, new `expected` — and the compensating
plan's own `snapshot_version` is stamped with it. The source plan's expectation
is never carried.

The keyword is **required and non-optional**: there is no argument to this
method that yields an unguarded compensating plan. Omitting it is a
`TypeError`; `None` is a `TypeError`/`ValueError`; and a source plan that
carried no snapshot guard does not produce one either — the guard is
synthesized on the plan's first candidate id (`candidate_ids` is non-empty by
contract). `against_snapshot` is validated as an epoch — a non-negative
integer or its decimal string — because a free-form value would otherwise
become an `expected` no epoch can ever equal, which is this ADR's own defect
in a new costume.

Per-subject `entity_version=0` guards continue to be dropped: they guarded
creation, not reversal, and an identity that now exists would fail them forever.

**2. `reversal_data` separates the inverse payload from the lineage.**
`kgcs.planner.INVERSE_PAYLOAD_KEY` (`"inverse_payload"`) names the sub-mapping
that is the reversing operation's payload. Everything else in `reversal_data`
stays lineage and provenance — `candidate_id`, `trigger_id`, `evidence_ids`,
`trace_id`, matcher/adviser/policy versions — and never reaches an operation
payload. `Compensator._invert` reads the key when present and falls back to the
whole dict when absent, so hand-built plans and third-party producers keep
working. An inverse's *own* `reversal_data` offers the forward payload under
the same key, so a compensation is itself compensable.

**3. Both inverse payloads are complete.**
- `ATTACH`→`RETRACT` uses the shared `kgcs.planner.retract_inverse_payload`,
  emitting four of the five keys a planned supersession does: `assertion_id`,
  `subject_identity`, `new_status`, `superseded_at`. It deliberately omits the
  fifth, `superseded_by` — a rollback has no superseding assertion, and
  inventing one would be a lie. "The same shape" would overclaim; the correct
  statement is that it carries every field the operation is *defined* by, and
  the one it drops is the one that does not apply.
- `RETRACT`→`ATTACH` (supersession) uses the full JSON dump of the
  pre-retraction assertion, so the inverse restores the record as it stood,
  status included. That is what `restore_status` was gesturing at, in a payload
  that could not have validated.

**4. Two sub-decisions inside `retract_inverse_payload`, both deliberate.**
- **`new_status=SUPERSEDED`, not `REVOKED`.** `REVOKED` is the semantically
  purer reading of "this attachment is withdrawn" — nothing superseded it. But
  the canonical read surface hides only `SUPERSEDED` by default
  (`GraphReadOptions.include_superseded`); `kg_contracts` deliberately has no
  `include_revoked`, and `MemoryGraphStore._is_visible` pins REVOKED as visible
  by default (its issue #8). A rolled-back record marked `REVOKED` would remain
  visible to an ordinary read — the opposite of a rollback. Changing that is a
  read-semantics ADR upstream, not a decision to smuggle in here.
- **`superseded_at` is the assertion's own `recorded_at`.** The compensator is
  pure and holds no clock (a replayed compensation must be byte-identical), so
  the instant must come from the plan. Closing the record's transaction-time
  interval at the instant it opened is the exact bitemporal statement a
  rollback makes: as of any query time, this record was never validly live.
  History is preserved, not rewritten (§9 law 10).

**5. A compensating `ATTACH_ASSERTION` is an upsert by `assertion_id`.**
Restoring a retracted record re-attaches the *same* `assertion_id`. A
`GraphMutationStore` MUST replace that record in place, never append a second
row under an existing id. This is a requirement on adapters — the `Compensator`
cannot enforce it — and it is not optional: measured, with append semantics a
compensation *of a compensation* commits and leaves two contradicting
assertions (2015 and 2014) both `ACTIVE` on the same subject and predicate,
because the redo's status change scans to the stale copy. That path is
reachable only now that rollback works at all, which is why it appears in the
same ADR. Real adapters express it as a uniqueness constraint on
`assertion_id`; `tests/kgcs/e2e_harness.py::E2EGraphStore._upsert_assertion`
is the reference. `kg_contracts.testing.memory.MemoryGraphStore.put_assertion`
appends unconditionally and does **not** satisfy this — an upstream gap, noted
alongside ADR candidate 0015.

**6. ADR candidate 0016 is superseded by this ADR.**

## Rationale

A precondition is an optimistic-concurrency guard. Its whole content is the
question *"is the world still as it was when I computed this plan?"* The
compensating plan was computed against the world the **original plan
produced**, so that is the only state it can honestly assert. Carrying the
source plan's expectation asks a question about a world that, by the time any
rollback exists, is two states old — and answers it "no" every time.

A guard that can never hold is not a guard; it is a refusal wearing a guard's
clothes. It was read as safety ("no blind rollback") precisely because a
refusal *looks* like caution. It is worse than no guard: it is indistinguishable
from a genuine concurrency rejection, so the one signal a caller needs — *did
someone else commit while I was deciding to roll back?* — carried no
information. Rebasing restores that signal: the compensation applies while the
graph is still at the epoch the original plan produced, and is correctly `STALE`
the moment anything else commits. Both directions are now tested.

Requiring the keyword rather than defaulting it is the point, not pedantry.
Every existing caller of `compensate()` is broken today — none of their
compensations can apply — so there is no behaviour worth preserving by default,
and a default would have to be either the old lie or a silent unguarded apply.
Making each caller state the snapshot converts an invisible failure into a
compile-time question. There are eight call sites in this repo; the change is
one keyword each.

Separating the inverse payload from the lineage is forced by the contract, not
chosen for tidiness. An `ATTACH` payload *is* an `Assertion`, `Assertion` is
`extra="forbid"`, and `trace_id` is both an `Assertion` field and a provenance
field meaning something different. There is no flat dict that can be both the
provenance block and a valid `Assertion`. Once the two are separated, C stops
being a separate fix: the inverse payload is built by a named function whose
job is to produce a complete `RETRACT` payload, and both producers call it.

Guaranteeing the guard rather than *reporting* whether one exists is the second
correction this ADR makes to itself. `non_compensable` is the right idiom for
"the v1 vocabulary genuinely has no inverse for this operation" — a fact about
the world the component cannot change. Whether a rollback is guarded is not
that: the component has the epoch in hand and can always emit the guard. A flag
would have been a report on a choice, and reports are only honest when the
alternative is impossible. Here it was not.

## Alternatives Considered

### Give the `Compensator` a `GraphReader` and read `current_epoch()` itself

No caller change, always-correct guard. Rejected: it breaks the two properties
the module is built on and documents — purity (the compensator would hold a
port and touch the graph, which `test_compensation_never_mutates_the_graph_by_itself`
exists to forbid in spirit) and determinism (a replayed compensation would no
longer be byte-identical, since the guard would vary with wall-clock graph
state). It also reintroduces a read between planning and applying, which is the
race the precondition exists to close.

### Keep `against_snapshot` optional, defaulting to the old carried guard

Backward compatible; unguarded-by-accident impossible. Rejected: it preserves
the defect for every caller who does not opt in, and the defect's whole
character is that it looks like correct fail-closed behaviour. A fix nobody has
to notice is a fix nobody gets.

### Default to omitting the guard when `against_snapshot` is not supplied

Rejected for the opposite reason: silently converting a fail-closed default into
a fail-open one is the worse of the two errors. Hence "no default at all".

### Derive the guard as the source plan's `snapshot_version` + 1

Needs no new argument. Rejected: it is only true when the original plan was the
sole commit at that epoch, which nothing guarantees. It would be right often
enough to be trusted and wrong exactly when concurrency matters.

### Strip known provenance keys from `reversal_data` instead of nesting

Keeps `reversal_data` flat, so no existing reader changes. Rejected: a denylist
of provenance key names is unmaintainable (every new provenance field is a
silent payload leak) and it cannot solve B at all — `trace_id` is legitimately
both a provenance field and an `Assertion` field, and stripping it would
*remove* a required field from the restored assertion.

### `new_status=REVOKED` for a rolled-back attach

Semantically the best description of the act. Rejected for now — see Decision 4:
under the current read surface a `REVOKED` record stays visible to a default
read, so the rollback would not roll anything back from a reader's point of
view. Revisit if `kg_contracts` gains `include_revoked`.

## Consequences

### Positive

- Compensation works. A committed supersession now round-trips end to end
  against the E2E store: 2015 superseded by 2014 at epoch 2, rolled back at
  epoch 3 to 2015 live and 2014 superseded, with all three transactions still
  queryable (§9 law 10). This path had never executed.
- The `RETRACT`→`ATTACH` inverse validates as an `Assertion` and restores the
  prior record exactly; the `ATTACH`→`RETRACT` inverse is complete, so a store
  no longer invents `superseded_at`.
- A stale compensation is now a *real* signal: it means someone else committed,
  not that a compensation was generated.
- `MERGE`/`SPLIT`/`REASSIGN` inverse payloads stop carrying provenance they
  never should have had. That is a partial fix and "untested" understates what
  remains: the inverse payload keys **measurably do not match** the forward
  operation's. A forward `SPLIT_IDENTITY` takes
  `['into_identities', 'source_identity']`; the inverse `SPLIT` this emits
  carries `['premerge_members', 'survivor_identity']`. A forward
  `MERGE_IDENTITIES` takes `['merged_identities', 'survivor_identity']`; the
  inverse `MERGE` carries `['premerge_members', 'survivor_identity']`. Any
  adapter that grows `MERGE`/`SPLIT` support will hit this the way the Neo4j
  adapter hit B and C. It is out of scope here only because no store applies
  those op types (ADR candidate 0015), so the correct payload shape is not yet
  defined by anything; scoping it out is a choice, not a claim that it works.
- The E2E harness no longer substitutes a default for a missing `superseded_at`;
  a producer that drops it now fails loudly instead of being papered over.

### Negative / Tradeoffs

- **Breaking change** at a tagged `1.0.0`, and it warrants a **major** bump,
  `2.0.0`. Two surfaces break. `Compensator.compensate(plan)` no longer
  compiles — loud, and caught at import. Worse is the quiet one: `reversal_data`
  is a *serialisation* shape that round-trips through `model_dump_json`, so a
  consumer reading a payload field flat off it gets a `KeyError` at rollback
  time, or — if it uses `.get()` — silently reverses nothing. The "nothing
  broke because compensation never worked" counter-argument covers only the
  first surface: reading `reversal_data` did work, and this changes it. An
  earlier revision of this ADR graded the change *minor*, which was a
  wrongly-graded ADR inside the fix for a wrongly-graded ADR; it is corrected
  here. `pyproject.toml` is untouched — the bump lands in a dedicated
  `chore(release)` PR (issue #31).
- `reversal_data`'s shape changed for `ATTACH`/`RETRACT`/`MERGE`/`SPLIT`/
  `REASSIGN`: payload material moved under `inverse_payload`. `candidate_id`,
  `identity_id`, `term_id` and the trigger-provenance block stay flat. Anything
  reading a payload field flat off `reversal_data` must follow the key. The
  compensator's fallback keeps *generation* working for un-migrated producers;
  it does not make an un-migrated producer's payload correct.
- A rolled-back attach is marked `SUPERSEDED` though nothing superseded it — a
  known imprecision, accepted against the read-visibility cost above.

### Risks

- ~~`against_snapshot=None` yields an unguarded plan~~ — **closed, not
  deferred.** An earlier revision of this ADR allowed `None` ("structure
  only") and reported `CompensationResult.snapshot_guarded`. Two things were
  wrong with that. The flag had **no production consumer** — `PlanExecutor`
  only ever sees a `CurationPlan`, never the result — so it documented a gap
  rather than closing one. And supplying a *real* epoch did not guarantee a
  guard either: a source plan with no snapshot precondition yielded
  `preconditions ()` with `snapshot_version` stamped and nothing enforced,
  which was not disclosed at all. Deferring a disclosed safety gap is the
  exact pattern this ADR condemns in candidate 0016, so it is fixed here:
  `against_snapshot` is non-optional, every compensating plan carries exactly
  one snapshot guard (synthesized on the first candidate id when there is
  nothing to rebase), and a non-epoch value raises `ValueError` instead of
  minting an `expected` no epoch can equal. `snapshot_guarded` is gone — a
  constant is not a signal.
- The fallback in `_invert` (no `INVERSE_PAYLOAD_KEY` → use the whole
  `reversal_data`) is back-compat, and it is also the exact shape that was
  wrong. It is right for hand-built plans, and it will silently do the old
  thing for a third-party producer that never migrates.
- **This guard is a waypoint, not the destination.** The executor compares one
  graph-global epoch and ignores `Precondition.subject`, so the guard asserts
  *"nothing at all has committed since"* when the question that matters is
  *"are the effects I am undoing still the latest state of the records I
  touch?"*. It is a safe over-approximation — it never permits an unsafe
  rollback — but it is strictly stronger than necessary in a way that costs:
  any unrelated commit refuses a valid rollback, and because nothing
  re-derives a compensation against a newer epoch, that refusal is
  **permanent** for that plan. The target state is per-subject guards over the
  records the compensation actually touches, which is ADR candidate 0003's
  subject; `Precondition.subject` is carried through today for provenance so
  the plans do not have to change shape when it lands. Recorded here so this
  decision reads as a step, not an answer.
- `superseded_at = recorded_at` gives a rolled-back record a zero-width
  transaction interval. That is the intended bitemporal reading, but an adapter
  that assumes `superseded_at > recorded_at` strictly will need to accept
  equality.

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

- [ADR candidate 0016 — an executed compensation needs re-stamping against the current snapshot](candidates/0016-compensation-needs-snapshot-restamp.md) (superseded by this ADR)
- [ADR candidate 0003 — deterministic core cannot emit per-subject preconditions without a snapshot read](candidates/0003-preconditions-need-snapshot-read.md)
- [ADR candidate 0015 — the reference `MemoryGraphStore` applies only two operation types](candidates/0015-reference-store-limited-operation-coverage.md)
- [kgcs v1 completion reconciliation](../kgcs-v1-completion-reconciliation.md)

## Related Issues / PRs

- Reported by the `agentic-kg` Neo4j `GraphMutationStore` adoption.

## Supersedes

ADR candidate 0016 (`candidates/0016-compensation-needs-snapshot-restamp.md`).

## Superseded By

None.
