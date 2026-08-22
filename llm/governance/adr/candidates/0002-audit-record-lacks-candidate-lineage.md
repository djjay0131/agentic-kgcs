# ADR candidate 0002: `AuditRecord` has no candidate/validation/resolution linkage

Status: Candidate (contract friction; not an accepted decision)
Date: 2026-07-17
Surfaced by: Sprint 1 (deterministic curation core), `kgcs.audit`

## Context

The sprint brief asks the audit stage to make "every decision explainable"
and to capture, per decision: candidate id, validation, policy version,
resolution decision, timestamps, and trace ids.

## Problem

`AuditRecord` (`kg_contracts.curation`) is `frozen=True, extra="forbid"` and
its fields are: `audit_id`, `operation_id`, `decided_by`, `score_vector`,
`evidence_ids`, `policy_version`, `trace_id`, `recorded_at`. There is:

- **no `candidate_id`** — the record keys on `operation_id` only;
- **no validation outcome** and **no resolution decision** reference;
- and, relatedly, **`ResolutionDecision` carries no `policy_version`**, so the
  routing policy version cannot flow from resolution into the audit record —
  it must be supplied out of band.

`extra="forbid"` means none of these can be added to the record without
changing the contract. Taken literally, an `AuditRecord` alone cannot answer
"which candidate, validated how, produced this?".

## Local workaround

Full explainability is reconstructed without touching the contract, three
ways:

1. **Universal `trace_id`.** The trace id flows candidate → validation →
   resolution → operation → audit, so an audit record joins back to its
   candidate by trace (`test_every_audit_traces_back_to_a_planned_operation_and_candidate`).
2. **Lineage in `reversal_data`.** Every emitted operation's `reversal_data`
   carries its source `candidate_id`, so `operation_id` → candidate is
   recoverable from the plan
   (`test_operation_reversal_data_carries_source_candidate_lineage`).
3. **Decisions are themselves audit artifacts.** `ValidationDecision` and
   `ResolutionDecision` are frozen, policy-versioned, trace-stamped records —
   they *are* the audit of stages 1–2 — and the engine retains them per
   candidate in `EngineResult.outcomes`.

The routing `policy_version` is passed explicitly into `AuditRecorder` (from
the `ConfidencePolicy`) rather than read off the `ResolutionDecision`.

## Possible future contract improvement

Consider adding an optional `candidate_id` (and/or `validation_id` /
`resolution_id`) to `AuditRecord`, and a `policy_version` to
`ResolutionDecision`, so the audit stream is self-describing without relying
on trace-id joins and reversal-data conventions. If the audit record is meant
to stay operation-scoped by design, documenting the trace-id join as the
official candidate↔audit linkage would remove the ambiguity.

## Reconciliation (Wave 0, 2026-08-21)

Still open, and now confirmed against the wider build plan. `AuditRecord`
fields are unchanged (`audit_id`, `operation_id`, `decided_by`,
`score_vector`, `evidence_ids`, `policy_version`, `trace_id`, `recorded_at`)
and `ResolutionDecision` still carries no `policy_version`. The build plan
(Wave 1 §"known audit seam", Wave 7) independently reaches the same
conclusion: KGIS Plan 2's *ledger-transition* audit is a different object
from `kg_contracts.curation.AuditRecord`, and the plan's guidance is to keep
**separate semantic curation audit records linked by trace/candidate IDs**
rather than silently conflate them. That is exactly the trace-id-join
workaround this candidate documents, so the local approach is now the
plan-sanctioned direction for v1. Contract change (optional `candidate_id` on
`AuditRecord`, `policy_version` on `ResolutionDecision`) remains an owner
decision; the richer semantic audit sink is built in Wave 7 on top of this
join, not by mutating the frozen contract.
