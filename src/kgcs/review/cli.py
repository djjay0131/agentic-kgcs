"""A stdlib-only terminal CLI to work the review queue (spec §7.6).

The polished web review UI is deferred (build plan Wave 6 / §11); this CLI is the
v1 way to work every non-auto route *without editing storage by hand*, which is
the wave's exit criterion. Pure `argparse`, no external CLI library, plain-text
output. Subcommands:

- `list` — pending items with priority, SLA deadline, kind, and evidence;
- `show <item_id>` — full detail incl. proposed action, provenance, adviser
  assessments;
- `resolve <item_id> --action ... --actor ...` — record a `ReviewDecision`
  (optionally with `--note` / `--edited-payload`), moving the item to history;
- `history <item_id>` — the item's recorded decisions in order;
- `backlog` — queue metrics and any machine-readable backpressure signals.

Everything operates over an *injected* `ReviewQueue` (and `Clock` / analyzer) —
there is no global state — so a test drives the same code path over an in-memory
queue. `main()` wires the durable JSON-file queue for real use.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from typing import Sequence, TextIO

from kg_contracts.curation import ReviewAction, ReviewDecision, ReviewItem, ReviewQueue

from kgcs.clock import Clock, SystemClock
from kgcs.review.backlog import BacklogAnalyzer
from kgcs.review.model import ReviewCase, sla_deadline
from kgcs.review.queue import PersistentReviewQueue, ReviewQueueError


def build_parser() -> argparse.ArgumentParser:
    """The `kgcs review` argument parser (stdlib argparse only)."""
    parser = argparse.ArgumentParser(prog="kgcs-review", description="Work the KGCS review queue.")
    parser.add_argument(
        "--store",
        default=None,
        help="Path to a JSON-file-backed queue store (default: ephemeral in-memory).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List pending review items.")
    p_list.add_argument("--limit", type=int, default=50)

    p_show = sub.add_parser("show", help="Show one review item in full.")
    p_show.add_argument("item_id")

    p_resolve = sub.add_parser("resolve", help="Resolve an item with a decision.")
    p_resolve.add_argument("item_id")
    p_resolve.add_argument(
        "--action", required=True, choices=[a.value for a in ReviewAction]
    )
    p_resolve.add_argument("--actor", required=True)
    p_resolve.add_argument("--note", default=None)
    p_resolve.add_argument(
        "--edited-payload",
        default=None,
        help="JSON object applied for an EDIT action (required by the contract for EDIT).",
    )

    p_history = sub.add_parser("history", help="Show an item's decision history.")
    p_history.add_argument("item_id")

    sub.add_parser("backlog", help="Show queue metrics and backpressure signals.")

    return parser


def run(
    argv: Sequence[str],
    *,
    queue: ReviewQueue,
    clock: Clock | None = None,
    analyzer: BacklogAnalyzer | None = None,
    out: TextIO,
) -> int:
    """Parse `argv` and dispatch over the injected `queue` — no global state."""
    args = build_parser().parse_args(list(argv))
    the_clock = clock or SystemClock()
    the_analyzer = analyzer or BacklogAnalyzer(clock=the_clock)
    return _dispatch(args, queue=queue, clock=the_clock, analyzer=the_analyzer, out=out)


def _dispatch(
    args: argparse.Namespace,
    *,
    queue: ReviewQueue,
    clock: Clock,
    analyzer: BacklogAnalyzer,
    out: TextIO,
) -> int:
    command: str = args.command
    if command == "list":
        return _cmd_list(queue, limit=int(args.limit), out=out)
    if command == "show":
        return _cmd_show(queue, item_id=str(args.item_id), out=out)
    if command == "resolve":
        return _cmd_resolve(queue, args, clock=clock, out=out)
    if command == "history":
        return _cmd_history(queue, item_id=str(args.item_id), out=out)
    if command == "backlog":
        return _cmd_backlog(queue, analyzer=analyzer, out=out)
    print(f"unknown command: {command}", file=out)  # pragma: no cover — argparse guards this
    return 2


# -- commands ----------------------------------------------------------------


def _cmd_list(queue: ReviewQueue, *, limit: int, out: TextIO) -> int:
    items = queue.pending(limit)
    if not items:
        print("no pending review items", file=out)
        return 0
    print(f"{len(items)} pending review item(s):", file=out)
    for item in items:
        _print_item_line(item, out)
    return 0


def _cmd_show(queue: ReviewQueue, *, item_id: str, out: TextIO) -> int:
    item = _find_pending(queue, item_id)
    if item is None:
        print(f"item {item_id} not pending (see `history {item_id}`)", file=out)
        return 1
    _print_item_detail(item, out)
    return 0


def _cmd_resolve(
    queue: ReviewQueue, args: argparse.Namespace, *, clock: Clock, out: TextIO
) -> int:
    edited_payload = _parse_edited_payload(args.edited_payload, out)
    if args.edited_payload is not None and edited_payload is None:
        return 2
    try:
        decision = ReviewDecision(
            item_id=str(args.item_id),
            action=ReviewAction(str(args.action)),
            actor=str(args.actor),
            edited_payload=edited_payload,
            note=args.note,
            decided_at=clock.now(),
        )
    except ValueError as exc:  # contract validator (e.g. EDIT requires edited_payload)
        print(f"invalid decision: {exc}", file=out)
        return 2
    try:
        queue.resolve(decision)
    except ReviewQueueError as exc:
        kind = "retryable" if exc.retryable else "permanent"
        print(f"resolve failed ({kind}): {exc}", file=out)
        return 1
    print(
        f"resolved {decision.item_id}: {decision.action.value} by {decision.actor}",
        file=out,
    )
    return 0


def _cmd_history(queue: ReviewQueue, *, item_id: str, out: TextIO) -> int:
    decisions = queue.history(item_id)
    if not decisions:
        print(f"no decisions recorded for {item_id}", file=out)
        return 0
    print(f"{len(decisions)} decision(s) for {item_id}:", file=out)
    for decision in decisions:
        note = f" note={decision.note}" if decision.note else ""
        print(
            f"  {_iso(decision.decided_at)}  {decision.action.value}  by {decision.actor}{note}",
            file=out,
        )
    return 0


def _cmd_backlog(queue: ReviewQueue, *, analyzer: BacklogAnalyzer, out: TextIO) -> int:
    items = queue.pending(limit=10_000)
    metrics = analyzer.metrics(items)
    print(f"queue depth: {metrics.depth}", file=out)
    print(f"oldest item age (h): {metrics.oldest_age_hours:.1f}", file=out)
    print(f"SLA breaches: {metrics.sla_breaches} {metrics.sla_breaches_by_priority}", file=out)
    print(f"depth by source: {metrics.depth_by_source}", file=out)
    print(f"depth by entity type: {metrics.depth_by_entity_type}", file=out)
    print(f"priority inversion: {metrics.priority_inversion}", file=out)
    if metrics.inverted_item_ids:
        print(f"  starved (higher-priority breaching): {list(metrics.inverted_item_ids)}", file=out)
    if metrics.starving_high_value:
        print(f"  starving high-value: {list(metrics.starving_high_value)}", file=out)
    signals = analyzer.backpressure(items)
    if not signals:
        print("backpressure: none", file=out)
    else:
        print(f"backpressure signals ({len(signals)}):", file=out)
        for signal in signals:
            print(
                f"  {signal.source}: {signal.action.value} "
                f"(depth={signal.depth}, breaches={signal.sla_breaches}, "
                f"max_intake={signal.recommended_max_intake}) — {signal.reason}",
                file=out,
            )
    return 0


# -- rendering helpers -------------------------------------------------------


def _print_item_line(item: ReviewItem, out: TextIO) -> None:
    case = ReviewCase.try_from_item(item)
    action = case.proposed_action if case else "?"
    print(
        f"  [{item.priority}] {item.item_id}  kind={item.kind}  "
        f"proposed={action}  sla_by={_iso(sla_deadline(item))}",
        file=out,
    )


def _print_item_detail(item: ReviewItem, out: TextIO) -> None:
    print(f"item_id:   {item.item_id}", file=out)
    print(f"kind:      {item.kind}", file=out)
    print(f"priority:  {item.priority}  (SLA by {_iso(sla_deadline(item))})", file=out)
    print(f"reason:    {item.reason}", file=out)
    print(f"enqueued:  {_iso(item.enqueued_at)}", file=out)
    case = ReviewCase.try_from_item(item)
    if case is None:
        print("payload:   (not a KGCS ReviewCase)", file=out)
        return
    print(f"proposed:  {case.proposed_action}", file=out)
    print(f"snapshot:  {case.snapshot_ref}", file=out)
    print(f"source:    {case.source}   entity_type: {case.entity_type}", file=out)
    print(f"risk/value:{case.risk} / {case.value}", file=out)
    print(f"trace_id:  {case.trace_id}", file=out)
    print(f"evidence:  {list(case.evidence_ids)}", file=out)
    if case.adviser_assessments:
        print("advisers:", file=out)
        for assessment in case.adviser_assessments:
            print(
                f"  - {assessment.adviser_type}/{assessment.adviser_version}: "
                f"{assessment.recommendation} "
                f"(abstained={assessment.abstained}, confidence={assessment.confidence})",
                file=out,
            )
    if case.proposed_plan is not None:
        ops = ", ".join(op.type.value for op in case.proposed_plan.operations)
        print(f"proposed_plan: {case.proposed_plan.plan_id}  ops=[{ops}]", file=out)


def _parse_edited_payload(raw: str | None, out: TextIO) -> dict[str, object] | None:
    """Parse `--edited-payload` JSON into an object, or `None` (absent/invalid)."""
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        print("invalid --edited-payload: not valid JSON", file=out)
        return None
    if not isinstance(parsed, dict):
        print("invalid --edited-payload: must be a JSON object", file=out)
        return None
    return parsed


def _find_pending(queue: ReviewQueue, item_id: str) -> ReviewItem | None:
    for item in queue.pending(limit=10_000):
        if item.item_id == item_id:
            return item
    return None


def _iso(instant: datetime) -> str:
    return instant.isoformat()


def main(argv: Sequence[str] | None = None) -> int:
    """Wire the durable JSON-file queue (or in-memory) and run one command."""
    raw = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(raw)
    store_path = args.store
    queue: ReviewQueue = (
        PersistentReviewQueue.json_file(store_path)
        if store_path
        else PersistentReviewQueue.in_memory()
    )
    clock = SystemClock()
    analyzer = BacklogAnalyzer(clock=clock)
    return _dispatch(args, queue=queue, clock=clock, analyzer=analyzer, out=sys.stdout)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
