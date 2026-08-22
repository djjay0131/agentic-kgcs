"""Stage 5 of entity resolution: cluster validation (spec §7.4 step 5).

Pairwise matches do **not** make valid clusters. If `A~B` and `B~C` but `A`
contradicts `C`, the transitive closure `{A, B, C}` is *invalid* — committing it
would merge two entities a strong identifier (or a disjoint valid period) proves
are different, contaminating every fact attached to the survivor. This is §9
law 7: pairwise similarity alone can never commit a cluster.

So a merge is validated against the *whole prospective membership*, not one
pair. `ClusterValidator` checks every internal pairwise relation (from the
matcher's `MatchResult`s) **and** every `ClusterConstraint`, and a single
contradiction blocks the whole merge. Each constraint is pure, deterministic,
and returns an explainable `ConstraintResult` (which members offended and why) —
never a silent rejection.

Concurrency is optimistic (spec §7.1): a decision is computed against a
`ClusterSnapshot` at a known `version`. If the live cluster has moved on, the
snapshot is *stale* and the decision must be invalidated rather than raced onto
a cluster that no longer looks the way the decision assumed. `ClusterSnapshot`
carries only `(cluster_id, version)` for exactly that check.

Nothing here mutates a graph or merges anything: validation returns a verdict,
and `select_survivor` names *which* member would survive a merge the policy gate
(stage 6) has separately authorised — by a fixed, documented rule so a replayed
merge picks the same survivor byte-for-byte.
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from kgcs.er.matcher import MatchResult
from kgcs.er.normalize import (
    FeatureAgreement,
    IdentityRule,
    NormalizedEntity,
    SharedStrongIdentifierRule,
    run_identity_rules,
)


class Cluster(BaseModel):
    """A prospective or committed identity cluster (spec §7.4 step 5).

    `members` are the stable member handles (`NormalizedEntity.source_key`s or
    canonical identity ids) — never whole models. `version` advances every time
    the committed cluster changes, and is what a `ClusterSnapshot` pins for
    optimistic concurrency. `graph_id`/`entity_type` are the cluster's declared
    tenant scope, checked against each member by `TenantBoundaryConstraint`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    cluster_id: str = Field(min_length=1)
    version: int = Field(default=0, ge=0)
    graph_id: str
    entity_type: str
    members: tuple[str, ...] = ()

    def snapshot(self) -> "ClusterSnapshot":
        """The `(cluster_id, version)` pin for optimistic concurrency."""
        return ClusterSnapshot(cluster_id=self.cluster_id, version=self.version)

    def with_member(self, member: str) -> "Cluster":
        """A prospective cluster adding `member` (idempotent; version unchanged).

        The version is deliberately *not* bumped: this is the hypothetical
        "what if we added M" membership the validator scores, not a commit.
        """
        if member in self.members:
            return self
        return self.model_copy(update={"members": (*self.members, member)})


class ClusterSnapshot(BaseModel):
    """A point-in-time pin of a cluster for optimistic-concurrency checks.

    A resolution decision records the snapshot it was computed against; before
    the decision is committed, `is_stale` re-checks it against the live cluster.
    A version (or id) mismatch means the cluster moved — the decision is stale
    and must be recomputed, never raced onto the changed cluster (spec §7.1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    cluster_id: str = Field(min_length=1)
    version: int = Field(ge=0)

    def is_stale(self, current: Cluster) -> bool:
        """True iff `current` is a different cluster or has advanced past this pin."""
        return current.cluster_id != self.cluster_id or current.version != self.version


class ConstraintResult(BaseModel):
    """The explainable verdict of one `ClusterConstraint` on one cluster.

    `ok=False` names the `offending` members and a human-readable `detail` so a
    rejected merge is always auditable — never a bare boolean.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    constraint: str
    ok: bool
    detail: str | None = None
    offending: tuple[str, ...] = ()


class ClusterValidation(BaseModel):
    """The whole-membership verdict the policy gate (stage 6) consumes.

    `valid` is true only when *no* internal pair is mutually exclusive and
    *every* constraint passed. `violations` carries each failure (including
    pairwise mutual-exclusion, reported under `pairwise_mutual_exclusion`), so
    the decision that rejects a merge can cite exactly what blocked it.

    `checked_pairs` vs `expected_pairs` makes coverage visible rather than
    silently trusted: `pairwise_complete` is False when a caller supplied fewer
    `MatchResult`s than the membership has internal pairs, so a consumer can
    tell "validated every pair" from "validated the pairs it was given" (the
    constraints still run over the whole membership regardless).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    cluster_id: str
    version: int
    valid: bool
    violations: tuple[ConstraintResult, ...] = ()
    checked_constraints: tuple[str, ...] = ()
    checked_pairs: int = 0
    expected_pairs: int = 0

    @property
    def pairwise_complete(self) -> bool:
        """True iff a `MatchResult` was supplied for every internal member pair."""
        return self.checked_pairs >= self.expected_pairs


@runtime_checkable
class ClusterConstraint(Protocol):
    """A pure, deterministic cluster-level rule returning an explainable verdict."""

    name: str

    def check(
        self, cluster: Cluster, entities: Mapping[str, NormalizedEntity]
    ) -> ConstraintResult: ...


# ---------------------------------------------------------------------------
# Temporal helpers (a cluster-level, tz-safe period-disjointness check)
# ---------------------------------------------------------------------------


def _strictly_before(a: datetime | None, b: datetime | None) -> bool:
    """`a < b`, but only when that comparison is actually well-defined.

    Returns `False` (disjointness *unproven*) when either bound is absent or the
    two datetimes disagree on tz-awareness — comparing a naive and an aware
    datetime raises in Python, and a producer's open `properties` can carry
    either. An unprovable disjointness must not crash; it simply is not proven
    (honest null over exception, §9 law 9). Mirrors the same guard in `features`.
    """
    if a is None or b is None:
        return False
    if (a.tzinfo is None) != (b.tzinfo is None):
        return False
    return a < b


def _provably_disjoint(a: NormalizedEntity, b: NormalizedEntity) -> bool:
    """True iff the two valid periods provably cannot overlap (open ends unbounded)."""
    return _strictly_before(a.valid_to, b.valid_from) or _strictly_before(b.valid_to, a.valid_from)


# ---------------------------------------------------------------------------
# Source derivation (for unique-source membership)
# ---------------------------------------------------------------------------


def default_source_of(entity: NormalizedEntity, *, separator: str = "/") -> str:
    """The source region of an entity: its `source_key` prefix up to the last
    separator (the whole key when there is none). Mirrors `SourceKeyChannel`."""
    head, sep, _tail = entity.source_key.rpartition(separator)
    return head if sep else entity.source_key


# ---------------------------------------------------------------------------
# Concrete constraints
# ---------------------------------------------------------------------------


class TemporalConsistencyConstraint:
    """Members' valid periods must be mutually compatible.

    Two members whose valid periods are provably disjoint cannot be the same
    entity existing over both — a cluster containing such a pair is rejected.
    """

    name = "temporal_consistency"

    def check(
        self, cluster: Cluster, entities: Mapping[str, NormalizedEntity]
    ) -> ConstraintResult:
        keys = sorted(k for k in set(cluster.members) if k in entities)
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                a, b = entities[keys[i]], entities[keys[j]]
                if _provably_disjoint(a, b):
                    return ConstraintResult(
                        constraint=self.name,
                        ok=False,
                        detail=(
                            f"valid periods of {keys[i]!r} and {keys[j]!r} are disjoint"
                        ),
                        offending=(keys[i], keys[j]),
                    )
        return ConstraintResult(constraint=self.name, ok=True)


class UniqueSourceConstraint:
    """At most one member per source, unless that source is allowlisted.

    An authoritative source does not list the same real-world entity twice, so
    two records from one source are (by default) distinct entities that must not
    be merged. `allow_multiple` names sources exempt from the rule; `source_of`
    derives a member's source (defaults to the `source_key` prefix).
    """

    name = "unique_source"

    def __init__(
        self,
        *,
        source_of: Callable[[NormalizedEntity], str] | None = None,
        allow_multiple: frozenset[str] = frozenset(),
    ) -> None:
        self._source_of = source_of or default_source_of
        self._allow_multiple = allow_multiple

    def check(
        self, cluster: Cluster, entities: Mapping[str, NormalizedEntity]
    ) -> ConstraintResult:
        seen: dict[str, str] = {}
        for key in sorted(k for k in set(cluster.members) if k in entities):
            source = self._source_of(entities[key])
            if source in self._allow_multiple:
                continue
            if source in seen:
                return ConstraintResult(
                    constraint=self.name,
                    ok=False,
                    detail=f"members {seen[source]!r} and {key!r} share source {source!r}",
                    offending=(seen[source], key),
                )
            seen[source] = key
        return ConstraintResult(constraint=self.name, ok=True)


class MutuallyExclusiveAttributeConstraint:
    """Contradicting strong identifiers (mutually exclusive attributes) block a cluster.

    Runs the identity rules pairwise over the membership; any `CONTRADICT` signal
    (e.g. two different DOIs, two different VINs) is positive evidence the pair
    cannot be the same entity, so the whole cluster is rejected — regardless of
    how similar their names or embeddings are (the §7.4 "same name, different
    entity" case, applied at cluster scope).
    """

    name = "mutually_exclusive_attribute"

    def __init__(self, *, rules: Sequence[IdentityRule] | None = None) -> None:
        self._rules: tuple[IdentityRule, ...] = (
            tuple(rules) if rules is not None else (SharedStrongIdentifierRule(),)
        )

    def check(
        self, cluster: Cluster, entities: Mapping[str, NormalizedEntity]
    ) -> ConstraintResult:
        keys = sorted(k for k in set(cluster.members) if k in entities)
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                a, b = entities[keys[i]], entities[keys[j]]
                for signal in run_identity_rules(self._rules, a, b):
                    if signal.agreement is FeatureAgreement.CONTRADICT:
                        return ConstraintResult(
                            constraint=self.name,
                            ok=False,
                            detail=(
                                f"{keys[i]!r} and {keys[j]!r} contradict on "
                                f"{signal.namespace}: {signal.detail}"
                            ),
                            offending=(keys[i], keys[j]),
                        )
        return ConstraintResult(constraint=self.name, ok=True)


class TenantBoundaryConstraint:
    """Members must share the cluster's graph/tenant and entity type.

    A cluster is scoped to one graph and one entity type (its purpose boundary);
    a member from a different graph or of a different type must never be merged
    across that boundary.
    """

    name = "tenant_boundary"

    def check(
        self, cluster: Cluster, entities: Mapping[str, NormalizedEntity]
    ) -> ConstraintResult:
        for key in sorted(k for k in set(cluster.members) if k in entities):
            entity = entities[key]
            if entity.graph_id != cluster.graph_id:
                return ConstraintResult(
                    constraint=self.name,
                    ok=False,
                    detail=(
                        f"member {key!r} is in graph {entity.graph_id!r}, "
                        f"cluster is {cluster.graph_id!r}"
                    ),
                    offending=(key,),
                )
            if entity.entity_type != cluster.entity_type:
                return ConstraintResult(
                    constraint=self.name,
                    ok=False,
                    detail=(
                        f"member {key!r} is type {entity.entity_type!r}, "
                        f"cluster is {cluster.entity_type!r}"
                    ),
                    offending=(key,),
                )
        return ConstraintResult(constraint=self.name, ok=True)


class IdentityAuthorityConstraint:
    """A client-authoritative member can never be *silently merged* (§9 law 13).

    When the prospective membership is a real merge (more than one member) and
    any member's identity is owned by a client-authoritative source, the merge is
    blocked here: ER may only *propose* the link (`POSSIBLY_SAME_AS`), never
    collapse the client's identity. `is_client_authoritative` is injected so this
    constraint stays profile-agnostic (the DG-5 profile supplies the predicate).
    """

    name = "identity_authority"

    def __init__(
        self, *, is_client_authoritative: Callable[[NormalizedEntity], bool]
    ) -> None:
        self._is_client_authoritative = is_client_authoritative

    def check(
        self, cluster: Cluster, entities: Mapping[str, NormalizedEntity]
    ) -> ConstraintResult:
        if len(set(cluster.members)) <= 1:
            return ConstraintResult(constraint=self.name, ok=True)
        protected = tuple(
            key
            for key in sorted(set(cluster.members))
            if key in entities and self._is_client_authoritative(entities[key])
        )
        if protected:
            return ConstraintResult(
                constraint=self.name,
                ok=False,
                detail=(
                    "client-authoritative members cannot be silently merged: "
                    f"{list(protected)!r}"
                ),
                offending=protected,
            )
        return ConstraintResult(constraint=self.name, ok=True)


# ---------------------------------------------------------------------------
# The validator
# ---------------------------------------------------------------------------


class ClusterValidator:
    """Validates a *whole prospective membership*, not a single pair (§9 law 7).

    Given a prospective `Cluster`, the member views, and the relevant pairwise
    `MatchResult`s, it checks two things and a single failure of either makes the
    cluster invalid:

    1. **Every internal pairwise relation** — any member pair whose `MatchResult`
       marks it `mutually_exclusive` is a contradiction (this is what stops an
       invalid transitive closure: `A~B`, `B~C`, but `A⊥C` fails here).
    2. **Every `ClusterConstraint`** — temporal, unique-source, mutually
       exclusive attributes, tenant boundary, identity authority.

    Pure and deterministic: members and constraints are visited in sorted order,
    so the same inputs yield the same `ClusterValidation` every time.
    """

    def __init__(self, constraints: Sequence[ClusterConstraint] = ()) -> None:
        self._constraints = tuple(constraints)

    def validate(
        self,
        cluster: Cluster,
        *,
        entities: Mapping[str, NormalizedEntity],
        match_results: Sequence[MatchResult] = (),
    ) -> ClusterValidation:
        """Return the whole-membership verdict for `cluster`."""
        violations: list[ConstraintResult] = []

        by_key: dict[tuple[str, str], MatchResult] = {
            result.pair.key: result for result in match_results
        }
        members = sorted(set(cluster.members))
        expected_pairs = len(members) * (len(members) - 1) // 2
        checked_pairs = 0
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                pair_key = (a, b) if a <= b else (b, a)
                result = by_key.get(pair_key)
                if result is None:
                    continue
                checked_pairs += 1
                if result.feature_vector.get("mutually_exclusive") == 1.0:
                    violations.append(
                        ConstraintResult(
                            constraint="pairwise_mutual_exclusion",
                            ok=False,
                            detail=(
                                f"match result marks {a!r} and {b!r} mutually exclusive "
                                f"(probability={result.probability})"
                            ),
                            offending=(a, b),
                        )
                    )

        checked_constraints: list[str] = []
        for constraint in self._constraints:
            checked_constraints.append(constraint.name)
            result_c = constraint.check(cluster, entities)
            if not result_c.ok:
                violations.append(result_c)

        return ClusterValidation(
            cluster_id=cluster.cluster_id,
            version=cluster.version,
            valid=not violations,
            violations=tuple(violations),
            checked_constraints=tuple(checked_constraints),
            checked_pairs=checked_pairs,
            expected_pairs=expected_pairs,
        )

    def validate_addition(
        self,
        cluster: Cluster,
        member: str,
        *,
        entities: Mapping[str, NormalizedEntity],
        match_results: Sequence[MatchResult] = (),
    ) -> ClusterValidation:
        """Validate the membership that would result from adding `member` to `cluster`."""
        return self.validate(
            cluster.with_member(member),
            entities=entities,
            match_results=match_results,
        )


def default_cluster_validator(
    *,
    is_client_authoritative: Callable[[NormalizedEntity], bool] | None = None,
    identity_rules: Sequence[IdentityRule] | None = None,
    allow_multiple_sources: frozenset[str] = frozenset(),
) -> ClusterValidator:
    """A validator wired with the five default constraints (spec §7.4 step 5).

    `IdentityAuthorityConstraint` is included only when a client-authoritative
    predicate is supplied — with no such sources there is nothing for it to
    protect.
    """
    constraints: list[ClusterConstraint] = [
        TenantBoundaryConstraint(),
        TemporalConsistencyConstraint(),
        UniqueSourceConstraint(allow_multiple=allow_multiple_sources),
        MutuallyExclusiveAttributeConstraint(rules=identity_rules),
    ]
    if is_client_authoritative is not None:
        constraints.append(
            IdentityAuthorityConstraint(is_client_authoritative=is_client_authoritative)
        )
    return ClusterValidator(constraints)


# ---------------------------------------------------------------------------
# Survivor selection
# ---------------------------------------------------------------------------


def select_survivor(
    members: Sequence[str],
    *,
    entities: Mapping[str, NormalizedEntity] | None = None,
) -> str:
    """Deterministically pick the surviving member of an *authorised* merge.

    This does not decide *whether* to merge (the policy gate does); it names the
    survivor once a merge is authorised, by a fixed, documented rule so a
    replayed merge always picks the same one:

    1. highest `source_reliability` (an absent reliability sorts lowest);
    2. tie → the most strong/complete identity (most distinct identifier values);
    3. tie → the lexicographically smallest `source_key` (a stable final
       tiebreak that needs no entity data).

    Raises `ValueError` on an empty membership — there is no survivor of nothing.
    """
    unique = sorted(set(members))
    if not unique:
        raise ValueError("cannot select a survivor from an empty membership")
    if entities is None:
        return unique[0]

    def rank(key: str) -> tuple[float, int, str]:
        entity = entities.get(key)
        if entity is None:
            # Unknown member: lowest reliability, no identifiers; the key still
            # provides the deterministic final tiebreak (negated below).
            return (float("-inf"), 0, key)
        reliability = (
            entity.source_reliability if entity.source_reliability is not None else float("-inf")
        )
        identifier_count = sum(len(values) for values in entity.identifiers.values())
        return (reliability, identifier_count, key)

    # Maximise reliability then identifier count; minimise the key. Sorting by
    # the key ascending and taking the max on (reliability, count) with the key
    # negated as a tiebreak is expressed directly with a stable sort:
    best = unique[0]
    best_rank = rank(best)
    for key in unique[1:]:
        candidate_rank = rank(key)
        # Higher reliability/count wins; on a tie the smaller key wins. Because
        # `unique` is ascending, the first-seen key is already the smallest, so
        # we only replace on a strictly greater (reliability, count).
        if candidate_rank[:2] > best_rank[:2]:
            best, best_rank = key, candidate_rank
    return best
