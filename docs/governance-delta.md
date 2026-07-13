# Governance Delta: agentic-kgcs

Status: Approved (amended per external design review, agentic-kgis PR #1)
Last updated: 2026-07-10
Governance: agentic-governance v0.2

This file localizes [agentic-governance](https://github.com/djjay0131/agentic-governance)
for this project.

## Mission

KGCS (Knowledge Graph Curation Service) is a reusable Python library that
manages knowledge admission, identity, evidence, and canonical graph state
for every project in this portfolio — a graph-oriented assertion and
identity curation layer, not a home for domain reasoning. Two halves: a
deterministic curation core (validation, ontology policy, identity syntax,
curation planning) whose plans are applied by a transaction-aware executor,
and an async resolution plane (calibrated entity resolution with a bounded
LLM adviser, review operations, immutable audit). Uncertain candidates
live in a candidate ledger, never as ordinary canonical-graph entities.
It depends only on `kg_contracts` from `agentic-kgis`.

## Design-Authority Document

`agentic-kgis/docs/superpowers/specs/2026-07-09-kgis-kgcs-design.md`
(the shared KGIS/KGCS design spec lives in the sibling repo).

## Project Principles

1. No application-facing surface can mutate canonical graph state; only
   KGCS executors apply serializable, precondition-checked mutation
   batches (ADR-0010 in agentic-kgis). Deterministic admission checks are
   cheap and unbypassable; probabilistic resolution is async and
   reversible.
2. Repair-or-reject at the admission boundary; never silently coerce or
   drop.
3. Rejections and failures are data (quarantine/ledger states + reason);
   exceptions are bugs. Fail closed on canonical mutation, with transient
   faults distinguished from bad data.
4. Governed ontology: PROPOSED → APPROVED → OBSERVED → DEPRECATED; writes
   require approval, observation is tracked — no phantom node/edge types.
5. Candidates live in the ledger with processing states; only accepted
   identities and assertions materialize canonically, with bitemporal
   validity and assertion-level status. Every merge is a compensable
   operation; every curation action gets an immutable audit record.
6. Calibrated-risk routing (auto / LLM-advised / human) is the automation
   path — thresholds and policies are config, not code; the LLM advises
   with cited evidence and never issues the merge.
7. The audit stream and registry lineage are future training data; never
   skip them.
8. Derived projections are built only from canonical data at a published
   curation epoch.

## Domain Review Questions

- Can any application-facing path mutate canonical graph state without a
  KGCS executor? (Must be no.)
- Is every new curation action audited and compensable?
- Does this preserve fail-closed behavior on canonical mutation?
- Are thresholds/policies config rather than hard-coded?
- Does any LLM component decide (rather than advise with cited evidence)?
- Could uncertain/ledger data leak into canonical reads or projections?
- Does this keep kgcs depending only on kg_contracts?

## Memory Bank

Path: `llm/memory_bank/`

## Roadmap

Path: none (the plan sequence in the design spec §11 — in `agentic-kgis` —
serves as the roadmap).

## Governance Check Command

`node ~/code/agentic-governance/governance/scripts/governance-checks.mjs`
(canonical script from the agentic-governance checkout; CI wiring pending.)

## L0 Path Allowlist

```l0-allowlist
allow llm/memory_bank/** path-only
allow docs/adr/README.md index-table-rows
allow docs/adr/[0-9][0-9][0-9][0-9]-*.md status-line-only
allow docs/** link-target-only
deny src/**
deny scripts/**
deny .github/**
deny docs/adr/0000-template.md
```

## Platform Enforcement Reality

- Branch protection on `main`: unavailable (private repo, free plan —
  verified via `gh api` 403 on 2026-07-09). Merge discipline is
  convention-enforced.
- Required status checks: unavailable (same constraint).
- Token/identity model: all agent sessions authenticate with the owner's
  token — steward/auditor/architect are procedural roles, not distinct
  identities; independence is temporal/artifactual.
- Hardening path: GitHub Pro or public visibility would enable branch
  protection and required checks; blocked on owner's plan decision.

## Steward Activation Status

Status: INACTIVE

Steward merge authority ships inert (agentic-governance
`docs/l0-fast-track.md` §Per-Repo Activation). No activation ADR or PR
exists; all merges are human-owner-only.

## Milestone Labels

- `phase-3-curation-core`
- `phase-5-entity-resolution`
- `phase-6-eval-review`
- `phase-7-registry`

(Numbering shared with agentic-kgis's plan sequence, v2.)

## Special Labels

- `gate` (changes to the admission path / curation core — highest review
  scrutiny)

## Constitution Adjustments

None.

## Related Repos

- `agentic-kgis` — hosts `kg_contracts` (this repo's only dependency), the
  shared design spec, and system-level ADRs. kgcs-local ADRs live here in
  `docs/adr/`.
