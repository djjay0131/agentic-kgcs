# ADR candidate 0009: no ER-decision/propose-link type in `kg_contracts`

Status: Accepted (KGCS-local durable decision, v1 — 2026-09-17)
Date: 2026-08-22
Surfaced by: Wave 3 (cluster validation + resolution policy), `kgcs.er.resolution`

## Context

The deterministic ER policy gate (spec §7.4 step 6) chooses among fine-grained
actions: auto-link, retain-separate, gather-more-evidence, propose-link
(POSSIBLY_SAME_AS), LLM-assess, human-review, abstain, reject.

## Problem

`kg_contracts` models routing only as the coarse `AdjudicationRoute`
(AUTO/LLM_ASSESS/HUMAN), and `CurationOperationType` has `MERGE_IDENTITIES` but
no "propose a POSSIBLY_SAME_AS link" operation distinct from an actual merge.
So the eight-way ER outcome cannot be expressed in a contract type, and a
reject-only *proposal* (POSSIBLY_SAME_AS) has no operation the executor can
apply as a first-class, non-merging action.

## Local workaround

`kgcs.er.resolution` defines a KGCS-local `ErAction` (8-way) and `ErDecision`,
and provides `ErDecision.to_route()` to project onto the contract's
`AdjudicationRoute` for the existing decision-production path. A propose-link is
represented with `IdentityLinkKind.POSSIBLY_SAME_AS` at the KGCS layer.

## Possible future contract improvement

Promote an ER-decision type (or extend `AdjudicationRoute`/`CurationOperationType`
with a `PROPOSE_IDENTITY_LINK` operation carrying `IdentityLinkKind`) so the
executor can consume ER decisions directly rather than through a lossy
projection, and so a reject-only proposal is a first-class, auditable,
non-merging operation. Until then, `ErAction` stays KGCS-local.

## Disposition (v1 completion, 2026-09-17)

PROMOTE — a durable KGCS-local decision; the frozen contract added nothing to resolve it. Accepted for v1.

See `llm/governance/kgcs-v1-completion-reconciliation.md` §2 for the full matrix.
