# ADR candidate 0011: identity authority mode is not a first-class contract field

Status: Accepted (KGCS-local durable decision, v1 — 2026-09-17)
Date: 2026-08-22
Surfaced by: Wave 3 (DG-5 curation profiles), `kgcs.profiles`

## Context

DG-5 and Issue #2 require per-source/entity-type **identity authority mode**:
some sources are `CLIENT_AUTHORITATIVE` (reject-only) — their identity must
never be silently repaired or merged; ER emits `POSSIBLY_SAME_AS` proposals
instead of merging. Spec §7.5 also calls for source **authority** ("who may
declare a fact") recorded separately from every confidence score.

## Problem

No `kg_contracts` field carries identity authority mode onto a candidate or a
canonical entity. `CandidateEnvelope` has `producer`/`producer_run_id` but no
authority/entitlement, and `CanonicalEntity` has no authority at all. So KGCS
cannot read "is this identity client-authoritative?" from the data — the
predicate has to be supplied out of band. (Related but distinct from ADR
candidate 0004, which is about *assertion* authority = producer; this is about
*identity ownership/repair* authority.)

## Local workaround

`kgcs.profiles` holds `IdentityAuthorityMode`
(OPEN/ADVISORY/CLIENT_AUTHORITATIVE) on a `CurationProfile`, resolved per
(graph_id, entity_type, source) by a `ProfileRegistry`. Reject-only is enforced
at two layers keyed off the profile: `IdentityAuthorityConstraint` blocks any
merge containing a client-authoritative member, and `ErResolutionPolicy`
downgrades any auto-link to `PROPOSE_LINK` for non-`OPEN` modes. This is
correct and tested, but authority lives in KGCS config, not in the data the
candidate carries.

## Possible future contract improvement

Add an identity-authority / entitlement declaration to the contract (on the
candidate, or a per-source registry `kg_contracts` blesses), so client-
authoritative status travels with the data and cannot be lost if a profile is
misconfigured or absent. Until then, treat the profile registry as the
authority source and default unknown scopes to the safe (non-`OPEN`) side where
a domain requires it. See also [[0004-no-authority-provenance-on-candidates]].

## Disposition (v1 completion, 2026-09-17)

PROMOTE — a durable KGCS-local decision; the frozen contract added nothing to resolve it. Accepted for v1.

See `llm/governance/kgcs-v1-completion-reconciliation.md` §2 for the full matrix.
