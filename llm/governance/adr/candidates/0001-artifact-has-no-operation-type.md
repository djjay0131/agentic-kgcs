# ADR candidate 0001: `artifact` candidate has no curation operation type

Status: Candidate (contract friction; not an accepted decision)
Date: 2026-07-17
Surfaced by: Sprint 1 (deterministic curation core), `kgcs.planner`

## Context

`IMPLEMENTED_KINDS` (`kg_contracts.candidates`) lists four v1 candidate kinds
the curation core accepts: `entity`, `relation`, `attribute_assertion`, and
`artifact`. The Sprint-1 planner turns validated, `AUTO`-routed candidates
into `CurationOperation`s an executor can apply.

## Problem

`CurationOperationType` (`kg_contracts.curation`) has seven values —
`CREATE_IDENTITY`, `ATTACH_ASSERTION`, `MERGE_IDENTITIES`, `SPLIT_IDENTITY`,
`REASSIGN_ASSERTION`, `RETRACT_ASSERTION`, `PROMOTE_ONTOLOGY_TERM` — and
**none of them corresponds to an artifact**. An artifact is, by the contract's
own words, "not a fact about the world … a produced object." So an
`ArtifactCandidate` can be validated and resolved, but the planner has no
operation type to emit for it. It is an *implemented* candidate kind with no
*applicable* operation.

This is consistent with the reference `MemoryGraphStore.apply`, which
implements only `CREATE_IDENTITY` and `ATTACH_ASSERTION` in Plan 1 — but that
store's silence on artifacts is about *execution* scope, whereas here the gap
is structural: there is no operation to plan even in principle.

## Local workaround

The planner treats an `artifact` candidate as producing **no operation**: it
validates and resolves like any other candidate, but contributes nothing to
the `CurationPlan`. This is deliberate and tested
(`test_artifact_yields_no_operation`), not a silent drop — the candidate's
`ValidationDecision` and `ResolutionDecision` are still retained in the
engine's `EngineResult`, so the artifact is accounted for, just not applied.

## Possible future contract improvement

Introduce an artifact-oriented operation type (e.g. `REGISTER_ARTIFACT`) — or
an explicit artifact store/ledger separate from the canonical mutation
operations — so that artifact candidates have a first-class destination
rather than resolving into a no-op. Alternatively, if artifacts are never
meant to reach the canonical graph, remove `artifact` from `IMPLEMENTED_KINDS`
and route it through a dedicated non-graph sink, so "implemented" and
"applicable" agree.

## Reconciliation (Wave 0, 2026-08-21)

Still open. `CurationOperationType` (`kg_contracts.curation`) is unchanged —
seven values, none artifact-oriented — and `artifact` remains in
`IMPLEMENTED_KINDS` (`kg_contracts.candidates`). The Wave-0 reconciliation of
the deterministic core preserves the local workaround (planner no-ops
artifacts, accounted for in `EngineResult`). Owner decision still required;
carry forward to the executor work (Wave 1), which is where a
`REGISTER_ARTIFACT`/artifact-sink destination would first have somewhere to
land.
