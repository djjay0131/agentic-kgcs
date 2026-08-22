# ADR candidate 0003: deterministic core cannot emit per-subject preconditions without a snapshot read

Status: Candidate (contract friction; not an accepted decision)
Date: 2026-07-17
Surfaced by: Sprint 1 (deterministic curation core), `kgcs.planner`

## Context

A `CurationPlan` carries `preconditions` — optimistic-concurrency guards the
executor checks before applying. The reference `MemoryGraphStore` enforces
preconditions of kind `entity_version`: it compares an expected version string
against a per-identity counter, and a mismatch fails the whole batch (a stale
snapshot). Sprint 1 does **no graph reads** by design — no entity resolution,
no snapshot fetch.

## Problem

For a `CREATE_IDENTITY`, the correct guard is unambiguous and needs no read:
the freshly minted identity must not already exist, i.e. `entity_version = 0`.
The planner emits exactly that.

For an `ATTACH_ASSERTION` to a **pre-existing** subject, the correct guard is
`entity_version = <the subject's current version>` — but the deterministic
core cannot know that version without reading the graph, which Sprint 1 does
not do. So the plan cannot carry a per-subject version guard for attach
operations, leaving an attach optimistically unguarded against a concurrent
change to its subject between planning and execution.

## Local workaround

The planner emits:

- an `entity_version = 0` precondition per minted identity (`CREATE_IDENTITY`),
  enforced by the reference store; and
- one plan-level `snapshot_version` precondition recording the graph snapshot
  the plan was computed against.

The Plan-1 reference store does not enforce non-`entity_version` preconditions,
so the `snapshot_version` guard is presently provenance rather than an
enforced check — but it is part of the plan's immutable record, so a later
snapshot-aware executor can reject a stale plan. Attach operations thus carry
the plan-level snapshot guard but no per-subject version guard in Sprint 1.

## Possible future contract improvement

Two directions, for the owner to weigh:

1. Give the executor a way to enforce a plan-level `snapshot_version`
   precondition (compare against a graph-wide snapshot/epoch), so a single
   plan-level guard covers all attach operations without per-subject reads.
2. When the async resolution plane (which *does* read the graph) produces the
   resolution decision, have it stamp the subject's observed `entity_version`
   onto the decision, so the planner can emit a precise per-subject guard.
   This keeps the deterministic core read-free while still yielding tight
   preconditions.

## Reconciliation (Wave 0, 2026-08-21)

Still open, and now the most actionable of the four — it is a Wave 1
(executor) concern, not a deterministic-core one. The current
`kg_contracts.stores` surface already supports the resolution: `GraphMutationStore.apply(batch, preconditions)`
takes preconditions and returns a `CommitResult` whose non-empty
`failed_preconditions` means "stale snapshot: re-evaluate, never blindly
retry." So a snapshot/epoch-aware executor (Wave 1) can enforce the
plan-level `snapshot_version` guard this candidate leaves as provenance —
option 1 above — without any contract change. The deterministic core keeps
emitting the read-free `entity_version = 0` guard per `CREATE_IDENTITY` plus
the plan-level snapshot guard; the executor is where enforcement lands.
Option 2 (stamping observed `entity_version` from the read-capable resolution
plane) is revisited in the ER waves. No contract change needed for v1.
