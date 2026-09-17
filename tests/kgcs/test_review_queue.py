"""Wave 6: the persistent review queue passes `ReviewQueueContract`, round-trips
durably, and surfaces typed retryable/permanent failures — never a silent drop
(§9 law 15)."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from kg_contracts.curation import ReviewAction, ReviewDecision, ReviewItem
from kg_contracts.testing.contract import ReviewQueueContract

from kgcs.review.queue import (
    PermanentQueueError,
    PersistentReviewQueue,
    QueueState,
    RetryableQueueError,
    ReviewQueueError,
)

NOW = datetime(2026, 8, 21, tzinfo=UTC)


def _item(payload: dict[str, object] | None = None) -> ReviewItem:
    return ReviewItem(
        kind="entity_merge", payload=payload or {"a": 1}, reason="r", enqueued_at=NOW
    )


class TestPersistentReviewQueueContract(ReviewQueueContract):
    """The persistent queue must pass the frozen reusable contract unchanged."""

    def make_queue(self) -> PersistentReviewQueue:  # type: ignore[override]
        return PersistentReviewQueue.in_memory()


def test_json_file_store_round_trips_across_new_instances(tmp_path: Path) -> None:
    path = tmp_path / "queue.json"
    q1 = PersistentReviewQueue.json_file(path)
    item = _item({"display_name": "old"})
    q1.enqueue(item)

    # A brand-new queue over the same file sees the pending item.
    q2 = PersistentReviewQueue.json_file(path)
    assert [i.item_id for i in q2.pending()] == [item.item_id]
    assert q2.pending()[0].payload == {"display_name": "old"}

    # Resolve through q2; a third instance still sees the history (never lost).
    decision = ReviewDecision(
        item_id=item.item_id, action=ReviewAction.APPROVE, actor="rev", decided_at=NOW
    )
    q2.resolve(decision)
    q3 = PersistentReviewQueue.json_file(path)
    assert q3.pending() == []
    assert q3.history(item.item_id) == [decision]


def test_history_is_append_only_across_reloads(tmp_path: Path) -> None:
    path = tmp_path / "queue.json"
    item = _item()
    PersistentReviewQueue.json_file(path).enqueue(item)
    first = ReviewDecision(
        item_id=item.item_id,
        action=ReviewAction.EDIT,
        actor="a1",
        edited_payload={"x": 1},
        decided_at=NOW,
    )
    second = ReviewDecision(
        item_id=item.item_id, action=ReviewAction.APPROVE, actor="a2", decided_at=NOW
    )
    PersistentReviewQueue.json_file(path).resolve(first)
    PersistentReviewQueue.json_file(path).resolve(second)
    assert PersistentReviewQueue.json_file(path).history(item.item_id) == [first, second]


# -- typed retryable vs permanent failures (§9 law 15) -----------------------


class _RaisingStore:
    """A store whose save raises a chosen exception — models a storage fault."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self._state = QueueState()

    def load(self) -> QueueState:
        return self._state.copy()

    def save(self, state: QueueState) -> None:
        raise self._exc


def test_transient_io_fault_is_retryable_not_silently_dropped() -> None:
    queue = PersistentReviewQueue(_RaisingStore(OSError("disk full")))
    with pytest.raises(RetryableQueueError) as caught:
        queue.enqueue(_item())
    assert caught.value.retryable is True
    # The failure is raised, not swallowed — the candidate is never silently lost.
    assert isinstance(caught.value, ReviewQueueError)


def test_data_fault_is_permanent() -> None:
    queue = PersistentReviewQueue(_RaisingStore(ValueError("bad json")))
    with pytest.raises(PermanentQueueError) as caught:
        queue.resolve(
            ReviewDecision(
                item_id="rv_x", action=ReviewAction.REJECT, actor="rev", decided_at=NOW
            )
        )
    assert caught.value.retryable is False


def test_failed_save_does_not_corrupt_prior_persisted_state() -> None:
    # A save that fails must leave earlier successfully-persisted state intact.
    class _FailSecondSave:
        def __init__(self) -> None:
            self._state = QueueState()
            self._saves = 0

        def load(self) -> QueueState:
            return self._state.copy()

        def save(self, state: QueueState) -> None:
            self._saves += 1
            if self._saves >= 2:
                raise OSError("transient")
            self._state = state.copy()

    store = _FailSecondSave()
    queue = PersistentReviewQueue(store)
    first = _item({"keep": True})
    queue.enqueue(first)  # first save succeeds
    with pytest.raises(RetryableQueueError):
        queue.enqueue(_item({"drop": True}))  # second save fails
    # The first item survives; the failed second enqueue did not corrupt it.
    assert [i.item_id for i in queue.pending()] == [first.item_id]


def test_json_file_store_write_failure_leaves_prior_file_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash mid-write (temp+replace) must not corrupt or truncate the prior
    persisted file — the first item survives a failed second save (§9 law 15)."""
    import os

    path = tmp_path / "queue.json"
    q1 = PersistentReviewQueue.json_file(path)
    first = _item({"display_name": "first"})
    q1.enqueue(first)
    good_bytes = path.read_bytes()

    # Force the atomic replace to fail on the next save.
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated crash during replace")

    monkeypatch.setattr(os, "replace", _boom, raising=True)
    q2 = PersistentReviewQueue.json_file(path)
    with pytest.raises(RetryableQueueError):
        q2.enqueue(_item({"display_name": "second"}))
    monkeypatch.undo()

    # The prior file is byte-identical and still loads the first item.
    assert path.read_bytes() == good_bytes
    q3 = PersistentReviewQueue.json_file(path)
    pending = q3.pending()
    assert len(pending) == 1
    assert pending[0].payload["display_name"] == "first"
