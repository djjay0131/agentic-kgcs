# KGCS Completion Orchestration Plan

Status: Proposed execution plan
Date: 2026-09-10
Repository: `djjay0131/agentic-kgcs`
Primary dependency: `djjay0131/agentic-kgis`
Governance authority: `djjay0131/agentic-governance`

## 1. Mission

Complete KGCS v1 by recovering, reconciling, re-reviewing, and merging the already-built Wave 0–8 implementation stack under the latest governance and current `kg_contracts` baseline.

This is **not** a rebuild. Nine functional waves already exist as draft PRs (#5, #10–#17). The work now is to establish a trustworthy new baseline, remove stale assumptions, resolve cross-repo integration regressions, revalidate every architectural invariant, and merge the stack in dependency order.

The orchestration agent must maximize safe parallelism with specialist agents, but all semantic changes remain independently reviewed and human-owner merged.

## 2. Live state at plan creation

### agentic-governance

- Canonical version is **0.5.2** as of 2026-09-10.
- v0.5.2 explicitly defines PR responsibilities and the normative lifecycle: Task → Branch → Draft PR → Review → Fix loop → Approval → Merge.
- The canonical governance repo now enforces its `governance-checks` status check on `main`.
- KGCS is still pinned to **agentic-governance v0.3** and must be migrated before KGCS build work resumes.

### agentic-kgis

KGIS v1 implementation is substantially complete and merged:

- candidate ledger + evidence registry;
- structured-sync ingestion;
- LLM document extraction;
- `kg_eval` extraction/evaluation harness;
- graph registry/advisor;
- contract/tooling hygiene.

The former KGIS ADR candidates 0001–0009 were promoted on 2026-09-08 to accepted ADRs **0015–0023**. KGCS must refresh any references to the former candidate numbers and validate its contract assumptions against current KGIS `main`.

The current `kg_contracts._frozen` implementation uses `FrozenMapping` plus Pydantic field serializers. Direct field serialization is supported, but KGCS must still test any path that nests a frozen contract mapping inside another generic object mapping (notably compensation/reversal metadata).

### agentic-kgcs

`main` is still primarily bootstrap/governance. The functional implementation remains in an open stacked PR set:

1. PR #5 — Wave 0 deterministic curation core
2. PR #10 — Wave 1 transaction-aware executor + compensation + curation epochs
3. PR #11 — Wave 2 entity-resolution substrate
4. PR #12 — Wave 3 cluster validation + deterministic resolution policy + profiles
5. PR #13 — Wave 4 bounded LLM curation orchestrator + specialist advisers
6. PR #14 — Wave 5 evidence-driven re-curation + concept/ontology evolution
7. PR #15 — Wave 6 review API/queue/CLI + backlog/backpressure
8. PR #16 — Wave 7 semantic audit/replay/evaluation seam
9. PR #17 — Wave 8 KGIS→KGCS end-to-end flagship

PRs #8 and #9 are old governance/plan-repair artifacts from August. They are not part of the functional build and must be dispositioned during governance migration rather than blindly merged.

All functional PRs are drafts. Their Aug 22 test/review claims are historical evidence, not current proof after the governance and KGIS changes.

## 3. Binding architecture

Do not redesign these unless current implementation evidence proves a contradiction and a governed ADR changes the decision:

1. Candidate ledger, canonical graph, and derived projections remain separate logical stores.
2. Applications never receive a canonical graph write API.
3. Pure KGCS logic emits typed decisions and `CurationPlan`; only the executor invokes `GraphMutationStore`.
4. Canonical writes are optimistic, precondition-checked, epoch/snapshot-aware, and fail closed.
5. Explicit curation operations are compensable where the vocabulary supports a valid inverse; non-compensable operations are declared as such.
6. Entity resolution uses normalization → multi-channel blocking → typed features → calibrated matcher → cluster validation → deterministic policy. No global cosine threshold controls merges.
7. LLMs are bounded evidence-citing advisers. They do not hold graph mutation surfaces or bypass deterministic policy.
8. New evidence can trigger targeted re-curation; old assertions/evidence remain historically traceable.
9. Human review converges on the same decision → plan → executor → audit path as automation.
10. Ontology evolution is governed; LLM recommendations cannot skip approval lifecycle.
11. Audit/replay is first-class and supports later threshold calibration.
12. `kgcs` depends only on `kg_contracts` at production-code level; KGIS implementations may be used by integration tests but not imported by `src/kgcs`.

## 4. Execution strategy

### Orchestrator

The top-level Claude session acts as Program Orchestrator / Repository Steward for execution only. It may author branches/PRs but must not self-approve or self-merge semantic PRs.

Responsibilities:

- refresh live repo state before every wave;
- maintain a dependency/merge board;
- allocate isolated worktrees to specialist agents;
- prevent overlapping writes across agents;
- require independent reviewer passes on actual diffs;
- distinguish code bugs, stale-base regressions, governance drift, contract friction, and new architecture decisions;
- keep PR descriptions and memory bank current;
- stop only on true owner decisions that cannot safely be defaulted.

### Specialist agents

Use parallel specialist agents where dependencies allow:

- Governance Migration Agent
- Toolchain/CI Stabilization Agent
- Contract Compatibility Agent
- Core/Executor Reviewer
- ER/Calibration Reviewer
- Cluster/Policy Reviewer
- LLM Adviser Safety Reviewer
- Re-curation/Ontology Reviewer
- Review/Backpressure Reviewer
- Audit/Eval Reviewer
- Cross-Repo E2E Reviewer
- ADR/Documentation Reconciliation Agent
- Final Integration / Chief Reviewer

Implementer and reviewer passes must be distinct. A reviewer must inspect the actual diff, current authority docs, and relevant tests; summaries are not review evidence.

## 5. Hard Gate G0 — migrate KGCS to latest governance

**No functional-wave rebasing or merging starts until this gate is merged.**

The user is already working on the KGCS governance upgrade. The orchestrator must treat the resulting governance migration PR as an external prerequisite and inspect it once available.

Migration target: current canonical governance (v0.5.2 at plan creation, or newer if `agentic-governance/VERSION` changed).

Verify the migration performs all required version-to-version changes, including:

- governance pin and canonical commit pin updates;
- current `llm/` control-plane layout declarations;
- `llm/plans/` and other actually-used slots correctly declared;
- current `CLAUDE.md` / `AGENTS.md` routing contract;
- current PR responsibility/lifecycle language inherited or localized correctly;
- governance-check command and workflow updated to canonical path/version;
- current platform-enforcement reality re-verified rather than copied from the August v0.3 delta;
- branch protection / required-check capability re-checked for this repo;
- stale cross-repo design-authority paths corrected if KGIS has migrated paths;
- no functional KGCS code mixed into the governance migration PR.

After the governance PR merges:

1. fetch/rebase local `main`;
2. run the exact canonical governance check including layout enforcement;
3. record the new governance baseline commit;
4. re-read the current governance rules before touching the build stack.

### Disposition PR #8 / #9

After the migration, inspect #8 and #9 for unique content.

Expected disposition: **close as superseded** if the governance migration and this current plan preserve everything still needed. Do not merge the old August repair pair merely to recreate historical process. If either contains unique required content, transplant that content through a clean current-governance PR, then close the stale PR.

## 6. Gate G1 — toolchain and dependency baseline

Create one small baseline/stabilization PR off post-governance `main` before rebasing the functional stack if the governance migration did not already address these items.

### Ruff

Current `main` declares `ruff>=0.4`; this caused CI/local divergence in August.

Owner direction from the prior session: adopt Ruff 0.16.4 rather than pinning an older release.

Required baseline:

- pin `ruff==0.16.4`;
- explicitly select the intended stable rules rather than inheriting future default expansion: `E4`, `E7`, `E9`, `F`, `I`;
- configure isort `known-first-party` for `kgcs` and test-local helper modules as needed;
- apply only mechanical import-order fixes required by this policy;
- do not opportunistically enable UP/RUF/PERF/DTZ/B or mass-refactor unrelated code in this PR.

If current canonical governance now prescribes a different toolchain policy, follow governance and document the deviation from this historical owner direction.

### Mypy / Python / packaging

- verify current CI and local environments use the same Python support floor;
- record the resolved mypy version; pin it only if current governance/reproducibility policy requires it or real drift is demonstrated;
- ensure KGCS installs the current KGIS/`kg_contracts` dependency deterministically enough for CI;
- verify no branch is testing against an accidental local editable KGIS checkout when CI will use a different revision.

### FrozenMapping integration regression

Before accepting Wave 1 or any later branch, add or run a focused regression proving that:

- `CurationPlan` and compensation/reversal metadata serialize to JSON;
- nesting `CurationOperation.payload` or another `FrozenMapping` inside `reversal_data`/generic metadata does not fail;
- round-trip preserves immutability semantics.

Preferred fix if a failure persists: normalize nested contract mappings to JSON-safe plain mappings at the KGCS composition boundary, with a targeted helper/test. Do not weaken `kg_contracts` immutability unless the problem is proven to be a general contract defect affecting multiple consumers.

## 7. Gate G2 — cross-repo contract reconciliation

Assign a Contract Compatibility Agent to current KGIS `main` before rebasing Wave 0.

Required review:

- accepted KGIS ADRs 0015–0023 and their implications for KGCS;
- current `kg_contracts` API and validators;
- `CommitResult` fail-closed narrowing from ADR-0021: verify no KGCS adapter/test returns `committed=False` without `error` or failed preconditions;
- current `FrozenMapping` behavior;
- current `MemoryGraphStore` supported operation set;
- KGIS `kg_eval.MetricProvider` seam consumed by KGCS Wave 7;
- KGIS extraction/structured ingestion behavior used by Wave 8 E2E;
- any changed public paths/names introduced since Aug 22.

Produce a short compatibility matrix: `unchanged / branch fix required / contract issue / obsolete ADR candidate` for every KGCS local ADR candidate 0001–0016.

Do not modify `kg_contracts` as part of KGCS stack recovery unless a cross-repo bug is proven. If a contract change is required, isolate it in KGIS under KGIS governance and block only the dependent KGCS wave.

## 8. Rebase/review waves

After G0–G2, recover the existing stack rather than reimplementing it.

### Wave A — PR #5 deterministic core

Rebase/reset PR #5 onto the new stabilized `main` while preserving only its semantic work.

Fresh review focus:

- deterministic replay;
- validation gates policy;
- all candidates accounted for, including no-plan artifact behavior;
- planner remains pure and does not execute;
- audit lineage remains recoverable;
- no `kgis` implementation import;
- current ADR paths/numbers are correct;
- all tests, ruff, mypy, governance checks green with the new baseline.

If clean, mark ready for human review. Do not merge automatically.

### Wave B — PR #10 executor + compensation

Rebase onto the accepted Wave-A branch or post-merge main according to the current stack mechanics.

Fresh review focus:

- every graph mutation originates from a `CurationPlan`;
- snapshot/precondition enforcement is real, including stale retry behavior;
- operation/execution IDs remain deterministic and non-colliding;
- replay cannot double-apply attachments or identity creation;
- compensation is safe and explicit;
- FrozenMapping nested serialization regression is closed;
- unsupported operations fail without partial mutation;
- epoch publication occurs only on commit.

Resolve local candidates related to snapshot enforcement and compensation re-stamping using current evidence; do not blindly preserve August workarounds.

### Wave C — PR #11 ER substrate

Can be reviewed in parallel with Wave B after Wave A has a stable rebased head because it is conceptually a sibling of the executor.

Fresh review focus:

- normalization is deterministic/idempotent and never merges;
- blocking is recall-oriented with explicit overflow behavior;
- feature absence is honest-null, not zero;
- strong-identifier contradiction dominates superficial similarity;
- calibrated matcher probability is reproducible from feature vector + version;
- calibration metadata remains keyed by graph/entity/source-pair/version/consequence;
- no global embedding threshold decides linkage.

### Wave D — PR #12 cluster validation + policy/profiles

Depends on Wave C.

Fresh review focus:

- pairwise matches cannot create invalid transitive clusters;
- cluster snapshots are versioned and staleness is enforced;
- client-authoritative/reject-only identity cannot auto-link under any score/profile combination;
- profile composition configures one policy path rather than introducing alternate mutation routes;
- deterministic baseline is complete without an LLM.

### Wave E — PR #13 bounded LLM advisers

Depends on Wave D.

Fresh review focus:

- no adviser/orchestrator type can hold or emit graph mutation surfaces;
- deterministic baseline is computed first;
- timeout/error/malformed/model failure returns the unchanged deterministic baseline;
- all recommendations cite evidence and record adviser/model/prompt versions;
- recorded completion fixtures replay deterministically;
- LLM `same` advice cannot bypass identity-authority restrictions;
- confirm intended semantics that an OPEN/ACTIVE profile routed to `LLM_ASSESS` may be upgraded to `AUTO_LINK` only by passing back through deterministic policy.

### Wave F — PR #14 re-curation + concept/ontology evolution

This was based on a throwaway integration branch because it requires both executor and adviser lines. After Waves B and E are on `main`, retarget/rebase it directly to current `main`; do not preserve the throwaway integration branch as authority.

Fresh review focus:

- re-curation triggers are idempotent and never mutate directly;
- dependency targeting is incremental rather than full-graph scan;
- concept merge/split/relabel/supersession preserves history and evidence;
- unresolved conflict preserves competing assertions;
- ontology promotion cannot skip APPROVED state;
- EvolutionRouter/auto path and later human path converge on governed plans;
- old assertions remain bitemporally queryable after supersession.

### Wave G — PR #15 review + backpressure

Depends on Wave F.

Fresh review focus:

- human review routes through the same typed decision/plan/executor path;
- persistent queue writes are atomic/durable;
- queue failures are typed and never silently drop candidates;
- SLA/priority/backpressure metrics are deterministic;
- THROTTLE/QUARANTINE is machine-readable for the KGIS integration seam;
- all review operations remain traceable and compensable where applicable.

### Wave H — PR #16 semantic audit/replay/eval

Depends on Wave G.

Fresh review focus:

- semantic audit, operation audit, and execution audit remain distinct but joinable;
- replay performs a real equality/divergence check, not a ceremonial pass;
- honest-null metrics distinguish zero from unknown;
- threshold-raising requires sufficient samples and guardrail evidence;
- KGCS implements the provider seam without importing `kg_eval` into `src/kgcs`;
- model/policy/matcher/adviser versions are captured well enough to reproduce decisions.

### Wave I — PR #17 E2E flagship + reconciliation

Depends on Wave H and current KGIS main.

Re-run and strengthen the six existing flagship scenarios against the real post-September KGIS contracts:

1. structured KGIS ingest → ledger → KGCS core → executor → canonical graph → epoch;
2. document + recorded LLM adviser → canonical decision path;
3. research-paper evidence update → targeted re-curation → adviser → deterministic gate → supersession/conflict → epoch N+1 with old assertion preserved;
4. same re-curation architecture with a non-paper source;
5. stale snapshot → reject → re-evaluate → safe commit and compensation behavior;
6. client-authoritative identity never auto-merges.

Also resolve whether the former test-only `E2EGraphStore` shim is still needed. If current `MemoryGraphStore` remains intentionally limited, keep the shim explicitly test-only and preserve the contract-friction record. If KGIS has widened the reference adapter, delete the redundant shim.

Wave I ends with a steward reconciliation, not new feature work.

## 9. Merge strategy

The old Aug 22 statement “merge in exact order #5, #10, #11, #12…” was broadly correct, but the current recovery should use dependency order rather than blindly trusting historical branch bases.

Recommended semantic merge order after governance/toolchain gates:

1. governance migration PR (external prerequisite)
2. toolchain/dependency stabilization PR, if needed
3. #5 Wave A core
4. #10 Wave B executor
5. #11 Wave C ER substrate
6. #12 Wave D cluster/policy
7. #13 Wave E LLM advisers
8. #14 Wave F re-curation (retarget to main after dependencies land)
9. #15 Wave G review/backpressure
10. #16 Wave H audit/eval
11. #17 Wave I E2E/reconciliation

Although #10 and #11 can be reviewed in parallel after #5, merge #10 before #11 for a simple linear release history unless current rebasing proves the reverse is cleaner. Their functional independence means review can overlap even when merge is serialized.

For each PR:

- refresh/rebase against the newest required base;
- run targeted tests;
- run full repo pytest;
- run pinned ruff;
- run strict mypy;
- run current governance checks;
- inspect CI, not just local output;
- independent reviewer inspects the real diff;
- fix blockers;
- update PR body to current facts;
- mark ready;
- stop for owner review/merge.

Do not merge a red PR. Do not self-merge.

## 10. ADR reconciliation

KGCS accumulated local ADR candidates 0001–0016 during the build. Do not promote them mechanically.

Before final release, assign an ADR Reconciliation Agent to classify each as:

- still-valid KGCS-local durable decision → promote to accepted KGCS ADR;
- cross-repo contract issue already decided by KGIS ADR 0015–0023 → replace with reference and close candidate;
- workaround no longer needed after rebasing/current contracts → mark obsolete;
- still-open owner decision → retain as candidate with exact blocker;
- adopter-specific concern → move/reference in adopter backlog rather than canonizing in KGCS.

Particular attention:

- artifact candidate destination;
- semantic audit linkage;
- snapshot/cluster preconditions;
- source authority carriage;
- strong-identifier taxonomy and embedding slots;
- client-authoritative profile contract surface;
- adviser assessment contract home;
- re-curation provenance source;
- ReviewQueue typed outcomes;
- reference memory-store operation coverage;
- compensation re-stamping.

No candidate becomes an ADR merely because code exists.

## 11. Definition of complete

KGCS v1 is complete when:

- governance is current and all governance checks pass;
- all semantic waves are merged to main;
- CI is green on main with deterministic tool versions/rules;
- full strict mypy is green;
- full pytest is green;
- KGIS→KGCS E2E scenarios pass against current KGIS main;
- no production KGCS module imports KGIS implementation packages or vendor LLM SDKs;
- no LLM component can mutate canonical state directly;
- every canonical mutation originates from a typed `CurationPlan` through the executor;
- stale plans fail closed;
- entity-resolution calibration and cluster constraints are reproducible;
- human review and automation converge on the same write/audit path;
- evidence-driven re-curation preserves bitemporal history;
- semantic replay detects real divergence;
- ADR candidates are reconciled;
- Issue #2 is dispositioned against what KGIS/KGCS now actually provide;
- memory bank and release/readme state describe current reality;
- a final independent whole-system review finds no blocking architectural or correctness defects.

## 12. Orchestrator final report

At completion, report:

- governance version and migration PR;
- toolchain baseline versions/rules;
- every KGCS PR updated/merged and final SHA;
- tests, ruff, mypy, governance-check, and CI results;
- all contract compatibility findings against KGIS current main;
- all ADR-candidate dispositions;
- Issue #2 disposition;
- any remaining deferred features;
- exact release-readiness verdict;
- adopter work still required for research-paper, baseball, traffic, and construction projects.

The orchestrator must not declare “complete” solely because all old PRs merged. Completion requires the current cross-repo E2E and architecture invariants to pass on the final `main` state.