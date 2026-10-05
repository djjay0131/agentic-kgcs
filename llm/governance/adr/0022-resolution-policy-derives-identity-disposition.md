# ADR-0022: `ResolutionPolicy` derives the identity disposition from resolution facts, before routing

Status: Accepted
Date: 2026-10-05

## Context

`agentic-kgis` 0.3.0 (commit `de48639`, ADR-0024 there) fixed the
`identity_confidence` AUTO deadlock. The fix has two halves:

- `IdentityDisposition` — `RESOLVED_EXISTING` (default) / `NEW_IDENTITY` /
  `UNRESOLVED` — names what resolution concluded about a candidate's identity,
  and `ConfidencePolicy.route(scores, identity_disposition=...)` accepts it.
- `allow_auto_for_new_identity` lets a `NEW_IDENTITY` candidate route `AUTO`
  with an *absent* `identity_confidence`: a brand-new identity has no link to
  be confident about, so demanding a resolution score for it is a category
  error. A *stated* low score is still enforced; `UNRESOLVED` still blocks.

**KGCS never passed it.** `kgcs.policy.ResolutionPolicy.resolve` called

```python
route = self._confidence_policy.route(candidate.scores)   # one argument
```

so `identity_disposition` took its `RESOLVED_EXISTING` default for every
candidate. Because neither KGIS, KGCS, nor `kg_eval` ever produces an
`identity_confidence` (KGIS structurally cannot: it holds no graph read
surface), the identity gate could never be satisfied for the real producer.
Measured downstream on 252 candidates at the published thresholds, nothing
lowered:

| path | AUTO | LLM_ASSESS |
|---|---:|---:|
| `kgcs.ResolutionPolicy` — the real producer | **0** | 252 |
| `route(scores, NEW_IDENTITY)` | 32 | 220 |
| `route(scores, <warranted disposition>)` | **8** | 244 |

The headline capability of the 0.3.0 release was unreachable from the only
producer that would use it.

It was also a closed cycle, not merely a missing argument. `_dispose` minted
an identity only when the route was *already* `AUTO`, so feeding the
decision's own disposition back into `route` could not break it:

```python
route = route(scores)                    # RESOLVED_EXISTING -> never AUTO
resolved, create_new, route = self._dispose(candidate, route)  # mints only if AUTO
# decision.identity_disposition() -> UNRESOLVED for a routed-away entity
```

The behaviour was strictly *stronger*, not unsafe: all 252 decisions came
back `UNRESOLVED`, which 0.3.0 blocks outright. Nothing that should have been
auto-applied was. The cost was that nothing could be auto-applied at all.

## Decision

### 1. Disposition is a resolution fact, computed before routing

`ResolutionPolicy` gains `identity_disposition(candidate) -> IdentityDisposition`
and `resolve` calls it **first**, then feeds the result to
`ConfidencePolicy.route`:

```python
def resolve(self, candidate: Candidate) -> ResolutionDecision:
    disposition = self.identity_disposition(candidate)   # facts, not the route
    route = self._confidence_policy.route(candidate.scores, disposition)
    resolved_identity, create_new_identity, route = self._dispose(candidate, route)
    ...
```

`_dispose` still owns minting and route escalation; it no longer *decides the
disposition input* to routing. The cycle is broken because the disposition is
a function of resolution facts and `_dispose` is a function of the route — the
dependency runs one way.

### 2. The warranted disposition in Sprint 1

This stage has no entity resolution (no matcher, no graph read, no
embeddings), so the facts are the candidate's kind and the form of its subject
references:

- `entity` → `NEW_IDENTITY`. No matching existing identity was found; this
  stage is where one is minted. A resolver wired in front of this stage would
  instead supply `RESOLVED_EXISTING` or `UNRESOLVED` — the seam ADR-0024
  anticipated, now named by the new method.
- `relation` / `attribute_assertion` whose every endpoint is already a minted
  identity id → `RESOLVED_EXISTING`. The link is known; the identity gate
  still demands a stated `identity_confidence`, so this path is unchanged.
- `relation` / `attribute_assertion` with an `EntityRef` alias endpoint →
  `UNRESOLVED`. Resolution has not decided which identity it is; an ambiguous
  or conflicting cluster is exactly this case. `AUTO` is blocked and the
  route is additionally floored at `LLM_ASSESS` in `_dispose`.
- `artifact` and any other kind that attaches to nothing → `RESOLVED_EXISTING`,
  the pre-ADR-0024 default. Such a candidate makes no identity claim, so it is
  gated exactly as it always was.

**Non-entity semantics are deliberately preserved, not redesigned.** An
assertion/relation with known identity ids remains `RESOLVED_EXISTING` (a
stated link confidence is still required, and an absent one still blocks); an
alias endpoint remains un-auto-applicable. The `_dispose` escalation for an
unresolved endpoint is retained as defense in depth: with
`require_identity_confidence_for_auto=False` the gate would otherwise permit
`AUTO` for an alias, and `_dispose` is the independent guard that prevents it.

### 3. No threshold value changes

No `ConfidencePolicy` field changes. The fix reads a disposition that already
existed into a parameter that already existed. A test pins the default
thresholds so a later "fix" cannot pass by loosening one.

## Rationale

The producer was the defect; `ConfidencePolicy.route` was always correct.
Deriving the disposition from the route it feeds is unrepresentable as a
correct function: it asks "did we link to an existing identity?" before
anything has decided whether to, and answers it with "yes" by default. Naming
the disposition as an input, computed from facts that exist before routing,
makes the question answerable and makes the call site testable — the
distinction issue #43 asks for ("an assertion that the producer's route
distribution responds to `IdentityDisposition` at all").

## Alternatives Considered

### Derive the disposition from `ResolutionDecision.identity_disposition()`

This is the tempting "use the contract's own projection" fix, and it is the
cycle. For a non-`AUTO` entity candidate the decision has
`create_new_identity=False` and `resolved_identity=None`, so
`identity_disposition()` returns `UNRESOLVED` — not `NEW_IDENTITY`. Routing
would then re-block it as `UNRESOLVED`, and the "regression" test in this PR
(a non-`AUTO` entity is still a `NEW_IDENTITY`) would fail. The projection is
correct for logging a *completed* decision; it is the wrong input to the gate
that produces the decision.

### Tell adopters to set `require_identity_confidence_for_auto=False`

This is what the E2E registry profile does, and it does unblock AUTO for new
identities. It also disables the identity gate *globally*: a
`RESOLVED_EXISTING` candidate with a low or absent `identity_confidence` would
then route `AUTO` too. That trades the intended, narrow exemption for a broad
one and silently drops a genuine protection. Rejected.

### Change an `auto_*` threshold

Issue #43 is explicit that nothing was lowered, and the brief for this change
forbids it. Rejected.

## Consequences

### Positive

- The real producer now emits `AUTO` for new identities, so kgis 0.3.0's AUTO
  fix is reachable (`CREATE_IDENTITY` plans are actually produced).
- The producer's route distribution responds to `IdentityDisposition`, which
  is now covered by tests that would have caught the original defect — tests
  that exercise `route()` directly could not, because `route()` was right.
- The disposition is a named, public method, so a future ER stage has one
  obvious place to supply `RESOLVED_EXISTING` / `UNRESOLVED`.

### Negative / Tradeoffs

- `ResolutionPolicy` now calls a method the contract added in 0.3.0, so the
  dependency floor moves from `>=0.2.0` to `>=0.3.0`. CI already installed
  `agentic-kgis @ main` (which carries `de48639`); the floor is now truthful
  rather than merely satisfied by a name match.
- A `NEW_IDENTITY` entity with extraction/source above the AUTO gates and no
  stated `identity_confidence` now auto-applies where it previously deferred
  to `LLM_ASSESS`. That is the intended behaviour change, and it is bounded:
  a stated low score still blocks, and `UNRESOLVED` still blocks.

### Risks

- Minting a new identity when an existing one was not matched is the classic
  false-split risk. In this stage no resolver ran, so there is no better
  disposition available; the risk is the one ADR-0024 accepted upstream. It
  remains bounded by the extraction/source gates and by the ability to
  compensate a `CREATE_IDENTITY` (ADR-0020).
- The dependency is pinned to `main` in CI rather than to a SHA. A future
  `kg_contracts` change to `IdentityDisposition` or `route` could surface
  here; that is the repo's existing cross-repo integration posture, not
  introduced by this change.

## Impacted Areas

- [ ] Product
- [x] Domain model
- [ ] Data architecture
- [ ] AI architecture
- [ ] Domain-specific systems (see governance delta)
- [ ] Integrations
- [ ] UX
- [ ] Security/privacy
- [x] Implementation
- [x] Documentation

## Related Documents

- `agentic-kgis` ADR-0024 — the `IdentityDisposition` / `allow_auto_for_new_identity`
  design this call site failed to use.
- `agentic-kgis` issue #47 — the contract-side question of what
  `resolved_identity` means together with `create_new_identity=True`; KGCS
  implements reading B ("the identity this candidate ends up attached to").
- `kgcs.policy` module docstring — the disposition derivation, stated in code.
- Design authority: `agentic-kgis/llm/specs/2026-07-09-kgis-kgcs-design.md`.

## Related Issues / PRs

- Fixes agentic-kgcs issue #43 — "ResolutionPolicy never passes
  IdentityDisposition, so kgis 0.3.0's AUTO fix is inert".
- Related upstream: agentic-kgis#47.

## Supersedes

None.

## Superseded By

None.
