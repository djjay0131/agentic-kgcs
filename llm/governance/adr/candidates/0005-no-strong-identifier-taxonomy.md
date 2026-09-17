# ADR candidate 0005: `kg_contracts` has no strong-identifier taxonomy

Status: Candidate (contract friction; not an accepted decision)
Date: 2026-08-22
Surfaced by: Wave 2 (ER 5a), `kgcs.er.normalize` / `kgcs.er.features`

## Context

Entity resolution's first stage normalizes entities and runs deterministic
identity rules: a shared *strong* identifier (DOI, ORCID, VIN, a source-native
primary key) is near-conclusive evidence that two entities are the same, and a
*contradicting* strong identifier is near-conclusive that they are not. The
flagship §7.4 behaviour (auto-link on identifier agreement, block a merge on
identifier contradiction) depends on knowing which namespaces are strong.

## Problem

An entity's external identifiers are `EntityRef{entity_type, namespace, key}`
aliases (`kg_contracts.identity`), all structurally equal. Nothing on the
contract marks a namespace as a near-unique strong identifier versus a weak or
descriptive one. So ER cannot tell "same DOI ⇒ almost certainly same paper"
from "same first-name namespace ⇒ almost nothing" without domain knowledge the
contract does not carry.

## Local workaround

`kgcs.er` ships a `DEFAULT_STRONG_NAMESPACES` set (doi, orcid, vin, isbn, …)
and treats agreement/contradiction on those namespaces as strong signals; the
set is injectable so an adopter can extend it. This is a KGCS-local heuristic,
honest about its provenance, but it duplicates knowledge that arguably belongs
with the identifier definition.

## Possible future contract improvement

Add an optional strength/uniqueness marker to the identifier namespace (e.g. a
registry of namespaces with a `uniqueness` class, or a flag on `EntityRef`'s
namespace vocabulary), so ER reads identifier strength from the contract rather
than from a KGCS-local list. Until then, treat `DEFAULT_STRONG_NAMESPACES` as a
configurable heuristic, not authority.
