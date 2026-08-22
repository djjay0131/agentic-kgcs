# ADR candidate 0006: no canonical name for an entity embedding

Status: Candidate (contract friction; not an accepted decision)
Date: 2026-08-22
Surfaced by: Wave 2 (ER 5a), `kgcs.er.blocking` / `kgcs.er.features`

## Context

ER uses embeddings in two places (spec §7.4): as an ANN *blocking channel*
(recall) and as one *feature* (embedding similarity). Both need to find "the"
embedding vector for an entity/candidate.

## Problem

A candidate carries `representations: dict[str, Representation]`
(`kg_contracts.candidates`) — a freeform map of named feature views, each
`text` or `vector`. There is no agreed key or typed slot for "the canonical
entity embedding". So the `EmbeddingChannel` and the embedding feature must
*guess* which representation is the embedding (currently: the first
`kind="vector"` representation by sorted key), which is fragile — two producers
can name the same thing differently, or a candidate can carry several vectors
with no signal for which one ER should use.

## Local workaround

`kgcs.er` selects the first `kind="vector"` representation in sorted-key order
and documents the heuristic. It degrades safely (no vector ⇒ the embedding
channel/feature simply contributes nothing — honest null), but pair scoring
across producers that name embeddings differently is not comparable.

## Possible future contract improvement

Define a convention (a reserved representation key, e.g. `"entity_embedding"`)
or a typed optional slot on the candidate for the canonical embedding plus its
model id, so ER selects the embedding exactly rather than heuristically. This
also lets calibration be keyed by embedding model version. Until then, treat
embedding selection as best-effort.
