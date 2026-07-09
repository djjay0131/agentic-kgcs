# System Patterns — agentic-kgcs

- CuratedGraphStore wraps any GraphStore; gate order: canonical ID →
  ontology (active types only) → versioned write.
- Rejections are data (quarantine + reason), exceptions are bugs;
  fail closed if ontology/registry unavailable.
- Lifecycle: PROVISIONAL → ACTIVE → SUPERSEDED/REVOKED; confidence-1.0
  structured syncs enter ACTIVE directly.
- Every merge reversible via version chains; every curation action gets
  an immutable audit record.
- System ADRs in agentic-kgis/docs/adr (0003 layered write path is the
  architectural core of this repo).
