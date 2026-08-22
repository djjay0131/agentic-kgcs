# ADR candidate 0004: no authority provenance on candidates

Status: Candidate (contract friction; not an accepted decision)
Date: 2026-07-17
Surfaced by: Sprint 1 (deterministic curation core), `kgcs.planner`

## Context

An `Assertion` in the canonical graph requires an `authority` field (min
length 1): "who is entitled to assert this." The contract is emphatic that
authority is recorded **separately from every score** (spec §7.5): a
high-confidence deterministic sync may still carry data the source was never
entitled to assert. When the planner builds an `ATTACH_ASSERTION`, it must
populate `authority` on the assertion it constructs.

## Problem

A `Candidate` (`CandidateEnvelope`) carries `producer`, `producer_run_id`,
`source_coordinates`, and `evidence_refs` — but **no `authority` field**.
There is no place on a candidate for the producer to declare who is entitled
to assert the proposed fact, distinct from who produced the candidate. So the
planner has no authority signal to copy into the assertion, even though the
target model demands one.

## Local workaround

The planner sets the assertion's `authority` to the candidate's `producer`,
and builds `provenance` from the producer plus source coordinates. This
satisfies the contract's non-empty `authority` requirement and is honest
about where the value came from, but it **conflates "who produced this" with
"who is entitled to assert it"** — exactly the collapse the spec warns
against. It is acceptable for Sprint 1 (deterministic sync from trusted
producers) but is not a general authority model.

## Possible future contract improvement

Add an explicit `authority` (and possibly an entitlement/authority-scope) to
`CandidateEnvelope`, so a producer can declare assertion authority separately
from its own identity, and the planner can carry it through faithfully rather
than substituting `producer`. Until then, treat planner-produced `authority`
values as producer identity, not entitlement.

## Reconciliation (Wave 0, 2026-08-21)

Still open. `CandidateEnvelope` (`kg_contracts.candidates`) is unchanged and
carries no `authority` field distinct from `producer`. The workaround
(assertion `authority := candidate.producer`) is retained for the
deterministic core. This friction gains weight later in the build: DG-5
(source/capability curation profiles) and Issue #2's reject-only /
client-authoritative identity mode both need a real authority/entitlement
signal that is not the producer. The recommendation is to revisit this
candidate when curation profiles land (Wave 3) — a profile may supply the
authority/entitlement that the candidate envelope does not — rather than
mutating the frozen contract now. Owner decision still required.
