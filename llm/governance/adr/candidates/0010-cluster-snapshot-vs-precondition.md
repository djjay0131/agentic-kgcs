# ADR candidate 0010: `ClusterSnapshot` and `Precondition(cluster_version)` model the same check twice

Status: Candidate (contract friction; not an accepted decision)
Date: 2026-08-22
Surfaced by: Wave 3 (cluster validation), `kgcs.er.cluster`

## Context

Cluster validation decides membership against a versioned cluster snapshot so a
stale decision is rejected rather than racing a concurrent change. The executor
already checks optimistic-concurrency guards via
`Precondition(kind="cluster_version", subject=<identity>, expected="17")`
(`kg_contracts.curation`, whose docstring names exactly this example).

## Problem

The same optimistic-concurrency fact is modelled from two directions: the ER
layer needs a rich `ClusterSnapshot` (cluster_id, version, members, entity_type)
to *validate* prospective membership, while the plan/executor layer needs a flat
`Precondition` string triple to *enforce* it at commit. There is no shared
cluster-version type, so a `ClusterSnapshot` must be translated into a
`Precondition` at plan-assembly time, and the two can drift.

## Local workaround

`kgcs.er.cluster` keeps a KGCS-local `ClusterSnapshot` with an `is_stale` check
for the resolution stage; when a resulting decision becomes a `CurationPlan`
(later wave), the snapshot's `(cluster_id, version)` is projected into a
`Precondition(kind="cluster_version", subject=cluster_id, expected=version)`
that the executor already knows how to reject on. The translation is
mechanical but manual.

## Possible future contract improvement

Consider a shared cluster-version value object (or a documented, tested
`ClusterSnapshot → Precondition` projection in the contract layer) so the ER
validator and the executor consume one representation of the cluster-version
guard rather than two that must be kept in sync. Relates to ADR candidate 0003
(snapshot-level precondition enforcement at the executor).
