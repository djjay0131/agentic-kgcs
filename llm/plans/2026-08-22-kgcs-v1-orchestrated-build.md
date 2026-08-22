# KGCS v1 — Orchestrated Build Plan

Status: Proposed execution plan
Date: 2026-08-22
Repository: `djjay0131/agentic-kgcs`
Design authority: `djjay0131/agentic-kgis` shared KGIS/KGCS v2 specification
Governance: agentic-governance v0.3

## 1. Mission

Complete the KGCS v1 implementation as the reusable curation library behind every portfolio knowledge graph.

KGCS begins at the `Candidate` boundary and owns knowledge admission into canonical graph state. It must support deterministic curation, transaction-safe execution, calibrated entity resolution, bounded LLM-assisted reasoning, evidence-driven concept evolution, ontology evolution, review operations, backlog controls, compensation, and immutable audit.

The critical architectural rule remains unchanged:

> LLMs may reason, compare evidence, propose decisions, and orchestrate specialist analysis; they do not hold a direct canonical graph write surface. Canonical mutation occurs only through a serializable `CurationPlan`, deterministic policy, and a KGCS executor using `GraphMutationStore` with preconditions.

This plan is intended for an orchestration agent that delegates work to specialist agents in isolated worktrees, enforces implementer → reviewer → fix-loop discipline, and keeps PRs independently reviewable.

## 2. Live repository baseline

At plan creation:

- `main` contains packaging/bootstrap and governance v0.3.
- PR #5 (`feature/sprint-1-curation-core`) is an older draft containing the first deterministic curation engine:
  `Candidate → Validator → Policy → Planner → Audit → EngineResult`.
- PR #5 predates governance v0.3 and still writes ADR candidates under `docs/adr/`; those artifacts must move to the declared control plane under `llm/governance/adr/` before merge.
- PR #5 deliberately contains no graph executor, no entity resolution, no embeddings, no LLM adviser, and no probabilistic behavior.
- Open Issue #2 contains baseball-ai compatibility requirements. The KGIS sibling has since implemented generic ledger-side logical erase, reject-only identity mode, and consumer-profile primitives; KGCS must consume the relevant contract behavior rather than duplicating storage semantics.
- The shared design authority already specifies the KGCS module families: pure core, executor/compensation, entity-resolution pipeline, bounded LLM adviser, review API/queue/CLI, policy, audit, and backlog controls.

The orchestrator must refresh this state before execution and adjust branch/PR sequencing if the repository changed after this plan was written.

## 3. Architecture already decided — do not redesign casually

The following are binding unless implementation proves a contradiction and a governed ADR changes them:

1. Three logical stores remain separated: candidate ledger, canonical graph, derived projections.
2. Applications never receive a canonical graph write API.
3. KGCS pure logic emits decisions and `CurationPlan`; the executor alone applies `GraphMutationStore` batches.
4. Canonical commits use optimistic preconditions and published curation epochs/snapshots.
5. Curation operations are explicit and compensable: create identity, attach assertion, merge identities, split identity, reassign assertion, retract assertion, promote ontology term.
6. Entity resolution is not cosine-threshold routing. It is normalization → multi-channel blocking → typed features → calibrated matcher → cluster validation → deterministic policy.
7. LLM curation is bounded and evidence-citing. Deterministic baselines stand on LLM failure.
8. Human review remains a first-class outcome; automation is policy configuration, not bypass logic.
9. Audit is immutable training/evaluation data, not optional logging.
10. Competing assertions are preserved with evidence and authority; they are not overwritten.
11. Ontology lifecycle is governed: PROPOSED → APPROVED → OBSERVED → DEPRECATED.
12. KGCS depends only on `kg_contracts`; vendor SDKs and KGIS implementation packages do not become KGCS dependencies.

## 4. Design/requirements gaps discovered before execution

The shared architecture is strong enough to build, but the user's intended research-paper behavior makes five requirements insufficiently explicit. These are execution-time design gates, not reasons to halt the whole program.

### DG-1 — Evidence-driven re-curation triggers

The design says new evidence can affect resolution and assertions, but does not define the reusable trigger model for revisiting already-curated knowledge.

Default for v1:

Create a KGCS-local immutable `CurationTrigger`/equivalent domain type representing why re-evaluation is requested. Required trigger classes:

- new candidate/evidence touching an existing identity or assertion;
- contradiction/conflict detected;
- source authority/reliability changed;
- ontology term/policy version changed;
- matcher/model/prompt version changed where compatibility class requires re-evaluation;
- explicit human/operator request.

Triggers enqueue work; they never mutate canonical data. Trigger handling must be idempotent and trace-linked. If this needs a shared `kg_contracts` type, file an ADR candidate and keep the first implementation local unless cross-repo use is proven.

### DG-2 — LLM curation orchestrator shape

The spec defines one bounded `ResolutionAssessment`, but the intended system needs broader evidence-driven reasoning than pairwise identity resolution alone.

Default for v1:

Implement a deterministic `CurationOrchestrator` that selects among bounded specialist advisers based on candidate/trigger type. Suggested specialists:

- `IdentityAdviser` — same/different/insufficient with cited evidence;
- `AssertionAdviser` — support/contradict/supersede/insufficient for an assertion;
- `ConflictAdviser` — compares competing assertions and identifies unresolved conflicts;
- `OntologyEvolutionAdviser` — proposes ontology candidates/promotion rationale from recurring evidence;
- `ConceptEvolutionAdviser` — recommends merge/split/relabel/same-concept-different-scope when accumulated evidence changes the conceptual model.

These may share one injected generic completion port and structured response machinery. They do not need to be separate model calls in every case. The orchestrator chooses which capability to invoke; the policy gate decides what may happen next.

No adviser returns a `GraphMutationBatch` or calls a graph writer.

### DG-3 — Concept evolution semantics

Merge and split operations exist, but the behavior of concepts changing as evidence accumulates is not yet an explicit workflow.

Default for v1:

Represent concept evolution through existing immutable operations and assertion history, never by rewriting history:

- promotion: candidate/mention becomes or attaches to a canonical concept;
- merge: two identities are judged equivalent, with deterministic survivor selection and reversible lineage;
- split: one identity is decomposed into multiple identities, reassigning assertions explicitly;
- relabel/scope refinement: represented as assertions/aliases/ontology relationships rather than destructive rename where possible;
- supersession: old assertions remain queryable bitemporally and are marked superseded/retracted as appropriate;
- unresolved conflict: preserve both assertions and route to review/gather-more-evidence.

Every such recommendation must cite evidence IDs and record matcher/adviser/policy versions.

### DG-4 — Adviser provenance and reproducibility

The contracts require model/version logging generally, but KGCS needs an explicit rule for LLM adviser provenance.

Default for v1:

Every adviser assessment records, directly or through audit/reversal metadata as current contracts allow:

- adviser type/version;
- model identifier/version;
- prompt/template version;
- evidence IDs supplied to the model;
- structured recommendation;
- contradictions found;
- confidence/abstention;
- trace ID;
- deterministic baseline decision before adviser input;
- final policy decision after adviser input.

Recorded/replay clients are mandatory in tests. A small live smoke test may exist outside deterministic CI.

### DG-5 — Curation profiles by source/capability class

Different sources require different curation behavior: an authoritative structured registry is not equivalent to a research-paper hypothesis or a code requirement extracted from prose.

Default for v1:

Add KGCS-local policy/profile composition rather than hard-coded source special cases. Profiles may constrain:

- identity authority mode (including reject-only/client-authoritative);
- whether ER is inert/advisory/active;
- required evidence count/types;
- false-merge cost class;
- ontology-promotion permissions;
- review thresholds/SLA;
- allowable auto-actions.

Profiles configure the existing policy machinery. They must not create alternate write paths.

## 5. Execution model

### Orchestrator responsibilities

The top-level agent owns:

- repository preflight and governance compliance;
- dependency graph and worktree allocation;
- task decomposition;
- assigning specialist agents;
- ensuring no concurrent edit collisions;
- maintaining a live execution board;
- ensuring every task receives an independent review;
- deciding whether a finding is a code fix, ADR candidate, or owner decision;
- keeping PR descriptions and memory bank current;
- final cross-branch integration verification;
- never self-merging.

### Specialist roles

Use these roles as needed; one agent may fill more than one role sequentially, but implementer and reviewer must be independent passes.

- Governance/Rebase Agent
- Deterministic Core Agent
- Executor/Compensation Agent
- Entity Resolution Data/Blocking Agent
- Matcher/Calibration Agent
- Cluster Validation Agent
- LLM Curation Orchestrator Agent
- Concept/Ontology Evolution Agent
- Review/Backpressure Agent
- Audit/Observability Agent
- Evaluation Integration Agent
- Integration/E2E Agent
- Chief Reviewer / Repository Steward

### Required task loop

For every semantic task:

1. implementer reads spec/contracts/ADRs and writes failing tests first where practical;
2. implementer changes only owned files;
3. implementer runs targeted tests + mypy + ruff;
4. independent reviewer inspects actual diff and authority documents;
5. fix loop until no blocking findings remain;
6. task is committed with a focused message;
7. branch-wide reviewer runs after the workstream completes.

Do not accept “tests pass” as architectural review.

## 6. Dependency-aware build waves

### Wave 0 — Reconcile and land the deterministic core foundation

Goal: convert PR #5 from stale draft into the governed, reviewed base for all later work.

Tasks:

- rebase/update PR #5 onto current `main`;
- migrate its `docs/adr/**` candidate content into `llm/governance/adr/candidates/**` per governance v0.3;
- reconcile PR #5's four ADR candidates against current `kg_contracts` and KGIS Plan 2/Plan 4 work;
- re-run full quality gates under current dependency versions;
- independently review engine purity, deterministic replay, plan validity, audit lineage, and failure routing;
- preserve the rule that Sprint 1 stops at `CurationPlan`; do not smuggle executor behavior into the pure core;
- mark ready only after governance layout and current CI pass.

Exit criteria:

- deterministic core is owner-ready;
- ADR candidates are in the right control-plane path;
- no code imports `kgis` implementation modules;
- plan output applies correctly to the reference memory graph when executed by a test-only adapter;
- all candidate outcomes are accounted for, including no-plan outcomes.

### Wave 1 — Transaction-aware executor + compensation + curation epochs

Goal: complete Plan 3.

Implement:

- `executor/executor.py` consuming `CurationPlan` and producing `GraphMutationBatch`/`CommitResult`;
- precondition evaluation against snapshot/cluster versions;
- idempotent operation IDs and retry behavior;
- stale-plan rejection and re-evaluation signaling;
- atomic mutation semantics through `GraphMutationStore`;
- curation epoch/watermark publication semantics using available contracts/adapters;
- `executor/compensate.py` generating compensating plans from operation/audit history;
- coverage for all operation types supported by v1 contracts, including merge/split/reassign/retract/promote ontology;
- explicit unsupported-operation failures where an adapter lacks capability;
- audit integration for attempted, committed, failed, and compensated plans.

Resolve the known audit seam from KGIS Plan 2: ledger transition audit is not the same object as `kg_contracts.curation.AuditRecord`. Do not silently conflate them. Prefer separate semantic curation audit records linked by trace/candidate IDs unless a governed schema migration proves a unified table is better.

Exit criteria:

- a `CurationPlan` can be executed, retried safely, rejected when stale, and compensated;
- one in-memory end-to-end path reaches canonical state and advances a visible epoch;
- no application-facing raw writer leaks into public API.

### Wave 2 — Entity Resolution 5a: normalization, blocking, typed features, calibration baseline

Goal: build the deterministic/learned ER substrate before any LLM adviser.

Parallel workstreams:

#### 2A. Normalization + identity rules

Implement type-specific normalization hooks and deterministic identifier rules. Explainable exact rules produce evidence/features, not silent merges.

#### 2B. Multi-channel blocking

Implement pluggable blocking channels:

- exact identifiers/source keys;
- normalized lexical/name blocks;
- optional phonetic/geographic/temporal blocks where domain data supports them;
- embedding/vector candidate retrieval through an injected capability, never a mandatory backend;
- graph-neighborhood/type-specific retrieval hooks.

Blocking is recall-oriented and returns candidate pairs/clusters with provenance of which channels produced them.

#### 2C. Typed feature extraction

Implement feature schemas that can express agreement and contradiction:

- identifier agreement/contradiction;
- lexical/name similarity;
- temporal compatibility;
- geographic distance where applicable;
- shared affiliations/relationships;
- attribute rarity;
- source reliability/authority;
- embedding similarity;
- graph-neighborhood compatibility;
- mutually exclusive evidence.

Missing features remain null/absent, not fake zeroes.

#### 2D. Calibrated matcher

Implement a matcher abstraction with at least one deterministic reference baseline and one trainable/calibratable implementation that does not require a heavyweight dependency in the core package. If Splink/dedupe/cross-encoder integrations are studied, keep them adapters/benchmarks unless adoption is justified.

Calibration metadata must be keyed by graph, entity type, source pair, matcher version, and consequence class.

Exit criteria:

- pairwise match probabilities are reproducible from stored feature vectors and matcher version;
- calibration can be evaluated against a golden-set fixture;
- no fixed global cosine threshold is used as the merge decision mechanism.

### Wave 3 — Cluster validation + deterministic resolution policy

Goal: ensure pairwise similarity cannot create invalid transitive clusters.

Implement:

- cluster snapshot/version model;
- prospective membership validation;
- temporal consistency constraints;
- unique-source/identity-authority constraints;
- mutually exclusive attribute checks;
- tenant/purpose boundaries;
- deterministic survivor selection;
- routes: auto-link, retain separate, gather-more-evidence, LLM-assess, human review, abstain/reject as represented by current contracts/policy.

Integrate the source/capability profiles from DG-5 here. Client-authoritative/reject-only identity must never be silently repaired or merged.

Exit criteria:

- A~B and B~C does not imply A~C without cluster validation;
- stale cluster snapshots invalidate plans rather than racing commits;
- deterministic policy establishes a complete baseline before any LLM call.

### Wave 4 — LLM Curation Orchestrator + bounded specialist advisers

Goal: implement the agentic reasoning layer without violating canonical-write governance.

Implement an injected LLM seam using only `kg_contracts`-available abstractions or a KGCS-local protocol that can later be promoted if proven reusable. Do not add provider SDKs to core logic.

Required components:

1. `CurationOrchestrator`
   - accepts candidate/trigger + graph/cluster snapshot + ontology/policy + evidence context;
   - runs deterministic baseline first;
   - selects zero or more specialist advisers based on the unresolved question;
   - aggregates structured assessments;
   - passes assessments into deterministic policy/planning;
   - never executes a graph mutation.

2. `IdentityAdviser`
   - same / different / insufficient evidence;
   - cites evidence IDs and contradictions.

3. `AssertionAdviser`
   - supports / contradicts / supersedes / insufficient;
   - compares incoming evidence with current preferred and competing assertions.

4. `ConflictAdviser`
   - preserves conflicting evidence;
   - recommends preference only when policy allows;
   - otherwise routes review/gather-more-evidence.

5. `ConceptEvolutionAdviser`
   - merge / split / relabel / same-concept-different-scope / no-change / insufficient;
   - recommendations become ordinary resolution decisions and curation operations.

6. `OntologyEvolutionAdviser`
   - proposes ontology candidates or promotion rationale;
   - never bypasses PROPOSED → APPROVED → OBSERVED governance.

7. Replay/recording test client
   - deterministic fixtures;
   - explicit failure/timeout/malformed-output behavior;
   - deterministic baseline survives every LLM failure.

LLM prompts/configs are versioned data, not hard-coded policy. Every assessment records the provenance listed in DG-4.

Exit criteria:

- LLM output cannot directly create `GraphMutationBatch` or call `GraphMutationStore`;
- removing/failing the LLM leaves a valid deterministic resolution path;
- same recorded fixture produces the same assessment/plan;
- unsupported or insufficient evidence routes conservatively.

### Wave 5 — Evidence-driven re-curation + concept/ontology lifecycle

Goal: make canonical knowledge evolve as new evidence accumulates.

Implement DG-1 trigger handling and DG-3 workflows.

Required scenarios:

- new research paper adds corroborating evidence to an existing concept;
- new paper contradicts a preferred assertion;
- repeated extracted concepts suggest an ontology term candidate;
- new evidence shows two concepts should merge;
- new evidence shows one concept should split;
- a previously preferred assertion becomes superseded/retracted while history remains queryable;
- model/policy/ontology version change schedules affected knowledge for re-evaluation without rewriting unrelated graph state.

Add dependency/index hooks needed to answer “what canonical identities/assertions could this evidence affect?” without coupling KGCS to a specific graph query language.

Exit criteria:

- re-curation is incremental and idempotent;
- every changed canonical conclusion has a traceable trigger and evidence set;
- concept and ontology evolution are compensable and bitemporal;
- no periodic sweep is required for correctness, though batch scheduling may be supported.

### Wave 6 — Review API, persistent queue, CLI, and backlog/backpressure controls

Goal: complete the human-governed path and prevent curation debt from becoming silent system failure.

Implement:

- persistent `ReviewQueue` implementation passing shared contract tests;
- review item model carrying candidate/cluster snapshot, proposed action, evidence, adviser assessment, risk/value, priority, SLA, trace;
- review operations: approve, reject, edit, split, relabel, link, merge elsewhere, same concept/different scope;
- terminal CLI sufficient to work the queue; polished web UI remains deferred;
- queue age/depth metrics by source/entity type;
- unresolved-cluster size and value/risk priority;
- source throttling/quarantine/backpressure signal to KGIS-facing integration seam;
- retryable vs permanent failures kept distinct.

Exit criteria:

- every non-auto route is actionable without editing database rows manually;
- human decisions produce the same typed decisions/plans/audit path as automated decisions;
- queue pressure is observable and can produce a machine-readable throttle/quarantine recommendation.

### Wave 7 — Audit, observability, evaluation integration

Goal: make the system measurable, replayable, and safe to automate later.

Implement/finish:

- immutable semantic curation audit sink/store;
- universal trace propagation through trigger → resolution → adviser → review → plan → commit/compensation;
- score vector, matcher/adviser/model/prompt/policy/ontology versions in audit;
- replay tooling for a decision from captured inputs;
- metric-provider integration with `kg_eval` without introducing a reverse dependency from `kg_eval` into KGCS;
- ER metrics: pairwise/cluster P/R, false merge/split, calibration error, abstention;
- curation metrics: review yield/agreement, rollback frequency, queue age, time to canonicalization;
- named comparison arms: deterministic baseline, calibrated matcher, matcher+LLM adviser, experimental multi-agent debate only if separately enabled.

Exit criteria:

- an LLM-enhanced path can be compared against deterministic baseline on named metrics;
- insufficient evaluation data yields an honest null, not a promotion claim;
- no threshold is raised based only on anecdotal success.

### Wave 8 — End-to-end cross-repo integration

Goal: prove KGIS and KGCS compose as the intended platform.

Use the current KGIS branches/main state after their owner-approved merges.

Required in-memory E2E scenarios:

1. structured source → KGIS candidate/evidence/ledger → KGCS deterministic curation → executor → canonical graph → epoch;
2. document → KGIS LLM extraction (recorded completion fixture) → candidate/evidence/ledger → KGCS ER/adviser/review or auto path → canonical graph;
3. second document provides new evidence → KGCS re-curation trigger → assertion preference or concept evolution → new epoch with old history preserved;
4. stale resolution snapshot → executor reject → re-evaluate → safe commit;
5. merge followed by compensating rollback;
6. reject-only/client-authoritative profile proving no silent identity repair/merge.

Do not make these tests depend on live OpenAI/Anthropic services.

### Wave 9 — Steward reconciliation and release readiness

Goal: leave the repo truthful and easy to resume.

Tasks:

- update memory bank to real merged/ready state;
- disposition Issue #2 against current KGIS/KGCS capabilities;
- close/split ADR candidates appropriately;
- update ADR index and governance links;
- verify no `docs/adr/` control-plane drift remains after v0.3;
- run governance layout check;
- run full pytest, ruff, and strict mypy;
- produce one backlog reconciliation document summarizing built/deferred/adopter-owned work;
- identify semantic-version/release boundary for first usable KGCS v1.

## 7. Recommended PR decomposition and merge order

Keep changes independently reviewable. Default sequence:

1. **PR A — Sprint 1 reconciliation:** rebase/migrate/review existing PR #5 deterministic core.
2. **PR B — Executor + compensation + epoch semantics.**
3. **PR C — ER 5a normalization/blocking/features/calibrated matcher.**
4. **PR D — Cluster validation + policy profiles.**
5. **PR E — Bounded LLM curation orchestrator + specialist advisers.**
6. **PR F — Evidence-driven re-curation + concept/ontology evolution.**
7. **PR G — Review queue/API/CLI + backlog/backpressure.**
8. **PR H — Audit/replay + kg_eval integration.**
9. **PR I — E2E integration + steward/release reconciliation.**

Parallelism:

- After PR A stabilizes, PR B and early ER work (PR C) may proceed in parallel if file ownership is isolated.
- Adviser framework design can begin in a separate worktree while PR C is underway, but it must not finalize routing until PR D's cluster/policy interfaces stabilize.
- Review queue persistence may begin in parallel with PR E if it consumes frozen decision types only.
- E2E integration waits for the relevant upstream PRs.

## 8. File ownership guidance

Prefer these package boundaries to minimize edit collisions:

```text
src/kgcs/
  core/                 pure validation/resolution/planning/ontology
  executor/             plan execution, preconditions, compensation, epochs
  er/                   normalize/blocking/features/matcher/clusters/adviser inputs
  advisers/             LLM curation orchestrator + specialist advisers
  recuration/           triggers, dependency targeting, concept-evolution workflows
  review/               review API/queue/CLI
  audit/                semantic audit/replay if package split is justified
  backlog.py            queue SLO/backpressure coordination
  policy.py             policy/profile loading/evaluation
  testing/              reusable adapter/behavior contract suites
```

Do not reorganize existing working files merely for aesthetics. Move only where the benefit is concrete and reviewable.

## 9. Testing laws / architectural invariants

Encode these as invariant tests, not just examples:

1. LLM failure never weakens the deterministic baseline.
2. No public KGCS application API exposes raw canonical write methods.
3. Every committed canonical mutation originated from a serializable `CurationPlan`.
4. Stale snapshot/cluster preconditions prevent commit.
5. Same deterministic inputs produce the same decisions/plans.
6. Recorded LLM fixture + same inputs produces the same assessment.
7. Pairwise ER scores alone cannot commit an invalid cluster.
8. Every merge/split/retract/promote operation is compensable or explicitly declared non-compensable and blocked from auto execution.
9. Every final curation action cites evidence or an explicit absence/error state where applicable.
10. Conflicting assertions are preserved; no overwrite destroys competing evidence.
11. New evidence can trigger targeted re-curation without scanning/mutating unrelated knowledge.
12. Ontology promotion cannot skip approval governance.
13. Client-authoritative/reject-only identity mode cannot silently repair or merge identity.
14. Human review and automated paths converge on the same plan/executor/audit pipeline.
15. Queue/backpressure failures cannot silently drop candidates.
16. An LLM adviser cannot construct/apply a `GraphMutationBatch` through its public interface.
17. Curation audit is replayable enough to explain which baseline, adviser, evidence, policy, and versions produced a decision.
18. Derived/projection concerns never contaminate canonical curation decisions unless represented as explicit evidence/candidates.

## 10. Quality gates

Every implementation PR must pass:

- governance layout checks;
- `pytest` repository-wide;
- `ruff check src tests`;
- `mypy src` with strict configuration;
- relevant shared `kg_contracts.testing` contract suites;
- no new direct dependency from `kgcs` to `kgis` implementation package;
- independent architectural review against this plan + shared spec + ADRs.

For probabilistic/LLM work, also require:

- recorded deterministic fixtures;
- failure/timeout/malformed-output tests;
- named golden-set/evaluation fixture where scoring is introduced;
- explicit honest-null behavior when calibration/evidence is insufficient.

## 11. What is deliberately deferred from KGCS v1

Unless an owner decision changes scope:

- polished web review UI;
- fully autonomous graph-level or entity-level decisions without policy/human thresholds;
- multi-agent debate as a production curation tier (evaluation arm only);
- mandatory live LLM calls in CI;
- every possible backend-specific vector/full-text implementation;
- service/network deployment wrapper;
- comprehensive existing-graph migration framework beyond the shared adoption-phase minimum tooling;
- domain reasoning (research ranking, building-code interpretation policy, athlete-development scoring, traffic risk scoring) — domains produce evidence/candidates and consume canonical graph state, but KGCS stays domain-neutral.

## 12. Orchestrator completion report

At the end of execution, report:

- PRs opened/updated and exact merge order;
- tests/ruff/mypy/governance status per PR;
- ADRs/ADR candidates created and owner decisions needed;
- which design gates DG-1..DG-5 became durable architecture;
- Issue #2 disposition;
- capabilities built vs deliberately deferred;
- KGIS integration status;
- remaining adopter-specific work for research, baseball, traffic, and construction;
- one sentence stating whether KGCS v1 is usable for a real end-to-end adopter and, if not, the exact blocking capability.

Do not claim completion merely because code exists on branches. Completion means independently reviewed, owner-ready PRs with truthful documentation and reproducible gates.
