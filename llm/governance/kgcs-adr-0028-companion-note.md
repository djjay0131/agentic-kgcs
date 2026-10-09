# KGCS companion note — agentic-kgis ADR-0028 (assertion lineage pointers)

Status: Applied
Date: 2026-10-09
References: agentic-kgis ADR-0028
`llm/governance/adr/0028-assertion-candidate-link-and-superseded-by.md`
(Accepted 2026-10-09); KGIS implementation PR #69; agentic-kgps#1; KGCS issue
#58.

This is a **note**, not an ADR: KGCS adopts agentic-kgis ADR-0028's semantics
unchanged and makes no new decision of its own. It is recorded here because the
KGCS half of that ADR is source-affecting (the record now carries two new
lineage pointers) and the repo records such adoptions in the control plane.

## What agentic-kgis ADR-0028 decided

`Assertion` gained two optional, read-only lineage fields (outside the ADR-0021
record seed):

- `source_candidate_ids: tuple[str, ...] = ()` — the candidate(s) a record was
  planned from. Set-like: deterministic first-seen order, duplicates forbidden,
  empty tuple the honest null for a record with no candidate origin.
- `superseded_by: str | None = None` — the record that replaced this one, with a
  **partial** invariant: when set, `status is SUPERSEDED` and `superseded_at` is
  set and the id is well-formed; the converse is *not* required, so a
  `SUPERSEDED` record with no single successor (a merge, or the non-injective
  ADR-0021 re-id backfill) stays representable. `GraphWriter.mark_superseded`
  was extended to `mark_superseded(assertion_id, at, replaced_by=None)`.

## What KGCS changed (this repo)

- **Planner.** `CurationPlanner._assertion` sets `source_candidate_ids =
  (candidate.candidate_id,)` on the single `ATTACH_ASSERTION` construction site.
- **Evolution.** `ConceptEvolutionPlanner.next_record` resets both pointers on
  the successor: `source_candidate_ids=()` (its origin is the *prior record*,
  not a candidate) and `superseded_by=None`.
  `plan_supersession` sets the **typed** `superseded_by = new record id` on the
  retired `SUPERSEDED` copy, and its `_check_supersedes` already refuses a
  self-pointer (a record cannot name itself its own replacement).
- **Compensation.** A compensating `RETRACT_ASSERTION` carries no successor, and
  `mark_superseded(replaced_by=None)` cannot clear a pointer (it leaves an
  existing one as-is). Compensation clears `superseded_by` by **re-attaching the
  pre-retraction assertion**, whose pointer is `None`. Pinned end to end in
  `tests/kgcs/test_e2e_concurrency.py`.
- **Audit.** `AssertionSemanticAuditRecord.source_candidate_ids` carries the
  candidate lineage of the attached record — the assertion-side join issue #48
  deferred until the contract field landed.
- **Tests / harness.** The test-only `E2EGraphStore` persists the pointer via
  `mark_superseded(assertion_id, at, replaced_by=...)`; the flagship re-curation
  test reads the typed pointer back off the canonical record.

## KGCS-local decisions recorded

- **The untyped `superseded_by` key in the forward `RETRACT_ASSERTION` payload
  is retained** as the operation's *transport* — it is what drives
  `mark_superseded(..., replaced_by=...)`, exactly the primitive ADR-0028 chose
  over a full-copy `put_assertion`. The typed field on the committed record is
  the **authoritative reader surface**; the payload key is read only for
  backward compatibility of already-persisted payloads, through the documented
  `kgcs.recuration.superseded_pointer` reader (which refuses a self-pointer).
  This is the one place the "stop relying on the untyped payload key" clause is
  interpreted: the payload remains data on the operation, but no reader answers
  "what replaced this?" from it when the record is available.

## What KGCS deliberately did NOT do

- No `kg_contracts` change: the fields and the `mark_superseded` extension ship
  from agentic-kgis (PR #69, contract 2.3.0), which CI installs from `main`.
- No new ADR: the semantics are agentic-kgis ADR-0028's; KGCS implements them.
- `Candidate.evidence_refs` population on the structured path remains an
  orthogonal upstream KGIS gap (ADR-0028 §Companion changes), not absorbed here.

## Verification

`ruff check src tests`, `mypy src` (strict), and `pytest` are green on the
implementing branch (`feat/58-assertion-candidate-link`). See the PR and the
memory-bank update for exact counts.
