# Active Context — agentic-kgcs

Update 2026-07-10: External design review (ChatGPT, agentic-kgis PR #1)
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
