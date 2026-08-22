# Active Context — agentic-kgcs

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
