# ADR-0024: Accept compatible `kg_contracts` minor versions at admission

Status: Accepted
Date: 2026-10-09

## Context

`ContractVersionRule` (`src/kgcs/validation.py`) required an **exact** string
match between a candidate's `contract_version` and the installed
`kg_contracts.CONTRACT_VERSION`. That was correct while `kg_contracts` stood
still, but `CONTRACT_VERSION` is semver and moves: `agentic-kgis` bumped it
`2.0.0 → 2.1.0` (ADR-0022/ADR-0023 — new optional fields) and again
`2.1.0 → 2.2.0` (candidate 0011 — `Evidence.span`, a new optional field).
Both were **backward-compatible, additive** changes by construction: nothing
already produced is invalidated by them.

The failure mode is durable, not transient. A candidate written before a minor
bump carries `contract_version="2.1.0"`; after the consumer upgrades to `2.2.0`
the exact-match rule rejects it as `BAD_DATA`. Re-ingest does **not** repair
this: the candidate ledger keeps the first row per `semantic_key`, so the
already-persisted row is never replaced, and a rejected pre-upgrade row is
rejected forever. The exact rule converts a compatible consumer upgrade into
silent, permanent data loss for the rows it was announced to protect.

The decision was escalated as issue #53 ("accept same-major (2.x ≤ installed)
for additive minors, or require draining received rows before upgrading") and
resolved by the owner on 2026-10-09 in favour of **accepting compatible minor
versions**.

## Decision

### 1. `ContractVersionRule` accepts same-major, not-newer-minor

The rule's default mode is `ContractVersionMode.COMPATIBLE_MINOR`. A candidate
is admissible when, parsed as strict `MAJOR.MINOR.PATCH` semver:

- `candidate.major == installed.major`, **and**
- `candidate.minor <= installed.minor` (the patch is ignored).

It rejects, each with a `BAD_DATA` reason naming **both** versions:

- a candidate with a **different major** — the contract shape is not promised
  compatible across a major;
- a candidate with a **newer minor** than the installed contract — the consumer
  is behind the producer and cannot know the shape it was written against, so
  it fails closed rather than guess;
- a candidate whose version is **not** strict semver.

### 2. Exact match remains available, explicitly

`ContractVersionMode.EXACT` restores the original string-equality check for any
caller who wants it. `default_validator` takes a `contract_version_mode`
keyword, defaulting to `COMPATIBLE_MINOR`; the mode is also readable off the
rule. Nothing silently downgrades to the old behaviour.

### 3. Strict semver, no new dependency

A private `_parse_semver` parses `MAJOR.MINOR.PATCH` with an ASCII `[0-9]`
regex and a canonical-form round trip, so leading zeros (`2.02.0`) and
pre-release/build suffixes (`2.2.0-rc1`) are refused rather than coerced.
`re` is stdlib; the dependency floor is unchanged.

## Rationale

`kg_contracts` bumps its **minor** only for backward-compatible, additive
changes, and says so at the bump site (`agentic-kgis` ADR-0022/0023, candidate
0011). A candidate produced at an older minor is therefore field-compatible by
the contract's own published compatibility class, and rejecting it is a false
positive. The ledger's first-row-per-`semantic_key` rule makes that false
positive unrecoverable. Accepting an older minor is the only decision that
keeps admission truthful to the compatibility the contract already declares.

The asymmetry is deliberate and is the safety argument: **older** minors are
accepted because the consumer understands everything the producer could have
written, while **newer** minors are rejected because it does not. A producer
ahead of its consumer is the one direction where guessing is unsafe, so it
fails closed. Different majors fail closed for the same reason.

## Alternatives Considered

### Keep exact match; drain received rows before upgrading

This is the other branch of issue #53. It requires an operational drain step
on every minor consumer upgrade, and it offers no protection for the rows the
ledger cannot replace — they are exactly the rows an upgrade would strand. It
also contradicts the contract's own declared compatibility class by treating a
backward-compatible bump as breaking. Rejected.

### Accept any `2.x` on the same major (ignore minor direction)

Accepting a *newer* minor would admit candidates that use fields the consumer
does not know. Validation would succeed and a later stage — the planner or the
contract model — would fail on unknown data, exactly the exception-where-data-
is-demanded failure principle 3 forbids. Rejected: the consumer must not
accept what it cannot interpret.

### Treat the patch as significant

The patch carries no shape information under semver; requiring it to match
reintroduces a strictness the decision exists to remove, with no compatibility
benefit. Rejected.

### Add a third-party semver library

`packaging` is already in the dev environment but not a runtime dependency, and
the grammar here is the three-integer core of semver, not the full range
grammar. A new runtime dependency for a fifteen-line parse is not warranted.
Rejected.

## Consequences

### Positive

- A KGCS minor upgrade no longer rejects, and therefore no longer permanently
  loses, ledger rows written at an older compatible `kg_contracts` minor.
- Admission now matches the compatibility class `kg_contracts` publishes at
  each bump, instead of being stricter than the contract it guards.
- The stricter-than-necessary direction (newer minor, different major,
  malformed) still fails closed and is pinned by tests.

### Negative / Tradeoffs

- A candidate whose version merely *looks* compatible is admitted on the
  contract's word that minor bumps are additive. If `kg_contracts` ever makes a
  non-additive minor bump, this rule would over-accept. That is a contract-
  governance failure to catch upstream, not something the consumer can detect
  from a version string; the same assumption is what makes a minor bump legal.
- Two versions now differ in `contract_version` yet both pass validation, so
  the version string is no longer a proxy for "exactly the running contract".
  Audit records already carry the producer's version verbatim, so lineage is
  not lost.

### Risks

- Anyone relying on `ContractVersionRule` as an exact-match gate must now opt
  into `EXACT` mode explicitly. This is a behaviour change in the default, and
  it is called out in the changelog and ADR so the opt-in is discoverable.

## Impacted Areas

- [ ] Product
- [x] Domain model
- [ ] Data architecture
- [ ] AI architecture
- [ ] Domain-specific systems (see governance delta)
- [x] Integrations
- [ ] UX
- [ ] Security/privacy
- [x] Implementation
- [x] Documentation

## Related Documents

- `agentic-kgis` `kg_contracts.versioning.CONTRACT_VERSION` — the bump-site
  documentation of the additive nature of 2.0.0 → 2.1.0 → 2.2.0.
- `agentic-kgis` ADR-0022 / ADR-0023 and candidate 0011 — the compatible minor
  bumps this decision admits.
- `kgcs.validation` — the rule and the parse, stated in code.

## Related Issues / PRs

- Fixes agentic-kgcs issue #53 — "ContractVersionRule exact match will reject
  pre-upgrade ledger rows when kg_contracts goes 2.0.0 → 2.1.0".
- Owner decision, 2026-10-09: accept compatible minor versions.
- Related: agentic-kgps#1.

## Supersedes

None.

## Superseded By

None.
