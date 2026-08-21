# Project Brief — agentic-kgcs

KGCS (Knowledge Graph Curation Service) is a reusable Python library that
guards and improves knowledge-graph content for every project in this
portfolio. Two halves: an inline synchronous gate (CuratedGraphStore:
canonical-ID repair-or-reject, data-backed ontology, versioned writes)
and an async curation plane (embedding entity resolution, confidence-routed
promotion PROVISIONAL→ACTIVE, human review queue, immutable audit).

Depends only on kg_contracts (from sibling repo agentic-kgis).
Authority: agentic-kgis/docs/superpowers/specs/2026-07-09-kgis-kgcs-design.md
Governance: agentic-governance v0.1 (llm/governance/governance-delta.md)
