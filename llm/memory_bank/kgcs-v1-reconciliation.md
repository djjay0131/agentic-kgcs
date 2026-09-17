# KGCS v1 — build reconciliation (Wave 9)

Date: 2026-08-22
Status: **SUPERSEDED by completion (2026-09-17).** All nine build PRs are now
MERGED to `main` and KGCS v1 is formally complete; governance is v0.9.0. This
document captured the pre-merge draft-PR state. For the current, authoritative
completion record — contract reconciliation, ADR-candidate dispositions, Issue
#2 re-disposition, and the definition-of-complete checklist — see
[`../governance/kgcs-v1-completion-reconciliation.md`](../governance/kgcs-v1-completion-reconciliation.md).
The pre-merge snapshot below is retained for history.

This is the steward reconciliation for the orchestrated build
(`llm/plans/2026-08-22-kgcs-v1-orchestrated-build.md`). It records what was
built, the merge order, design-gate and Issue-#2 dispositions, what is
deliberately deferred, and the release boundary.

## PR stack and recommended merge order

Each PR is a draft, independently reviewed, gates green. Merge **in order**;
each is stacked on the previous (GitHub retargets to `main` as ancestors land).

| # | PR | Wave | Base | Gates |
|---|----|------|------|-------|
| #5 | PR A — deterministic core (reconciled) | 0 | `main` | 100 tests |
| #10 | PR B — executor + compensation + epochs | 1 | PR A branch | 127 tests |
| #11 | PR C — ER 5a substrate | 2 | PR A branch | 148 tests |
| #12 | PR D — cluster validation + policy + DG-5 profiles | 3 | PR C | 208 tests |
| #13 | PR E — bounded LLM curation orchestrator + advisers | 4 | PR D | 255 tests |
| #14 | PR F — evidence-driven re-curation + concept/ontology evolution | 5 | integration(B⊕E) | 333 tests |
| #15 | PR G — review API/queue/CLI + backlog/backpressure | 6 | PR F | 364 tests |
| #16 | PR H — semantic audit + replay + honest-null eval + kg_eval seam | 7 | PR G | 410 tests |
| #17 | PR I — KGIS→KGCS end-to-end (flagship) + steward reconciliation | 8+9 | PR H | 435 tests |

**Topology note.** Waves 2–4 (ER/cluster/advisers) were built as a sibling line
of Wave 1 (executor) off the Wave-0 core. Wave 5 is the first wave needing both
lines, so PR F is based on a throwaway integration branch
`integration/pre-recuration` (Wave 1 ⊕ Wave 4). After PR B and PR E land on
`main`, PR F retargets to `main` cleanly (its diff is recuration-only).

Independent, from-scratch review by a separate agent pass for every wave;
implementer and reviewer were distinct passes. Final full suite on the top of
the stack: **435 pytest, ruff clean, mypy --strict clean (49 source files),
governance layout 4/4.**

## What is fully implemented (Candidate → canonical → epoch → audit)

- **Deterministic core** (`kgcs.engine`): Candidate → validation → policy →
  planner → audit → immutable `CurationPlan`. Byte-identical replay.
- **Transaction-aware executor** (`kgcs.executor`): the only write path; stale
  plans rejected by preconditions; explicit unsupported-op failures; curation
  epoch published on commit; compensating-plan generation.
- **Entity resolution substrate** (`kgcs.er`): normalization + identity rules →
  multi-channel blocking → typed honest-null features → calibrated matcher
  (rule baseline + logistic) with golden-set evaluation. No cosine-threshold
  decision.
- **Cluster validation + deterministic resolution policy + profiles**
  (`kgcs.er.cluster`, `kgcs.er.resolution`, `kgcs.profiles`): invalid transitive
  clusters blocked; risk/consequence routing; DG-5 source/capability profiles
  including reject-only / client-authoritative.
- **Bounded LLM curation orchestrator + specialists** (`kgcs.advisers`):
  advisers reason with cited evidence and never write the graph; the
  deterministic baseline survives every LLM failure; recorded/replay clients.
- **Evidence-driven re-curation + concept/ontology evolution**
  (`kgcs.recuration`): triggers (enqueue-only), targeting (incremental),
  supersession/merge/split/conflict (bitemporal, compensable, never rewrite
  history), governed ontology lifecycle, and `EvolutionRouter` (auto-path
  recommendation → plan).
- **Review API/queue/CLI + backlog/backpressure** (`kgcs.review`): persistent
  queue passing the shared contract, human decisions converge on the same plan
  path as auto, typed retryable/permanent failures, backpressure signal.
- **Semantic audit + replay + honest-null evaluation + kg_eval seam**
  (`kgcs.observability`): decision-scoped semantic audit joined by trace_id,
  deterministic replay, honest-null ER/curation metrics, named comparison arms,
  a `MetricProvider` seam with no reverse dependency.
- **KGIS→KGCS end-to-end**: real `kgis.IngestPipeline` driven; the flagship
  research-paper re-curation (epoch N → N+1, history preserved) parametrized
  over a paper shape AND a non-paper sensor shape; six scenarios; §9 laws
  asserted end-to-end.

## Design-gate dispositions (DG-1 … DG-5)

All five became durable architecture using the plan's defaults; none required a
contract change (each contract tension is an ADR candidate, below).

- **DG-1 (re-curation triggers):** DURABLE. `kgcs.recuration.triggers` —
  immutable `CurationTrigger`, six trigger kinds, idempotent enqueue-only queue,
  trace-linked; triggers never mutate canonical data.
- **DG-2 (LLM orchestrator shape):** DURABLE. `kgcs.advisers` — deterministic
  `CurationOrchestrator` selecting among five bounded specialists over one
  injected completion port.
- **DG-3 (concept evolution):** DURABLE. `kgcs.recuration.evolution` +
  `router` — promotion/merge/split/relabel/supersession/conflict via immutable,
  compensable, bitemporal operations; history never rewritten.
- **DG-4 (adviser provenance):** DURABLE. `AdviserAssessment` records adviser/
  model/prompt versions, cited evidence, baseline-before/final-after; carried
  into the semantic audit by trace_id. Recorded/replay clients mandatory in CI.
- **DG-5 (curation profiles):** DURABLE. `kgcs.profiles` — per source/type
  profiles composing the existing policy (identity authority mode, ER mode,
  evidence requirements, false-merge cost class, ontology-promotion permission,
  review SLA, allowable auto-actions); no alternate write path.

## Issue #2 (baseball-AI compatibility) disposition

- **Item 2 — per-source reject-only identity mode (PC-3/IC-2): ADDRESSED in
  KGCS v1.** `IdentityAuthorityMode.CLIENT_AUTHORITATIVE` (`kgcs.profiles`),
  enforced in depth by `IdentityAuthorityConstraint` (cluster layer) and
  `ErResolutionPolicy` (policy layer): a client-authoritative identity is never
  auto-linked/merged/repaired; ER emits `POSSIBLY_SAME_AS` proposals; malformed
  refs are `REJECT`ed, never repaired. Verified across a full probability sweep
  and end-to-end (law 13).
- **Item 3 — deterministic projection-consumer profile (PC-4): ADDRESSED.**
  `projection_consumer_profile()` — confidence-1.0 auto, ER inert.
- **Item 1 — erasure/purge primitive (PC-1/IC-1, adoption-gating): NOT a KGCS
  concern; belongs to the ledger/store layer (KGIS) and the contract.** KGCS v1
  never deletes history by design (supersession is bitemporal). A subject-scoped
  purge is a storage/contract capability; KGCS would consume it, not implement
  it. Open on the KGIS/contract side.
- **Item 4 — PII-safe canonical keys (G5): contract/identity-model concern.**
  KGCS uses opaque `kg://…/identity/<ulid>` identity ids (no PII), but the
  natural-key guidance for `EntityRef` is a `kg_contracts` matter. Open on the
  contract side.

Recommendation: keep Issue #2 open; items 2 and 3 are delivered by KGCS v1
(PR D), items 1 and 4 are KGIS/contract-owned.

## ADR candidates raised (all still open — owner adjudicates promotion)

Sixteen contract-friction candidates, `llm/governance/adr/candidates/`
(0001–0016). None mutated a frozen contract. Themes: operation-type/vocabulary
gaps (0001, 0009, 0015), audit/provenance lineage not first-class (0002, 0012),
precondition/snapshot enforcement seams (0003, 0010, 0016), authority/identity
model gaps (0004, 0011), ER contract gaps (0005, 0006, 0007, 0008), and the
review surface (0014), plus re-curation plan provenance (0013). Several point at
the same eventual promotion (a semantic-audit + adviser-assessment contract
type; a wider reference-store `apply()`).

## Deliberately deferred from KGCS v1 (plan §11)

Polished web review UI; fully autonomous graph/entity decisions without policy/
human thresholds; multi-agent debate as a production tier (evaluation arm only,
off by default); mandatory live-LLM CI; every backend-specific vector/full-text
implementation; service/network deployment wrapper; a comprehensive existing-
graph migration framework; and all domain reasoning (KGCS stays domain-neutral).

## Adopter-specific work remaining (domain, not KGCS core)

KGCS is domain-neutral: adopters supply candidates/evidence and consume
canonical state. Remaining per adopter is domain wiring, not KGCS changes:
- **Research:** a real extractor emitting paper concept/assertion/evidence
  candidates; strong-namespace config (DOI/ORCID) and a paper curation profile.
- **Baseball:** client-authoritative roster profile (mechanism shipped);
  never-recycled-ID sync; and Issue-#2 item 1 (erasure) on the KGIS/contract
  side before youth-athlete PII.
- **Traffic / construction:** entity+observation candidate producers; geographic/
  temporal feature tuning; source profiles. The non-paper sensor E2E shape shows
  the pipeline already supports these shapes.

## Release boundary

Recommended first release when PRs A–I merge to `main`: **KGCS v0.1.0** (the
first usable, end-to-end KGCS). `pyproject.toml` currently declares `0.2.0`
(inherited); the owner should reconcile the version string at merge. KGCS
depends only on `kg_contracts` (verified: no `kgis` import in `src/kgcs`).

## Is KGCS v1 usable for a real end-to-end adopter?

Yes for the deterministic + calibrated + bounded-LLM + review + re-curation
platform, proven end-to-end in-memory against real `kgis` ingestion. The one
adoption gate that is **not** KGCS's to close is Issue #2 item 1 (subject-scoped
erasure), required before ingesting regulated personal data (e.g. youth-athlete
PII) — that is a KGIS/contract capability KGCS will consume.
