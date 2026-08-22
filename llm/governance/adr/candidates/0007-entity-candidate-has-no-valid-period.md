# ADR candidate 0007: `EntityCandidate` carries no `valid_period`

Status: Candidate (contract friction; not an accepted decision)
Date: 2026-08-22
Surfaced by: Wave 2 (ER 5a), `kgcs.er.features`

## Context

The flagship §7.4 mutual-exclusion example is temporal: "two athletes with
similar names appearing on different teams **at the same time**" must not be
merged. ER's typed features therefore need entity-level temporal extent to
compute temporal compatibility / mutual exclusion.

## Problem

`RelationCandidate` and `AttributeAssertionCandidate` carry a `valid_period`
(`kg_contracts.candidates`), but `EntityCandidate` does not. An entity's own
active window (an athlete's career span, an organization's lifetime) has no
typed home on the candidate, so ER must read it out of the untyped
`properties: dict[str, object]` — a per-producer convention with no schema —
or reconstruct it from attached relation/attribute candidates it may not have.

## Local workaround

The `temporal_compatible` / mutual-exclusion feature reads entity-level
temporal hints from `properties` when a producer supplies them under a known
key, and returns honest-null when absent. This works but is unschema'd and
producer-specific.

## Possible future contract improvement

Either add an optional `valid_period` (or `active_period`) to `EntityCandidate`
/ `CanonicalEntity`, or make it explicit that entity temporal extent is always
modelled at the relation/attribute level and provide ER a defined way to fold
those into an entity temporal view. Until then, entity-level temporal features
depend on a `properties` convention rather than a typed field.
