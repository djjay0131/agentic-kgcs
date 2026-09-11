## Governance Level

Declare exactly one level. Definitions live in the canonical repo at
`llm/governance/governance-levels.md` — resolve it against the `Canon checkout`
declared in `llm/governance/governance-delta.md` §Canon Location, or read it at
<https://github.com/djjay0131/agentic-governance>. Uncertain classification is
semantic: pick the lowest plausible semantic level.

- [ ] L0 — Administrative (non-semantic). Complete and include the
      **Administrative Change Certification** block from canonical
      `llm/governance/l0-fast-track.md` in this PR body.
- [ ] L1 — Governance & Architecture (semantic; human review required)
- [ ] L2 — Implementation (semantic; human review required)
- [ ] L3 — Product (semantic; human review required)

One-line classification justification:

## Problem

## Motivation

Why now?

## Summary of Changes

## Design Decisions

## Tradeoffs

## Open Questions

## Related Docs / ADRs

- Design spec: agentic-kgis/llm/specs/2026-07-09-kgis-kgcs-design.md
- ADRs:

## Memory-Bank Updates

- [ ] Included / [ ] Not needed — why:

## Review Checklist (author)

- [ ] Aligns with the design spec and governance delta principles
- [ ] `kg_contracts` stays free of engine/LLM/I/O code (if touched)
- [ ] No undocumented durable decisions (ADR created/updated if needed)
- [ ] Provenance and content-hash discipline preserved on data paths
- [ ] Tests or validation included
- [ ] Scope appropriate for one PR
