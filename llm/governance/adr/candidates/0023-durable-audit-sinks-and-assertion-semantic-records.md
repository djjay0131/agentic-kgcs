# ADR candidate 0023: durable audit sinks and an assertion-decision semantic record are KGCS-local

Status: Open
Date: 2026-10-08
Surfaced by: issue #48 (durable audit storage), `kgcs.persistence.sqlite`,
`kgcs.observability.semantic_audit`

## Context

Issue #48 requires the audit streams to survive a process restart and to cover
assertion / re-curation decisions, not just ER decisions. The three existing
audit objects are all KGCS-local (`SemanticAuditRecord`, `ExecutionRecord`) or
frozen-contract-but-sinkless (`kg_contracts.curation.AuditRecord`):
`kgcs.memory` held the only sinks, and `SemanticAuditRecord` was scoped to
`ErDecision`.

## Problem

`kg_contracts` defines the audit *record* shapes but no durable *sink*, and its
`AuditRecord` is operation-scoped only — there is no contract type for a
decision-scoped semantic audit and none for an assertion / concept-evolution
decision. Requiring a new contract type (and a contract-owned sink) to persist
the audit would block issue #48 on a cross-repo change and put I/O in
`kg_contracts`, which the governance delta forbids.

## Local workaround

Implement all of it KGCS-local, without touching the frozen contract:

- `kgcs.persistence.sqlite` adds `SqliteAuditSink` / `SqliteExecutionSink` /
  `SqliteSemanticAuditSink` — append-only-at-rest tables whose immutability is
  enforced by `BEFORE UPDATE`/`BEFORE DELETE` triggers (`RAISE(ABORT)`), the
  pattern `agentic-kgis` uses in `src/kgis/ledger/audit.py`. `BEFORE UPDATE`/
  `BEFORE DELETE` alone does not close `INSERT OR REPLACE`, which deletes the
  conflicting row without firing a `DELETE` trigger; every table also carries a
  `BEFORE INSERT` duplicate-key guard, so a replacement is aborted before
  conflict resolution. The schema is created with individual `execute()` calls
  inside a `SAVEPOINT` (never `executescript`, which commits a shared
  connection's open transaction), each append wraps its main and ref rows in one
  transaction, and `PRAGMA user_version` stamps and checks the schema version on
  open. The in-memory sinks stay the defaults.
- `SemanticAuditRecord` gains an injected-clock `recorded_at` (optional,
  default `None` — the honest unknown for a pre-#48 record, so main-era
  serialized records still validate and replay) and a `decision_kind`
  discriminant; a sibling `AssertionSemanticAuditRecord` covers assertion /
  re-curation decisions, produced by the same
  `SemanticAuditBuilder.build_assertion` and recorded through the same
  `SemanticAuditSink` (a union), joined by `trace_id` / `plan_id` / assertion
  ids.
- The semantic sink keeps indexed assertion/operation ref tables, so
  `records_for_assertion` / `records_for_operation` are joins, not scans.

## Possible future contract improvement

If durable audit becomes cross-repo, promote a `SemanticAuditRecord` /
assertion-decision contract type and an append-only sink protocol into
`kg_contracts` (or share KGIS's ledger sink). Until then, the KGCS-local shape
keeps the contract free of engine and I/O code. Relates to ADR candidate 0002
(audit lacks candidate lineage) and 0012 (no contract home for an adviser
assessment).
