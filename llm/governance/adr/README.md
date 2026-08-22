# Architecture Decision Records — agentic-kgcs (local)

Only kgcs-local decisions live here. System-level ADRs (spanning KGIS and
KGCS) live in `agentic-kgis/docs/adr/` — see ADRs 0001–0005 there for the
founding decisions. Use `0000-template.md`.

## Index

(no accepted local ADRs yet)

## Candidates (contract-friction log)

Open contract frictions surfaced during implementation live in
[`candidates/`](candidates/README.md) as ADR *candidates* — inputs to a
later governance review, not accepted decisions. As of Wave 0 (2026-08-21)
there are four, all still open against current `kg_contracts`:

- [0001 — `artifact` candidate has no curation operation type](candidates/0001-artifact-has-no-operation-type.md)
- [0002 — `AuditRecord` has no candidate/validation/resolution linkage](candidates/0002-audit-record-lacks-candidate-lineage.md)
- [0003 — deterministic core cannot emit per-subject preconditions without a snapshot read](candidates/0003-preconditions-need-snapshot-read.md)
- [0004 — no authority provenance on candidates](candidates/0004-no-authority-provenance-on-candidates.md)
