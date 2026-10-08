"""Deterministic replay of a recorded decision (Wave 7, §9 law 17; issue #48).

Law 17: "curation audit is replayable enough to explain which baseline,
adviser, evidence, policy, and versions produced a decision." This module makes
that operational: given a semantic audit record, `replay` re-runs the decision
from the captured inputs through the *same* machinery the live path used, and
asserts the reproduced decision is byte-identical to the recorded one.

Two decision families, one entry point:

- `SemanticAuditRecord` (ER) replays through `CurationOrchestrator` — the
  deterministic baseline is a pure function of the captured `MatchResult` +
  `CurationProfile`, and the LLM arm is replayed through a
  `RecordedCompletionClient` carried by the injected `IdentityAdviser`. Same
  fixture + same inputs ⇒ same assessment ⇒ same fold ⇒ byte-identical
  `ErDecision` (`model_dump_json`).
- `AssertionSemanticAuditRecord` (assertion / re-curation) replays through the
  `ConceptEvolutionPlanner` + `EvolutionRouter` pair, rebuilt from the captured
  `AssertionReplayInputs`. The planner is pure, so the same inputs reproduce a
  byte-identical `EvolutionDecision` (kind, rationale, plan id, assertion ids).

A divergence is **detected and reported, never hidden**: `ReplayResult.reproduced`
is `False` with a `divergence` string when the replayed decision differs — for
example when the caller replays an adviser-influenced decision without supplying
the matching recorded fixtures. (A missing fixture surfaces as a
`CompletionMiss` from the adviser machinery — a wiring error, raised loudly, not
a silent mismatch.)

Because both record types are frozen and JSON-serializable, replay works on a
record read straight back from a durable sink (SQLite) exactly as on a live one.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from kgcs.advisers.orchestrator import CurationOrchestrator
from kgcs.advisers.specialists import IdentityAdviser
from kgcs.er.resolution import ErResolutionPolicy
from kgcs.observability.semantic_audit import (
    AssertionSemanticAuditRecord,
    SemanticAuditRecord,
    SemanticAuditRecordT,
)


class ReplayResult(BaseModel):
    """The outcome of replaying one semantic audit record.

    `reproduced` is `True` iff the replayed final decision serializes
    byte-identically to the recorded one. `divergence` is `None` on success and
    a human-readable diff summary otherwise — the audit's guarantee that a
    mismatch is surfaced, not swallowed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    reproduced: bool
    recorded_final_action: str
    replayed_final_action: str
    consulted_adviser: bool
    divergence: str | None = None


def replay(
    record: SemanticAuditRecordT,
    *,
    identity_adviser: IdentityAdviser | None = None,
    policy: ErResolutionPolicy | None = None,
) -> ReplayResult:
    """Re-run `record`'s decision from its captured inputs and compare.

    Dispatches on the record family: ER records replay through the orchestrator
    (needs the original `identity_adviser` fixtures for an adviser-influenced
    decision to reproduce); assertion records replay through the evolution
    planner + router. Returns a `ReplayResult` reporting reproduction and any
    divergence; it never mutates anything and never raises on a mismatch.
    """
    if isinstance(record, AssertionSemanticAuditRecord):
        return replay_assertion(record)
    return _replay_er(record, identity_adviser=identity_adviser, policy=policy)


def _replay_er(
    record: SemanticAuditRecord,
    *,
    identity_adviser: IdentityAdviser | None,
    policy: ErResolutionPolicy | None,
) -> ReplayResult:
    """ER-decision replay through the `CurationOrchestrator`."""
    orchestrator = CurationOrchestrator(
        policy=policy or ErResolutionPolicy(),
        identity_adviser=identity_adviser,
    )
    inputs = record.replay_inputs
    replayed = orchestrator.resolve(
        inputs.match_result,
        profile=inputs.profile,
        cluster_validation=inputs.cluster_validation,
        snapshot_stale=inputs.snapshot_stale,
        evidence_count=inputs.evidence_count,
        malformed=inputs.malformed,
        evidence_ids=inputs.evidence_ids,
        trace_id=inputs.trace_id,
    )

    recorded_json = record.final.model_dump_json()
    replayed_json = replayed.decision.model_dump_json()
    return _result(
        reproduced=recorded_json == replayed_json,
        recorded_final=record.final.action.value,
        replayed_final=replayed.decision.action.value,
        consulted_adviser=replayed.consulted,
        recorded_json=recorded_json,
        replayed_json=replayed_json,
    )


def replay_assertion(record: AssertionSemanticAuditRecord) -> ReplayResult:
    """Re-run an assertion / re-curation decision from its captured inputs.

    Rebuilds the `ConceptEvolutionPlanner` from the captured version coordinates
    and the `EvolutionRouter` (with no audit recorder — replay must not write),
    re-routes the captured recommendation, and compares the replayed
    `EvolutionDecision` byte-for-byte with the recorded one.
    """
    # Imported here, not at module scope: `kgcs.observability` is imported while
    # `kgcs.recuration` may be mid-initialisation (the evolution path imports no
    # observability at runtime, so this stays a one-way, lazy edge).
    from kgcs.observability.evolution_audit import evolution_decision_of
    from kgcs.recuration.evolution import ConceptEvolutionPlanner
    from kgcs.recuration.router import EvolutionRouter

    inputs = record.replay_inputs
    planner = ConceptEvolutionPlanner(
        snapshot_version=inputs.snapshot_version,
        policy_version=inputs.policy_version,
        matcher_version=inputs.matcher_version,
        adviser_version=inputs.adviser_version,
    )
    router = EvolutionRouter(planner=planner)
    replayed = router.route_assertion(
        recommendation=inputs.recommendation_enum(),
        old_assertion=inputs.old_assertion,
        new_assertion=inputs.new_assertion,
        trigger=inputs.trigger,
        supersession_allowed=inputs.supersession_allowed,
    )
    replayed_decision = evolution_decision_of(replayed)
    recorded_json = record.final.model_dump_json()
    replayed_json = replayed_decision.model_dump_json()
    return _result(
        reproduced=recorded_json == replayed_json,
        recorded_final=record.final.kind,
        replayed_final=replayed_decision.kind,
        consulted_adviser=bool(record.assessments),
        recorded_json=recorded_json,
        replayed_json=replayed_json,
    )


def _result(
    *,
    reproduced: bool,
    recorded_final: str,
    replayed_final: str,
    consulted_adviser: bool,
    recorded_json: str,
    replayed_json: str,
) -> ReplayResult:
    """Assemble a `ReplayResult`, formatting the divergence diff when it differs."""
    divergence = (
        None
        if reproduced
        else f"final decision diverged on replay; recorded={recorded_json} replayed={replayed_json}"
    )
    return ReplayResult(
        reproduced=reproduced,
        recorded_final_action=recorded_final,
        replayed_final_action=replayed_final,
        consulted_adviser=consulted_adviser,
        divergence=divergence,
    )
