"""The injected completion seam: deterministic replay + explicit misses + failures.

Proves the seam is a pure function of recorded inputs (a stable request hash
keys the fixture), that an unrecorded key is a *loud* `CompletionMiss` (not a
silent empty answer), and that the failure-mode clients raise the errors the
adviser machinery turns into abstains (§9 laws 1, 6).
"""

import pytest

from kgcs.advisers.completion import (
    CompletionError,
    CompletionMiss,
    CompletionRequest,
    CompletionResponse,
    CompletionTimeout,
    FailingCompletionClient,
    MalformedCompletionClient,
    RecordedCompletionClient,
    TimeoutCompletionClient,
)


def _request(prompt: str = "p", evidence: tuple[str, ...] = ("ev_1",)) -> CompletionRequest:
    return CompletionRequest.build(
        template_id="t", template_version="1", prompt=prompt, evidence_ids=evidence
    )


class TestRequestHash:
    def test_hash_is_deterministic_for_equal_inputs(self) -> None:
        assert _request().request_hash == _request().request_hash

    def test_hash_separates_prompt_and_evidence_and_version(self) -> None:
        base = _request()
        assert base.request_hash != _request(prompt="other").request_hash
        assert base.request_hash != _request(evidence=("ev_2",)).request_hash
        assert (
            base.request_hash
            != CompletionRequest.build(
                template_id="t", template_version="2", prompt="p", evidence_ids=("ev_1",)
            ).request_hash
        )

    def test_evidence_order_is_significant(self) -> None:
        # The hash reflects exactly the inputs supplied, order included.
        assert _request(evidence=("a", "b")).request_hash != _request(evidence=("b", "a")).request_hash


class TestRecordedClient:
    def test_replays_the_recorded_response(self) -> None:
        request = _request()
        response = CompletionResponse(text="{}", model_id="m", model_version="1")
        client = RecordedCompletionClient({request.request_hash: response})
        assert client.complete(request) is response

    def test_unknown_key_raises_completion_miss(self) -> None:
        client = RecordedCompletionClient({})
        with pytest.raises(CompletionMiss):
            client.complete(_request())

    def test_completion_miss_is_not_a_recoverable_error(self) -> None:
        # A miss is a wiring error, not an LLM failure: it must NOT be catchable
        # as CompletionError (which the adviser swallows into an abstain).
        assert not issubclass(CompletionMiss, CompletionError)


class TestFailureClients:
    def test_failing_client_raises_completion_error(self) -> None:
        with pytest.raises(CompletionError):
            FailingCompletionClient().complete(_request())

    def test_timeout_client_raises_a_completion_error_subclass(self) -> None:
        with pytest.raises(CompletionError):
            TimeoutCompletionClient().complete(_request())
        assert issubclass(CompletionTimeout, CompletionError)

    def test_malformed_client_returns_unparseable_text(self) -> None:
        response = MalformedCompletionClient().complete(_request())
        assert isinstance(response, CompletionResponse)
        with pytest.raises(ValueError):
            import json

            json.loads(response.text)
