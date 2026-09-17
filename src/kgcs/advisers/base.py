"""Shared adviser machinery + DG-4 provenance (Wave 4, spec §7.4).

The whole point of the wave in one sentence: **the LLM is a bounded,
evidence-citing adviser, never the write authority.** This module enforces that
in the type system. An adviser's public output is an `AdviserAssessment` — a
frozen bag of a *recommendation string*, cited evidence ids, contradictions, a
confidence, and provenance. It has no field that is (or contains) a
`GraphMutationBatch`, a `CurationOperation`, or a `CurationPlan`, and an adviser
holds no `GraphMutationStore`. There is simply no way to express a canonical
write here (§9 law 16).

`StructuredAdviser` is the shared pipeline every specialist reuses:

    render prompt → CompletionPort.complete → parse JSON → validated assessment

with two disciplines baked in:

- **On any completion failure or malformed output, abstain — never raise.** A
  `CompletionError` (or any unexpected port error) and any parse failure both
  return an `abstained=True` assessment whose recommendation is the specialist's
  conservative `insufficient` value. The orchestrator therefore always gets a
  well-formed assessment, and the deterministic baseline stands (§9 law 1). A
  `CompletionMiss` is the one exception that propagates — it is a missing test
  fixture, not a runtime failure.
- **An adviser can only cite evidence it was given.** Parsed `evidence_ids` are
  intersected with the evidence supplied on the request, so a model cannot
  invent a citation; the supplied set itself is recoverable from the
  `CompletionRequest`.

DG-4 provenance is mandatory on *every* assessment: adviser type/version, model
id/version, prompt version, supplied/cited evidence, structured recommendation,
contradictions, confidence, abstention, trace id, and — stamped by the
orchestrator — the deterministic `baseline_action_before` and the
`final_action_after`. With a `RecordedCompletionClient`, the same fixture and
inputs yield a byte-identical `model_dump_json` (§9 law 6).
"""

import json
from typing import ClassVar, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from kgcs.advisers.completion import (
    CompletionMiss,
    CompletionPort,
    CompletionRequest,
    CompletionResponse,
)

_UNAVAILABLE = "unavailable"
"""Honest-null model id/version stamped when no completion was obtained (a port
error): there was no model, so we name that rather than fabricate a version."""


class AdviserQuestion(BaseModel):
    """The structured, deterministic input to any adviser.

    Carries only stable strings — the two subjects under comparison, the
    evidence ids the model may cite, optional textual `context` lines, and the
    universal `trace_id`. Because every field is a plain string/tuple, the
    rendered prompt (and thus the request key) is a pure function of the
    question, with no clock, graph read, or randomness.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    subject: str
    other: str | None = None
    evidence_ids: tuple[str, ...] = ()
    context: tuple[str, ...] = ()
    trace_id: str = ""


class AdviserAssessment(BaseModel):
    """Structured evidence from one adviser — never an operation (§9 law 16).

    This is the adviser's entire public output. `recommendation` is a plain
    string (each specialist's recommendation `StrEnum` *value*, so it compares
    equal to the enum member and serializes stably); there is deliberately no
    field capable of holding a `CurationOperation`/`GraphMutationBatch`/
    `CurationPlan`. `evidence_ids` are the evidence the assessment *cites*
    (always a subset of what was supplied); `confidence` is `None` when the
    model gave none (honest null); `abstained` marks an insufficient/failed
    assessment.

    `baseline_action_before`/`final_action_after` are the DG-4 before/after
    provenance the `CurationOrchestrator` stamps on: the deterministic Wave-3
    action *before* any adviser input, and the final action *after* folding the
    advice back through the deterministic policy.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    adviser_type: str
    adviser_version: str
    model_id: str
    model_version: str
    prompt_version: str
    recommendation: str
    evidence_ids: tuple[str, ...] = ()
    contradictions: tuple[str, ...] = ()
    confidence: float | None = None
    abstained: bool = False
    rationale: str = ""
    trace_id: str = ""
    baseline_action_before: str | None = None
    final_action_after: str | None = None


@runtime_checkable
class Adviser(Protocol):
    """A bounded specialist: a question in, a structured assessment out.

    The return type is the whole guarantee — an `AdviserAssessment`, never a
    mutation. `build_request` exposes the deterministic request the adviser will
    send, so a test can key a `RecordedCompletionClient` fixture by its
    `request_hash`.
    """

    adviser_type: str
    adviser_version: str

    def assess(self, question: AdviserQuestion) -> AdviserAssessment: ...

    def build_request(self, question: AdviserQuestion) -> CompletionRequest: ...


class StructuredAdviser:
    """The shared render → complete → parse → validate pipeline.

    Subclasses set the class-level configuration (`ADVISER_TYPE`,
    `ADVISER_VERSION`, `TEMPLATE_ID`, `PROMPT_VERSION`, `INSTRUCTION`, the
    permitted `RECOMMENDATIONS`, and the conservative `INSUFFICIENT`
    recommendation) and inherit `assess`/`build_request` unchanged — the
    specialists differ only in their data, not their control flow. Holds only an
    injected `CompletionPort`; no mutable state, no clock, no graph.
    """

    ADVISER_TYPE: ClassVar[str] = "structured"
    ADVISER_VERSION: ClassVar[str] = "1"
    TEMPLATE_ID: ClassVar[str] = "structured"
    PROMPT_VERSION: ClassVar[str] = "1"
    INSTRUCTION: ClassVar[str] = "Compare the evidence and return a structured assessment."
    RECOMMENDATIONS: ClassVar[frozenset[str]] = frozenset()
    INSUFFICIENT: ClassVar[str] = "insufficient"

    def __init__(self, *, port: CompletionPort) -> None:
        self._port = port

    @property
    def adviser_type(self) -> str:
        return self.ADVISER_TYPE

    @property
    def adviser_version(self) -> str:
        return self.ADVISER_VERSION

    def build_request(self, question: AdviserQuestion) -> CompletionRequest:
        """The deterministic completion request for `question` (key = request_hash).

        Caller contract: `question.evidence_ids` order is part of the request
        key, so callers must supply evidence in a stable order (derive it from
        an ordered container, never an unordered set) or replay keys will drift.
        """
        return CompletionRequest.build(
            template_id=self.TEMPLATE_ID,
            template_version=self.PROMPT_VERSION,
            prompt=self._render(question),
            evidence_ids=question.evidence_ids,
        )

    def assess(self, question: AdviserQuestion) -> AdviserAssessment:
        """Return a validated assessment; abstain on any failure, never raise.

        A `CompletionMiss` propagates (a missing fixture is a wiring error);
        every other port error and any parse failure become an abstain, so the
        baseline is never disturbed by a broken LLM. Both the port call *and*
        parsing are guarded, so "never raise to the orchestrator" (law 1) is
        structural: a future edit that introduced a raising path into `_parse`
        would still abstain rather than escape.
        """
        request = self.build_request(question)
        try:
            response = self._port.complete(request)
        except CompletionMiss:
            raise
        except Exception as exc:  # noqa: BLE001 — any port failure must abstain, not raise
            return self._abstain(
                question,
                rationale=f"completion port error: {type(exc).__name__}: {exc}",
            )
        try:
            return self._parse(response, question)
        except Exception as exc:  # noqa: BLE001 — parsing must abstain, never escape
            return self._abstain(
                question,
                rationale=f"assessment parse error: {type(exc).__name__}: {exc}",
            )

    # -- rendering -----------------------------------------------------------

    def _render(self, question: AdviserQuestion) -> str:
        """A deterministic prompt: instruction + subjects + evidence + context."""
        lines = [self.INSTRUCTION, f"kind: {question.kind}", f"subject: {question.subject}"]
        if question.other is not None:
            lines.append(f"other: {question.other}")
        lines.append("evidence_ids: " + ", ".join(question.evidence_ids))
        lines.extend(f"context: {line}" for line in question.context)
        allowed = ", ".join(sorted(self.RECOMMENDATIONS))
        lines.append(f"allowed_recommendations: {allowed}")
        return "\n".join(lines)

    # -- parsing -------------------------------------------------------------

    def _parse(
        self, response: CompletionResponse, question: AdviserQuestion
    ) -> AdviserAssessment:
        """Parse the model's JSON payload into a validated assessment, or abstain."""
        try:
            payload = json.loads(response.text)
        except (json.JSONDecodeError, ValueError):
            return self._abstain(
                question, rationale="malformed completion: not valid JSON", response=response
            )
        if not isinstance(payload, dict):
            return self._abstain(
                question, rationale="malformed completion: payload is not an object", response=response
            )

        recommendation = payload.get("recommendation")
        if not isinstance(recommendation, str) or recommendation not in self.RECOMMENDATIONS:
            return self._abstain(
                question,
                rationale=f"malformed completion: recommendation {recommendation!r} not permitted",
                response=response,
            )

        supplied = set(question.evidence_ids)
        cited = tuple(
            evidence_id
            for evidence_id in _coerce_str_tuple(payload.get("evidence_ids"))
            if evidence_id in supplied
        )
        contradictions = _coerce_str_tuple(payload.get("contradictions"))
        confidence = _coerce_confidence(payload.get("confidence"))
        rationale = payload.get("rationale")

        return AdviserAssessment(
            adviser_type=self.ADVISER_TYPE,
            adviser_version=self.ADVISER_VERSION,
            model_id=response.model_id,
            model_version=response.model_version,
            prompt_version=self.PROMPT_VERSION,
            recommendation=recommendation,
            evidence_ids=cited,
            contradictions=contradictions,
            confidence=confidence,
            abstained=False,
            rationale=rationale if isinstance(rationale, str) else "",
            trace_id=question.trace_id,
        )

    def _abstain(
        self,
        question: AdviserQuestion,
        *,
        rationale: str,
        response: CompletionResponse | None = None,
    ) -> AdviserAssessment:
        """A conservative abstain assessment (recommendation = `INSUFFICIENT`).

        When a response was received (a parse failure) its model id/version are
        preserved for provenance; when none was (a port error) they are the
        honest-null sentinel. Cited evidence falls back to the supplied set so
        the assessment still records what the model was given.
        """
        return AdviserAssessment(
            adviser_type=self.ADVISER_TYPE,
            adviser_version=self.ADVISER_VERSION,
            model_id=response.model_id if response is not None else _UNAVAILABLE,
            model_version=response.model_version if response is not None else _UNAVAILABLE,
            prompt_version=self.PROMPT_VERSION,
            recommendation=self.INSUFFICIENT,
            evidence_ids=question.evidence_ids,
            contradictions=(),
            confidence=None,
            abstained=True,
            rationale=rationale,
            trace_id=question.trace_id,
        )


def _coerce_str_tuple(value: object) -> tuple[str, ...]:
    """Read a JSON value as a tuple of strings (non-strings dropped), else empty."""
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _coerce_confidence(value: object) -> float | None:
    """Read a numeric confidence clamped to [0, 1]; `None`/non-numeric → None.

    Mirrors the spec's clamp-and-fallback discipline: an out-of-range score is
    clamped rather than trusted, and a missing one stays an honest null.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0.0, min(1.0, float(value)))
