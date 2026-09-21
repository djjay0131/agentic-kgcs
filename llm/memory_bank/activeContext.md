# Active Context — agentic-kgcs

Update 2026-09-21 (Defect 2 — the CREATE_IDENTITY inverse): **rollback of an
identity-creation run is now demonstrated, not asserted (ADR-0020).** Branch
`fix/complete-create-identity-inverse`, the KGCS half of KGIS ADR-0025.
**agentic-kgis PR #46 is MERGED** (`agentic-kgis` main `de48639`), so the gate
is lifted and CI is green. Re-verified against the merged contract rather than
the PR head: 535 pytest, 0 skipped, ruff, mypy strict, governance 4/4, and the
full 10-mutation battery reproduces kill-for-kill — nothing silently stopped
running when the imports started resolving.

Three merged-#46 points verified rather than assumed: (1) the inverse payload
carries the **pre-revoke ACTIVE** entity dump (KGCS gets this right generically
by never reading the graph back for reversal data); (2) the revoke round trip
restores the identity `ACTIVE` but **not** its creation epoch — a stated bound,
KGIS issue #51's `RESTORE_IDENTITY`, and the in-place fix is ruled out by KGIS
mutant B1′; (3) `INVERSE_OPERATION_TYPES` is a **vocabulary** statement, not an
executability one — 7 types have a named inverse, the reference store executes
3, so `fully_compensable=True` does not mean "rollback works today". All three
are pinned by tests. Measured against the #46 head: KGCS's
527-test suite had **exactly one** failure — `INVERSE_OPERATION` missing
`REVOKE_IDENTITY` — and nothing else in the contract change touched KGCS.

Done: `INVERSE_OPERATION` is now *derived* from `kg_contracts.INVERSE_OPERATION_TYPES`
rather than hand-transcribed (the drift that let `CREATE_IDENTITY` sit as
non-compensable for a release is now unrepresentable); both producers of a
`CREATE_IDENTITY` — planner and `recuration.evolution.plan_promotion` — carry a
revoke payload built by one shared `revoke_inverse_payload`, per ADR-0018's
two-producer lesson; `REVOKE_IDENTITY` joined `DEFAULT_SUPPORTED_OPERATIONS`;
and the rollback is executed end to end — 8 identities, forward COMMITTED at
epoch 1, rollback COMMITTED at epoch 2, default read 0 (was 8),
`include_revoked=True` 8 all REVOKED all at `curation_epoch=1`, epoch-scoped
read at 1 still 8, `include_superseded=True` 0.

**Read-semantics audit (REVOKED now hidden by default):** KGCS production code
makes exactly **one** canonical read — `GraphReader.current_epoch()` — so
nothing here breaks. No `get_entity`/`find_entities`/`assertions_for`/
`neighborhood` call exists in `src/` on this branch. **That audit expires the
moment KGCS reads entities back**, and ADR-0019's sibling branch adds the first
such read (`assertions_for` in `_assertion_present`); whichever merges second
must decide whether it also wants `include_revoked=True`. It should.

Still open: **assertion** rollback is compensated correctly and in the right
order but cannot execute against the reference store, which implements no
`RETRACT_ASSERTION` (ADR candidate 0015, upstream). Identity rollback is
demonstrated; assertion rollback is still only asserted.

Update 2026-09-21 (upstream finding, resolved upstream — no KGCS change):
an in-flight `kg_contracts` validator forbidding `create_new_identity=True`
together with `resolved_identity` broke **61** KGCS tests. Reported rather than
worked around; the KGIS agent withdrew the validator as unsound (it could not
distinguish a freshly minted id from a pre-existing one, so it rejected the
legitimate case and missed the illegitimate one). Re-measured after the
withdrawal: 61 failures → 1. **`kgcs.policy.ResolutionPolicy` is correct as it
stands and must not be changed.** The open semantic question — what
`resolved_identity` means when `create_new_identity=True` — is agentic-kgis
issue #47; KGCS implements reading B ("the identity this candidate ends up
attached to"), which the planner requires for the `CREATE_IDENTITY` payload.

Update 2026-09-19 (post-v1 defect fix): **ADR-0018 — a compensating plan
asserts post-application state, and carries a payload a store can apply.**
Branch `fix/compensation-precondition-and-inverse-payload`, opened after the
`agentic-kg` adopter hit three defects building a Neo4j `GraphMutationStore`.
All three reproduce against KGCS's own reference store, and they are **one**
defect with three faces.

The gate: `Compensator` carried the *source* plan's snapshot precondition into
the compensating plan. The executor enforces that guard itself against the
graph's current epoch whenever the store is also a `GraphReader` — the
reference `MemoryGraphStore` is — so the original plan's own commit invalidated
the guard its own rollback inherited. Measured on `v1.0.0`: source plan guard
`('snapshot_version','g1','0')`, forward `COMMITTED` at epoch 1, compensating
plan guard **still `'0'`**, compensation `STALE`, store untouched.
**Compensation has never been executable against a readable store.**

Behind the gate, two malformed inverses nobody could reach. `RETRACT`→`ATTACH`
produced a payload with 17 `Assertion` validation errors (10 required fields
missing — `predicate` among them — and 7 `extra_forbidden` from the provenance
block that shared `reversal_data`). `ATTACH`→`RETRACT` dropped `new_status` and
`superseded_at`, at both producers. Root cause of both: `reversal_data` was
simultaneously the inverse payload and the forward op's lineage, and
`_invert` used the whole dict as the payload.

Fix. `compensate(plan, *, against_snapshot)` — **required and non-optional** —
rebases the source plan's snapshot guards onto the epoch the original plan
committed at (`ExecutionRecord.new_epoch`). Every compensating plan carries
**exactly one** snapshot guard, synthesized on the first candidate id when
there is nothing to rebase; a non-epoch value raises `ValueError`. There is no
argument that yields an unguarded plan. `INVERSE_PAYLOAD_KEY`
separates the inverse payload from the lineage in `reversal_data`, with a
fallback to the whole dict for un-migrated producers. Shared
`retract_inverse_payload` gives both producers a complete `RETRACT` payload
(`new_status=SUPERSEDED`, `superseded_at=recorded_at`; no `superseded_by` —
a rollback has nothing superseding it); the supersession `RETRACT`'s inverse is
the full pre-retraction assertion dump.

**Two artifacts had pinned the defect as correct.**
`test_executed_attach_is_rolled_back_by_its_compensation` asserted the
compensation was `STALE` and then hand-rebuilt the precondition to proceed; and
**ADR candidate 0016** described defect A exactly and, at v1 completion, *accepted
it as a durable KGCS-local decision* — graded an ergonomics gap when it was a
non-functional path. Candidate 0016 is now `Superseded by ADR-0018`. The E2E
harness's `superseded_at` fallback (a fixed instant when none was carried) was
the third piece of cover and is removed.

A supersession now rolls back end to end for the first time: 2015 superseded by
2014 at epoch 2, rolled back at epoch 3 to 2015 live / 2014 `SUPERSEDED`,
nothing deleted (§9 law 10). Both directions tested — commits when the graph is
where it should be, `STALE` when a concurrent writer got there first.

Review round 2 found a defect **this PR created by unblocking the path**:
compensating a compensation committed and left 2015 *and* 2014 both `ACTIVE` on
one subject and predicate, because a compensating `ATTACH` re-attaches an
existing `assertion_id` and `MemoryGraphStore.put_assertion` appends. ADR-0018
now states that a compensating `ATTACH` is an **upsert by `assertion_id`** (an
adapter obligation — a uniqueness constraint in a real store), the E2E store
implements it, and the test runs the second compensation and asserts one row
per id landing back on the post-supersession state. Also closed rather than
deferred: the `against_snapshot=None` fail-open path and the undocumented
"real epoch, still no guard" case. `snapshot_guarded` is gone — it had zero
production consumers, since the executor only ever sees a `CurationPlan`.

Verification round 3 confirmed the involution to depth 3 (`e2→e3→e4→e5`, two
rows and no duplicate id at every depth, LIVE alternating 2014↔2015) and
sharpened one point in our favour: because a conforming adapter's upsert makes
the inverses **idempotent**, the graph-global guard is a **liveness** problem,
not a safety one — over-broad guards stick valid rollbacks but cannot corrupt
state, so per-subject guards (candidate 0003) are an availability improvement
to schedule rather than a correctness hole. Recorded in the ADR, along with the
one gap left open: Decision 5's upsert is a *stated* obligation whose only
conforming implementation lives in `tests/`, while `PlanExecutor` already holds
a `GraphReader` and knows `is_compensation=True` — a post-apply duplicate-id
check there needs no new `ExecutionOutcome` and would make it verified rather
than asserted. Named in the ADR so it is not rediscovered. Also closed: `int()`
**coerced** rather than rejected (`1.5→'1'`, `-0.4→'0'`, the latter slipping
past the non-negative check into a wrong-but-*meetable* guard, which is worse
than an unmeetable one); floats are now refused outright.

490 → 519 passing (+29 net). **19** pre-existing `compensate()` call sites
gained the keyword — all in `tests/`, **zero in `src/`**, which is itself the
tell: nothing in production ever called the rollback path. 3 tests changed to
read the new `reversal_data` shape; 1 rewritten because it encoded the defect.
ruff clean, mypy strict clean (49 files), governance 4/4.

Release: `pyproject.toml` is deliberately untouched — this repo bumps in a
dedicated `chore(release)` PR (issue #31, convention set by #29). This change
is **source-breaking**: `Compensator.compensate(plan)` no longer compiles, and
the `reversal_data` shape moved payload material under `inverse_payload`.
Strict semver on a tagged `1.0.0` makes that **2.0.0**, shipped **standalone**
rather than folded into the `1.1.0` queued on #31 — folding it would leave the
version number silent about the one break a reader most needs warning of. The "nothing broke, compensation never worked" counter-argument
covers only the signature: `reversal_data` is a *serialisation* shape, and
reading it always worked, so a flat reader breaks **silently** at rollback time.
An earlier revision of ADR-0018 graded this *minor*, contradicting the PR body —
a wrongly-graded ADR inside the fix for a wrongly-graded ADR; corrected.

Update 2026-09-18 (post-v1 defect fix, rev 2 after review): **ADR-0017 —
identifier strength is entity-type relative.** Branch
`fix/container-identifier-strength` (PR #30), opened against tagged `v1.0.0`
after an adopter probe. `DEFAULT_STRONG_NAMESPACES` held `issn`/`isbn`/`orcid`,
none of which names a *work*: two different papers sharing only an ISSN scored
p = 0.998383 and **AUTO_LINK even at HIGH** cost (risk 0.001617 inside the
documented 0.002 budget); two different papers by one author sharing only an
ORCID scored identically. Independent review then found three more instances of
the same fault, all reproduced and now fixed: `venue` in the first revision's
ISSN subject set let two different conferences sharing the LNCS series ISSN
auto-link at p = 0.998028; one journal's print vs electronic ISSN (and one
book's ISBN-10 vs ISBN-13) read as `CONTRADICT`, scoring p = 0.000001 and
getting the cluster rejected — a defect the first revision had *pinned green*
with a test; and a shared ISSN still moved probability through
`attribute_rarity` (weight +2.0, 0.604806 → 0.918754) after the signal channel
had refused it.

Fix, two axes, both data: `DEFAULT_STRONG_NAMESPACES = {doi, vin}` (strong for
whatever carries them); new `NamespaceScope` + `DEFAULT_SCOPED_NAMESPACES`
carrying, per namespace, the entity types it *names* and whether disagreement
is decisive — `orcid → person/author/… contradicts=True`, `issn →
journal/serial/periodical contradicts=False`, `isbn → book/monograph/…
contradicts=False`. Off-subject use emits an auditable `UNKNOWN`;
`_attribute_rarity` now excludes suppressed namespaces. All injectable;
`strong_namespaces` membership outranks a scope (the one-arg escape hatch).
Journal/book/person resolution preserved exactly (a Journal pair sharing an
ISSN measures p = 0.996727 before and after). `FEATURE_KEYS` and `to_vector`
untouched, so replay stays bit-identical. **No pre-existing test changed** —
nothing in the suite encoded the old behaviour (435 → 488 passing, +53 new).
ADR candidate 0005 amended, not re-dispositioned. Warrants a **minor** release,
not a patch: default resolution outcomes change, and the *value* of an exported
constant changed, so even an adopter who explicitly pinned
`strong_namespaces=DEFAULT_STRONG_NAMESPACES` is affected.

Review pass 2 (APPROVE-WITH-FINDINGS) added two more, both fixed: the ORCID
subject list was too narrow, silently costing person↔person ORCID matching for
the type names `Human`/`Individual`/`Agent`/`Scholar` and similar (measured
0.997112 AUTO_LINK → 0.461150 GATHER_MORE_EVIDENCE, invisible to the suite
because the one pre-existing ORCID test bypasses scoping) — now 17 person-shaped
type names with a test that exercises the default rule; and the ADR documented
only the upside of `contradicts=False`. Its **cost** is now recorded and pinned
by a test: two genuinely different journals with disjoint ISSNs went from
p = 0.000001 / RETAIN_SEPARATE / cluster-rejected at every cost class to
p = 0.997792 / **AUTO_LINK at STANDARD** with 12 shared affiliations. Judged the
right trade because the old behaviour was wrong on the *common* case (a journal
normally carries print + electronic ISSN) and failed closed irreversibly, while
the new one is wrong only on a conjunction and fails open into a routable
decision; HIGH still declines. Mitigations recorded: run journal ER at HIGH, or
add an ISSN-L authority at normalization and restore `contradicts=True`.

**Structural note recorded in the ADR:** MAJOR-A, the deferred DOI problem
(#32), and the declined "weak corroborating evidence" alternative are all the
same gap — `PairFeatures` has no *weak negative* evidence channel, only
three-valued `identifier_agreement` where CONTRADICT dominates. Every identifier
signal must be decisive or silent. One signed small-weight feature fixes all
three; it is deferred because adding a key changes `FEATURE_KEYS` and breaks
replay comparability against v1-recorded decisions — a migration with its own
ADR, not a defect fix.

Known remaining exposure, deliberately not fixed in that PR and filed as
follow-ups: `doi` has the mirror problem (a preprint DOI vs a published DOI are
disjoint, so `CONTRADICT` blocks a merge that should happen — but §7.4 names
"two different DOIs" as its canonical contradiction, so flipping it is a
spec-level call); blocking still fans out quadratically on a shared ISSN; and
the release-version bump itself.

Four further findings were verified against `main` during the same pass and are
**not** fixed there (each deserves its own issue): (1) `ErRoutingThresholds` is
absent from `ReplayInputs` and its `version` is stamped nowhere — `replay()`
substitutes defaults, a determinism hole (HIGH); (2) `ClusterValidation` can be
`valid=True` with `checked_pairs=0`, and the gate reads only `.valid`, never
`pairwise_complete` — unvalidated membership can auto-link (HIGH); (3)
`calibrate_logistic` on an empty golden set returns an all-zero model without
raising, and `CalibratedMatcher` cannot signal it is uncalibrated — every pair
scores exactly 0.5 (MEDIUM); (4) no `py.typed`, no `LICENSE` (LOW).

Update 2026-09-17 (wrap-up): **KGCS v1 build fully closed out.** All build PRs
merged (#5, #10–#17) + the completion PR (#27); PR #18 closed as executed;
Issue #2 re-dispositioned (kept open — items 1/4 KGIS/contract-owned). Stale
branches cleaned: deleted `backup/pr5-original` and remote
`docs/2026-08-22-kgcs-v1-orchestrated-build`. Kept: `docs/2026-09-10-kgcs-completion-orchestration`
(the completion plan's home, referenced by the reconciliation record, pending
an owner declaration of the `llm/plans/` slot). **Left for the owner** (not
touched — active governance domain): local branches
`governance/upgrade-v0.5` (superseded by the merged v0.9 upgrade) and
`governance/repoint-kgis-design-authority` (1 unmerged commit).
**Only open items — all owner/upstream/adopter, none KGCS-core:** (a) tag the
**v1.0.0** release + bump `pyproject` (currently 0.2.0); (b) declare the
`llm/plans/` governance slot so plans can live on `main`; (c) decide the two
governance branches above; (d) upstream KGIS/contract asks — ADR candidates
0004 (authority field) & 0015 (reference-store op coverage), Issue #2 item 1
(subject-scoped erasure), the `kg_eval` MetricProvider adapter; (e) adopter
domain wiring (research/baseball/traffic/construction). `main` green: ruff,
mypy strict (49 files), 435 pytest, governance 4/4.

Update 2026-09-17 (v1 COMPLETE): **KGCS v1 is merged and formally complete on
`main`.** All nine build PRs (#5, #10–#17) are merged; governance is current
(agentic-governance v0.9.0). `main` is green: ruff==0.16.4 (pinned rules
E4/E7/E9/F/I), mypy strict (49 files), 435 pytest, governance checks 4/4. The
ruff import-sort drift and the `FrozenMapping` compensation-serialization break
are resolved (the latter by KGIS's `_frozen` serializer). Gate G2 (cross-repo
contract reconciliation vs KGIS ADRs 0015–0023 + `kg_contracts`) found **no
blocking defect**; the 16 ADR candidates are reconciled (10 accepted KGCS-local,
2 retained upstream-blocked — 0004/0015, 4 adopter-deferred — 0005–0008); Issue
#2 re-dispositioned (items 2/3 KGCS-satisfied, items 1/4 KGIS/contract-owned).
Full record: `llm/governance/kgcs-v1-completion-reconciliation.md`. Remaining is
adopter domain wiring + the deferred v1 items + upstream asks (0004/0015, the
`kg_eval` MetricProvider adapter) — none KGCS-core. NEXT: owner tags the v1
release; adopters wire their domains.

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
