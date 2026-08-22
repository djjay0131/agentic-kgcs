# ADR candidate 0012: no contract home for an LLM adviser assessment

Status: Candidate (contract friction; not an accepted decision)
Date: 2026-08-22
Surfaced by: Wave 4 (LLM curation orchestrator), `kgcs.advisers`

## Context

Every bounded LLM adviser assessment must record its provenance (DG-4): adviser
type/version, model id/version, prompt/template version, cited evidence ids,
recommendation, contradictions, confidence/abstention, trace id, and the
deterministic baseline decision before adviser input vs the final policy
decision after. The audit stream is future training data (§7.8), so this
provenance should be durable.

## Problem

The contract layer has no type for an adviser assessment. The spec sketches a
`ResolutionAssessment{recommendation, evidence_ids, contradictions, rationale,
confidence}`, but `kg_contracts` does not define it, and
`kg_contracts.curation.AuditRecord` is `frozen=True, extra="forbid"` and keys
only on `operation_id` — it carries no adviser type/version, prompt version,
recommendation, contradictions, or baseline-before/final-after. So an adviser
assessment cannot be represented, nor attached to the shared audit stream,
without a contract change.

## Local workaround

`kgcs.advisers` defines a KGCS-local `AdviserAssessment` (frozen) carrying the
full DG-4 provenance, and links it to the rest of the pipeline by `trace_id`
and `evidence_ids` — exactly as DG-4 permits ("directly or through
audit/reversal metadata as current contracts allow"). Adviser output never
becomes a contract type; it is folded into the deterministic decision as
evidence and retained locally.

## Possible future contract improvement

If adviser provenance must be first-class in the shared audit stream (for
cross-repo training/eval), add a semantic adviser-assessment record to
`kg_contracts` (or extend the audit record with an optional adviser-provenance
block). This is the same underlying friction as the audit-lineage gap already
logged — see [[0002-audit-record-lacks-candidate-lineage]]. Until then,
`AdviserAssessment` stays KGCS-local, joined by trace id.
