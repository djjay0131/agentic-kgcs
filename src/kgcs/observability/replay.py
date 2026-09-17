"""Deterministic replay of a recorded decision (Wave 7, §9 law 17).

Law 17: "curation audit is replayable enough to explain which baseline,
adviser, evidence, policy, and versions produced a decision." This module makes
that operational: given a `SemanticAuditRecord`, `replay` re-runs the decision
from the captured `ReplayInputs` through the *same* `CurationOrchestrator` the
live path used, and asserts the reproduced final decision is byte-identical to
the recorded one.

Determinism is total: the orchestrator's deterministic baseline is a pure
function of the captured `MatchResult` + `CurationProfile`, and the LLM arm is
replayed through a `RecordedCompletionClient` (carried by the injected
`IdentityAdviser`). Same fixture + same inputs ⇒ same assessment ⇒ same fold ⇒
byte-identical `ErDecision` (`model_dump_json`).

A divergence is **detected and reported, never hidden**: `ReplayResult.reproduced`
is `False` with a `divergence` string when the replayed decision differs — for
example when the caller replays an adviser-influenced decision without supplying
the matching recorded fixtures. (A missing fixture surfaces as a
`CompletionMiss` from the adviser machinery — a wiring error, raised loudly, not
a silent mismatch.)
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from kgcs.advisers.orchestrator import CurationOrchestrator
from kgcs.advisers.specialists import IdentityAdviser
from kgcs.er.resolution import ErResolutionPolicy
from kgcs.observability.semantic_audit import SemanticAuditRecord


class ReplayResult(BaseModel):
    """The outcome of replaying one `SemanticAuditRecord`.

    `reproduced` is `True` iff the replayed final `ErDecision` serializes
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
    record: SemanticAuditRecord,
    *,
    identity_adviser: IdentityAdviser | None = None,
    policy: ErResolutionPolicy | None = None,
) -> ReplayResult:
    """Re-run `record`'s decision from its captured inputs and compare.

    Reconstructs a `CurationOrchestrator` from the injected `policy` (default:
    `ErResolutionPolicy()` with default thresholds — pass the original policy if
    it used custom `ErRoutingThresholds`) and the `identity_adviser` (which must
    carry the same `RecordedCompletionClient` fixtures for an adviser-influenced
    decision to reproduce). Returns a `ReplayResult` reporting reproduction and
    any divergence; it never mutates anything and never raises on a mismatch.
    """
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
    reproduced = recorded_json == replayed_json
    divergence = (
        None
        if reproduced
        else (
            "final decision diverged on replay; "
            f"recorded={recorded_json} replayed={replayed_json}"
        )
    )
    return ReplayResult(
        reproduced=reproduced,
        recorded_final_action=record.final.action.value,
        replayed_final_action=replayed.decision.action.value,
        consulted_adviser=replayed.consulted,
        divergence=divergence,
    )
