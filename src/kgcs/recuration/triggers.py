"""Evidence-driven re-curation triggers (Wave 5, DG-1, §9 law 11).

The design says new evidence can affect resolution and assertions, but leaves
the *reusable trigger model* for revisiting already-curated knowledge
undefined. DG-1 fills that gap with one KGCS-local, immutable domain type:
a `CurationTrigger` records **why** a piece of canonical knowledge should be
re-evaluated — never *what* to do about it. Deciding what to do is the job of
`targeting` (what could this affect?) and `evolution`/`ontology` (produce a
compensable `CurationPlan`).

Two disciplines are load-bearing (build plan §DG-1):

- **Triggers enqueue work; they never mutate canonical data.** A
  `CurationTrigger` is a frozen value object and `InMemoryTriggerQueue` holds
  only its own bookkeeping — it is handed no `GraphMutationStore`, no canonical
  mapping, nothing writable. There is structurally no path from enqueueing or
  handling a trigger to a canonical write.
- **Handling is idempotent and trace-linked.** A trigger's `trigger_id` is a
  deterministic content address of its *semantic* payload (kind, target refs,
  evidence, version context) — so the same re-curation request always collapses
  to one queue entry no matter how many times it is raised, and `mark_handled`
  is a no-op the second time. Every trigger carries a `trace_id`, so the plan
  that eventually changes a canonical conclusion traces back through the trigger
  to the evidence that motivated it (§9 laws 9, 11).

If a future need proves cross-repo, this type is the ADR candidate to promote
into `kg_contracts`; until then it stays local (build plan §DG-1).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class TriggerKind(StrEnum):
    """Why re-evaluation is requested (build plan §DG-1, the required classes).

    Each value names a distinct cause; none implies an action. The version-
    context fields relevant to a kind are carried on `CurationTrigger`.
    """

    NEW_EVIDENCE = "NEW_EVIDENCE"
    CONTRADICTION_DETECTED = "CONTRADICTION_DETECTED"
    SOURCE_AUTHORITY_CHANGED = "SOURCE_AUTHORITY_CHANGED"
    ONTOLOGY_OR_POLICY_VERSION_CHANGED = "ONTOLOGY_OR_POLICY_VERSION_CHANGED"
    MATCHER_MODEL_PROMPT_VERSION_CHANGED = "MATCHER_MODEL_PROMPT_VERSION_CHANGED"
    OPERATOR_REQUEST = "OPERATOR_REQUEST"


class VersionContext(BaseModel):
    """The version coordinates a trigger re-evaluates against (honest null).

    Every field is optional: a `NEW_EVIDENCE` trigger names none, while a
    `MATCHER_MODEL_PROMPT_VERSION_CHANGED` trigger names the new matcher/model/
    prompt version whose compatibility class forced re-evaluation. The context
    is part of a trigger's identity: the *same* targets re-evaluated at a
    *different* ontology version are a different trigger, so a version bump does
    not collapse into an earlier evidence trigger.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    matcher_version: str | None = None
    model_version: str | None = None
    prompt_version: str | None = None
    ontology_version: str | None = None
    policy_version: str | None = None
    source_authority: str | None = None

    def canonical(self) -> str:
        """A stable string of the set fields, for content-address hashing."""
        items = self.model_dump(mode="json")
        return "|".join(f"{key}={items[key] or ''}" for key in sorted(items))


class CurationTrigger(BaseModel):
    """An immutable record of *why* canonical knowledge should be revisited.

    Carries the affected target refs (canonical `identity_ids` / `assertion_ids`
    / conceptual `concept_keys`), the `evidence_ids` motivating the request, a
    human `reason`, the `trace_id` that links downstream plans back here, and the
    `version_context`. It never carries an operation, a plan, or a store — a
    trigger states a cause, not an effect.

    `trigger_id` is a deterministic content address of the trigger's *semantic*
    payload (kind + sorted target refs + sorted evidence + version context) and
    is what makes enqueueing idempotent. `reason` and `trace_id` are deliberately
    *excluded* from the address: two raisings of the same re-curation request
    (same targets, same evidence, same versions) are the same trigger however
    they are worded or correlated. Build with `CurationTrigger.of(...)`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    trigger_id: str = Field(min_length=1)
    kind: TriggerKind
    identity_ids: tuple[str, ...] = ()
    assertion_ids: tuple[str, ...] = ()
    concept_keys: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    reason: str = ""
    trace_id: str = ""
    version_context: VersionContext = Field(default_factory=VersionContext)

    @classmethod
    def of(
        cls,
        *,
        kind: TriggerKind,
        identity_ids: Sequence[str] = (),
        assertion_ids: Sequence[str] = (),
        concept_keys: Sequence[str] = (),
        evidence_ids: Sequence[str] = (),
        reason: str = "",
        trace_id: str = "",
        version_context: VersionContext | None = None,
    ) -> "CurationTrigger":
        """Build a trigger with a deterministic `trigger_id` from its content."""
        context = version_context or VersionContext()
        trigger_id = _derive_trigger_id(
            kind=kind,
            identity_ids=identity_ids,
            assertion_ids=assertion_ids,
            concept_keys=concept_keys,
            evidence_ids=evidence_ids,
            version_context=context,
        )
        return cls(
            trigger_id=trigger_id,
            kind=kind,
            identity_ids=tuple(identity_ids),
            assertion_ids=tuple(assertion_ids),
            concept_keys=tuple(concept_keys),
            evidence_ids=tuple(evidence_ids),
            reason=reason,
            trace_id=trace_id,
            version_context=context,
        )

    @property
    def target_refs(self) -> tuple[str, ...]:
        """All affected canonical/conceptual refs, in a fixed order."""
        return (*self.identity_ids, *self.assertion_ids, *self.concept_keys)


def _derive_trigger_id(
    *,
    kind: TriggerKind,
    identity_ids: Sequence[str],
    assertion_ids: Sequence[str],
    concept_keys: Sequence[str],
    evidence_ids: Sequence[str],
    version_context: VersionContext,
) -> str:
    """A stable `trg_…` content address of a trigger's semantic payload.

    Target refs and evidence are sorted before hashing so the address is
    order-independent — two requests over the same set collapse to one id.
    """
    seed = "::".join(
        (
            kind.value,
            "|".join(sorted(identity_ids)),
            "|".join(sorted(assertion_ids)),
            "|".join(sorted(concept_keys)),
            "|".join(sorted(evidence_ids)),
            version_context.canonical(),
        )
    )
    return "trg_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


@runtime_checkable
class TriggerQueue(Protocol):
    """Enqueue-only work intake for re-curation (never a canonical writer).

    The contract is deliberately tiny and mutation-free with respect to the
    graph: `enqueue` records a trigger (idempotently by `trigger_id`), `pending`
    lists the unhandled ones, and `mark_handled` retires one. Nothing here
    touches canonical data.
    """

    def enqueue(self, trigger: CurationTrigger) -> bool:
        """Record `trigger`; return True if newly added, False if a duplicate."""
        ...

    def pending(self, limit: int | None = None) -> tuple[CurationTrigger, ...]:
        """The not-yet-handled triggers, in insertion order."""
        ...

    def mark_handled(self, trigger_id: str) -> None:
        """Retire a trigger by id (idempotent; unknown ids are ignored)."""
        ...


class InMemoryTriggerQueue:
    """A reference `TriggerQueue`: pure bookkeeping, no canonical state.

    Holds two structures and *nothing writable to the graph*: an insertion-
    ordered map of `trigger_id → CurationTrigger` and a set of handled ids. This
    is what makes "a trigger never mutates canonical data" structural rather than
    a convention — the queue is handed no store to mutate.

    Idempotency is by `trigger_id`: enqueueing an id already present is a no-op
    (returns False) and never resurrects a handled trigger; `mark_handled` is
    idempotent. Deterministic: `pending` preserves first-seen order.
    """

    def __init__(self) -> None:
        self._triggers: dict[str, CurationTrigger] = {}
        self._handled: set[str] = set()

    def enqueue(self, trigger: CurationTrigger) -> bool:
        """Idempotent by `trigger_id`; enqueueing a known id changes nothing."""
        if trigger.trigger_id in self._triggers:
            return False
        self._triggers[trigger.trigger_id] = trigger
        return True

    def pending(self, limit: int | None = None) -> tuple[CurationTrigger, ...]:
        """Unhandled triggers in insertion order (optionally capped)."""
        items = tuple(
            trigger
            for trigger_id, trigger in self._triggers.items()
            if trigger_id not in self._handled
        )
        return items if limit is None else items[:limit]

    def mark_handled(self, trigger_id: str) -> None:
        """Retire `trigger_id`; unknown or already-handled ids are ignored."""
        if trigger_id in self._triggers:
            self._handled.add(trigger_id)

    def is_handled(self, trigger_id: str) -> bool:
        """Whether `trigger_id` has been retired."""
        return trigger_id in self._handled

    def get(self, trigger_id: str) -> CurationTrigger | None:
        """The trigger for `trigger_id`, or None if never enqueued."""
        return self._triggers.get(trigger_id)

    def all_triggers(self) -> tuple[CurationTrigger, ...]:
        """Every enqueued trigger (handled or not), in insertion order."""
        return tuple(self._triggers.values())


def merge_evidence(
    trigger: CurationTrigger, extra_evidence: Sequence[str] = ()
) -> tuple[str, ...]:
    """The trigger's evidence plus `extra`, deduplicated in first-seen order.

    The shared evidence provenance for any plan a trigger produces: every
    changed conclusion cites the evidence that justified it (§9 law 9).
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for evidence_id in (*trigger.evidence_ids, *extra_evidence):
        if evidence_id not in seen:
            seen.add(evidence_id)
            ordered.append(evidence_id)
    return tuple(ordered)


def trigger_provenance(
    trigger: CurationTrigger,
    *,
    matcher_version: str | None,
    adviser_version: str | None,
    policy_version: str,
    extra_evidence: Sequence[str] = (),
) -> dict[str, object]:
    """The provenance block stamped into every re-curation operation.

    Carries the originating `trigger_id`/kind, the cited `evidence_ids`, and the
    matcher/adviser/policy versions the decision was made under — so a
    compensator or auditor can trace a canonical change back to its cause
    (build plan §DG-3; §9 laws 8, 9, 11).
    """
    return {
        "trigger_id": trigger.trigger_id,
        "trigger_kind": trigger.kind.value,
        "trace_id": trigger.trace_id,
        "evidence_ids": list(merge_evidence(trigger, extra_evidence)),
        "matcher_version": matcher_version,
        "adviser_version": adviser_version,
        "policy_version": policy_version,
    }
