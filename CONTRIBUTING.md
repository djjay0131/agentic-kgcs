# Contributing to agentic-kgcs

Status: Active
Last updated: 2026-07-09

This project follows [agentic-governance](https://github.com/djjay0131/agentic-governance)
(see `docs/governance-delta.md` for project specifics).

## Before You Start

1. `llm/memory_bank/activeContext.md`
2. Design authority: `agentic-kgis/docs/superpowers/specs/2026-07-09-kgis-kgcs-design.md`
3. `docs/governance-delta.md`
4. agentic-governance: `docs/architecture-governance.md`,
   `docs/project-operating-system.md`

## Contribution Rules

- No direct commits to `main`. Issue → Branch → Draft PR → Review → Merge.
- ADRs: kgcs-local decisions in `docs/adr/`; system-level decisions go to
  `agentic-kgis/docs/adr/`.
- Update `llm/memory_bank/` when project context changes.
- AI agents: follow assigned scope, identify ADR candidates, never merge
  your own PR.

## Definition of Done

See agentic-governance `docs/definition-of-done.md`. For this repo
additionally: `pytest` and `ruff check src tests` green; tests run against
`kg_contracts.testing.memory_store` (no graph infrastructure required).
