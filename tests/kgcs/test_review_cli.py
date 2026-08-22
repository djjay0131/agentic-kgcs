"""Wave 6: the terminal CLI can work the queue end to end over an injected
in-memory queue — list → resolve → history — without editing storage by hand.
"""

import io
from datetime import UTC, datetime

from kgcs.clock import FixedClock
from kgcs.review.cli import run
from kgcs.review.model import ReviewCase
from kgcs.review.queue import PersistentReviewQueue

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


def _seed_queue() -> tuple[PersistentReviewQueue, str]:
    queue = PersistentReviewQueue.in_memory()
    case = ReviewCase(
        kind="er_merge",
        proposed_action="AUTO_LINK",
        priority="P1",
        source="s1",
        entity_type="Person",
        evidence_ids=("ev1",),
        reason="uncertain merge",
    )
    item = case.to_item(enqueued_at=NOW)
    queue.enqueue(item)
    return queue, item.item_id


def _run(queue: PersistentReviewQueue, *argv: str) -> tuple[int, str]:
    out = io.StringIO()
    code = run(list(argv), queue=queue, clock=FixedClock(NOW), out=out)
    return code, out.getvalue()


def test_cli_list_resolve_history_flow() -> None:
    queue, item_id = _seed_queue()

    code, listing = _run(queue, "list")
    assert code == 0
    assert item_id in listing
    assert "[P1]" in listing
    assert "AUTO_LINK" in listing

    code, resolved = _run(
        queue, "resolve", item_id, "--action", "APPROVE", "--actor", "reviewer"
    )
    assert code == 0
    assert "resolved" in resolved

    # The item has moved out of pending...
    _, listing_after = _run(queue, "list")
    assert "no pending review items" in listing_after

    # ...and its decision is in history.
    code, history = _run(queue, "history", item_id)
    assert code == 0
    assert "APPROVE" in history
    assert "reviewer" in history

    # And the queue itself reflects it (not just the printed text).
    assert queue.pending() == []
    assert [d.action.value for d in queue.history(item_id)] == ["APPROVE"]


def test_cli_show_renders_evidence_and_proposal() -> None:
    queue, item_id = _seed_queue()
    code, detail = _run(queue, "show", item_id)
    assert code == 0
    assert "proposed:  AUTO_LINK" in detail
    assert "ev1" in detail


def test_cli_edit_without_payload_is_rejected() -> None:
    queue, item_id = _seed_queue()
    code, out = _run(queue, "resolve", item_id, "--action", "EDIT", "--actor", "rev")
    assert code == 2
    assert "invalid decision" in out
    # The item is untouched — a rejected decision never silently resolves it.
    assert [i.item_id for i in queue.pending()] == [item_id]


def test_cli_backlog_reports_metrics() -> None:
    queue, _ = _seed_queue()
    code, out = _run(queue, "backlog")
    assert code == 0
    assert "queue depth: 1" in out
    assert "priority inversion:" in out
