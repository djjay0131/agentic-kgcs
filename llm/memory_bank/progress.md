# Progress — agentic-kgcs

- 2026-07-09: Repo created; governance established (agentic-governance
  v0.1): delta, ADR dir, GitHub surface, memory bank. Bootstrap commits
  grandfathered; PR workflow applies from here.
- 2026-07-10: External design review dispositioned (agentic-kgis PR #1,
  chief-reviewer-checked, owner-approved). Delta amended (mechanism-neutral
  principles, ledger/canonical separation, new plan numbering) on branch
  governance/delta-amendment-v2.

- 2026-07-12: PR #1 (delta amendment) merged by owner. Governance upgraded
  to agentic-governance v0.2 (steward INACTIVE) via delta upgrade PR.
- 2026-07-12: Bootstrapped with packaging + cross-repo contract
  verification (agentic-kgis Plan 1 v2 Task 19 counterpart):
  `tests/test_contracts_available.py` imports `kg_contracts` v2's public
  API and runs `CandidateSinkContract` + `GraphMutationStoreContract`
  green against the memory adapters; CI wired. `pytest` and
  `ruff check src tests` green.

- 2026-08-21: Wave 0 of the orchestrated KGCS v1 build. Reconciled the
  Sprint-1 deterministic curation core (PR #5) onto governance v0.3: reset
  `feature/sprint-1-curation-core` onto current `main` (backup at
  `backup/pr5-original`), re-applied `src/kgcs/**` + `tests/kgcs/**`, moved
  the four ADR candidates from the stale `docs/adr/` plane to
  `llm/governance/adr/candidates/**`, and added reconciliation notes (all
  four still open vs current `kg_contracts`). Gates green: 98 pytest, ruff,
  strict mypy. Core still stops at `CurationPlan`; no executor smuggled in.

- 2026-08-22: Wave 2 (PR C) — ER 5a substrate. `src/kgcs/er/` (normalize,
  blocking, features, matcher) + `testing/er.py` MatcherContract. Deterministic,
  honest-null, calibrated matcher (rule baseline + logistic), golden-set
  evaluation; no cluster validation / policy / LLM (later waves); no
  cosine-threshold decision. 146 pytest, ruff, strict mypy. Surfaced ADR
  candidates 0005–0008. Branch `wave2/er`, sibling of PR B off Wave-0 core.
- 2026-08-21: Wave 1 (PR B) — transaction-aware executor + compensation +
  curation epochs. `src/kgcs/executor/` (PlanExecutor, Compensator,
  ExecutionRecord/Outcome, EpochPublisher/ExecutionAuditSink) + in-memory
  adapters. Only mutation path is `GraphMutationStore.apply`; stale plans
  rejected via preconditions; unsupported ops fail explicitly; epoch published
  on commit; compensating plans generated (LIFO inverse map, CREATE_IDENTITY /
  PROMOTE_ONTOLOGY_TERM declared non-compensable). 127 pytest, ruff, strict
  mypy green. Branch `wave1/executor`, stacked on Wave-0 core.

- 2026-08-22: Wave 3 (PR D) — cluster validation + deterministic ER resolution
  policy + DG-5 curation profiles. `er/cluster.py`, `profiles.py`,
  `er/resolution.py`. Completes the deterministic decision spine; enforces
  Issue #2 reject-only/client-authoritative (law 13) at cluster + policy
  layers; invalid transitive clusters blocked (law 7). 201 pytest, ruff,
  strict mypy. ADR candidates 0009–0011. Branch `wave3/cluster-policy`,
  stacked on Wave 2.

- 2026-08-22: Wave 4 (PR E) — bounded LLM curation orchestrator + 5 specialist
  advisers. `src/kgcs/advisers/` (completion seam + recorded/replay + failure
  clients; StructuredAdviser; CurationOrchestrator). Advisers never write the
  graph (law 16), the deterministic baseline survives every LLM failure (law
  1), advice never overrides reject-only (law 13). 254 pytest, ruff, strict
  mypy. ADR candidate 0012. Branch `wave4/llm-advisers`, stacked on Wave 3.
- 2026-08-22: Wave 5 (PR F) — evidence-driven re-curation (DG-1) +
  concept/ontology evolution (DG-3). `src/kgcs/recuration/` (triggers,
  targeting, evolution, ontology). Triggers enqueue-only (never mutate);
  targeting incremental (law 11); supersession bitemporal, never deletes (law
  10); evolution ops compensable (law 8); ontology promotion gated (law 12).
  333 pytest, ruff, strict mypy. ADR candidate 0013. PR F based on
  `integration/pre-recuration` (Wave 1 ⊕ Wave 4), diff recuration-only.
- 2026-08-22: Wave 6 (PR G) — review API/queue/CLI + backlog/backpressure.
  `src/kgcs/review/` (PersistentReviewQueue passing ReviewQueueContract; typed
  retryable/permanent failures — law 15; ReviewRouter converging human
  decisions on the same plan path as auto — law 14; BacklogAnalyzer +
  BackpressureSignal; stdlib CLI). 363 pytest, ruff, strict mypy. ADR candidate
  0014. Branch `wave6/review-queue`, stacked on Wave 5.
- 2026-08-22: Wave 7 (PR H) — semantic curation audit + replay + honest-null
  evaluation + kg_eval seam. `src/kgcs/observability/` (SemanticAuditRecord —
  third audit object joined by trace_id; replay reproduces decisions — law 17;
  honest-null ER + curation metrics — law 9/ADR-0009; named comparison arms;
  MetricProvider with no kg_eval reverse dep). 409 pytest, ruff, strict mypy.
  No new ADR candidate (reuses 0002/0012). Branch `wave7/audit-eval`, stacked
  on Wave 6.
- 2026-08-22: Wave 8 (PR I) — KGIS→KGCS end-to-end (flagship). Test-only
  (`tests/kgcs/e2e_harness.py` + `test_e2e_*.py`). Real `kgis.IngestPipeline`
  driven in scenario 1; flagship re-curation parametrized over paper + sensor
  shapes (epoch N → N+1, history preserved); six scenarios + §9 laws asserted
  end-to-end. 430 pytest, ruff, strict mypy. ADR candidates 0015, 0016. Branch
  `wave8/e2e`, stacked on Wave 7.
- 2026-08-22: Wave 9 — steward reconciliation. All nine waves implemented as
  independently-reviewed owner-ready DRAFT PRs (#5, #10–#17). DG-1..DG-5
  dispositioned (all durable), Issue #2 dispositioned (items 2/3 delivered;
  1/4 KGIS/contract-owned), 16 ADR candidates open for owner adjudication,
  release boundary v0.1.0 recommended. Full detail in
  `llm/memory_bank/kgcs-v1-reconciliation.md`. Top-of-stack: 435 pytest, ruff,
  strict mypy, governance 4/4.

Works: packaging + cross-repo contract verification against `kg_contracts`
v2; deterministic curation core (Candidate → validate → policy → plan →
audit) reconciled onto v0.3, gates green, pending independent review + owner
merge (PR #5 / PR A).
Not built yet: transaction-aware executor + compensation + epochs (Wave 1),
ER 5a/5b (Waves 2–3), LLM curation orchestrator (Wave 4), re-curation +
concept/ontology evolution (Wave 5), review queue/CLI + backpressure (Wave
6), semantic audit/replay + kg_eval (Wave 7), cross-repo E2E (Wave 8),
steward/release reconciliation (Wave 9).
