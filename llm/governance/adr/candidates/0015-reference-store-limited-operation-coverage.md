# ADR candidate 0015: the reference `MemoryGraphStore` applies only two operation types

Status: Open (retained — upstream kg_contracts/KGIS blocker)
Date: 2026-08-22
Surfaced by: Wave 8 (KGIS→KGCS end-to-end), `tests/kgcs/e2e_harness.py`

## Context

The end-to-end demonstration of the flagship re-curation flow needs to actually
*execute* a supersession (an `ATTACH_ASSERTION` for the new fact plus a
`RETRACT_ASSERTION`/status-change marking the prior assertion `SUPERSEDED`) and
observe the canonical graph reach epoch N+1 with the old record still
queryable.

## Problem

The reference `kg_contracts.testing.memory.MemoryGraphStore.apply()` implements
only `CREATE_IDENTITY` and `ATTACH_ASSERTION` (Plan 1); the other five
`CurationOperationType`s raise `NotImplementedError`. So no supersession,
merge, split, reassign, or promotion can be executed against the shared
reference adapter — the Wave-1 executor correctly reports
`UNSUPPORTED_OPERATION`, but that means the reference store cannot demonstrate
the very operations Waves 3/5 plan.

## Local workaround

The E2E suite defines a **test-only** `E2EGraphStore` that widens the reference
store to also apply `RETRACT_ASSERTION` via the contract's own
`GraphWriter.mark_superseded` writer (preserving atomicity and epoch
semantics). It is confined to `tests/` and never imported by `src/kgcs`.

## Possible future contract improvement

Widen `kg_contracts.testing.memory.MemoryGraphStore.apply()` to cover the full
`CurationOperationType` vocabulary (at least `RETRACT_ASSERTION`,
`MERGE_IDENTITIES`, `SPLIT_IDENTITY`, `REASSIGN_ASSERTION`) so every downstream
repo can execute and test the compensable operations against one shared
reference adapter instead of each re-implementing a shim. Relates to ADR
candidate 0001 (artifact has no operation type) — both are about the gap
between planned and executable operations.

## Disposition (v1 completion, 2026-09-17)

RETAIN — still-open; resolution requires a kg_contracts/KGIS change (owner-owned). KGCS's local handling stands for v1.

See `llm/governance/kgcs-v1-completion-reconciliation.md` §2 for the full matrix.
