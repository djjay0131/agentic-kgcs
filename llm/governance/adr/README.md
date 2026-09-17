# Architecture Decision Records — agentic-kgcs (local)

Only kgcs-local decisions live here. System-level ADRs (spanning KGIS and
KGCS) live in `agentic-kgis/docs/adr/` — see ADRs 0001–0005 there for the
founding decisions. Use `0000-template.md`.

## Index

(no accepted local ADRs yet)

## Candidates (contract-friction log)

Open contract frictions surfaced during implementation live in
[`candidates/`](candidates/README.md) as ADR *candidates* — inputs to a
later governance review, not accepted decisions. There are eight, all still
open against current `kg_contracts`:

- [0001 — `artifact` candidate has no curation operation type](candidates/0001-artifact-has-no-operation-type.md)
- [0002 — `AuditRecord` has no candidate/validation/resolution linkage](candidates/0002-audit-record-lacks-candidate-lineage.md)
- [0003 — deterministic core cannot emit per-subject preconditions without a snapshot read](candidates/0003-preconditions-need-snapshot-read.md)
- [0004 — no authority provenance on candidates](candidates/0004-no-authority-provenance-on-candidates.md)
- [0005 — `kg_contracts` has no strong-identifier taxonomy](candidates/0005-no-strong-identifier-taxonomy.md)
- [0006 — no canonical name for an entity embedding](candidates/0006-no-canonical-entity-embedding-slot.md)
- [0007 — `EntityCandidate` carries no `valid_period`](candidates/0007-entity-candidate-has-no-valid-period.md)
- [0008 — `CanonicalEntity` carries no scores](candidates/0008-canonical-entity-has-no-scores.md)

(0001–0004 from Wave 0; 0005–0008 from Wave 2.)
