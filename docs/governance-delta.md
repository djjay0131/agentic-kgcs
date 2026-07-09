# Governance Delta: agentic-kgcs

Status: Approved
Last updated: 2026-07-09
Governance: agentic-governance v0.1

This file localizes [agentic-governance](https://github.com/djjay0131/agentic-governance)
for this project.

## Mission

KGCS (Knowledge Graph Curation Service) is a reusable Python library that
guards and improves knowledge-graph content for every project in this
portfolio. Two halves: an inline synchronous gate (`CuratedGraphStore`:
canonical-ID repair-or-reject, data-backed ontology, versioned writes) and
an async curation plane (embedding entity resolution, confidence-routed
promotion PROVISIONAL→ACTIVE, human review queue, immutable audit). It
depends only on `kg_contracts` from `agentic-kgis`.

## Design-Authority Document

`agentic-kgis/docs/superpowers/specs/2026-07-09-kgis-kgcs-design.md`
(the shared KGIS/KGCS design spec lives in the sibling repo).

## Project Principles

1. The gate is deterministic, synchronous, cheap, and unbypassable; the
   curation plane is probabilistic, async, and reversible (ADR-0003 in
   agentic-kgis).
2. Repair-or-reject at the write boundary; never silently coerce or drop.
3. Rejections are data (quarantine + reason); exceptions are bugs. Fail
   closed if ontology/registry cannot load.
4. Data-backed-only ontology: no phantom node/edge types.
5. Lifecycle PROVISIONAL → ACTIVE → SUPERSEDED/REVOKED; every merge
   reversible via version chains; every curation action gets an immutable
   audit record.
6. Confidence-routing (auto / LLM evaluate / consensus / human) is the
   automation path — thresholds are config, not code.
7. The audit stream is future training data; never skip it.

## Domain Review Questions

- Can this write path bypass the gate? (Must be no.)
- Is every new curation action audited and reversible?
- Does this preserve fail-closed behavior?
- Are thresholds/policies config rather than hard-coded?
- Does this keep kgcs depending only on kg_contracts?

## Memory Bank

Layout: `llm/memory_bank/`

## Milestone Labels

- `phase-2-gate`
- `phase-4-curation-plane`
- `phase-5-registry`

(Numbering shared with agentic-kgis's plan sequence.)

## Special Labels

- `gate` (changes to the inline write gate — highest review scrutiny)

## Constitution Adjustments

None.

## Related Repos

- `agentic-kgis` — hosts `kg_contracts` (this repo's only dependency), the
  shared design spec, and system-level ADRs. kgcs-local ADRs live here in
  `docs/adr/`.
