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

These are inputs to a later governance review, not accepted ADRs. Promotion
to a real ADR (here or in `agentic-kgis/docs/adr/`) is the owner's call.

## Index

- [0001 — `artifact` candidate has no curation operation type](0001-artifact-has-no-operation-type.md)
- [0002 — `AuditRecord` has no candidate/validation/resolution linkage](0002-audit-record-lacks-candidate-lineage.md)
- [0003 — deterministic core cannot emit per-subject preconditions without a snapshot read](0003-preconditions-need-snapshot-read.md)
- [0004 — no authority provenance on candidates](0004-no-authority-provenance-on-candidates.md)
- [0005 — `kg_contracts` has no strong-identifier taxonomy](0005-no-strong-identifier-taxonomy.md)
- [0006 — no canonical name for an entity embedding](0006-no-canonical-entity-embedding-slot.md)
- [0007 — `EntityCandidate` carries no `valid_period`](0007-entity-candidate-has-no-valid-period.md)
- [0008 — `CanonicalEntity` carries no scores](0008-canonical-entity-has-no-scores.md)

0001–0004 surfaced in Wave 0 (deterministic core); 0005–0008 in Wave 2 (ER 5a).
