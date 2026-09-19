# ADR candidate 0016: an executed compensation needs re-stamping against the current snapshot

Status: Superseded by ADR-0018 (2026-09-19)
Date: 2026-08-22
Surfaced by: Wave 8 (KGIS→KGCS end-to-end), `kgcs.executor.compensate`

## Context

Rollback is a compensating `CurationPlan` applied through the same executor
(Wave 1). The executor enforces the plan-level `snapshot_version` precondition
against the graph's current epoch (ADR candidate 0003, realized in Wave 1), so
a stale plan is rejected rather than raced.

## Problem

`Compensator.compensate(plan)` carries over the *original* plan's snapshot
precondition verbatim. But by the time a rollback runs, the original plan has
already committed and advanced the epoch — so the compensation's snapshot guard
is, by construction, stale, and the executor correctly rejects it `STALE`. This
is honest (no blind rollback), but there is no first-class way to say
"re-evaluate this compensation against the *current* snapshot and then apply":
the caller must hand-rebuild the precondition before the compensation can
commit.

## Local workaround

The E2E test re-stamps the compensation's snapshot precondition to the current
epoch before executing it — demonstrating a safe, deliberate rollback. This
works but is a manual, per-caller step.

## Possible future contract improvement

Add a first-class re-stamp path — e.g. `Compensator.compensate(plan, *,
against_snapshot=...)` or an executor "apply-compensation-against-current"
entry that re-evaluates the snapshot guard as part of a deliberate rollback —
so an executed rollback is ergonomic without hand-rebuilding preconditions,
while still refusing a blind retry. Relates to ADR candidate 0003 (snapshot
precondition enforcement) and 0010 (ClusterSnapshot vs Precondition).

## Disposition (v1 completion, 2026-09-17)

PROMOTE — a durable KGCS-local decision; the frozen contract added nothing to resolve it. Accepted for v1.

See `llm/governance/kgcs-v1-completion-reconciliation.md` §2 for the full matrix.

## Superseded (2026-09-19) — the accepted behaviour was a defect

[ADR-0018](../0018-compensating-plans-assert-post-application-state.md)
supersedes this candidate. Its "Problem" section is accurate and its
"possible future contract improvement" named the right remedy; what was wrong
was the **grading**. This was promoted at v1 completion as a durable
KGCS-local decision — an ergonomics gap with a working manual step. It was
not: because `PlanExecutor` enforces the plan-level snapshot guard itself
whenever the store is also a `GraphReader` (the reference `MemoryGraphStore`
is), a carried guard is false by construction and **every** compensation was
rejected `STALE` before reaching a store. Compensation was non-functional, not
merely inconvenient, and the "local workaround" was the only reason any
rollback had ever been observed to apply.

Two further defects sat behind it, undiscovered precisely because nothing ever
got past the guard to execute an inverse: the `RETRACT`→`ATTACH` inverse
payload could not validate as an `Assertion`, and the `ATTACH`→`RETRACT`
inverse dropped `new_status`/`superseded_at`. All three are fixed together in
ADR-0018; see its Context for the measured reproductions.
