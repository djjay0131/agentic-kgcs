# KGCS v1 — completion reconciliation (Gate G2 + ADR + Issue #2)

Status: Completion record
Date: 2026-09-17
Governance: agentic-governance v0.9.0
Plan: the KGCS completion-orchestration plan (Gates G2, §10, §11), tracked on
branch `docs/2026-09-10-kgcs-completion-orchestration` — not yet on `main` (its
`llm/plans/` slot is undeclared in the governance delta; declaring it is the
owner's governance action). Its definition-of-complete is reproduced in §4
below, so this record is self-contained.

This is the formal completion reconciliation for KGCS v1. All nine build waves
are merged to `main` (PRs #5, #10–#17) and CI is green (ruff pinned 0.16.4 +
explicit `E4/E7/E9/F/I`; mypy strict, 49 files; 435 pytest; governance checks
4/4). Governance is current (v0.9.0). This record closes Gate G2 (cross-repo
contract reconciliation), §10 (ADR-candidate reconciliation), and the Issue #2
re-disposition.

## 1. Contract compatibility vs current KGIS `main`

KGIS ADRs 0015–0023 are all *Accepted*, but **only ADR-0021 landed an actual
`kg_contracts` narrowing**; 0015–0020, 0022, 0023 are KGIS-internal or
ship-with-KGIS-local-workaround (no contract-field additions). Consequently no
KGCS candidate is resolved by a new contract field.

| Area | Verdict | Evidence |
|---|---|---|
| ADR-0021 `CommitResult` fail-closed narrowing | **OK — compliant** | Contract requires `error` or non-empty `failed_preconditions` when `committed=False`. KGCS constructs `CommitResult(committed=False,…)` only in the test-only `tests/kgcs/e2e_harness.py`, guarded so `failed_preconditions` is non-empty; the executor only *consumes* `CommitResult`. No violation in `src/` or tests. |
| ADR-0022 structured snapshot provenance | No impact | No structured snapshot type added; `Precondition` unchanged. KGCS candidates 0003/0010 project into the same flat `Precondition` as before. |
| ADR-0020 recommendation-outcomes / honest-null | No impact | Governs the KGIS registry extend-vs-create decision; defines no ER/adviser type for KGCS. Honest-null is shared discipline only. |
| ADR-0023 candidate/extractor-version fields | No impact | ADR-0023 concerns extractor/model-version provenance (not authority); it added no `authority`, `valid_period`, or extractor-version field to `CandidateEnvelope`. KGCS candidates 0004/0007/0011 therefore remain unaddressed by contract. |
| ADR-0015/0016/0018 | No KGCS counterpart | KGIS ingestion/registry concerns. |
| ADR-0017 public deterministic-id helper | Not exported; no KGCS candidate | KGCS reimplements Crockford base32 in `ids.py`; dedupe possible if KGIS exports it later. |
| ADR-0019 open-backend identifier | Unrelated to KGCS 0005 | It concerns `GraphDescriptor.backend`, not `EntityRef` identifier strength. |
| `FrozenMapping` serialization | **OK — resolved** | `_frozen.py` `FrozenMapping` subclasses `dict` so a frozen payload nested as an opaque value serializes; this closed the compensation-serialization break. |
| `MemoryGraphStore` op set | Unchanged | Still only `CREATE_IDENTITY`/`ATTACH_ASSERTION`; others raise. Basis of candidates 0001/0015; KGCS's `E2EGraphStore` widens it in tests only. |
| `kg_eval.MetricProvider` seam | **Non-blocking divergence** | KGIS `kg_eval.MetricProvider.evaluate(output, gold)` vs KGCS `provider.snapshot()`. KGCS deliberately does not import `kg_eval` (no reverse dep). Reconcile via an adapter at the `kg_eval` consumer side (kg_eval may import kgcs). Recorded, not a KGCS defect. |

**No blocking KGCS correctness/compat defect.**

## 2. ADR-candidate dispositions (§10)

None obsolete; none resolved by a KGIS contract change (the mirroring KGIS ADRs
added no fields). Disposition of the 16 KGCS candidates:

**PROMOTE — accepted as durable KGCS-local decisions (v1):**
0001 (artifact no-op accounting), 0002 (audit lineage via trace-id + Wave-7
semantic sink), 0003 (read-free preconditions + executor snapshot enforcement),
0009 (`ErAction`/`ErDecision` + `to_route()`), 0010 (`ClusterSnapshot` →
`Precondition(cluster_version)` projection), 0011 (identity-authority
profiles — also satisfies Issue #2 item 2 locally), 0012 (`AdviserAssessment`
DG-4 provenance), 0013 (re-curation plan provenance via `reversal_data`), 0014
(typed review-queue errors + `ReviewCase`-in-payload), 0016 (compensation
snapshot re-stamp pattern).

**RETAIN — still-open, upstream (KGIS/`kg_contracts`) blocker:**
- **0004** — no `authority` field on `CandidateEnvelope` distinct from
  `producer`; `authority := producer` is a trusted-sync stopgap. Same field ask
  underlies Issue #2 item 2.
- **0015** — reference `MemoryGraphStore.apply()` covers only two op types;
  widening it is KGIS's call. KGCS's `E2EGraphStore` shim covers v1 in tests.

**ADOPTER — deferred to adopter/upstream backlog (not a KGCS v1 need):**
0005 (strong-identifier taxonomy — injectable `DEFAULT_STRONG_NAMESPACES`),
0006 (canonical embedding slot — heuristic degrades honest-null), 0007
(`EntityCandidate.valid_period` — read from `properties`), 0008
(`CanonicalEntity` scores — honest-null by design, must not smuggle uncertainty).

## 3. Issue #2 re-disposition (against current KGIS)

1. **Subject-scoped erasure (CRITICAL): still a gap.** `kg_contracts.security`
   ships the *shape* (`DeletionBehavior{HARD_DELETE,TOMBSTONE,RETAIN}`,
   `PolicyContext`) but **no executable purge** — no PURGE/ERASE op, `PolicyContext`
   not wired onto candidates/results. Regulated-PII adopters still blocked;
   KGIS/contract-owned.
2. **Reject-only identity mode: KGCS-satisfied locally** (`kgcs.profiles`
   `CLIENT_AUTHORITATIVE`, enforced at cluster + policy layers; ER emits
   `POSSIBLY_SAME_AS`). Contract-level per-source authority declaration remains
   an upstream ask (candidate 0004).
3. **Projection-consumer profile: addressed in KGCS** (`projection_consumer_profile`,
   `ErMode.INERT`); not contract-formalized.
4. **PII-safe keys: structurally mitigated** (opaque `kg://…/identity/<ulid>`
   internal IDs; `EntityRef` namespacing enforced). Explicit "no PII in keys"
   guidance still absent upstream.

Recommendation: keep Issue #2 open; items 2 & 3 delivered by KGCS v1, items 1 &
4 KGIS/contract-owned.

## 4. Definition-of-complete checklist (plan §11)

- Governance current + checks pass — **yes** (v0.9.0, 4/4).
- All semantic waves merged to `main` — **yes** (#5, #10–#17).
- CI green with deterministic tools/rules — **yes** (ruff==0.16.4 pinned rules).
- Strict mypy green — **yes** (49 files). Full pytest green — **yes** (435).
- KGIS→KGCS E2E vs current KGIS main — **yes** (flagship + 5 scenarios; real
  `kgis.IngestPipeline` in scenario 1).
- No prod KGCS import of KGIS impl / vendor LLM SDK — **yes** (grep clean).
- No LLM mutates canonical state; every mutation from a `CurationPlan` via the
  executor; stale plans fail closed — **yes** (laws 1/3/4/16, tested).
- ER calibration + cluster constraints reproducible — **yes** (law 7).
- Human review and automation converge — **yes** (law 14). Re-curation preserves
  bitemporal history — **yes** (law 10). Semantic replay detects divergence —
  **yes** (law 17).
- ADR candidates reconciled — **yes** (§2 above).
- Issue #2 dispositioned — **yes** (§3).
- Memory bank / release state current — **this PR**.
- Final independent whole-system review — **this PR** (`governance:chief-reviewer`).

## 5. Release readiness

**KGCS v1 is complete and usable for a real end-to-end adopter** over the
deterministic + calibrated + bounded-LLM + review + re-curation platform,
proven end-to-end against real KGIS ingestion. The one adoption gate that is
**not KGCS's to close** is Issue #2 item 1 (subject-scoped erasure), required
before ingesting regulated personal data — a KGIS/contract capability KGCS will
consume.

Recommended release boundary: tag **v1.0.0** on merge of this reconciliation
(the functional stack is already on `main`). `pyproject` version bump left to
the owner's release action.

## 6. Remaining (deliberately deferred / not KGCS-core)

Adopter domain wiring (research / baseball / traffic / construction), the
plan §11-deferred v1 items (polished web review UI, live-LLM CI, backend-specific
vector/full-text, deployment wrapper, full migration framework), and the two
RETAIN upstream asks (0004, 0015) + the `kg_eval` MetricProvider adapter, all of
which are KGIS/adopter-owned, not KGCS v1 blockers.
