# ADR candidates — contract friction log

The architecture (and `kg_contracts`) is **frozen**. When implementing a
sprint surfaces a genuine tension with a contract, the rule is: implement a
**local workaround**, document the tension here as an ADR *candidate*, and
keep going — never redesign the contract in place.

Each candidate records:

- **Context** — what was being built when the tension appeared.
- **Problem** — the specific contract friction.
- **Local workaround** — what Sprint 1 actually did, within the frozen
  contract.
- **Possible future contract improvement** — a suggestion for a *future*
  contract revision, to be adjudicated by the contract owner. Not a decision.

**Reconciled at v1 completion (2026-09-17)** — see
[`../kgcs-v1-completion-reconciliation.md`](../../kgcs-v1-completion-reconciliation.md)
§2. Each candidate now carries a `Status` and a *Disposition* note:
**Accepted (KGCS-local durable decision, v1):** 0001, 0002, 0003, 0009, 0010,
0011, 0012, 0013, 0014, 0016. **Open (retained — upstream kg_contracts/KGIS
blocker):** 0004, 0015. **Deferred (adopter/upstream backlog):** 0005, 0006,
0007, 0008. No candidate was resolved by a KGIS contract change (KGIS ADRs
0015–0023 added no fields that retire these) and none is obsolete. System-level
contract changes remain the owner's call, tracked in
`agentic-kgis/llm/governance/adr/`.

## Index

- [0001 — `artifact` candidate has no curation operation type](0001-artifact-has-no-operation-type.md)
- [0002 — `AuditRecord` has no candidate/validation/resolution linkage](0002-audit-record-lacks-candidate-lineage.md)
- [0003 — deterministic core cannot emit per-subject preconditions without a snapshot read](0003-preconditions-need-snapshot-read.md)
- [0004 — no authority provenance on candidates](0004-no-authority-provenance-on-candidates.md)
- [0005 — `kg_contracts` has no strong-identifier taxonomy](0005-no-strong-identifier-taxonomy.md)
- [0006 — no canonical name for an entity embedding](0006-no-canonical-entity-embedding-slot.md)
- [0007 — `EntityCandidate` carries no `valid_period`](0007-entity-candidate-has-no-valid-period.md)
- [0008 — `CanonicalEntity` carries no scores](0008-canonical-entity-has-no-scores.md)
- [0009 — no ER-decision/propose-link type in `kg_contracts`](0009-no-er-action-contract-type.md)
- [0010 — `ClusterSnapshot` and `Precondition(cluster_version)` model the same check twice](0010-cluster-snapshot-vs-precondition.md)
- [0011 — identity authority mode is not a first-class contract field](0011-identity-authority-not-first-class.md)
- [0012 — no contract home for an LLM adviser assessment](0012-no-adviser-assessment-contract-type.md)
- [0013 — a re-curation plan's provenance is a trigger, not a candidate](0013-recuration-plan-provenance-source.md)
- [0014 — the `ReviewQueue` contract can't express typed outcomes or a typed review case](0014-review-queue-surface-limits.md)
- [0015 — the reference `MemoryGraphStore` applies only two operation types](0015-reference-store-limited-operation-coverage.md)
- [0016 — an executed compensation needs re-stamping against the current snapshot](0016-compensation-needs-snapshot-restamp.md)

0001–0004 surfaced in Wave 0 (deterministic core); 0005–0008 in Wave 2 (ER 5a);
0009–0011 in Wave 3 (cluster validation + resolution policy + profiles); 0012
in Wave 4 (LLM curation orchestrator); 0013 in Wave 5 (re-curation); 0014 in
Wave 6 (review queue); 0015–0016 in Wave 8 (end-to-end).
