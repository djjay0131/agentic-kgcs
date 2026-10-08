"""Evidence-text rendering for adviser prompts (KGPS U7, KGCS issue #49).

Before this module an adviser prompt named evidence only by id
(`evidence_ids: a, b`), so the LLM was asked to judge support without ever
seeing the text it was judging. The KGPS provenance audit (2026-10-07) filed
this as prerequisite **U7** — "Adviser prompts carry evidence *text*, not only
ids" — and KGCS issue #49 tracks the fix here.

The seam is a narrow read port, `EvidenceLookup`, whose only method is
`get(evidence_id) -> Evidence | None`. KGIS's `SqliteEvidenceRegistry` already
satisfies it structurally, so no adapter is needed. The lookup is *optional*
everywhere: with no lookup the adviser renders exactly what it rendered before
(the id-only line), so existing recorded fixtures and behaviour are untouched.

`render_evidence` is a pure, clock-free function of the cited ids, their
citation relationships, and the looked-up `Evidence`. Its output is
deterministic, which is what lets a `RecordedCompletionClient` fixture stay
keyed by request hash:

- **PRESENT** — the text (`span.quote` when a typed span carries one, else
  `content`) truncated to a configurable character count;
- **ABSENT** — an explicit marker naming the `absence_reason`;
- **ERROR** — an explicit marker carrying the `error` from a record the lookup
  *did* return, or the failure from a `get()` call that raised;
- **not found** — an explicit `UNKNOWN` marker (the id was cited but the lookup
  has no such evidence), never silently dropped and never confused with ABSENT.

Rendering is faithful to the issue and to `kg_contracts.evidence`'s design
principle that "no source queried", "source omitted it", "source unavailable"
and a genuinely present fact stay distinct — a model reading the prompt can't
mistake absent data for an inferred fact.

**A failing lookup never escapes.** A `lookup.get(id)` that raises is caught and
rendered as an explicit `ERROR` marker naming the failure; the adviser (and the
orchestrator) therefore degrade to a conservative assessment/baseline rather than
propagating a registry fault. Such an id is *not* counted as rendered — no record
was found — and is reported in `unresolved_ids` alongside a plain not-found id.

Because the rendered line is prompt input the model reads, `evidence_id` and the
citation `relationship` are escaped exactly like the content, so a crafted id or
relationship cannot break the one-evidence-per-line structure or smuggle a
newline into the prompt.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from kg_contracts.evidence import Evidence, EvidenceAvailability

DEFAULT_EVIDENCE_MAX_CHARS = 500
"""Default per-evidence character budget for the text rendered into a prompt.

Configurable on the adviser and on the orchestrator; a positive integer.
"""

UNKNOWN_RELATIONSHIP = "UNKNOWN"
"""Rendered when an evidence id is cited without a known citation relationship."""


@runtime_checkable
class EvidenceLookup(Protocol):
    """The narrow read port an adviser uses to resolve evidence text.

    Deliberately one method: `get`. KGIS's `SqliteEvidenceRegistry.get` already
    satisfies this Protocol, so any registry-shaped store can be injected
    without an adapter. `None` means the id is not known to the lookup — a
    distinct state from ABSENT evidence, which the registry does hold.
    """

    def get(self, evidence_id: str) -> Evidence | None: ...


@dataclass(frozen=True)
class EvidenceRender:
    """The deterministic result of rendering a set of cited evidence ids.

    `lines` are the prompt lines, one per cited id, in citation order;
    `rendered_ids` are the ids actually rendered **from a found record** (PRESENT
    text, or an ABSENT/ERROR marker the lookup returned); `unresolved_ids` are the
    ids for which no record was found — a not-found id (`UNKNOWN` marker) or one
    whose `get()` raised (`ERROR` marker) — kept separate so provenance does not
    claim a record that never existed; `truncated` is `True` iff any PRESENT text
    hit the character budget.
    """

    lines: tuple[str, ...]
    rendered_ids: tuple[str, ...]
    unresolved_ids: tuple[str, ...]
    truncated: bool


def render_evidence(
    evidence_ids: tuple[str, ...],
    *,
    lookup: EvidenceLookup,
    relationships: Mapping[str, str] | None = None,
    max_chars: int = DEFAULT_EVIDENCE_MAX_CHARS,
) -> EvidenceRender:
    """Render one deterministic prompt line per cited evidence id.

    Caller contract: `evidence_ids` order is part of the request key, so callers
    must pass a stable order (an ordered container), as `build_request` already
    requires. `relationships` maps an id to its citation relationship
    (`SUPPORTS`/`CONTRADICTS`/...); an id without one renders `UNKNOWN`.

    A `lookup.get()` that raises is caught here: the id renders an explicit
    `ERROR` marker and is reported in `unresolved_ids`, so a broken registry can
    never make rendering (or the adviser, or `resolve`) raise.
    """
    if max_chars < 1:
        raise ValueError("max_chars must be >= 1")
    rel = relationships or {}
    lines: list[str] = []
    rendered: list[str] = []
    unresolved: list[str] = []
    truncated = False
    for evidence_id in evidence_ids:
        relationship = rel.get(evidence_id)
        try:
            evidence = lookup.get(evidence_id)
        except Exception as exc:  # noqa: BLE001 — a broken lookup degrades, never escapes
            lines.append(
                _error_line(
                    evidence_id,
                    relationship,
                    f"lookup error: {type(exc).__name__}: {exc}",
                )
            )
            unresolved.append(evidence_id)
            continue
        line, hit_budget = _render_one(evidence_id, relationship, evidence, max_chars)
        lines.append(line)
        truncated = truncated or hit_budget
        if evidence is None:
            unresolved.append(evidence_id)
        else:
            rendered.append(evidence_id)
    return EvidenceRender(tuple(lines), tuple(rendered), tuple(unresolved), truncated)


def _render_one(
    evidence_id: str,
    relationship: str | None,
    evidence: Evidence | None,
    max_chars: int,
) -> tuple[str, bool]:
    """Render one evidence line; returns (line, was_truncated)."""
    prefix = _prefix(evidence_id, relationship)
    if evidence is None:
        return f"{prefix}, availability=UNKNOWN: not found in evidence lookup", False
    if evidence.availability is EvidenceAvailability.PRESENT:
        text, truncated = _present_text(evidence, max_chars)
        return f'{prefix}, availability=PRESENT: "{text}"', truncated
    if evidence.availability is EvidenceAvailability.ABSENT:
        reason = (
            evidence.absence_reason.value if evidence.absence_reason is not None else "UNSPECIFIED"
        )
        return f"{prefix}, availability=ABSENT: reason={_escape(reason)}", False
    error = evidence.error if evidence.error is not None else "unspecified"
    return f'{prefix}, availability=ERROR: error="{_escape(error)}"', False


def _prefix(evidence_id: str, relationship: str | None) -> str:
    """The escaped `[id] relationship=...` prefix shared by every marker line."""
    rel = _escape(relationship or UNKNOWN_RELATIONSHIP)
    return f"[{_escape(evidence_id)}] relationship={rel}"


def _error_line(evidence_id: str, relationship: str | None, error: str) -> str:
    """An explicit `ERROR` marker for a lookup that raised on `evidence_id`."""
    return f'{_prefix(evidence_id, relationship)}, availability=ERROR: error="{_escape(error)}"'


def _present_text(evidence: Evidence, max_chars: int) -> tuple[str, bool]:
    """The PRESENT text — span quote preferred, else content — bounded to `max_chars`.

    A PRESENT evidence may carry only a `payload_hash` (no text); that is
    rendered as the hash so the model still knows something is there but cannot
    read it. Truncation is marked with a trailing `...` *and* returned as a flag
    so it is recorded on the assessment provenance.
    """
    quote = _span_quote(evidence)
    source = quote if quote is not None else evidence.content
    if source is None:
        return f"payload_hash={evidence.payload_hash or 'unknown'}", False
    truncated = len(source) > max_chars
    text = _escape(source[:max_chars])
    if truncated:
        text = text + "..."
    return text, truncated


def _span_quote(evidence: Evidence) -> str | None:
    """The typed span quote when the contract carries one (KGIS issue #56, U1).

    `Evidence.span` does not exist in the pinned `agentic-kgis` yet, so this is
    read defensively: when the field lands, a non-empty `span.quote` is preferred
    over `content`; until then it is `None` and `content` is used unchanged.
    """
    span = getattr(evidence, "span", None)
    quote = getattr(span, "quote", None)
    if isinstance(quote, str) and quote:
        return quote
    return None


def _escape(text: str) -> str:
    """Escape text so a rendered evidence line stays on one deterministic line.

    A literal newline in evidence content would otherwise break the prompt's
    line structure; escaping keeps one evidence per line. Backslash first so an
    existing escape sequence is not double-decoded.
    """
    return (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )
