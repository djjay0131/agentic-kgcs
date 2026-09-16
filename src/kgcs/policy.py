"""Deterministic policy evaluation → `ResolutionDecision`.

Stage 2 of the core. It receives a candidate the validator already accepted
and produces the `ResolutionDecision` the planner will act on. Two questions
are answered here, both deterministically and both without touching a graph:

1. **Routing** — how much oversight does acting on this candidate need? This
   is delegated verbatim to `kg_contracts`' `ConfidencePolicy`, which maps
   the full `CandidateScores` set (never a single confidence) to
   `AUTO | LLM_ASSESS | HUMAN`. Thresholds live on that policy as data, so
   loosening automation later is config, not code (governance principle 6).

2. **Identity disposition** — does this candidate create a new identity,
   attach to a known one, or neither?

Sprint 1 has **no entity resolution** — no matcher, no graph read, no
embeddings. So disposition uses only what the candidate already carries:

- An `entity` candidate proposes a *new* identity. If it routes `AUTO`, this
  stage mints its identity id (via the injected `IdFactory`, so replay is
  byte-identical) and records `create_new_identity=True`. If it does not
  route `AUTO`, minting is deferred — the decision records no identity.
- A `relation` / `attribute_assertion` candidate *attaches* to an existing
  subject. If the candidate already carries that subject as a minted
  identity id, the subject is known and travels into the decision. If it
  carries an `EntityRef` alias instead, resolving it *requires* ER — so the
  route is escalated to at least `LLM_ASSESS` and no identity is claimed.
  This escalation is the one place routing is not confidence alone: a
  candidate can have flawless scores and still be un-auto-applicable simply
  because nobody has resolved its subject yet.
- An `artifact` candidate names no identity and no fact; it produces no
  canonical operation in v1 (see `kgcs.planner` and the ADR candidate on the
  missing artifact operation type), so its disposition is always "neither".

`matcher_version` is `None` throughout — honest null, not a fabricated
version, because no matcher ran. `snapshot_version` is the injected graph
snapshot the decision was computed against; with no graph read in Sprint 1
it is a configured constant, but it is carried so a later executor can detect
a stale plan.
"""

from kg_contracts.candidates import (
    AttributeAssertionCandidate,
    Candidate,
    RelationCandidate,
    SubjectRef,
)
from kg_contracts.curation import ResolutionDecision
from kg_contracts.identity import is_identity_id
from kg_contracts.policy import AdjudicationRoute, ConfidencePolicy

from kgcs.ids import DerivedIdFactory, IdFactory
from kgcs.scores import score_vector

# Route severity for escalation (mirrors kg_contracts' internal ordering,
# re-stated here so this module does not import a private helper). HUMAN
# demands the most oversight, AUTO the least.
_ROUTE_SEVERITY: dict[AdjudicationRoute, int] = {
    AdjudicationRoute.AUTO: 0,
    AdjudicationRoute.LLM_ASSESS: 1,
    AdjudicationRoute.HUMAN: 2,
}

DEFAULT_SNAPSHOT_VERSION = "0"
"""The empty-graph snapshot. Sprint 1 reads no graph, so every decision is
computed against the same configured snapshot unless one is injected."""


def _escalate(route: AdjudicationRoute, floor: AdjudicationRoute) -> AdjudicationRoute:
    """Return the more conservative of `route` and `floor`."""
    return route if _ROUTE_SEVERITY[route] >= _ROUTE_SEVERITY[floor] else floor


def _subject_refs(candidate: Candidate) -> tuple[SubjectRef, ...]:
    """The identity-bearing endpoints a candidate must resolve to be auto-applied.

    A relation has both a subject and an object; an attribute assertion has
    only a subject; entity and artifact candidates have neither (an entity
    *mints* its identity; an artifact names none).
    """
    if isinstance(candidate, RelationCandidate):
        return (candidate.subject, candidate.object)
    if isinstance(candidate, AttributeAssertionCandidate):
        return (candidate.subject,)
    return ()


def _known_identity(ref: SubjectRef) -> str | None:
    """The identity id a ref already names, or `None` if it needs resolution.

    A bare identity-id string is known; an `EntityRef` alias is not (resolving
    an alias to an identity is ER's job, absent in Sprint 1).
    """
    if isinstance(ref, str) and is_identity_id(ref):
        return ref
    return None


class ResolutionPolicy:
    """Maps a validated candidate to a deterministic `ResolutionDecision`.

    Injected with a `ConfidencePolicy` (routing thresholds as data) and an
    `IdFactory` (so a minted identity is replay-stable). Holds no mutable
    state and reads no graph — calling `resolve` twice on the same candidate
    yields equal decisions.
    """

    def __init__(
        self,
        *,
        confidence_policy: ConfidencePolicy | None = None,
        id_factory: IdFactory | None = None,
        snapshot_version: str = DEFAULT_SNAPSHOT_VERSION,
    ) -> None:
        self._confidence_policy = confidence_policy or ConfidencePolicy()
        self._id_factory = id_factory or DerivedIdFactory()
        self._snapshot_version = snapshot_version

    @property
    def policy_version(self) -> str:
        """The confidence policy's version, stamped through the pipeline."""
        return self._confidence_policy.policy_version

    @property
    def snapshot_version(self) -> str:
        """The graph snapshot every decision here is computed against."""
        return self._snapshot_version

    def resolve(self, candidate: Candidate) -> ResolutionDecision:
        """Produce the `ResolutionDecision` for one validated candidate."""
        route = self._confidence_policy.route(candidate.scores)
        resolved_identity, create_new_identity, route = self._dispose(candidate, route)
        return ResolutionDecision(
            candidate_id=candidate.candidate_id,
            resolved_identity=resolved_identity,
            create_new_identity=create_new_identity,
            route=route,
            score_vector=score_vector(candidate.scores),
            matcher_version=None,
            snapshot_version=self._snapshot_version,
            trace_id=candidate.trace_id,
        )

    def _dispose(
        self, candidate: Candidate, route: AdjudicationRoute
    ) -> tuple[str | None, bool, AdjudicationRoute]:
        """Decide identity disposition, possibly escalating the route.

        Returns `(resolved_identity, create_new_identity, route)`.
        """
        if candidate.candidate_kind == "entity":
            # A new identity: mint it only when the route already permits
            # auto-application; otherwise defer and claim no identity.
            if route is AdjudicationRoute.AUTO:
                identity = self._id_factory.identity_id(
                    candidate.graph_id, candidate.candidate_id
                )
                return identity, True, route
            return None, False, route

        refs = _subject_refs(candidate)
        if not refs:
            # An artifact (or any kind that attaches to nothing): no identity
            # disposition, route unchanged.
            return None, False, route

        # Attribute / relation: every endpoint must already be a known
        # identity, or the candidate needs ER and cannot auto-apply.
        if any(_known_identity(ref) is None for ref in refs):
            return None, False, _escalate(route, AdjudicationRoute.LLM_ASSESS)

        subject_identity = _known_identity(refs[0])
        return subject_identity, False, route
