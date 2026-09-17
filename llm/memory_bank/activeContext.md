# Active Context — agentic-kgcs

Update 2026-08-22 (Wave 9 — steward reconciliation): **KGCS v1 orchestrated
build complete — all nine waves implemented as independently-reviewed,
owner-ready DRAFT PRs (#5, #10–#17). Nothing merged; awaiting owner review.**
Full reconciliation (PR stack + merge order, DG-1..DG-5 dispositions, Issue #2
disposition, deferred/adopter work, release boundary) is in
[`kgcs-v1-reconciliation.md`](kgcs-v1-reconciliation.md). Top-of-stack gates:
435 pytest, ruff, strict mypy (49 files), governance layout 4/4. DG-1..DG-5 all
became durable architecture (plan defaults, no contract change). Issue #2 items
2 & 3 delivered (reject-only profile + projection-consumer); items 1 (erasure)
& 4 (PII keys) are KGIS/contract-owned. 16 ADR candidates raised, all open for
owner adjudication. Recommended first release: KGCS v0.1.0 on merge of A–I.

Update 2026-08-22 (Wave 8, PR I): **KGIS→KGCS end-to-end — the flagship.** On
branch `wave8/e2e`, stacked on Wave 7. Test-only (`tests/kgcs/e2e_harness.py` +
`test_e2e_*.py`), no `src/` change; `src/kgcs` still imports no `kgis`.
- Scenario 1 drives the REAL `kgis.IngestPipeline` (structured mode) →
  MemoryCandidateSink → CurationEngine → PlanExecutor → MemoryGraphStore → epoch
  (genuine cross-repo composition over the shared contract).
- Flagship re-curation is parametrized over a research-PAPER shape AND a
  non-paper SENSOR shape (domain-neutral): source A → epoch N; contradicting
  evidence → CurationTrigger → targeting → AssertionAdviser reasons over the
  cited evidence → supersession plan executes → epoch N+1 with the old
  assertion still queryable (SUPERSEDED, bitemporal).
- Six required scenarios covered (structured auto; document+recorded-LLM; the
  flagship; stale→reject→safe commit; merge+compensating rollback;
  client-authoritative never repaired). §9 laws 1,3,4,6,8,9,10,11,13,14,16,17
  asserted end-to-end. A test-only `E2EGraphStore` widens the reference store to
  execute RETRACT_ASSERTION via the contract's `mark_superseded`.
Gates: 430 pytest (+20), ruff, strict mypy (48 files). ADR candidates 0015
(reference-store op coverage) + 0016 (compensation re-stamp). NEXT: Wave 9 —
steward reconciliation + Issue #2 disposition + release readiness + the
consolidated report.

Update 2026-08-22 (Wave 7, PR H): **Semantic curation audit + replay + honest-null
evaluation + kg_eval seam.** On branch `wave7/audit-eval`, stacked on Wave 6. New
package `src/kgcs/observability/` (the plan's "audit/" slot, renamed to avoid
clashing with the Wave-0 `audit.py` module):
- `semantic_audit.py` — `SemanticAuditRecord`: the THIRD audit object (decision-
  scoped), distinct from `kg_contracts.AuditRecord` (operation) and
  `ExecutionRecord` (execution), joined by `trace_id`/`plan_id` + compact refs.
  Captures baseline decision + adviser provenance + review + final + plan_id +
  score_vector + all versions. `SemanticAuditSink` + in-memory impl;
  `SemanticAuditBuilder`. Realizes ADR candidates 0002/0012's "semantic audit
  sink" by trace-join, no contract change.
- `replay.py` — `replay()` re-runs a decision through the same
  `CurationOrchestrator` (RecordedCompletionClient for the LLM arm) and reports
  byte-identical reproduction or a divergence (law 17).
- `metrics.py` — ER metrics (pairwise/cluster P/R, false-merge/split,
  calibration error, abstention) + curation metrics (review yield/agreement,
  rollback, queue age, time-to-canonicalization). Honest-null: insufficient
  data → None+count, never fake 0/1 (law 9, ADR-0009).
- `arms.py` — named comparison arms (baseline / calibrated / matcher+LLM;
  MULTI_AGENT_DEBATE off unless enabled). `should_raise_threshold` refuses to
  promote on anecdote (insufficient-evidence below min_samples).
- `provider.py` — `MetricProvider` seam KGCS implements + kg_eval consumes; NO
  `kg_eval` import (no reverse dep).
Gates: 409 pytest (+45), ruff, strict mypy (48 files). No new ADR candidate
(reuses 0002/0012). NEXT: Wave 8 — KGIS→KGCS end-to-end (flagship research-paper
re-curation scenario) + a non-paper source shape.

Update 2026-08-22 (Wave 6, PR G): **Review API/queue/CLI + backlog/backpressure.**
On branch `wave6/review-queue`, stacked on Wave 5. New package `src/kgcs/review/`:
- `queue.py` — `PersistentReviewQueue` (passes `ReviewQueueContract`; swappable
  `ReviewStore`: in-memory or atomic JSON-file; append-only history across
  reloads) + typed `RetryableQueueError`/`PermanentQueueError` (law 15 — never
  a silent drop).
- `model.py` — `ReviewCase` enrichment (snapshot ref, proposed plan, adviser
  assessments, risk/value, priority, SLA P1=24h/P2=7d/P3=30d, trace) in the
  contract's opaque payload.
- `operations.py` — `ReviewRouter`: a human decision routes through the SAME
  `ConceptEvolutionPlanner` the auto path uses, so APPROVE yields a
  byte-identical plan and SPLIT/RELABEL/MERGE/etc. map to the same planner
  methods (law 14 — human & auto converge on one plan/executor/audit path).
- `backlog.py` — `BacklogAnalyzer`/`QueueMetrics`/`BackpressureSignal`:
  age/depth by source+entity-type, unresolved-cluster size, priority-inversion
  + starving-high-value detection, machine-readable THROTTLE/QUARANTINE signal
  for the KGIS seam (§7.7).
- `cli.py` — stdlib-argparse `kgcs review` (list/show/resolve/history/backlog)
  over an injected queue.
Gates: 363 pytest (+30), ruff, strict mypy (42 files). ADR candidate 0014.
NEXT: Wave 7 — semantic audit/replay + kg_eval metric integration.

Update 2026-08-22 (Wave 5, PR F): **Evidence-driven re-curation (DG-1) +
concept/ontology evolution (DG-3).** On branch `wave5/recuration`. New package
`src/kgcs/recuration/`:
- `triggers.py` — `CurationTrigger` (6 `TriggerKind`s; content-addressed
  trigger_id) + `TriggerQueue`/`InMemoryTriggerQueue` (idempotent enqueue;
  holds no canonical writer — triggers enqueue work, never mutate).
- `targeting.py` — `DependencyIndex`/`InMemoryDependencyIndex`: "what could this
  evidence affect?" via keyed lookups → targeted/incremental re-curation, no
  full scan (law 11).
- `evolution.py` — `ConceptEvolutionPlanner`: turns an evolution decision +
  trigger into a compensable `CurationPlan`. Supersession marks the old
  assertion SUPERSEDED (bitemporal, still queryable) — never deletes (law 10);
  merge uses `select_survivor` + reversible lineage; split reassigns explicitly;
  unresolved conflict preserves both via an UNRESOLVED `ConflictRecord`. Every
  op traces to trigger_id + evidence_ids + versions.
- `ontology.py` — `OntologyLifecycle` (PROPOSED→APPROVED→OBSERVED→DEPRECATED);
  promotion refused for non-APPROVED terms; skipping governance is
  unconstructable (law 12).
Branch-topology note: Wave 5 depends on BOTH the executor (Wave 1) and advisers
(Wave 4), which were sibling branches; PR F is therefore based on an integration
branch (`integration/pre-recuration` = Wave 1 ⊕ Wave 4) so its diff is
recuration-only. Gates: 333 pytest, ruff, strict mypy (36 files). ADR candidate
0013. NEXT: Wave 6 — review API/queue/CLI + backlog/backpressure.

Update 2026-08-22 (Wave 4, PR E): **Bounded LLM curation orchestrator +
specialist advisers.** On branch `wave4/llm-advisers`, stacked on Wave 3. New
package `src/kgcs/advisers/`:
- `completion.py` — the injected LLM seam: `CompletionPort` (the only LLM
  surface; no provider SDK), `RecordedCompletionClient` (deterministic replay
  keyed by request hash; `CompletionMiss` is loud, not silent), and
  `Failing/Timeout/Malformed` clients for tests.
- `base.py` — `StructuredAdviser` (render→complete→parse→abstain; catches every
  port error/parse failure and abstains, never raises to the orchestrator) +
  DG-4 `AdviserAssessment` (adviser/model/prompt versions, cited evidence
  intersected with supplied, contradictions, confidence, abstained, trace_id,
  baseline_before/final_after).
- `specialists.py` — 5 bounded specialists (Identity/Assertion/Conflict/
  ConceptEvolution/OntologyEvolution), each returns a typed assessment only,
  never an operation.
- `orchestrator.py` — `CurationOrchestrator`: runs the Wave-3 deterministic
  baseline FIRST, consults advisers only when the baseline routed LLM_ASSESS,
  folds advice back through the SAME policy gate, and returns
  `OrchestrationResult{decision, baseline, assessments}` — never an
  operation/plan. On any LLM failure/absence, returns EXACTLY the baseline
  decision (law 1). Advice can never upgrade a CLIENT_AUTHORITATIVE pair past
  the reject-only gate (law 13); law 16 (no write authority) holds — no
  mutation surface anywhere in advisers.
Gates: 254 pytest (+46), ruff, strict mypy (27 files). No SDK/network/kgis.
Surfaced ADR candidate 0012. NEXT: Wave 5 — evidence-driven re-curation +
concept/ontology evolution.

Update 2026-08-22 (Wave 3, PR D): **Cluster validation + deterministic ER
resolution policy + DG-5 curation profiles.** On branch `wave3/cluster-policy`,
stacked on Wave 2 (ER). Completes the deterministic decision spine — the
baseline that stands before any LLM.
- `er/cluster.py` — `Cluster`/`ClusterSnapshot` (versioned, optimistic
  concurrency), five `ClusterConstraint`s (temporal, unique-source,
  mutually-exclusive, tenant, identity-authority), `ClusterValidator` that
  validates the WHOLE prospective membership (all internal pairwise relations +
  every constraint) so A~B, B~C, A⊥C never forms {A,B,C} (law 7);
  deterministic `select_survivor`.
- `profiles.py` (DG-5) — `IdentityAuthorityMode`
  (OPEN/ADVISORY/CLIENT_AUTHORITATIVE), `ErMode`, `FalseMergeCostClass`,
  `CurationProfile` (wraps the existing `ConfidencePolicy` — no alternate write
  path), `ProfileRegistry`, factories incl. `projection_consumer_profile` and
  `client_authoritative_profile`.
- `er/resolution.py` — `ErAction` (8-way), `ErDecision`, `ErResolutionPolicy`:
  routes on calibrated risk + consequence class + cluster validity + profile
  (NOT a fixed similarity band); complete without any LLM. `to_route()` bridges
  to the contract `AdjudicationRoute`. Wave-0 `policy.py` untouched.
- **Issue #2 / law 13 (reject-only) enforced in depth:** a CLIENT_AUTHORITATIVE
  identity never AUTO_LINK/SAME_AS/merges (full-sweep test) — at most
  PROPOSE_LINK (POSSIBLY_SAME_AS); malformed → REJECT, never repair. Enforced
  at both cluster (`IdentityAuthorityConstraint`) and policy layers.
Gates: 201 pytest (+53), ruff, strict mypy (22 files). Surfaced 3 contract
frictions → ADR candidates 0009–0011. NEXT: Wave 4 — bounded LLM curation
orchestrator + specialist advisers (recorded/replay clients; deterministic
baseline survives every LLM failure).

Update 2026-08-22 (Wave 2, PR C): **Entity-resolution substrate (ER 5a).** On
branch `wave2/er`, stacked on the Wave-0 core (sibling of PR B). New package
`src/kgcs/er/`: `normalize` (deterministic normalization + identity rules —
never merges; `DEFAULT_STRONG_NAMESPACES` heuristic), `blocking` (multi-channel
recall-oriented blocking — exact-identifier / normalized-name / source-key
channels + optional injected `VectorIndex` embedding channel; canonical
deduped `CandidatePair`s with channel provenance), `features` (typed
honest-null `PairFeatures` with explicit AGREE/CONTRADICT/UNKNOWN; pure-Python
jaro-winkler/cosine/haversine; mutual-exclusion on contradicting strong ids or
disjoint time), `matcher` (`DeterministicRuleMatcher` baseline +
`CalibratedMatcher` with dependency-free logistic fit; `CalibrationKey` by
graph/type/source-pair/version/consequence; golden-set `evaluate`). NO cluster
validation, NO policy gate, NO LLM (later waves). Exit criteria proven:
reproducible probabilities from stored feature vector + version; no
cosine/embedding threshold decides (embedding=1.0 + contradicting ids scores
<0.5); calibration evaluable on a golden set; empty golden set → honest-null
metrics. Gates: 146 pytest (+46), ruff, strict mypy (19 files). Surfaced 4
contract frictions → ADR candidates 0005–0008. NEXT: Wave 3 — cluster
validation + deterministic resolution policy + DG-5 curation profiles.
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
