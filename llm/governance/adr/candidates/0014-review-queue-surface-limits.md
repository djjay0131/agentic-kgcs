# ADR candidate 0014: the `ReviewQueue` contract can't express typed outcomes or a typed review case

Status: Accepted (KGCS-local durable decision, v1 — 2026-09-17)
Date: 2026-08-22
Surfaced by: Wave 6 (review API/queue), `kgcs.review`

## Context

Wave 6 implements a persistent `ReviewQueue` (spec §7.6) and must (law 15)
keep retryable and permanent failures distinct and never silently drop work,
and (law 14) route a human decision through the same typed pipeline as an
automated one, carrying rich review context (proposed plan, adviser
assessments, risk/value, SLA).

## Problem

Two shape limits in the frozen contract:

1. The `ReviewQueue` Protocol fixes `enqueue -> str` and `resolve -> None`, so
   the retryable-vs-permanent distinction can only surface as **typed
   exceptions**, never a typed result a caller can branch on without
   `try/except`.
2. `ReviewItem.payload` is an opaque `dict[str, object]`, so KGCS review
   enrichment — the `ReviewCase` (snapshot ref, proposed `CurationPlan`,
   adviser assessments, risk/value, SLA, trace) — must live *inside* that dict
   rather than as first-class, validated fields.

## Local workaround

`kgcs.review` raises a typed failure hierarchy (`RetryableQueueError` /
`PermanentQueueError`, both carrying a `retryable` flag) and stores a frozen,
fully-serializable `ReviewCase` inside `ReviewItem.payload`, round-tripping it
via its own `model_dump(mode="json")`/`model_validate`. This is clean and
serializable, but the typed outcome and the review-case shape are KGCS-local
conventions, not contract-guaranteed.

## Possible future contract improvement

If the review surface proves cross-repo, consider a typed `ReviewQueue`
outcome object (so callers branch without exceptions) and a first-class
review-case surface on `ReviewItem` (or a typed variant) in `kg_contracts`.
Until then, the payload convention + typed exceptions are the KGCS contract.

## Disposition (v1 completion, 2026-09-17)

PROMOTE — a durable KGCS-local decision; the frozen contract added nothing to resolve it. Accepted for v1.

See `llm/governance/kgcs-v1-completion-reconciliation.md` §2 for the full matrix.
