# ADR-0017: Identifier strength is relative to the entity type, and container identifiers are scoped

Status: Proposed
Date: 2026-09-18

> **Numbering.** This is the first *standalone* local ADR file in this repo.
> It takes `0017` rather than `0001` because `adr/candidates/` already occupies
> `0001`–`0016`, and ten of those candidates carry accepted decisions in place
> (see `adr/README.md`). Reusing a candidate number for an unrelated decision
> would make every citation ambiguous.

## Context

`kgcs.er.normalize.DEFAULT_STRONG_NAMESPACES` is the list of identifier
namespaces `SharedStrongIdentifierRule` treats as near-conclusive identity
evidence. It shipped as `{doi, orcid, isbn, vin, issn}`. Agreement on any of
them emits `FeatureAgreement.AGREE`, which the `DefaultFeatureExtractor` turns
into `identifier_agreement = +1.0` — the heaviest positive weight in the
reference matcher (`+6.0`, against a `-2.5` intercept). Disagreement emits
`CONTRADICT`, which sets `mutually_exclusive` and hard-blocks a cluster.

ADR candidate 0005 records *why* this list exists: `kg_contracts` carries no
strong-identifier taxonomy, so ER must supply one locally. That candidate's
disposition — a KGCS-local, injectable, honest heuristic — is not in question
here. What is in question is the **content** of the default.

### Reproduction (measured on `v1.0.0`, `rules/1`, default thresholds)

Two *different* papers whose only shared identifier is an ISSN:

| left / right title | `name_similarity` | p | HIGH (budget 0.002) | STANDARD (budget 0.02) |
|---|---|---|---|---|
| "a survey of deep learning methods" / "…models" | 0.9752 | **0.998383** | **AUTO_LINK** | **AUTO_LINK** |
| "learning to rank for information retrieval" / "…for recommender systems" | 0.8729 | 0.997803 | LLM_ASSESS | **AUTO_LINK** |
| "attention is all you need" / "deep residual learning for image recognition" | 0.6296 | 0.995453 | LLM_ASSESS | **AUTO_LINK** |

With the ISSN signal removed, the same three pairs score 0.6048 / 0.5296 /
0.3518 and all route to `GATHER_MORE_EVIDENCE`.

Two readings of that table matter:

1. A shared ISSN alone supplies +6.0 of logit. At `STANDARD` the auto-link
   budget is cleared by any pair with `name_similarity > 0.13` — that is,
   essentially **any two English-titled papers in the same journal**.
2. At `HIGH` — the cost class an adopter selects precisely because a false
   merge is expensive — two unrelated survey papers still auto-linked, inside
   a documented 0.002 false-merge risk budget.

### The symmetric defect

The same rule emits `CONTRADICT` on *disjoint* ISSNs, which sets
`mutually_exclusive` and makes `MutuallyExclusiveAttributeConstraint` reject
the cluster outright. So one record of a paper carrying a print ISSN and
another carrying the electronic ISSN (or the journal ISSN versus a preprint
venue's) was positive evidence that they are **different papers**. The bad
default generates false non-merges as well as false merges.

### The general fault

An ISSN names a *journal*; an ISBN names a *book*. Neither names a *work*.
The defect is not that these namespaces are low-quality — as identifiers they
are excellent, and near-unique for the things they actually identify. The
defect is that identifier strength was modelled as a property of the
**namespace**, when it is a property of the **(namespace, entity type)** pair:
an identifier is strong evidence of identity only for the entity it names, and
is mere container metadata on anything else. `doi`, `orcid` and `vin` each name
one individual thing (a work, a person, a vehicle), so they are strong wherever
they legitimately appear; `issn` and `isbn` name containers, so on the contents
of those containers they are not.

This is wrong for every corpus of published works, not only for this
portfolio's paper corpus, so it is a platform defect rather than adopter
configuration.

## Decision

1. `DEFAULT_STRONG_NAMESPACES` becomes `{doi, orcid, vin}` — the namespaces
   that identify the entity carrying them, wherever they appear. The
   docstring states that membership test explicitly, so the next namespace
   added has to answer it.
2. A new `DEFAULT_CONTAINER_NAMESPACES` maps a container namespace to the
   entity types it genuinely identifies — `issn → {journal, serial,
   periodical, publicationvenue, venue}`, `isbn → {book, monograph,
   bookedition}`. `SharedStrongIdentifierRule` promotes such a namespace to
   full strength (both `AGREE` and `CONTRADICT`) for a pair **only when both
   sides carry one of those entity types**, compared casefolded and ignoring
   separators.
3. On any other entity type, a shared or disjoint container identifier yields
   an explicit `UNKNOWN` `IdentitySignal` naming the namespace and the reason.
   `UNKNOWN` is already the honest-null value: the feature extractor counts it
   as neither agreement nor contradiction, and the cluster constraint ignores
   it. The suppression is therefore recorded in the evidence trail rather than
   being invisible.
4. Both the strong set and the container mapping stay constructor-injectable.
   `container_namespaces={}` disables promotion; an adopter who genuinely
   wants the old behaviour passes `strong_namespaces=DEFAULT_STRONG_NAMESPACES
   | {"issn"}` and gets it explicitly.

## Rationale

The scoping rule is the only option that fixes the false merge *and* keeps the
evidence it destroys. A shared ISSN between two `Journal` entities is exactly
as conclusive as a shared DOI is between two papers; deleting `issn` from the
defaults would have thrown that away to fix an unrelated case, and would have
left the same trap for the next container-shaped namespace someone adds.

It also matches the module's stated discipline. `normalize.py` already insists
that identity rules produce *explainable evidence*, never silent merges, and
that absent evidence is `UNKNOWN` rather than a fabricated verdict. "This
identifier was present and deliberately not weighed, because it does not name
this kind of entity" is an evidence statement of exactly that shape.

The `UNKNOWN` signal is safe by construction: `_identifier_agreement` returns
`AGREE` only if some signal is `AGREE` and `CONTRADICT` only if some signal is
`CONTRADICT`, so an `UNKNOWN` cannot move a probability; and
`MutuallyExclusiveAttributeConstraint` reacts only to `CONTRADICT`. It changes
the audit trail, not the arithmetic.

Direction of change is conservative in the sense the gate is documented to be:
every affected pair moves *away* from auto-linking and toward gathering
evidence, except journal-on-journal matching, which is unchanged.

## Alternatives Considered

### Alternative 1 — remove `issn`/`isbn` from the strong set entirely

Simplest, and it fixes the reproduction. Rejected because it is a net loss of
correct behaviour: it silently downgrades the one case where these identifiers
*are* near-conclusive (resolving journals and books), and it leaves the
underlying model — strength as a property of the namespace alone — intact, so
the same bug returns the moment someone adds `issn` back for venue resolution
or introduces another container namespace.

### Alternative 2 — reclassify them as weak / corroborating evidence

Appealing, and directionally true: two papers in the same journal *are*
marginally more likely to be the same paper. Rejected for this change because
there is no weak channel in `PairFeatures` today — `identifier_agreement` is
three-valued and dominated by `CONTRADICT`, so "weak" would require a new
feature key. Adding one changes `FEATURE_KEYS`, every stored `feature_vector`,
and therefore replay comparability, for a second-order gain. That is a
separate, larger feature (a `shared_container` feature with a small weight),
not part of a defect fix, and it is strictly easier to add on top of this
decision than instead of it. Recorded as follow-up work rather than declined.

### Alternative 3 — keep them strong but make strength configurable

Already true: the set was injectable before this change, which is precisely
what makes the old default indefensible rather than defensible. A default that
is wrong for every corpus of published works is not rescued by being
overridable; the adopters who most need the correction are the ones least
likely to know they need it. Configurability is retained (and extended to the
container mapping) — it is just not the fix.

## Consequences

### Positive

- Two distinct papers sharing only an ISSN can no longer auto-link at any cost
  class; the measured worst case falls from p = 0.998383 to 0.6048.
- Two records of one paper carrying different ISSNs are no longer declared
  mutually exclusive, so the cluster constraint stops blocking legitimate
  merges.
- ISSN/ISBN matching for journals and books is unchanged and now explicitly
  tested in both directions.
- The membership rule for the strong set is written down, so the next addition
  is a decision rather than a reflex.

### Negative / Tradeoffs

- The default now embeds a small entity-type vocabulary (`journal`, `book`, …).
  That is more domain knowledge in KGCS than the namespace list alone carried,
  and it is unavoidable if scoped strength is to work out of the box. It stays
  a heuristic, not authority — ADR candidate 0005's framing is unchanged — and
  an adopter with a different type vocabulary passes its own mapping.
- An adopter whose `Journal` entities are typed something unrecognised loses
  ISSN strength silently until it supplies a mapping. The `UNKNOWN` signal
  makes that visible in the evidence trail rather than merely absent.
- `IdentitySignal` output is more verbose for container identifiers on
  non-container types.

### Risks

- Behaviour change on an adopter that (knowingly or not) relied on
  ISSN-driven auto-linking: previously auto-linked pairs now route to
  `GATHER_MORE_EVIDENCE` or `LLM_ASSESS`, increasing review volume. This is
  the intended correction — those merges were the defect — but it will show up
  as a throughput change, not only as a quality change.
- `v1.0.0` is tagged, and this changes default resolution behaviour. It is a
  defect fix, not a feature, but it is not source-compatible in *outcome* and
  should be released as a minor version with the change called out, not as a
  patch.

## Impacted Areas

- [ ] Product
- [x] Domain model
- [ ] Data architecture
- [x] AI architecture
- [x] Domain-specific systems (see governance delta)
- [ ] Integrations
- [ ] UX
- [ ] Security/privacy
- [x] Implementation
- [x] Documentation

## Related Documents

- `llm/governance/adr/candidates/0005-no-strong-identifier-taxonomy.md` — why
  the local list exists at all; this ADR corrects its contents, not its status.
- Design authority: `agentic-kgis/llm/specs/2026-07-09-kgis-kgcs-design.md`
  §7.4 (identity rules, cost matrix, calibration discipline).
- `src/kgcs/er/normalize.py`, `src/kgcs/er/features.py`,
  `src/kgcs/er/resolution.py`.

## Related Issues / PRs

- PR: `fix/container-identifier-strength`.

## Supersedes

None.

## Superseded By

None.
