"""Wave 5 / DG-1: re-curation triggers enqueue work but never mutate canonical
data; enqueueing is idempotent by content-addressed `trigger_id`; handling is
idempotent and trace-linked (§9 law 11)."""

from kgcs.recuration import (
    CurationTrigger,
    InMemoryTriggerQueue,
    TriggerKind,
    VersionContext,
    merge_evidence,
    trigger_provenance,
)


def _trigger(**overrides: object) -> CurationTrigger:
    kwargs: dict[str, object] = {
        "kind": TriggerKind.NEW_EVIDENCE,
        "identity_ids": ("kg://g1/identity/A",),
        "evidence_ids": ("ev_1",),
        "reason": "new paper",
        "trace_id": "trace-1",
    }
    kwargs.update(overrides)
    return CurationTrigger.of(**kwargs)  # type: ignore[arg-type]


# --- deterministic, content-addressed identity ------------------------------


def test_trigger_id_is_deterministic_from_content() -> None:
    assert _trigger().trigger_id == _trigger().trigger_id


def test_trigger_id_is_order_independent_for_evidence_and_targets() -> None:
    a = CurationTrigger.of(
        kind=TriggerKind.NEW_EVIDENCE,
        identity_ids=("i1", "i2"),
        evidence_ids=("ev_a", "ev_b"),
    )
    b = CurationTrigger.of(
        kind=TriggerKind.NEW_EVIDENCE,
        identity_ids=("i2", "i1"),
        evidence_ids=("ev_b", "ev_a"),
    )
    assert a.trigger_id == b.trigger_id


def test_reason_and_trace_id_do_not_change_identity() -> None:
    a = _trigger(reason="one", trace_id="t1")
    b = _trigger(reason="two", trace_id="t2")
    assert a.trigger_id == b.trigger_id


def test_version_context_is_part_of_identity() -> None:
    base = _trigger()
    bumped = _trigger(
        kind=TriggerKind.ONTOLOGY_OR_POLICY_VERSION_CHANGED,
        version_context=VersionContext(ontology_version="2"),
    )
    assert base.trigger_id != bumped.trigger_id


def test_target_refs_concatenates_all_ref_kinds() -> None:
    trigger = CurationTrigger.of(
        kind=TriggerKind.OPERATOR_REQUEST,
        identity_ids=("i1",),
        assertion_ids=("as_1",),
        concept_keys=("concept/x",),
    )
    assert trigger.target_refs == ("i1", "as_1", "concept/x")


# --- enqueue idempotency ----------------------------------------------------


def test_enqueue_same_trigger_twice_is_one_entry() -> None:
    queue = InMemoryTriggerQueue()
    trigger = _trigger()
    assert queue.enqueue(trigger) is True
    assert queue.enqueue(trigger) is False  # duplicate is a no-op
    assert len(queue.pending()) == 1
    assert len(queue.all_triggers()) == 1


def test_reenqueue_does_not_resurrect_a_handled_trigger() -> None:
    queue = InMemoryTriggerQueue()
    trigger = _trigger()
    queue.enqueue(trigger)
    queue.mark_handled(trigger.trigger_id)
    assert queue.enqueue(trigger) is False
    assert queue.pending() == ()
    assert queue.is_handled(trigger.trigger_id) is True


# --- handling: idempotent, trace-linked, ordered ----------------------------


def test_pending_excludes_handled_and_preserves_order() -> None:
    queue = InMemoryTriggerQueue()
    first = _trigger(identity_ids=("i1",))
    second = _trigger(identity_ids=("i2",))
    queue.enqueue(first)
    queue.enqueue(second)
    queue.mark_handled(first.trigger_id)
    assert queue.pending() == (second,)


def test_mark_handled_is_idempotent_and_ignores_unknown() -> None:
    queue = InMemoryTriggerQueue()
    trigger = _trigger()
    queue.enqueue(trigger)
    queue.mark_handled(trigger.trigger_id)
    queue.mark_handled(trigger.trigger_id)  # no error, still handled
    queue.mark_handled("trg_unknown")  # ignored
    assert queue.is_handled(trigger.trigger_id) is True


def test_trigger_is_trace_linked() -> None:
    trigger = _trigger(trace_id="trace-xyz")
    assert trigger.trace_id == "trace-xyz"
    provenance = trigger_provenance(
        trigger, matcher_version="m/1", adviser_version="a/1", policy_version="1"
    )
    assert provenance["trigger_id"] == trigger.trigger_id
    assert provenance["trace_id"] == "trace-xyz"
    assert provenance["evidence_ids"] == ["ev_1"]


# --- triggers never mutate canonical data -----------------------------------


def test_queue_never_mutates_canonical_data() -> None:
    # A stand-in "canonical store": the queue is handed no reference to it and
    # cannot touch it. Enqueueing and handling leaves it byte-for-byte unchanged.
    canonical = {"kg://g1/identity/A": {"status": "ACTIVE"}}
    before = {k: dict(v) for k, v in canonical.items()}
    queue = InMemoryTriggerQueue()
    trigger = _trigger()
    queue.enqueue(trigger)
    queue.mark_handled(trigger.trigger_id)
    assert canonical == before


def test_queue_holds_no_writable_graph_state() -> None:
    # Structural check: the queue's only state is its own bookkeeping.
    queue = InMemoryTriggerQueue()
    assert not hasattr(queue, "store")
    assert not hasattr(queue, "graph")


# --- evidence merge helper --------------------------------------------------


def test_merge_evidence_dedupes_first_seen() -> None:
    trigger = _trigger(evidence_ids=("ev_1", "ev_2"))
    assert merge_evidence(trigger, ("ev_2", "ev_3")) == ("ev_1", "ev_2", "ev_3")
