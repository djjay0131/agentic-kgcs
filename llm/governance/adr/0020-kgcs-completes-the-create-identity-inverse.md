# ADR-0020: KGCS completes the `CREATE_IDENTITY` inverse, and stops keeping its own inverse table

Status: Proposed
Date: 2026-09-21

## Context

`CREATE_IDENTITY` had no inverse. `kgcs.executor.compensate.INVERSE_OPERATION`
mapped it to `None`, so a committed identity-creation run was reported
`non_compensable` and a rollback of it was, correctly, refused. That was honest
while the vocabulary genuinely had no reversing type — invariant law 8 is
"compensable **or explicitly declared** non-compensable", and KGCS declared it.

KGIS ADR-0025 supplies the missing half: `CurationOperationType.REVOKE_IDENTITY`
(a tombstone — set `CurationStatus.REVOKED`, retain the record, **retain its
original `curation_epoch`**), a published `INVERSE_OPERATION_TYPES` map, a
reference implementation in `MemoryGraphStore.apply()`, and
`GraphReadOptions.include_revoked` with `REVOKED` hidden by default.

Running KGCS's own suite against that contract measured the gap precisely: of
527 tests, **exactly one** failed —
`test_every_operation_is_compensable_or_declared_non_compensable`, on
`REVOKE_IDENTITY` being absent from KGCS's table. Nothing else in the contract
change touched KGCS. That is the whole surface of this ADR.

## Decision

### 1. The inverse table is derived from the contract, never transcribed

```python
INVERSE_OPERATION = {t: INVERSE_OPERATION_TYPES.get(t) for t in CurationOperationType}
```

A second table maintained by hand in this repo is how the two repos drift, and
did: `CREATE_IDENTITY` sat here as non-compensable for a full release after the
contract could express its inverse. The projection is **total** over
`CurationOperationType` while the contract map is deliberately **partial**, so a
type the contract omits is *declared* non-compensable here rather than silently
absent — which is what lets a caller tell "cannot be reversed" from "nobody
considered it". `PROMOTE_ONTOLOGY_TERM` is the only such type (KGIS issue #45),
and KGCS does not invent an inverse for it.

### 2. Both producers of a `CREATE_IDENTITY` carry a revoke payload

`kgcs.planner.revoke_inverse_payload(identity_id, created_by_operation_id)` is
the single constructor, used by `kgcs.planner` and by
`kgcs.recuration.evolution.plan_promotion`. ADR-0018's lesson was that two
producers of one operation type drift apart unless they share the constructor;
this is the second producer, and it shares it.

The payload is `{"identity_id", "reason"}` — **an identity reference, not an
entity dump** (ADR-0025). The executor revokes the entity actually in the graph,
so a stale copy carried in the plan cannot overwrite it. The pre-revoke entity
travels in the compensating operation's own `reversal_data`, which
`Compensator._invert` already puts there generically, so nothing type-specific
was added to the compensator. `reason` names the operation being undone rather
than a timestamp or run id, because the compensator is pure and holds no clock:
every field must be a function of the plan alone or a replayed compensation
would not be byte-identical.

### 2a. The inverse payload is the **pre-revoke (ACTIVE)** entity

`Compensator._invert` carries the forward operation's own `payload` as the
inverse's `reversal_data[INVERSE_PAYLOAD_KEY]`. That payload was written at plan
time, so it holds the entity as `ACTIVE` — which is what KGIS ADR-0025 §6
requires. Compensating from a *post*-revoke copy would "restore" the identity
still `REVOKED`, restoring nothing. KGCS gets this right generically, by never
reading the graph back for reversal data; verified and pinned by
`test_the_revoke_carries_the_pre_revoke_active_entity_for_its_own_inverse`
(carried status `ACTIVE`, equal to the forward payload, and still `ACTIVE` after
the revoke has actually committed and the graph says `REVOKED`).

### 3. `REVOKE_IDENTITY` joins `DEFAULT_SUPPORTED_OPERATIONS`

The reference store implements it, so the executor must be willing to send it.
Leaving that constant stale would have kept the compensation path dead while
every other piece of it worked — the executor would have returned
`UNSUPPORTED_OPERATION` before the store was ever asked.

### 4. Reverse order is load-bearing, and is now asserted as a sequence

`Compensator.compensate` already iterated `reversed(plan.operations)`. That was
untested for the case where it matters: with `CREATE_IDENTITY` compensable, a
plan that creates an identity and then attaches an assertion to it must retract
the assertion **before** revoking the identity. The new test asserts the
operation types as a **list**, not a set — order is the observable here, and a
set comparison would pass either way.

## Rationale

The alternative to deriving the table is to update the local copy and add a test
that the two agree. That is strictly worse: it keeps a second source of truth and
adds a test whose only job is to detect drift that would not exist if there were
one source. Deriving it makes the drift unrepresentable.

The cost of deriving it is that the old exhaustiveness assertion
(`set(INVERSE_OPERATION) == set(CurationOperationType)`) became **true by
construction** — it could no longer fail whatever either repo did. Leaving a
passing assertion that cannot fail is the defect class this programme exists to
remove, so it was replaced rather than kept: the invariant test now asserts
*agreement* with the published map, that no contract-omitted type is given a
locally invented inverse, and that the compensable/non-compensable split is what
it is claimed to be. All three can fail.

## Alternatives Considered

### Keep the hand-maintained table and update it

Rejected: see above. Two sources of truth plus a drift test, instead of one
source and no drift.

### Give `PROMOTE_ONTOLOGY_TERM` a plausible inverse

Rejected, and the contract deliberately does not either. There is no
"demote ontology term" in the vocabulary. A plausible-looking entry would make a
partial rollback report itself as a full one, which is worse than refusing.

### Carry the pre-revoke entity as the `REVOKE_IDENTITY` payload

Rejected — this is ADR-0025's ruling and it is right. A plan is a description
written at planning time; the entity in the graph at execution time may differ.
Revoking by reference means a stale copy cannot overwrite the live record.

## Consequences

### Positive

- **A committed identity-creation run can now actually be rolled back**, and the
  repository can show it rather than claim it. Executed, 8 identities:
  forward `COMMITTED` at epoch 1, rollback `COMMITTED` at epoch 2, default read
  `0` entities (was 8), `include_revoked=True` read `8` entities all `REVOKED`
  all at `curation_epoch=1`, an epoch-scoped read at epoch 1 returning
  **exactly the 8 identities that were created**, and `include_superseded=True`
  `0` — the two history switches are independent.

  That epoch-scoped limb asserts **identities, not a count**. It originally
  compared `len(...) == 8`, which review showed to be decorative: the epoch read
  is as-of (`record_epoch > epoch` hides), so `@1`, `@2` and `@999` all return 8
  and the assertion could not fail in the direction it claimed — a "counting
  when identity matters" defect sitting inside the very test that proves the
  release-critical property. It now pins which records come back, plus a
  companion assertion that the epoch *before* creation returns nothing, which is
  what makes the epoch argument load-bearing. Measured against a store mutant
  returning the right count with wrong identities: the old form passes, the new
  form fails.
- A promotion plan from `recuration.evolution` is compensable for the same
  reason and by the same constructor.
- The two repos cannot disagree about which type reverses which.

### Negative / Tradeoffs

- **The round trip restores the identity but NOT its creation epoch.**
  `REVOKE_IDENTITY` inverts to `CREATE_IDENTITY`, which restores the entity
  `ACTIVE` — measured — but `curation_epoch` is assigned by the executor at
  apply time, so the restored record carries the epoch of the batch that
  re-created it, not the one that originally created it. This is a **stated
  bound, not an oversight**: KGIS established (mutant B1′) that making
  `CREATE_IDENTITY` honour an epoch carried in its payload corrupts the forward
  leg's own guarantee, so the in-place repair is ruled out. The fix is a
  distinct `RESTORE_IDENTITY` operation, proposed in agentic-kgis issue #51.
  KGCS does **not** attempt the in-place fix, and the bound is pinned by
  `test_the_round_trip_restores_the_identity_but_not_its_creation_epoch` so it
  is a known limit rather than a surprise.
- **A named inverse is not an executable rollback, and the two must not be
  conflated.** `INVERSE_OPERATION_TYPES` answers *"what type reverses this
  type"* — a vocabulary statement. It does **not** answer *"can this plan be
  rolled back today"*, which depends on what the executing store implements.
  Measured on the merged contract: **7** types have a named inverse, the
  reference store executes **3** (`CREATE_IDENTITY`, `ATTACH_ASSERTION`,
  `REVOKE_IDENTITY`), leaving `RETRACT_ASSERTION`, `MERGE_IDENTITIES`,
  `SPLIT_IDENTITY` and `REASSIGN_ASSERTION` named-but-not-executable. So
  `CompensationResult.fully_compensable` means *every operation had an inverse*
  and nothing stronger: an attach plan reports `fully_compensable=True` and its
  rollback still returns `UNSUPPORTED_OPERATION`. A caller must consult both.
  Pinned by `test_a_named_inverse_does_not_mean_an_executable_rollback`.
  `fully_compensable` is a poor name for this — it reads as "the rollback will
  work" — and renaming it alongside an `executable_against(supported_operations)`
  predicate, so "consult both" is an API rather than prose, is filed as
  issue #41.
- **Assertion rollback is still not demonstrable.** `RETRACT_ASSERTION` is not
  in `DEFAULT_SUPPORTED_OPERATIONS` because the reference store does not
  implement it (ADR candidate 0015, an upstream ask). So a *mixed* plan's
  rollback compensates correctly and in the right order but cannot execute
  end-to-end against the reference store. Identity rollback is demonstrated;
  assertion rollback remains asserted. This ADR does not close that, and says so
  rather than letting the identity demonstration imply it.
- KGCS now requires a `kg_contracts` that defines `REVOKE_IDENTITY`,
  `INVERSE_OPERATION_TYPES` and `GraphReadOptions.include_revoked`. This is a
  hard version floor, not a soft one.

### Risks

- **`REVOKED` is now hidden from default canonical reads.** Audited: KGCS's
  production code makes exactly one canonical read, `GraphReader.current_epoch()`
  (`executor.py`, `compensate.py`), which is unaffected. No `get_entity`,
  `find_entities`, `assertions_for` or `neighborhood` call exists in `src/`
  today, so there is nothing in KGCS for the default-hiding change to break —
  and the measured single test failure against the new contract confirms it.
  **This audit expires the moment KGCS reads entities back**, and the first such
  read must decide explicitly whether it wants revoked records.
- **Both of `_invert`'s silent fallbacks are now loud.** ADR-0018 added a
  back-compat path: an absent `INVERSE_PAYLOAD_KEY` meant "use the whole
  `reversal_data` as the payload". Its *intended* precondition was "this
  producer predates the key"; its *actual* precondition is "`reversal_data`
  holds nothing but the payload", and mutation proved those differ — removing
  the key from the planner's `CREATE_IDENTITY` left the 8-identity rollback
  committing and reading back perfectly, with `candidate_id` (lineage) sitting
  inside the operation payload: the exact defect ADR-0018 introduced the key to
  fix. Review then found a **second, undisclosed** fallback on the same branch:
  a key *present but malformed* (not a mapping) fell through the same
  `isinstance` test, leaking lineage **and the sentinel key `inverse_payload`
  itself** — a shape no consumer expects.

  Both are closed, asymmetrically and deliberately: the **absent** key raises
  by default with an explicit `Compensator(allow_legacy_reversal_data=True)`
  opt-in for a caller that knows its `reversal_data` holds nothing else; the
  **malformed** key raises **unconditionally**, opt-in or not, because it is
  never back-compat and always a producer bug.

  The general point, and why it is in the ADR rather than the changelog: a
  fallback is only safe if something checks its real precondition. This one
  checked a proxy, so it silently converted "the producer forgot" into "ship
  the lineage as the payload". Making it opt-in does not make it safer — it
  makes the caller state the precondition it is relying on.

- A sibling PR (ADR-0019) adds the first such read — `assertions_for` in
  `PlanExecutor._assertion_present`, with `include_superseded=True`. Whichever
  merges second must decide whether that read should also pass
  `include_revoked=True`. It should: the question that read asks is "does this
  assertion id exist at all", and an id whose record was revoked still exists.
  Nothing in KGCS sets an assertion to `REVOKED` today, so this is latent rather
  than live — which is exactly why it is written down here.

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

- KGIS ADR-0025 (`REVOKE_IDENTITY` inverts `CREATE_IDENTITY`) — the contract half.
- `llm/governance/adr/0018-compensating-plans-assert-post-application-state.md` —
  the shared-constructor lesson this ADR applies to a second operation type.
- `llm/governance/adr/candidates/0015-reference-store-limited-operation-coverage.md` —
  why assertion rollback is still not demonstrable.

## Related Issues / PRs

- Gated on `agentic-kgis` PR #46. KGIS issue #44 is the defect this completes;
  KGIS issue #45 is `PROMOTE_ONTOLOGY_TERM`'s deliberate absence.

## Supersedes

None. It narrows `llm/governance/adr/candidates/0015` only in the identity case.

## Superseded By

None.
