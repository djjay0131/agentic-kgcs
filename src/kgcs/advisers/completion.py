"""The injected LLM seam for bounded advisers (Wave 4, DG-2/DG-4, spec §7.4).

This is the *only* LLM surface the curation core touches, and it is deliberately
KGCS-local: a `CompletionPort` Protocol plus small in-package clients, with **no
provider SDK** anywhere (no `openai`/`anthropic` import, no network). A real
provider adapter would implement `CompletionPort` *outside* the core — this
module never does.

The seam exists so an adviser is a pure function of *recorded* inputs in tests:
`RecordedCompletionClient` maps a deterministic request key
(`CompletionRequest.request_hash`, over template id + version + rendered prompt +
supplied evidence ids) to a canned `CompletionResponse`. An unknown key raises a
typed `CompletionMiss` — a *test-wiring* error, surfaced loudly, never silently
abstained (contrast the runtime failures below).

The failure-mode clients prove the §9-law-1 guarantee that the deterministic
baseline stands on any LLM failure: `FailingCompletionClient` raises,
`TimeoutCompletionClient` raises a timeout-like error, and
`MalformedCompletionClient` returns unparseable output. The adviser machinery
(`kgcs.advisers.base`) turns each of these into an *abstain*, never an exception
that reaches the orchestrator — so removing or breaking the LLM leaves the
Wave-3 decision untouched.
"""

import hashlib
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class CompletionError(Exception):
    """A *recoverable* LLM failure: the adviser abstains and the baseline stands.

    Every runtime completion failure (a provider error, a timeout) is one of
    these, so `kgcs.advisers.base` can catch exactly this class (plus any other
    unexpected port exception) and fall back to an abstain assessment.
    """


class CompletionTimeout(CompletionError):
    """A timeout-like failure — a `CompletionError`, so it also abstains."""


class CompletionMiss(Exception):
    """No recorded fixture matched a request key.

    Deliberately **not** a `CompletionError`: a miss means the test did not
    record the fixture it needed, which is a wiring error to surface loudly, not
    a runtime LLM failure to swallow. The adviser machinery lets it propagate.
    """


class CompletionRequest(BaseModel):
    """A rendered completion request plus a deterministic key for replay.

    `request_hash` is a pure function of the versioned template id, the template
    version, the rendered `prompt`, and the `evidence_ids` supplied to the model
    — so the same inputs always produce the same key, and a recorded fixture
    keyed by that hash replays byte-identically. Nothing here is time- or
    randomness-dependent.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    template_id: str = Field(min_length=1)
    template_version: str = Field(min_length=1)
    prompt: str
    evidence_ids: tuple[str, ...] = ()
    request_hash: str

    @classmethod
    def build(
        cls,
        *,
        template_id: str,
        template_version: str,
        prompt: str,
        evidence_ids: tuple[str, ...] = (),
    ) -> "CompletionRequest":
        """Build a request, computing the deterministic `request_hash`."""
        return cls(
            template_id=template_id,
            template_version=template_version,
            prompt=prompt,
            evidence_ids=evidence_ids,
            request_hash=_hash_request(template_id, template_version, prompt, evidence_ids),
        )


class CompletionResponse(BaseModel):
    """A completion result: the raw payload plus the model that produced it.

    `text` is the raw (possibly structured, JSON-encoded) payload the adviser
    parses; it is never trusted to be well-formed. `model_id`/`model_version` are
    carried onto every assessment for DG-4 provenance.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    model_id: str
    model_version: str


@runtime_checkable
class CompletionPort(Protocol):
    """The single LLM surface: render a request, get a response.

    A real provider adapter implements this *outside* the core. The core only
    ever sees this Protocol, so it depends on no provider SDK and makes no
    network call.
    """

    def complete(self, request: CompletionRequest) -> CompletionResponse: ...


class RecordedCompletionClient:
    """Deterministic replay: a request key maps to a canned response.

    Fixtures are keyed by `CompletionRequest.request_hash`; author them by
    building the request the adviser will build (via `Adviser.build_request`) and
    reading its `request_hash`. An unrecorded key raises `CompletionMiss` so a
    missing fixture is explicit, never a silent empty answer.
    """

    def __init__(self, fixtures: dict[str, CompletionResponse]) -> None:
        self._fixtures = dict(fixtures)

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        try:
            return self._fixtures[request.request_hash]
        except KeyError:
            raise CompletionMiss(
                f"no recorded completion for request_hash {request.request_hash!r} "
                f"(template {request.template_id}@{request.template_version})"
            ) from None


class FailingCompletionClient:
    """Always raises `CompletionError` — proves the baseline survives a failure."""

    def __init__(self, *, message: str = "completion port failed") -> None:
        self._message = message

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        raise CompletionError(self._message)


class TimeoutCompletionClient:
    """Always raises `CompletionTimeout` — proves the baseline survives a timeout."""

    def __init__(self, *, message: str = "completion port timed out") -> None:
        self._message = message

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        raise CompletionTimeout(self._message)


class MalformedCompletionClient:
    """Returns unparseable output — proves the baseline survives bad output.

    The response *is* a valid `CompletionResponse` (the transport worked), but
    its `text` is not the JSON the adviser expects, so parsing fails and the
    adviser abstains.
    """

    def __init__(
        self,
        *,
        text: str = "not-json <<< malformed >>>",
        model_id: str = "malformed",
        model_version: str = "0",
    ) -> None:
        self._response = CompletionResponse(text=text, model_id=model_id, model_version=model_version)

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        return self._response


def _hash_request(
    template_id: str, template_version: str, prompt: str, evidence_ids: tuple[str, ...]
) -> str:
    """A stable sha256 over the request's identifying inputs (field-separated)."""
    digest = hashlib.sha256()
    for part in (template_id, template_version, prompt, "\n".join(evidence_ids)):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()
