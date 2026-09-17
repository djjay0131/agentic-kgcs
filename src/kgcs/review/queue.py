"""A persistent `ReviewQueue` + typed retryable/permanent failures (spec §7.6, §9 law 15).

The frozen `kg_contracts.curation.ReviewQueue` Protocol is tiny — enqueue /
pending / resolve / history — and history is *part of the contract*, not an
afterthought: the future review UI reads `history()` directly, so a decision is
never lost once recorded. This module implements that Protocol over a swappable
storage seam so the same queue behaviour is durable-shaped rather than tied to a
single process's list:

- `ReviewStore` — the storage port: `load()` returns a fresh mutable snapshot,
  `save(state)` persists one. A failed `save()` therefore leaves the previously
  persisted snapshot intact, so a write that never lands is a *raised, typed
  failure*, never a silent drop of the item (§9 law 15).
- `InMemoryReviewStore` — the dependency-free reference store (holds one state,
  hands out copies).
- `JsonFileReviewStore` — a durable store using only stdlib `json` and an
  injected path; it round-trips every `ReviewItem`/`ReviewDecision` through the
  contract models' own `model_dump(mode="json")`/`model_validate`, so a queue
  reopened over the same file shows the same pending items and the same history.
- `PersistentReviewQueue` — the `ReviewQueue` implementation over any store.

**Retryable vs permanent (law 15).** Every storage interaction is classified:
a transient I/O fault (`OSError`) surfaces as `RetryableQueueError` (retry the
same call) and a data/serialization fault surfaces as `PermanentQueueError`
(the item will never persist as-is — quarantine it). Both are raised, never
swallowed, so a queue/backpressure failure can never silently discard a
candidate. The distinction is a typed attribute (`retryable`) on a common
`ReviewQueueError` base so a caller can branch without string-matching.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from kg_contracts.curation import ReviewDecision, ReviewItem


class ReviewQueueError(Exception):
    """Base for every review-queue failure — always raised, never swallowed.

    `retryable` lets a caller branch on the failure class without parsing the
    message: True means the same call may succeed on retry (a transient fault),
    False means it will not (a data fault that must be quarantined, not retried).
    """

    retryable: bool = False


class RetryableQueueError(ReviewQueueError):
    """A transient storage fault (e.g. an `OSError`) — retrying may succeed."""

    retryable = True


class PermanentQueueError(ReviewQueueError):
    """A data/serialization fault — retrying the same input will not succeed."""

    retryable = False


@dataclass
class QueueState:
    """The mutable snapshot a `ReviewStore` loads and saves.

    `pending` is insertion-ordered (Python dicts preserve order), so
    `pending()` reproduces enqueue order. `history` is append-only per item —
    resolving an item more than once (an `EDIT` pass then a final `APPROVE`)
    keeps every decision.
    """

    pending: dict[str, ReviewItem] = field(default_factory=dict)
    history: dict[str, list[ReviewDecision]] = field(default_factory=dict)

    def copy(self) -> QueueState:
        """A deep-enough copy: fresh containers over the frozen contract models."""
        return QueueState(
            pending=dict(self.pending),
            history={item_id: list(decisions) for item_id, decisions in self.history.items()},
        )


@runtime_checkable
class ReviewStore(Protocol):
    """The queue's storage seam: a fresh snapshot in, a persisted snapshot out.

    `load()` MUST return a snapshot the queue may mutate freely without touching
    what is persisted, and `save()` MUST replace the persisted snapshot only on
    success — so a failed `save()` never corrupts or drops what was already
    stored.
    """

    def load(self) -> QueueState: ...

    def save(self, state: QueueState) -> None: ...


class InMemoryReviewStore:
    """The reference `ReviewStore`: one in-process state, copies handed out.

    Persists across `PersistentReviewQueue` instances that share the *same*
    store object, which is how the durability round-trip is exercised without a
    filesystem. `load()` returns a copy and `save()` stores a copy, so a queue
    mutating its working snapshot cannot retroactively change persisted state
    until it explicitly saves.
    """

    def __init__(self) -> None:
        self._state = QueueState()

    def load(self) -> QueueState:
        return self._state.copy()

    def save(self, state: QueueState) -> None:
        self._state = state.copy()


class JsonFileReviewStore:
    """A durable `ReviewStore` backed by a single JSON file (stdlib only).

    Serializes each `ReviewItem`/`ReviewDecision` via the contract models' own
    `model_dump(mode="json")` and reloads via `model_validate`, so a queue
    reopened over the same path sees byte-equivalent items and decisions. Writes
    are atomic (temp file + `os.replace`) so a crash mid-write cannot leave a
    half-written queue. A missing file loads as an empty queue; a corrupt file
    raises rather than silently discarding history.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def load(self) -> QueueState:
        if not self._path.exists():
            return QueueState()
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        pending_raw = raw.get("pending", [])
        history_raw = raw.get("history", {})
        pending: dict[str, ReviewItem] = {}
        for entry in pending_raw:
            item = ReviewItem.model_validate(entry)
            pending[item.item_id] = item
        history: dict[str, list[ReviewDecision]] = {}
        for item_id, decisions in history_raw.items():
            history[item_id] = [ReviewDecision.model_validate(d) for d in decisions]
        return QueueState(pending=pending, history=history)

    def save(self, state: QueueState) -> None:
        payload = {
            "pending": [item.model_dump(mode="json") for item in state.pending.values()],
            "history": {
                item_id: [decision.model_dump(mode="json") for decision in decisions]
                for item_id, decisions in state.history.items()
            },
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self._path)


def _classify(exc: Exception, action: str) -> ReviewQueueError:
    """Map a raw storage exception to a typed, non-silent queue failure.

    An `OSError` is transient (disk full, locked file) and retryable; anything
    else reaching here is a data/serialization fault that will not fix itself on
    retry. Nothing is ever swallowed — every path returns an error to raise.
    """
    if isinstance(exc, ReviewQueueError):
        return exc
    if isinstance(exc, OSError):
        return RetryableQueueError(f"review queue {action} failed transiently: {exc!r}")
    return PermanentQueueError(f"review queue {action} failed permanently: {exc!r}")


class PersistentReviewQueue:
    """A durable-shaped `ReviewQueue` over any `ReviewStore` (spec §7.6).

    Satisfies the frozen `kg_contracts.curation.ReviewQueue` Protocol and the
    reusable `ReviewQueueContract`. Every operation loads a fresh snapshot,
    mutates it, and saves it back, classifying any storage fault as a typed
    retryable/permanent failure (§9 law 15). History is append-only: a decision
    is added, never replaced, so resolving an item twice keeps both decisions
    and no decision is ever lost.
    """

    def __init__(self, store: ReviewStore) -> None:
        self._store = store

    @classmethod
    def in_memory(cls) -> PersistentReviewQueue:
        """A queue over a fresh `InMemoryReviewStore` (tests / local runs)."""
        return cls(InMemoryReviewStore())

    @classmethod
    def json_file(cls, path: str | Path) -> PersistentReviewQueue:
        """A queue durable to `path` via `JsonFileReviewStore`."""
        return cls(JsonFileReviewStore(path))

    def enqueue(self, item: ReviewItem) -> str:
        """Record `item` as pending; return its `item_id`. Raises a typed failure."""
        state = self._load()
        state.pending[item.item_id] = item
        self._save(state, "enqueue")
        return item.item_id

    def pending(self, limit: int = 50) -> list[ReviewItem]:
        """The pending items in enqueue order, capped at `limit`."""
        state = self._load()
        return list(state.pending.values())[:limit]

    def resolve(self, decision: ReviewDecision) -> None:
        """Remove the item from pending and append `decision` to its history.

        Appending (never replacing) is the append-only history guarantee: a
        second `resolve()` of the same item keeps the first decision. A failed
        save leaves the prior persisted state intact — the decision is *not*
        lost, the caller gets a typed failure to retry (§9 law 15).
        """
        state = self._load()
        state.pending.pop(decision.item_id, None)
        state.history.setdefault(decision.item_id, []).append(decision)
        self._save(state, "resolve")

    def history(self, item_id: str) -> list[ReviewDecision]:
        """Every decision recorded for `item_id`, in decision order."""
        state = self._load()
        return list(state.history.get(item_id, []))

    # -- storage seam with typed-failure classification ----------------------

    def _load(self) -> QueueState:
        try:
            return self._store.load()
        except Exception as exc:  # noqa: BLE001 — classified into a typed queue failure
            raise _classify(exc, "load") from exc

    def _save(self, state: QueueState, action: str) -> None:
        try:
            self._store.save(state)
        except Exception as exc:  # noqa: BLE001 — classified into a typed queue failure
            raise _classify(exc, action) from exc
