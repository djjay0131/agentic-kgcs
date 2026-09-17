# Architecture Decision Records — agentic-kgcs (local)

Only kgcs-local decisions live here. System-level ADRs (spanning KGIS and
KGCS) live in `agentic-kgis/docs/adr/` — see ADRs 0001–0005 there for the
founding decisions. Use `0000-template.md`.

## Index

(no accepted local ADRs yet)

## Candidates (contract-friction log)

Open contract frictions surfaced during implementation live in
[`candidates/`](candidates/README.md) as ADR *candidates* — inputs to a
later governance review, not accepted decisions. There are sixteen, all still
open against current `kg_contracts`:

- [0001 — `artifact` candidate has no curation operation type](candidates/0001-artifact-has-no-operation-type.md)
- [0002 — `AuditRecord` has no candidate/validation/resolution linkage](candidates/0002-audit-record-lacks-candidate-lineage.md)
- [0003 — deterministic core cannot emit per-subject preconditions without a snapshot read](candidates/0003-preconditions-need-snapshot-read.md)
- [0004 — no authority provenance on candidates](candidates/0004-no-authority-provenance-on-candidates.md)
- [0005 — `kg_contracts` has no strong-identifier taxonomy](candidates/0005-no-strong-identifier-taxonomy.md)
- [0006 — no canonical name for an entity embedding](candidates/0006-no-canonical-entity-embedding-slot.md)
- [0007 — `EntityCandidate` carries no `valid_period`](candidates/0007-entity-candidate-has-no-valid-period.md)
- [0008 — `CanonicalEntity` carries no scores](candidates/0008-canonical-entity-has-no-scores.md)
- [0009 — no ER-decision/propose-link type in `kg_contracts`](candidates/0009-no-er-action-contract-type.md)
- [0010 — `ClusterSnapshot` vs `Precondition(cluster_version)`](candidates/0010-cluster-snapshot-vs-precondition.md)
- [0011 — identity authority mode is not a first-class contract field](candidates/0011-identity-authority-not-first-class.md)
- [0012 — no contract home for an LLM adviser assessment](candidates/0012-no-adviser-assessment-contract-type.md)
- [0013 — a re-curation plan's provenance is a trigger, not a candidate](candidates/0013-recuration-plan-provenance-source.md)
- [0014 — the `ReviewQueue` contract can't express typed outcomes or a typed review case](candidates/0014-review-queue-surface-limits.md)
- [0015 — the reference `MemoryGraphStore` applies only two operation types](candidates/0015-reference-store-limited-operation-coverage.md)
- [0016 — an executed compensation needs re-stamping against the current snapshot](candidates/0016-compensation-needs-snapshot-restamp.md)

(0001–0004 from Wave 0; 0005–0008 from Wave 2; 0009–0011 from Wave 3; 0012 from Wave 4; 0013 from Wave 5; 0014 from Wave 6; 0015–0016 from Wave 8.)
