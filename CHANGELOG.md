# Changelog

All notable changes to `agentic-kgcs` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- The durable semantic-audit ref tables (`semantic_audit_assertions`,
  `semantic_audit_operations`) now reject `INSERT OR REPLACE … (rowid, …)` on
  an existing row, which previously deleted it past the append-only `DELETE`
  trigger (#55). Existing databases gain the guard on their next open; the
  on-disk schema version stays 1, so older builds still open them.

### Added

- The curation planner now records **lineage pointers** on canonical
  assertions, the KGCS half of agentic-kgis ADR-0028 (contract
  `kg_contracts` 2.3.0). `CurationPlanner` sets
  `Assertion.source_candidate_ids = (candidate.candidate_id,)` on every
  `ATTACH_ASSERTION`, so a caller holding an assertion can reach the candidate
  ledger (and, on the structured path, the `candidate_id`-keyed evidence
  registry) with no scan. `ConceptEvolutionPlanner.plan_supersession` sets
  `Assertion.superseded_by = <new record id>` on the retired `SUPERSEDED` copy,
  so "what replaced this?" is answerable from the record. Neither field is in
  the ADR-0021 record seed, so adding or changing either never re-mints an
  `assertion_id`; an evolved record minted by `next_record` carries
  `source_candidate_ids=()` (its origin is the prior record, not a candidate).
- `AssertionSemanticAuditRecord.source_candidate_ids` names the candidate(s)
  the attached record was planned from — the assertion-side candidate lineage
  issue #48 deferred until the contract field landed (`()` for an evolved
  record).
- `kgcs.recuration.superseded_pointer(payload)` is the documented
  backward-compatibility reader for an already-persisted `RETRACT_ASSERTION`
  payload's untyped `superseded_by` key; it refuses a self-pointer.
- `ReplayInputs.rendered_evidence` captures the exact evidence block an adviser
  prompt carried (`RenderedEvidence`: rendered lines, relationships, and
  present/absent/error provenance), auto-filled by `SemanticAuditBuilder` from
  the adviser assessments. Replay rebuilds the identical prompt and request hash
  **without** the live `EvidenceLookup`/registry, so an evidence-rendered
  decision (prompt version `2+evidence`) is reproducible. The field is optional
  and defaults to `None`, so records written before it still load and replay as
  before. A rebuilt request with no matching recorded fixture is now reported as
  a `replay` divergence rather than raised.

### Changed

- `ContractVersionRule` now defaults to `ContractVersionMode.COMPATIBLE_MINOR`:
  a candidate is admitted when its `contract_version` shares the installed
  major and its minor is **not newer** (patch ignored), instead of requiring an
  exact match. A `kg_contracts` additive minor bump no longer rejects ledger
  rows written at an older 2.x — which, because the ledger keeps the first row
  per `semantic_key`, would otherwise strand them permanently and unrepairably.
  A different major, a newer minor than installed (producer ahead of consumer),
  and non-semver versions all still fail closed as `BAD_DATA`, with a reason
  naming both versions. Pass
  `contract_version_mode=ContractVersionMode.EXACT` (on the rule or on
  `default_validator`) to restore the old exact-match behaviour. (ADR-0024)

## [2.0.0] - 2026-10-05

This is a **major** release: it contains the source-breaking compensation change
of ADR-0018. The `1.1.0` queued for the ADR-0017 identity-strength change on
issue #31 was never tagged, because `main` acquired ADR-0018 before that bump
landed; the ADR-0017 change therefore ships inside this release. ADR-0018 states
why the major is required and explicitly rules out folding the break behind a
minor.

### Changed (BREAKING)

- `Compensator.compensate(plan)` now requires a keyword-only `against_snapshot`,
  the curation epoch the original plan committed at. Every compensating plan
  carries exactly one snapshot precondition rebased onto that epoch, so a
  compensation is evaluated against the state it expects to find now. There is
  no argument that yields an unguarded compensating plan; a non-epoch value
  raises `ValueError`. Callers must pass `ExecutionRecord.new_epoch`. (ADR-0018)
- `reversal_data` moved the inverse operation's payload under
  `INVERSE_PAYLOAD_KEY` (`inverse_payload`); `candidate_id`, `identity_id`,
  `term_id` and the trigger-provenance block stay flat. A reader that read a
  payload field flat off `reversal_data` must follow the key. The `Compensator`
  still falls back to the whole dict for un-migrated producers — that keeps
  generation working, it does not make an un-migrated producer's payload
  correct. (ADR-0018)
- `INVERSE_OPERATION` is now derived from
  `kg_contracts.INVERSE_OPERATION_TYPES` instead of a hand-maintained second
  table. `CREATE_IDENTITY` is compensable via `REVOKE_IDENTITY`. (ADR-0020)

### Changed

- Default entity-resolution outcomes: `DEFAULT_STRONG_NAMESPACES` is now
  `{doi, vin}`. ISSN, ISBN and ORCID no longer auto-link distinct works;
  identifier strength is entity-type relative through scoped namespaces.
  **Escape hatch** for adopters who need the previous set:
  `SharedStrongIdentifierRule(strong_namespaces=DEFAULT_STRONG_NAMESPACES | {"issn", "isbn", "orcid"})`.
  The *value* of the exported constant changed, so an adopter who explicitly
  wrote `strong_namespaces=DEFAULT_STRONG_NAMESPACES` is affected too. (ADR-0017)
- `ResolutionPolicy` derives the identity disposition from resolution facts
  before routing, so a `NEW_IDENTITY` candidate can reach `AUTO` with an absent
  `identity_confidence`. A stated low score and `UNRESOLVED` still block, and no
  threshold changed. (ADR-0022)
- `CurationPlanner` emits a per-subject `assertion_absent` precondition on
  `ATTACH_ASSERTION`, so a replayed attach is refused as `STALE` rather than
  silently re-applied. (ADR-0019)
- `assertion_id` is the record identity: it is derived from the record seed
  (object, valid period, evidence, origin) rather than the evidence-free
  `candidate_id`. Re-asserting a known fact with new evidence mints a new record,
  so supersession is representable; `plan_supersession` refuses self-, cross-fact
  and already-superseded supersession. (ADR-0021)

### Dependencies

- `agentic-kgis>=0.3.0` (was `>=0.2.0`): the first release that carries
  `IdentityDisposition` and `ConfidencePolicy.route(..., identity_disposition=)`,
  which `ResolutionPolicy` now requires. (ADR-0022)

## [1.0.0] - 2026-09-17

The completed KGCS v1 platform: deterministic curation core, transaction-aware
executor with compensation, ER substrate, cluster validation and resolution
policy, bounded LLM curation orchestrator and advisers, evidence-driven
re-curation, review API/queue/CLI, semantic audit/replay, and the KGIS-to-KGCS
end-to-end harness. Tagged at the merge of #29.
