# ADR-0017: Identifier strength is entity-type relative; scoped identifiers

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

### The general fault

An ISSN names a *journal*; an ISBN names a *book*; an ORCID names a *person*.
None of them names a *work*. The defect is not that these namespaces are
low-quality — as identifiers they are excellent, and near-unique for the things
they actually identify. The defect is that identifier strength was modelled as
a property of the **namespace**, when it is a property of the **(namespace,
entity type)** pair: an identifier is identity evidence only for the kind of
thing it names, and is mere metadata on anything else. An ISSN on a `Paper`
says where it appeared; an ORCID on a `Paper` says who wrote it. Neither says
*which* paper it is.

This is wrong for every corpus of published works, not only for this
portfolio's paper corpus, so it is a platform defect rather than adopter
configuration.

### A second axis, found in review: disagreement is not always decisive

The rule also emitted `CONTRADICT` on *disjoint* values, setting
`mutually_exclusive` and causing `MutuallyExclusiveAttributeConstraint` to
reject the cluster outright. That is correct only where the issuing registry
intends **one value per subject**. It does not hold for the container
namespaces:

- A journal normally carries a **print ISSN and an electronic ISSN** — which is
  exactly why ISSN-L was created. JMLR's `1532-4435` and `1533-7928` are one
  journal; pre-fix they scored **p = 0.000001**, `RETAIN_SEPARATE`, cluster
  rejected.
- A book normally carries an **ISBN-10 and an ISBN-13** (a checksum
  re-encoding of each other) plus a distinct ISBN per format. `0201896834` and
  `9780201896831` are one book; same result.

So the first revision of this change, which scoped ISSN/ISBN strength down to
`Journal`/`Book`, *concentrated* this false-non-merge risk onto exactly the
entity types where multi-valued identifiers are the norm. Strength therefore
needs two axes, not one: **what a namespace names**, and **whether the registry
issues one value per named thing**.

## Decision

Identifier strength is modelled on both axes, as data.

1. `DEFAULT_STRONG_NAMESPACES` becomes `{doi, vin}` — namespaces strong for
   *whatever* entity carries them, because their subject type is too open to
   enumerate (a DOI names any citable work: paper, dataset, chapter, software;
   a VIN names a vehicle). The docstring states the membership test explicitly,
   so the next namespace added has to answer it.
2. A new `NamespaceScope` model carries `subjects` (the entity types a
   namespace names, normalized by the now-exported `_type_key`) and
   `contradicts` (whether disjoint values are positive evidence of difference).
3. `DEFAULT_SCOPED_NAMESPACES` maps the type-bound namespaces:

   | namespace | subjects | `contradicts` |
   |---|---|---|
   | `orcid` | person, author, researcher, contributor, creator | `True` |
   | `issn` | journal, serial, periodical | `False` |
   | `isbn` | book, monograph, bookedition | `False` |

   `SharedStrongIdentifierRule` treats a scoped namespace as strong for a pair
   only when **both** sides carry one of its subjects.
4. On any other entity type, a shared or disjoint scoped identifier yields an
   explicit `UNKNOWN` `IdentitySignal` naming the namespace, its subjects, and
   the observed types. `UNKNOWN` is already the honest-null value: the feature
   extractor counts it as neither agreement nor contradiction, and the cluster
   constraint ignores it. The suppression is therefore recorded in the evidence
   trail rather than being invisible.
5. `contradicts=False` means a namespace can reach `AGREE` but never
   `CONTRADICT`: agreement still proves identity while disagreement proves
   nothing. Disjoint values yield `UNKNOWN` with a detail saying why.
6. `DefaultFeatureExtractor._attribute_rarity` now excludes namespaces the
   rules suppressed with an `UNKNOWN` signal. Without this the same shared ISSN
   re-entered the score through `attribute_rarity` (weight `+2.0`) after the
   signal channel had refused it — measured at p = 0.918754 versus 0.604806
   with a `rarity_index` injected. Namespaces the rules say nothing about are
   still counted: rarity is precisely the channel for a shared *weak*
   identifier, and scoping must not gut it.
7. Both the strong set and the scope mapping stay constructor-injectable.
   `scoped_namespaces={}` disables scoping; membership in `strong_namespaces`
   outranks any scope for the same namespace, which is the one-argument escape
   hatch for an adopter who knows its ingest attaches a namespace only to the
   entity it names.

### Why `venue` and `conference` are not ISSN subjects

The first revision listed `venue` and `publicationvenue` as ISSN subjects.
Review showed this reintroduced the original defect one type over: a
proceedings **series** carries a single ISSN across unrelated conferences —
LNCS `0302-9743`, CEUR-WS `1613-0073`, PMLR `2640-3498` — and conference names
share heavy boilerplate ("international conference on …"), so `name_similarity`
reliably clears the auto-link floor. Two different conferences typed `Venue`
sharing the LNCS ISSN measured **p = 0.998028, AUTO_LINK at HIGH**. An ISSN
identifies a *serial*, and a venue is not reliably a serial. Only `journal`,
`serial` and `periodical` are subjects.

## Rationale

The scoping rule is the only option that fixes the false merge *and* keeps the
evidence it destroys. A shared ISSN between two `Journal` entities is exactly
as conclusive as a shared DOI is between two papers; deleting `issn` from the
defaults would have thrown that away to fix an unrelated case, and would have
left the same trap for the next type-bound namespace someone adds. The `orcid`
case proves the point: it was already in the strong set, already wrong for the
same reason, and the mechanism absorbed it as three lines of data rather than
new code.

The two-axis model also matches the module's stated discipline. `normalize.py`
already insists that identity rules produce *explainable evidence*, never
silent merges, and that absent evidence is `UNKNOWN` rather than a fabricated
verdict. "This identifier was present and deliberately not weighed, because it
does not name this kind of entity" and "these values differ, but one journal
legitimately holds several" are both evidence statements of exactly that shape.

The `UNKNOWN` signal is safe by construction: `_identifier_agreement` returns
`AGREE` only if some signal is `AGREE` and `CONTRADICT` only if some signal is
`CONTRADICT`, so an `UNKNOWN` cannot move a probability; and
`MutuallyExclusiveAttributeConstraint` reacts only to `CONTRADICT`. With the
`attribute_rarity` filter added, the claim that a suppressed identifier is "not
weighed as identity evidence" is now true of the whole pipeline rather than of
one channel.

Replay comparability is preserved: `FEATURE_KEYS` and `PairFeatures.to_vector`
are untouched, and `replay()` re-routes a *stored* `MatchResult` without
re-extracting features, so previously recorded decisions replay bit-identically.

## Alternatives Considered

### Alternative 1 — remove `issn`/`isbn` from the strong set entirely

Simplest, and it fixes the reproduction. Rejected because it is a net loss of
correct behaviour: it silently downgrades the one case where these identifiers
*are* near-conclusive (resolving journals and books), and it leaves the
underlying model — strength as a property of the namespace alone — intact, so
the same bug returns the moment someone adds `issn` back for venue resolution
or introduces another type-bound namespace. The `orcid` finding confirms this
would have been the wrong call.

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
scope mapping) — it is just not the fix.

### Alternative 4 — make `contradicts` value-count-sensitive instead of a flag

Considered for the ISSN/ISBN case: emit `CONTRADICT` only when both sides are
*single-valued* and disjoint, on the theory that a record listing one ISSN has
asserted it exclusively. Rejected as a false inference — a record carrying only
the print ISSN has not asserted the absence of an e-ISSN, it has simply not
listed it, which is the overwhelmingly common case in practice. The flag states
the registry's actual semantics; the value count states only how complete one
record happens to be.

## Consequences

### Positive

- Two distinct papers sharing only an ISSN can no longer auto-link at any cost
  class; the measured worst case falls from p = 0.998383 to 0.604806.
- Two different papers by the same author sharing only an ORCID fall from
  p = 0.998383 / AUTO_LINK to 0.604806 / `GATHER_MORE_EVIDENCE`.
- Two different conferences sharing a proceedings-series ISSN fall from
  p = 0.998028 / AUTO_LINK to 0.556401 / `GATHER_MORE_EVIDENCE`.
- One journal's print and electronic ISSNs, and one book's ISBN-10 and
  ISBN-13, no longer contradict: p = 0.000001 / cluster-rejected becomes the
  score the pair would have had with no identifier at all, and the cluster
  passes.
- A suppressed identifier cannot re-enter through `attribute_rarity`.
- ISSN/ISBN matching for journals and books, and ORCID matching for people, are
  unchanged (a `Journal` pair sharing an ISSN measures p = 0.996727 before and
  after) and are now explicitly tested in both directions.
- The membership rule for the strong set is written down, so the next addition
  is a decision rather than a reflex.

### Negative / Tradeoffs

- The default now embeds a small entity-type vocabulary (`journal`, `book`,
  `person`, …). That is more domain knowledge in KGCS than the namespace list
  alone carried, and it is unavoidable if scoped strength is to work out of the
  box. It stays a heuristic, not authority — ADR candidate 0005's framing is
  unchanged — and an adopter with a different type vocabulary passes its own
  mapping.
- An adopter whose `Journal` or `Person` entities are typed something
  unrecognised loses strength silently until it supplies a mapping. The
  `UNKNOWN` signal makes that visible in the evidence trail rather than merely
  absent.
- `IdentitySignal` output is more verbose for scoped identifiers on
  non-subject types.
- **`contradicts=False` gives up the only hard evidence separating two
  *different* journals or books.** This is the direct cost of curing the
  print-vs-e-ISSN false non-merge, and it is not small. Measured on two
  genuinely different journals with disjoint ISSNs (J. Phys. A `1751-8113` vs
  J. Phys. B `0953-4075`):

  | shared affiliations | before (CONTRADICT) | after (`contradicts=False`) |
  |---|---|---|
  | 0 | p = 0.000001, `RETAIN_SEPARATE`, cluster rejected | p = 0.528353, `GATHER_MORE_EVIDENCE` |
  | 6 | p = 0.000019, `RETAIN_SEPARATE`, cluster rejected | p = 0.957448, `LLM_ASSESS` |
  | 12 | p = 0.000376, `RETAIN_SEPARATE`, cluster rejected | p = 0.997792, **`AUTO_LINK` at STANDARD** |

  Before, the ISSN alone separated them at every cost class and the cluster
  constraint refused the merge outright. Now they are judged on soft evidence,
  and a crowded shared-affiliation set carries them to an auto-link.

  **Why it is still the right trade.** The two errors are not symmetric. The
  old behaviour was wrong on the *common* case — a journal carrying both its
  print and electronic ISSN is the norm, not the exception, which is precisely
  why ISSN-L exists — and it failed *closed* in a way no amount of other
  evidence could reopen, silently splitting one journal forever. The new
  behaviour is wrong only on a *conjunction*: two different journals, disjoint
  ISSNs, similar names, **and** a dozen shared affiliations. It also fails
  *open*, into a routing decision a human or adviser can still catch, and HIGH
  cost class still refuses to auto-link it (verified: `LLM_ASSESS`, not
  `AUTO_LINK`).

  **Mitigations, in order of preference.** (a) Run journal resolution under
  `FalseMergeCostClass.HIGH`, which declines the auto-link on its own. (b) Add
  an ISSN-L authority at normalization so a journal's print and electronic
  ISSNs collapse to one value — then `contradicts=True` becomes correct again
  for `issn` and can be restored per-adopter through `NamespaceScope`. (c) A
  cluster-level constraint expressing "these two ISSNs are registered to
  different titles", which is real negative evidence rather than an inference
  from disjointness. Not (d) restoring `CONTRADICT`, which re-breaks the
  common case to patch the rare one.
- Blocking is unchanged and still fans out quadratically on a shared ISSN
  (`ExactIdentifierChannel.block_keys` emits a key per identifier), so every
  pair of papers in a journal is still *proposed*. Those pairs now reliably
  produce `UNKNOWN` and are wasted work. Blocking is deliberately recall-biased
  so this is not a correctness bug, but the cost is real on a bibliographic
  corpus — see the follow-up issue.

### Risks

- Behaviour change on an adopter that (knowingly or not) relied on ISSN- or
  ORCID-driven auto-linking: previously auto-linked pairs now route to
  `GATHER_MORE_EVIDENCE` or `LLM_ASSESS`, increasing review volume. This is
  the intended correction — those merges were the defect — but it will show up
  as a throughput change, not only as a quality change.
- **The *value* of an exported constant changed.** An adopter who explicitly
  wrote `SharedStrongIdentifierRule(strong_namespaces=DEFAULT_STRONG_NAMESPACES)`
  believing they had pinned behaviour silently gets the new set. Source-
  compatible, outcome-incompatible — the careful adopter is affected exactly
  like the default one, and only a release note reaches them.
- `v1.0.0` is tagged, and this changes default resolution behaviour. It is a
  defect fix, not a feature, but it should be released as a **minor** version
  with the change called out, not as a patch.

### The one missing capability behind three of these problems

Worth recording explicitly, because it is easy to file these as three
unrelated deferrals and miss that they are one gap.

`PairFeatures` has a **three-valued** `identifier_agreement` in which
`CONTRADICT` dominates absolutely (`features._identifier_agreement`), plus a
`mutually_exclusive` flag that hard-blocks a cluster. There is no channel for
*weak negative* evidence — no way to say "this slightly argues against a match"
as distinct from "this proves they differ" or "this says nothing". Every
identifier signal must therefore be decisive or silent.

That single gap is what forces:

- **MAJOR-A above.** Disjoint ISSNs on two journals are genuinely mild
  negative evidence. With only `CONTRADICT` and `UNKNOWN` available, the choice
  was "hard-block the common print/e-ISSN case" or "say nothing"; there was no
  third option, so the trade had to be made at all.
- **The deferred DOI problem (issue #32).** A preprint DOI and a published DOI
  are disjoint but name one work. Same shape, same forced choice.
- **Alternative 2 above** (weak/corroborating positive evidence for a shared
  container identifier), declined for the same structural reason in the other
  direction.

The fix for all three is one feature: a small-weight, non-dominating evidence
channel — signed, so it serves both directions. It is deferred, not declined,
and for a concrete reason: adding a key changes `FEATURE_KEYS` and therefore
every stored `feature_vector`, breaking replay comparability against decisions
recorded under v1. That is a migration, not a defect fix, and it wants its own
ADR. Whoever picks up #32 should treat it as this capability, not as a DOI
special case — and should expect MAJOR-A's mitigation to fall out of the same
work.

### Known remaining exposure (deliberately not fixed here)

- **`doi` has the mirror problem.** A preprint DOI and a published DOI name the
  *same* work but are disjoint, so `CONTRADICT` hard-blocks the merge — the
  same class as the ISSN/ISBN case above. It is not fixed here because the
  design spec §7.4 names "two different DOIs" as its canonical example of a
  contradiction and an existing test pins that behaviour; flipping it is a
  spec-level decision, not a defect fix. Tracked as a follow-up issue.
- **Any namespace an ingester attaches to something it does not name.** Whether
  the membership test holds is partly a property of the ingest pipeline, not
  only of the namespace. `DEFAULT_SCOPED_NAMESPACES` fixes the three known
  cases; a novel one would need the adopter to declare it.
- **`scoped_namespaces={}` is a wider opt-out than it looks.** It disables the
  `attribute_rarity` suppression too, because that filter keys off the
  `UNKNOWN` signal the scoping emits (measured: a shared ISSN between two
  papers returns to rarity-eligible, p = 0.918754 with a `rarity_index`). The
  semantics are kept — an empty mapping is the only way to express "no
  scoping", and re-reading it as "use defaults" would leave no way to opt out —
  but it is documented on the constructor and pinned by a test rather than
  left to be discovered.
- **Scoping a namespace that is also in `strong_namespaces` is a silent
  no-op.** Strong membership wins, which is what makes the restore-old-
  behaviour hatch a single argument. Documented on the constructor; not made an
  error, because the overlap is exactly how the hatch is expressed.

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

- PR #30 (`fix/container-identifier-strength`) — this change.
- Follow-ups filed from PR #30 review: release-version bump; DOI
  preprint-vs-published contradiction; ISSN blocking fan-out.

## Supersedes

None.

## Superseded By

None.
