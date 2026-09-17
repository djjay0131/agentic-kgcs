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

Works: packaging + cross-repo contract verification against `kg_contracts`
v2; deterministic curation core (Candidate → validate → policy → plan →
audit) reconciled onto v0.3 (PR #5 / PR A); transaction-aware executor +
compensation + epochs (PR B, `wave1/executor`). Both gates green, pending
independent review + owner merge.
Not built yet: ER 5a/5b (Waves 2–3), LLM curation orchestrator (Wave 4),
re-curation + concept/ontology evolution (Wave 5), review queue/CLI +
backpressure (Wave 6), semantic audit/replay + kg_eval (Wave 7), cross-repo
E2E (Wave 8), steward/release reconciliation (Wave 9).
