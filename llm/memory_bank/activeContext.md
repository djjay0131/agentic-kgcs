# Active Context — agentic-kgcs

Update 2026-08-21 (Wave 1): **Transaction-aware executor + compensation +
curation epochs (PR B).** On branch `wave1/executor`, stacked on the Wave-0
core branch. New package `src/kgcs/executor/`:
- `executor.py` — `PlanExecutor` compiles a `CurationPlan` → `GraphMutationBatch`
  and applies it via `GraphMutationStore` (the *only* mutation path). Outcomes:
  `COMMITTED` (advances + publishes the curation epoch), `STALE` (failed
  preconditions → re-evaluate, never blind-retry), `UNSUPPORTED_OPERATION`
  (op outside the adapter's support — fails *before* touching the store, with a
  `NotImplementedError` backstop), `EMPTY`, `ERROR`. Every attempt (incl.
  compensations) yields an immutable KGCS-local `ExecutionRecord` — deliberately
  NOT `kg_contracts.AuditRecord` (per Wave-1 audit-seam guidance + ADR cand
  0002), linked by `plan_id`.
- `compensate.py` — `Compensator` builds a compensating `CurationPlan` (inverse
  ops, LIFO). `INVERSE_OPERATION` covers all 7 op types; `CREATE_IDENTITY` and
  `PROMOTE_ONTOLOGY_TERM` are declared non-compensable (no inverse op in the v1
  vocabulary) — invariant 8 stated precisely.
- `memory/execution.py` — `InMemoryEpochPublisher`, `InMemoryExecutionAuditSink`.
- `ids.py` gained deterministic `batch_id`/`execution_id`.
Idempotency falls out of deterministic op ids: re-executing a committed plan is
rejected `STALE` (the `entity_version=0` guard no longer holds). Gates:
127 pytest (+27), ruff, strict mypy. Wave-1 exit criteria met: an in-memory
Candidate→canonical path advances a visible epoch; ADR candidate 0003
(snapshot-level precondition enforcement) is now realized at the executor.
NEXT: Wave 2 — ER 5a (normalization, blocking, typed features, calibrated
matcher).

Update 2026-08-21: **Wave 0 of the orchestrated KGCS v1 build
(`llm/plans/2026-08-22-kgcs-v1-orchestrated-build.md`) — reconciling the
deterministic curation core (PR #5) onto governance v0.3.** PR #5 was
branched before the v0.3 `llm/`-control-plane migration, so its diff wrongly
"deleted" main's new layout and wrote ADR candidates under `docs/adr/`. The
branch was reset onto current `main` (backup at `backup/pr5-original`), the
substantive code re-applied (`src/kgcs/**`, `tests/kgcs/**`), and the four
ADR candidates relocated to `llm/governance/adr/candidates/**` with Wave-0
reconciliation notes appended (all four remain open against current
`kg_contracts`; 0003 is now a Wave-1 executor concern, 0002 matches the
plan's separate-semantic-audit guidance). Gates green on the reconciled
branch: 98 pytest, `ruff check src tests`, `mypy src` strict — against
`kg_contracts` from `agentic-kgis` main. The deterministic core still stops
at `CurationPlan` (no executor). NEXT: independent architectural review of
the core, then Wave 1 (transaction-aware executor + compensation + epochs).

Update 2026-07-12: **Bootstrapped with packaging + cross-repo contract
verification.** agentic-kgis Plan 1 v2 (19 tasks) shipped `kg_contracts`
v2's public API. This repo installs it editable
(`pip install -e ../agentic-kgis`) and verifies consumability in
`tests/test_contracts_available.py`: imports `AdjudicationRoute`,
`CandidateScores`, `ConfidencePolicy` from the top-level package and
`CandidateSink`/`GraphMutationStore` from `kg_contracts.stores`, then
runs the reusable `CandidateSinkContract` and `GraphMutationStoreContract`
suites (`kg_contracts.testing`) against the memory adapters — both green,
plus the confidence-policy smoke test. CI wired
(`.github/workflows/ci.yml`, installs the sibling repo via
`CONSTELLATION_PAT`). `pytest` and `ruff check src tests` green.
NEXT: implementation starts at Plan 3 (curation core + executor).

Prior state (2026-07-10): External design review (ChatGPT, agentic-kgis PR #1)
dispositioned and approved. Architecture amended: candidate ledger separate
from canonical graph; pure curation core → CurationPlan → executor
(replaces CuratedGraphStore wrapper; ADR-0010 in agentic-kgis); ER =
calibrated matcher + bounded LLM adviser (multi-agent debate demoted to
eval arm); bitemporal assertions; kg_eval package added in agentic-kgis.
Spec v2 + ADRs 0006–0010 landing on agentic-kgis PR #1. This repo's delta
amended on branch governance/delta-amendment-v2. KGCS implementation is now
Plans 3 (curation core+executor), 5a/5b (ER), 6 (eval+review), 7 (registry
advisor).

Prior state (2026-07-09):

- Repo bootstrapped with governance only (no code yet).
- Governance adopted (agentic-governance v0.1): delta, local ADR dir,
  .github surface, CONTRIBUTING. From now on: Issue → Branch → Draft PR →
  Review → Merge; no direct commits to main.
- Package scaffolding (pyproject, src/kgcs) lands via Plan 1 Task 2
  (agentic-kgis/docs/superpowers/plans/2026-07-09-01-bootstrap-and-contracts.md).
- KGCS implementation starts in Plan 2 (inline gate), then Plan 4
  (async plane + review), Plan 5 (registry advisor).
- Blocked behind: ChatGPT feedback review cycle on the design spec, then
  Plan 1 execution in agentic-kgis.
