# Governance Delta: agentic-kgcs

Status: Approved (amended per external design review, agentic-kgis PR #1)
Last updated: 2026-09-16
Governance: agentic-governance v0.9

This file localizes [agentic-governance](https://github.com/djjay0131/agentic-governance)
for this project.

## Mission

KGCS (Knowledge Graph Curation Service) is a reusable Python library that
manages knowledge admission, identity, evidence, and canonical graph state
for every project in this portfolio — a graph-oriented assertion and
identity curation layer, not a home for domain reasoning. Two halves: a
deterministic curation core (validation, ontology policy, identity syntax,
curation planning) whose plans are applied by a transaction-aware executor,
and an async resolution plane (calibrated entity resolution with a bounded
LLM adviser, review operations, immutable audit). Uncertain candidates
live in a candidate ledger, never as ordinary canonical-graph entities.
It depends only on `kg_contracts` from `agentic-kgis`.

## Design-Authority Document

`agentic-kgis/llm/specs/2026-07-09-kgis-kgcs-design.md`
(the shared KGIS/KGCS design spec lives in the sibling repo).

**Cross-repo path, not yet migrated.** This is `agentic-kgis`'s current
path. When that repo adopts v0.3 the spec moves to
`agentic-kgis/llm/specs/2026-07-09-kgis-kgcs-design.md`, and this field —
plus the citations in `README.md`, `CONTRIBUTING.md` and
`.github/pull_request_template.md` — must be repointed in the same pass.

## Project Principles

1. No application-facing surface can mutate canonical graph state; only
   KGCS executors apply serializable, precondition-checked mutation
   batches (ADR-0010 in agentic-kgis). Deterministic admission checks are
   cheap and unbypassable; probabilistic resolution is async and
   reversible.
2. Repair-or-reject at the admission boundary; never silently coerce or
   drop.
3. Rejections and failures are data (quarantine/ledger states + reason);
   exceptions are bugs. Fail closed on canonical mutation, with transient
   faults distinguished from bad data.
4. Governed ontology: PROPOSED → APPROVED → OBSERVED → DEPRECATED; writes
   require approval, observation is tracked — no phantom node/edge types.
5. Candidates live in the ledger with processing states; only accepted
   identities and assertions materialize canonically, with bitemporal
   validity and assertion-level status. Every merge is a compensable
   operation; every curation action gets an immutable audit record.
6. Calibrated-risk routing (auto / LLM-advised / human) is the automation
   path — thresholds and policies are config, not code; the LLM advises
   with cited evidence and never issues the merge.
7. The audit stream and registry lineage are future training data; never
   skip them.
8. Derived projections are built only from canonical data at a published
   curation epoch.

## Domain Review Questions

- Can any application-facing path mutate canonical graph state without a
  KGCS executor? (Must be no.)
- Is every new curation action audited and compensable?
- Does this preserve fail-closed behavior on canonical mutation?
- Are thresholds/policies config rather than hard-coded?
- Does any LLM component decide (rather than advise with cited evidence)?
- Could uncertain/ledger data leak into canonical reads or projections?
- Does this keep kgcs depending only on kg_contracts?

## Repository Layout

The paths this repo binds (agentic-governance
`llm/governance/project-operating-system.md` §Repository Areas prescribes
the shape; this block binds it here). Only the slots this repo uses are
declared.

- Governance directory: `llm/governance/`
- ADR directory: `llm/governance/adr/`
- Memory-bank path: `llm/memory_bank/`
- Artifacts directory (the data plane): `docs/`

Not yet declared, because this repo has no such content today:
constitution directory (role charters are canonical, not local), features
directory, plans directory, spec directory, and **sprints directory** — the
design authority is the sibling repo's shared spec. `CLAUDE.md` still routes
new design specs to `llm/specs/` and new implementation plans to
`llm/plans/`; declare those slots here when the first one is created.

The sprints slot became declarable in agentic-governance v0.5.0. This repo
executes dependency-ordered *waves* against the shared design spec rather
than time-boxed sprints, so it has no sprint content and the slot stays
absent. An absent slot is not a violation; declaring a path that nothing
occupies is.

`llm/plans/` is deliberately undeclared **and** currently empty. The plan
that occupied it was written directly to `main`, bypassing issue → branch →
PR, and was reverted in #8. Declaring the slot while the directory is empty
is precisely the failure this repo hit: `FAIL layout — plans declared as
"llm/plans", which does not exist`. The slot gets declared by the PR that
lands the next plan, not before it.

## Roadmap

Path: none (the plan sequence in the design spec §11 — in `agentic-kgis` —
serves as the roadmap).

## Canon Location

Where the canonical `agentic-governance` repo lives, declared once. **This is
the only machine-specific path this repo is permitted to contain** — every
canon citation in `CLAUDE.md`, `AGENTS.md` and the check command below resolves
against it, so it changes in one place instead of a dozen.

- Canon checkout: `~/code/agentic-governance`
- Canon repository: `https://github.com/djjay0131/agentic-governance`
- Plugin registered: `repo` (`.claude/settings.json`)

Skills and agents running as the installed plugin resolve canon from
`${CLAUDE_PLUGIN_ROOT}/..` and need none of this; the declaration exists for
everything that is read *without* the plugin loaded — static instructions in
`CLAUDE.md`, and a check command run from a plain shell.

**Deliberately not verified by `--layout`.** A canon checkout is
environment-specific: CI fetches canon into a runner temp directory and has no
such path, so asserting it would fail every CI run for a repo whose local
declaration is perfectly correct. Verify it yourself when you change it —
`ls <canon checkout>/VERSION`.

## Governance Check Command

`node "${CLAUDE_PLUGIN_ROOT}/scripts/governance-checks.mjs" --layout` when the
governance plugin is loaded — preferred, because it needs no declared path.
From a plain shell, the same script — `plugin/scripts/governance-checks.mjs
--layout` — under the `Canon checkout` declared in §Canon Location above. The
checkout path is deliberately **not** expanded here: the machine-specific value
must appear in exactly one place per repo, and twenty lines below the
declaration is still a second place. Both forms reach canon through that single
declaration.

`--layout` must be in the recorded command, not run only at onboarding.

CI wiring: **live** via `.github/workflows/governance-checks.yml` (runs on
every PR and on pushes to `main`). Because the canonical script lives
outside this repo, the workflow fetches agentic-governance pinned to a
commit SHA, kept in sync with the governance version above. Not yet a
*required* status check: branch protection on `main` **is** live as of
2026-09-10, but its only required context is `test`; adding
`governance-checks` is an owner settings change (see Platform Enforcement
Reality).

## L0 Path Allowlist

```l0-allowlist
# Instance of agentic-governance llm/governance/l0-fast-track.md
# §Template Allowlist — the source of this rule set and its grammar.
allow llm/memory_bank/** path-only
allow llm/governance/adr/README.md index-table-rows
allow llm/governance/adr/[0-9][0-9][0-9][0-9]-*.md status-line-only
allow llm/** link-target-only
allow docs/** link-target-only
deny src/**
deny scripts/**
deny .github/**
deny llm/governance/governance-delta.md
deny llm/governance/adr/0000-template.md
```

## Platform Enforcement Reality

Re-verified against the GitHub API on 2026-09-10 via
`gh api repos/djjay0131/agentic-kgcs/branches/main/protection`, not assumed.
The prior revision recorded a 2026-07-09 free-plan 403 and concluded that
branch protection was unavailable; the repo is now public, that 403 is gone,
and protection is live. This section records the response, not the intent.

- **Branch protection on `main`: configured.** Pull requests are required,
  force pushes are blocked (`allow_force_pushes: false`), branch deletion is
  blocked (`allow_deletions: false`), conversation resolution is required
  (`required_conversation_resolution: true`), and stale reviews dismiss on a
  new push (`dismiss_stale_reviews: true`). The Issue → Branch → Draft PR →
  Review → Merge flow is no longer convention-only: the platform now
  refuses a direct push to `main`.
- **Required status checks: enabled, one context.** `test` — the `test` job
  of `.github/workflows/ci.yml`, supplied by GitHub Actions — with
  `strict: true`, so a PR must be up to date with `main` before it can
  merge. This is the one gate genuinely enforced by the platform rather than
  by convention: a PR whose `test` job fails cannot be merged.
- **Convention-only, despite the above.** What the platform is configured
  *not* to enforce:
  - `required_approving_review_count` is **0** — a PR is required, an
    approval is not. Review remains a procedural commitment.
  - `enforce_admins` is **off** — the owner (an admin) can bypass the
    required `test` check. The gate binds agents and ordinary flow; it does
    not bind the owner.
  - The `governance-checks` job of `.github/workflows/governance-checks.yml`
    is **not** a required context. It runs on every PR and its result is
    visible, but a red governance check does not block a merge. Only `test`
    does.
  - Also off, and deliberately unremarkable here: `required_signatures`,
    `required_linear_history` (merges are squashed by practice, not by
    rule), and `require_code_owner_reviews`.
- **Branch cleanup: `delete_branch_on_merge` is `true`.** Verified
  2026-09-16 via `gh api repos/djjay0131/agentic-kgcs -q
  .delete_branch_on_merge` — a different endpoint from the protection call
  above, because this is a **repository** setting rather than branch
  protection. That distinction is why it is available on every plan,
  including the private-repo-on-a-free-plan case where the protection call
  403s — the case this repo was actually in on 2026-07-09. It makes the PR
  lifecycle's closing clause ("Branch deleted post-merge") a platform fact
  instead of a flag whoever merges has to remember; canon states the
  default in agentic-governance `llm/governance/branch-protection.md`
  §Branch Cleanup (v0.9.0).
- **Token/identity model:** all agent sessions authenticate with the owner's
  token — steward/auditor/architect are procedural roles, not distinct
  identities; independence is temporal/artifactual. The platform cannot tell
  them apart, so no amount of branch protection makes role separation real
  here.
- **Hardening path — what remains.** All three are owner settings changes,
  not repo changes:
  - Add `governance-checks` to the required contexts. The cheapest real
    gain: the workflow already runs, and requiring it would make the
    governance checks binding instead of advisory. Likeliest next step.
  - `enforce_admins` → on. Makes the required checks bind the owner too.
    Not taken because it also closes the owner's emergency path on a
    single-maintainer repo.
  - `required_approving_review_count` → 1. **Blocked, not deferred.** A
    single-maintainer repo cannot supply a second approver, so a required
    review would deadlock every PR until a second human or a distinct
    steward machine account exists.

## Steward Activation Status

Status: INACTIVE

Steward merge authority ships inert (agentic-governance
`llm/governance/l0-fast-track.md` §Per-Repo Activation). No activation ADR or PR
exists; all merges are human-owner-only.

## Milestone Labels

- `phase-3-curation-core`
- `phase-5-entity-resolution`
- `phase-6-eval-review`
- `phase-7-registry`

(Numbering shared with agentic-kgis's plan sequence, v2.)

## Special Labels

- `gate` (changes to the admission path / curation core — highest review
  scrutiny)

## Constitution Adjustments

None.

## Related Repos

- `agentic-kgis` — hosts `kg_contracts` (this repo's only dependency), the
  shared design spec, and system-level ADRs. kgcs-local ADRs live here in
  `llm/governance/adr/`.
