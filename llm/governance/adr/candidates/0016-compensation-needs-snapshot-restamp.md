# ADR candidate 0016: an executed compensation needs re-stamping against the current snapshot

Status: Candidate (contract friction; not an accepted decision)
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
