# ADR candidate 0013: a re-curation plan's provenance is a trigger, not a candidate

Status: Candidate (contract friction; not an accepted decision)
Date: 2026-08-22
Surfaced by: Wave 5 (evidence-driven re-curation), `kgcs.recuration`

## Context

A `CurationPlan` (`kg_contracts.curation`) requires `candidate_ids`
(`min_length=1`) — the candidate-ledger candidates the plan acts on. In the
forward path (Waves 0–1) a plan is always driven by fresh candidates, so this
is natural.

## Problem

An evidence-driven re-curation plan is driven by a `CurationTrigger` over
records that are *already canonical* (an existing identity/assertion the new
evidence affects), not by a fresh ledger candidate. There is no candidate to
name, yet `candidate_ids` is mandatory and non-empty. The plan's true
provenance is the trigger (why re-evaluate) plus the affected canonical refs
and evidence ids — none of which `candidate_ids` is meant to hold.

## Local workaround

`kgcs.recuration` populates `candidate_ids` with the affected canonical
identity/assertion refs, falling back to the `trigger_id` when none is
specific, and carries the real provenance (`trigger_id`, `evidence_ids`,
matcher/adviser/policy versions) in each operation's `reversal_data`. This
satisfies the non-empty constraint honestly (the refs *are* what the plan acts
on) but overloads `candidate_ids` with two meanings.

## Possible future contract improvement

Model a plan's provenance source as either a candidate set OR a re-curation
trigger — e.g. an optional `trigger_id` / `source_kind` on `CurationPlan`, or a
union provenance field — so a re-curation plan need not borrow `candidate_ids`.
Relates to the audit-lineage friction ([[0002-audit-record-lacks-candidate-lineage]]).
Until then, treat a re-curation plan's `candidate_ids` as "affected refs", with
the trigger as the authoritative provenance in `reversal_data`.
