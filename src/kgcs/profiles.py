"""DG-5 source/capability curation profiles (build plan §DG-5, Issue #2).

Different sources need different curation behaviour: an authoritative structured
registry is not a research-paper hypothesis, and neither is a code requirement
extracted from prose. Rather than hard-coding source special cases into the
policy, a `CurationProfile` *configures* the existing policy machinery for a
scope (graph / entity type / source).

The load-bearing DG-5 rule: **profiles configure the existing policy machinery;
they must not create alternate write paths.** A profile carries data (an
`IdentityAuthorityMode`, an `ErMode`, cost class, evidence requirements, an
embedded `ConfidencePolicy`, the set of auto-actions it permits) that the
deterministic gates read — it never becomes a second code path that merges or
writes on its own.

Two profile axes enforce Issue #2 / §9 law 13:

- `IdentityAuthorityMode.CLIENT_AUTHORITATIVE` (reject-only): the gate may reject
  a malformed reference but must **never** repair or merge the identity — the
  most ER can do is *propose* a `POSSIBLY_SAME_AS` link to the client. The
  `ErResolutionPolicy` reads this mode and can never emit `AUTO_LINK` for it.
- `ErMode.INERT`: ER does not run at all for this scope (e.g. a derived
  projection whose identities are already canonical); every pair is retained
  separate. The `projection_consumer` profile pairs this with a confidence
  policy that routes a confidence-1.0 projection straight to `AUTO`.

`allowable_auto_actions` holds `ErAction` *values* as strings, deliberately, so
this module stays independent of `kgcs.er.resolution` (which imports it); the
policy compares `ErAction.AUTO_LINK.value` against the set.
"""

from collections.abc import Sequence
from enum import StrEnum
from typing import Literal

from kg_contracts.policy import ConfidencePolicy
from pydantic import BaseModel, ConfigDict, Field

ReviewPriority = Literal["P1", "P2", "P3"]
"""Review SLA priority: P1=24h, P2=7d, P3=30d (spec §7.6)."""


def _default_review_sla() -> dict[str, ReviewPriority]:
    """The default review-SLA mapping (a P3 catch-all)."""
    return {"default": "P3"}


class IdentityAuthorityMode(StrEnum):
    """Who is entitled to change identity for a scope (spec §7.5, Issue #2).

    - `OPEN` — ER may auto-link and merge identities within its policy budget.
    - `ADVISORY` — ER may only *propose* links; it never merges autonomously.
    - `CLIENT_AUTHORITATIVE` — reject-only: the gate may reject a malformed
      reference but never repairs or merges the identity. The client owns
      identity; ER emits `POSSIBLY_SAME_AS` proposals, nothing more.
    """

    OPEN = "OPEN"
    ADVISORY = "ADVISORY"
    CLIENT_AUTHORITATIVE = "CLIENT_AUTHORITATIVE"


class ErMode(StrEnum):
    """How active entity resolution is for a scope.

    - `INERT` — ER does not run; pairs are retained separate (or abstained).
    - `ADVISORY` — ER runs and may propose, but never auto-links.
    - `ACTIVE` — ER runs and may auto-link within its policy budget.
    """

    INERT = "INERT"
    ADVISORY = "ADVISORY"
    ACTIVE = "ACTIVE"


class FalseMergeCostClass(StrEnum):
    """The consequence class of an erroneous merge (spec §7.4 cost matrix).

    A false merge contaminates every fact attached to the survivor, so higher
    classes make the deterministic gate *more conservative* about auto-linking —
    the same calibrated probability routes to review at `HIGH` where it would
    auto-link at `LOW`.
    """

    LOW = "LOW"
    STANDARD = "STANDARD"
    HIGH = "HIGH"


class ProfileScope(BaseModel):
    """The (graph, entity type, source) selector a profile applies to.

    A `None` field is a wildcard. `specificity` counts the pinned fields, so the
    registry can prefer the most specific matching profile deterministically.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    graph_id: str | None = None
    entity_type: str | None = None
    source: str | None = None

    def matches(
        self, *, graph_id: str, entity_type: str, source: str | None = None
    ) -> bool:
        """True iff every pinned selector equals the corresponding value."""
        return (
            (self.graph_id is None or self.graph_id == graph_id)
            and (self.entity_type is None or self.entity_type == entity_type)
            and (self.source is None or self.source == source)
        )

    @property
    def specificity(self) -> int:
        """The number of pinned (non-wildcard) selectors."""
        return sum(
            1 for selector in (self.graph_id, self.entity_type, self.source) if selector is not None
        )


class CurationProfile(BaseModel):
    """A source/capability curation profile (build plan §DG-5).

    Every field is *data* the existing deterministic gates read — the profile
    creates no new write path. `confidence_policy` is the embedded
    `ConfidencePolicy` the candidate-level `kgcs.policy.ResolutionPolicy` routes
    with; `allowable_auto_actions` (ER-action value strings) bounds what the
    `ErResolutionPolicy` may auto-apply for this scope.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_id: str = Field(min_length=1)
    version: str = "1"
    scope: ProfileScope = Field(default_factory=ProfileScope)
    identity_authority_mode: IdentityAuthorityMode = IdentityAuthorityMode.OPEN
    er_mode: ErMode = ErMode.ACTIVE
    required_evidence_count: int = Field(default=1, ge=0)
    required_evidence_kinds: tuple[str, ...] = ()
    false_merge_cost_class: FalseMergeCostClass = FalseMergeCostClass.STANDARD
    ontology_promotion_allowed: bool = False
    review_sla: dict[str, ReviewPriority] = Field(default_factory=_default_review_sla)
    allowable_auto_actions: frozenset[str] = Field(default_factory=frozenset)
    confidence_policy: ConfidencePolicy = Field(default_factory=ConfidencePolicy)


def default_profile() -> CurationProfile:
    """The permissive v1 fallback: OPEN authority, ACTIVE ER, STANDARD cost.

    It may auto-link (within the policy budget) and retain separate. Any scope
    needing tighter control is registered as a more specific profile.
    """
    return CurationProfile(
        profile_id="default",
        identity_authority_mode=IdentityAuthorityMode.OPEN,
        er_mode=ErMode.ACTIVE,
        false_merge_cost_class=FalseMergeCostClass.STANDARD,
        allowable_auto_actions=frozenset({"AUTO_LINK", "RETAIN_SEPARATE"}),
        review_sla={"default": "P3"},
    )


def projection_consumer_profile() -> CurationProfile:
    """A derived-projection consumer: confidence-1.0 → AUTO, ER inert (Issue #2 item 3).

    A projection re-emits already-canonical facts, so its confidence is 1.0 and
    the (default) `ConfidencePolicy` routes it straight to `AUTO`; its identities
    are already resolved, so ER is `INERT` and never re-links them.
    """
    return CurationProfile(
        profile_id="projection_consumer",
        scope=ProfileScope(source="projection"),
        identity_authority_mode=IdentityAuthorityMode.OPEN,
        er_mode=ErMode.INERT,
        false_merge_cost_class=FalseMergeCostClass.LOW,
        allowable_auto_actions=frozenset(),
        review_sla={"default": "P3"},
        confidence_policy=ConfidencePolicy(),
    )


def client_authoritative_profile(
    *,
    profile_id: str = "client_authoritative",
    scope: ProfileScope | None = None,
    false_merge_cost_class: FalseMergeCostClass = FalseMergeCostClass.HIGH,
) -> CurationProfile:
    """A reject-only profile: the client owns identity (Issue #2 / §9 law 13).

    `ADVISORY` ER so it can still *propose* `POSSIBLY_SAME_AS`, but
    `CLIENT_AUTHORITATIVE` authority so the gate can never auto-link or merge —
    and `allowable_auto_actions` is empty, belt-and-braces.
    """
    return CurationProfile(
        profile_id=profile_id,
        scope=scope or ProfileScope(),
        identity_authority_mode=IdentityAuthorityMode.CLIENT_AUTHORITATIVE,
        er_mode=ErMode.ADVISORY,
        false_merge_cost_class=false_merge_cost_class,
        allowable_auto_actions=frozenset(),
        review_sla={"default": "P1"},
    )


class ProfileRegistry:
    """Resolves the applicable `CurationProfile` for a (graph, type, source).

    Deterministic: among all matching profiles it returns the most specific one,
    breaking a specificity tie by the lexicographically largest `profile_id`;
    with no match it returns the configured `default`.
    """

    def __init__(
        self,
        profiles: Sequence[CurationProfile] = (),
        *,
        default: CurationProfile | None = None,
    ) -> None:
        self._profiles = tuple(profiles)
        self._default = default or default_profile()

    @property
    def default(self) -> CurationProfile:
        """The fallback profile returned when nothing more specific matches."""
        return self._default

    def resolve(
        self, *, graph_id: str, entity_type: str, source: str | None = None
    ) -> CurationProfile:
        """The applicable profile, most-specific first, or the default fallback."""
        matching = [
            profile
            for profile in self._profiles
            if profile.scope.matches(graph_id=graph_id, entity_type=entity_type, source=source)
        ]
        if not matching:
            return self._default
        return max(matching, key=lambda profile: (profile.scope.specificity, profile.profile_id))


def default_registry() -> ProfileRegistry:
    """A registry carrying the built-in `projection_consumer` profile + default."""
    return ProfileRegistry(profiles=(projection_consumer_profile(),), default=default_profile())
