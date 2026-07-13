# agentic-kgcs

Knowledge Graph Curation Service. Ships `kgcs`:

- **admission/** — synchronous deterministic checks on candidate submission
  (contract validation, identity syntax repair-or-reject, ontology
  enforcement, policy, idempotency). Gates canonical semantic mutations only.
- **core/** — pure curation core: Candidate → ValidationDecision →
  ResolutionDecision → CurationPlan (no database connection; ADR-0010).
- **executor/** — applies CurationPlans against GraphMutationStore under
  optimistic preconditions; compensating rollback.
- **resolution/** — ER pipeline: blocking → typed features → calibrated
  matcher → cluster validation → policy gate (ADR-0007).
- **review/** — review-domain operations API + CLI.

Depends only on `kg_contracts` (shipped by sibling repo `agentic-kgis`).
Design: `agentic-kgis/docs/superpowers/specs/2026-07-09-kgis-kgcs-design.md` (v2)

## Dev setup

    python3 -m venv .venv
    .venv/bin/pip install -e ../agentic-kgis -e '.[dev]'
    .venv/bin/pytest
