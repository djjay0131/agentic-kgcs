"""The KGCS review-item enrichment: `ReviewCase`, SLA math (spec §7.6).

The frozen contract `ReviewItem` is deliberately generic — an `item_id`, a
`kind`, an opaque `payload`, a `priority`, a `reason`, and an `enqueued_at`.
KGCS needs to carry *more* into review than that, but without widening the
frozen contract: what candidate/cluster snapshot is under review, the action the
automated path proposed, the evidence, the bounded adviser assessments that
informed it, a risk/value estimate, and the trace id — plus the *proposed plan*
the auto path would have applied, so an `APPROVE` has something concrete to
converge on (§9 law 14).

`ReviewCase` is that enrichment. It is a frozen, JSON-serializable pydantic
model that lives *inside* `ReviewItem.payload`: `to_item()` writes it there and
`from_item()`/`try_from_item()` read it back. Every field is honest-null — an
absent risk, value, source, or snapshot is `None`, never a fabricated default.

SLA is a pure function of priority (spec §7.6: P1=24h, P2=7d, P3=30d).
`sla_deadline(item)` needs only the frozen `ReviewItem` (priority + enqueued_at),
so the backlog metrics can compute breaches from the contract item alone, even
for items whose payload is not a `ReviewCase`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from kg_contracts.curation import CurationPlan, ReviewItem
from pydantic import BaseModel, ConfigDict

from kgcs.advisers.base import AdviserAssessment

Priority = Literal["P1", "P2", "P3"]

SLA_HOURS_BY_PRIORITY: dict[str, int] = {
    "P1": 24,  # 24 hours
    "P2": 7 * 24,  # 7 days
    "P3": 30 * 24,  # 30 days
}
"""Curation SLA by priority (spec §7.6). The single source of these windows."""


def sla_hours(priority: str) -> int:
    """SLA window in hours for `priority` (P3/30d fallback for an unknown value)."""
    return SLA_HOURS_BY_PRIORITY.get(priority, SLA_HOURS_BY_PRIORITY["P3"])


def sla_deadline(item: ReviewItem) -> datetime:
    """The instant `item` breaches its SLA — a pure function of the frozen item."""
    return item.enqueued_at + timedelta(hours=sla_hours(item.priority))


class ReviewCase(BaseModel):
    """KGCS enrichment carried inside a `ReviewItem.payload` (spec §7.6).

    Serializable and honest-null. `proposed_plan` is the compensable
    `CurationPlan` the automated path would have applied had it not been routed
    to review; carrying it is what lets an `APPROVE` reach the *same* plan the
    auto path produced (§9 law 14). `source`/`entity_type` power the backlog's
    depth-by-source / depth-by-entity-type metrics; `risk`/`value` drive
    starvation detection; the adviser assessments preserve the bounded LLM
    evidence that informed the proposal (never an operation — §9 law 16).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    proposed_action: str
    priority: Priority = "P3"
    reason: str = ""
    trace_id: str = ""
    snapshot_ref: str | None = None
    source: str | None = None
    entity_type: str | None = None
    evidence_ids: tuple[str, ...] = ()
    adviser_assessments: tuple[AdviserAssessment, ...] = ()
    risk: float | None = None
    value: float | None = None
    proposed_plan: CurationPlan | None = None

    def sla_hours(self) -> int:
        """SLA window in hours for this case's priority."""
        return sla_hours(self.priority)

    def to_item(
        self,
        *,
        enqueued_at: datetime,
        item_id: str | None = None,
        reason: str | None = None,
    ) -> ReviewItem:
        """Build the frozen `ReviewItem` whose payload is this case (JSON-native).

        `reason`/`priority` on the item mirror the case unless a `reason`
        override is given; the item keeps its contract-default `item_id` unless
        one is supplied (a caller minting a deterministic id).
        """
        payload = self.model_dump(mode="json")
        fields: dict[str, object] = {
            "kind": self.kind,
            "payload": payload,
            "priority": self.priority,
            "reason": reason if reason is not None else self.reason,
            "enqueued_at": enqueued_at,
        }
        if item_id is not None:
            fields["item_id"] = item_id
        return ReviewItem(**fields)  # type: ignore[arg-type]

    @classmethod
    def from_item(cls, item: ReviewItem) -> ReviewCase:
        """Reconstruct the `ReviewCase` from `item.payload` (raises if not one)."""
        return cls.model_validate(item.payload)

    @classmethod
    def try_from_item(cls, item: ReviewItem) -> ReviewCase | None:
        """The `ReviewCase` in `item.payload`, or `None` if the payload is not one.

        Lets the backlog analyzer read enrichment where present without assuming
        every queued item carries a KGCS case (a bare contract item validates to
        `None` here rather than raising).
        """
        try:
            return cls.model_validate(item.payload)
        except Exception:  # noqa: BLE001 — a non-case payload is a legitimate None
            return None
