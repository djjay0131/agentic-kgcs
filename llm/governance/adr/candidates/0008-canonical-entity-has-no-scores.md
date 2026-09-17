# ADR candidate 0008: `CanonicalEntity` carries no scores

Status: Deferred (adopter/upstream backlog — not a KGCS v1 blocker)
Date: 2026-08-22
Surfaced by: Wave 2 (ER 5a), `kgcs.er.features`

## Context

ER frequently scores a *candidate ↔ canonical entity* pair (does this incoming
candidate resolve to an entity already in the graph?). Typed features include
`source_reliability` — how trustworthy the source of each side is.

## Problem

`CandidateEnvelope` carries `scores: CandidateScores` (with
`source_reliability`), but `CanonicalEntity` (`kg_contracts.assertions`) carries
none — an accepted entity has no score set. So for a candidate↔canonical pair,
the canonical side contributes no `source_reliability`, an asymmetry with a
candidate↔candidate pair where both sides have scores.

## Local workaround

The `source_reliability` feature is honest-null for the canonical side and the
matcher treats a null feature as "not measured" (never fake-zero). The pair is
still scored on the remaining features; reliability simply informs less. This
is correct honest-null behaviour, but it means one side of every
candidate↔canonical match never contributes reliability.

## Possible future contract improvement

Consider whether accepted canonical records should retain a provenance/reliability
summary (e.g. the reliability of the authority that last asserted them), so ER
can weigh both sides symmetrically — noting this must not smuggle uncertainty
into the canonical graph (ADR-0006/0011). It may instead be recovered from the
entity's assertions' `authority`/scores rather than added to `CanonicalEntity`.
Until resolved, canonical-side reliability is absent by design.

## Disposition (v1 completion, 2026-09-17)

ADOPTER — deferred to the adopter/upstream backlog; KGCS's local handling (injectable/honest-null) is correct for v1.

See `llm/governance/kgcs-v1-completion-reconciliation.md` §2 for the full matrix.
