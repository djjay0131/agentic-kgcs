"""E2E — evidence evolution: one fact, two records, nothing lost (ADR-0021).

The release-critical acceptance criterion the `agentic-kg` adopter is gated
on, demonstrated rather than asserted:

    a fact is asserted citing evidence A
    → the same fact is re-asserted citing evidence B
    → BOTH records exist
    → the ordinary read shows the LATEST
    → the prior record is reachable with `include_superseded=True`
    → nothing vanished, and the prior record still cites evidence A

Everything here is asserted **by identity**, never by counting rows. A count
is exactly what hid this defect: an in-place overwrite and a genuine second
record both leave "1 live row", and a plan that superseded itself left "1
historical row" while the fact had disappeared from the live graph. So each
test names the ids and the evidence it expects to find.

The producer shape is the adopter's own: `candidate_id` is derived from
`(graph_id, candidate_kind, semantic_key)` with evidence deliberately
**excluded**, so re-ingesting the same fact mints the *same* candidate id.
That is correct — the same fact from two sources is corroboration, not two
facts — and it is the shape under which all three routes used to fail.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from kg_contracts.assertions import Assertion, CurationStatus
from kg_contracts.candidates import AttributeAssertionCandidate
from kg_contracts.curation import CurationPlan
from kg_contracts.evidence import EvidenceRef, EvidenceRelationship
from kg_contracts.stores import GraphReadOptions
from kg_contracts.testing.factories import make_attribute_candidate, make_scores

from e2e_harness import (
    AUTO_SOURCE_RELIABILITY,
    REGISTRY_CONFIDENCE_POLICY,
    SUPPORTED_WITH_RETRACT,
    T0,
    E2EGraphStore,
)
from kgcs import CurationEngine, ExecutionOutcome, FixedClock, PlanExecutor
from kgcs.recuration import ConceptEvolutionPlanner, CurationTrigger, TriggerKind

GRAPH_ID = "evidence-evolution"
ATTRIBUTE = "proposed_year"
VALUE = 2015
_CLOCK = FixedClock(datetime(2026, 8, 22, tzinfo=UTC))
_LATER = datetime(2026, 9, 1, tzinfo=UTC)

#: Crockford base32, the ULID character class — the adopter's id alphabet.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode(data: bytes, length: int) -> str:
    value = int.from_bytes(data, byteorder="big")
    chars = ["0"] * length
    for index in range(length - 1, -1, -1):
        chars[index] = _ALPHABET[value & 0x1F]
        value >>= 5
    return "".join(chars)


def adopter_candidate_id(graph_id: str, candidate_kind: str, semantic_key: str) -> str:
    """`kgis.ids.DeterministicIdStrategy.candidate_id`, reproduced in shape.

    Reimplemented rather than imported: `kgis` is the adopter's *producer*, not
    a KGCS dependency, and the point of the test is that KGCS behaves correctly
    under **this shape of id**, not that one particular library computes it.
    Evidence is not an input — deliberately.
    """
    digest = hashlib.blake2b(
        b"\x00".join(p.encode("utf-8") for p in (graph_id, candidate_kind, semantic_key)),
        digest_size=16,
    ).digest()
    return "cand_" + _encode(digest, 26)


def _candidate(subject: str, evidence_id: str) -> AttributeAssertionCandidate:
    """The SAME proposed fact, cited from a different piece of evidence."""
    candidate = make_attribute_candidate(
        graph_id=GRAPH_ID,
        subject=subject,
        attribute=ATTRIBUTE,
        value=VALUE,
        scores=make_scores(extraction_confidence=1.0, source_reliability=AUTO_SOURCE_RELIABILITY),
    )
    return candidate.model_copy(  # type: ignore[return-value]
        update={
            "candidate_id": adopter_candidate_id(
                GRAPH_ID, "attribute_assertion", candidate.semantic_key
            ),
            "trace_id": f"trace_{evidence_id}",
            "created_at": T0,
            "evidence_refs": (
                EvidenceRef(evidence_id=evidence_id, relationship=EvidenceRelationship.SUPPORTS),
            ),
        }
    )


def _engine(snapshot_version: str) -> CurationEngine:
    return CurationEngine.create(
        graph_id=GRAPH_ID,
        confidence_policy=REGISTRY_CONFIDENCE_POLICY,
        clock=_CLOCK,
        snapshot_version=snapshot_version,
    )


def _plan_for(candidate: AttributeAssertionCandidate, snapshot_version: str) -> CurationPlan:
    plan = _engine(snapshot_version).curate([candidate]).plan
    assert plan is not None, "fixture broken: the candidate produced no plan"
    return plan


def _evidence_of(assertion: Assertion) -> list[str]:
    return [ref.evidence_id for ref in assertion.evidence_refs]


def _by_id(assertions: list[Assertion]) -> dict[str, Assertion]:
    return {a.assertion_id: a for a in assertions}


@pytest.fixture
def graph() -> tuple[E2EGraphStore, PlanExecutor, str, Assertion]:
    """A graph holding one identity and one assertion of one fact, citing ev_A."""
    store = E2EGraphStore()
    executor = PlanExecutor(store, clock=_CLOCK, supported_operations=SUPPORTED_WITH_RETRACT)

    from e2e_harness import PAPER_SHAPE, entity_candidate

    entity_plan = _plan_for(entity_candidate(PAPER_SHAPE, graph_id=GRAPH_ID), "0")  # type: ignore[arg-type]
    assert executor.execute(entity_plan).outcome is ExecutionOutcome.COMMITTED
    subject = str(entity_plan.operations[0].reversal_data["identity_id"])

    first = _plan_for(_candidate(subject, "ev_A"), str(store.current_epoch()))
    assert executor.execute(first).outcome is ExecutionOutcome.COMMITTED
    (record_a,) = store.assertions_for(subject)
    assert _evidence_of(record_a) == ["ev_A"], "fixture broken: evidence A did not land"
    return store, executor, subject, record_a


class TestEvidenceEvolutionRoundTrip:
    def test_a_re_assertion_with_new_evidence_supersedes_the_prior_record(
        self, graph: tuple[E2EGraphStore, PlanExecutor, str, Assertion]
    ) -> None:
        """The full round trip, asserted by identity at every step."""
        store, executor, subject, record_a = graph

        # The adopter re-ingests the SAME fact from a second source. Its
        # candidate id is unchanged — evidence is not an input to it.
        candidate_b = _candidate(subject, "ev_B")
        assert candidate_b.candidate_id == adopter_candidate_id(
            GRAPH_ID, "attribute_assertion", candidate_b.semantic_key
        )
        plan_b = _plan_for(candidate_b, str(store.current_epoch()))
        record_b = Assertion.model_validate(
            {**plan_b.operations[0].payload, "curation_epoch": 0}
        )

        # A NEW record of the SAME fact: the identities differ.
        assert record_b.assertion_id != record_a.assertion_id

        trigger = CurationTrigger.of(
            kind=TriggerKind.NEW_EVIDENCE,
            assertion_ids=(record_a.assertion_id,),
            evidence_ids=("ev_B",),
            reason="a second source cites this fact",
        )
        result = ConceptEvolutionPlanner(
            snapshot_version=str(store.current_epoch())
        ).plan_supersession(
            old_assertion=record_a, new_assertion=record_b, trigger=trigger
        )
        assert result.plan is not None
        assert executor.execute(result.plan).outcome is ExecutionOutcome.COMMITTED

        # --- read back -----------------------------------------------------
        live = _by_id(store.assertions_for(subject))
        history = _by_id(
            store.assertions_for(subject, GraphReadOptions(include_superseded=True))
        )

        # BOTH records exist.
        assert set(history) == {record_a.assertion_id, record_b.assertion_id}

        # The ordinary read shows the LATEST, and only it.
        assert set(live) == {record_b.assertion_id}
        assert _evidence_of(live[record_b.assertion_id]) == ["ev_B"]

        # The prior record is reachable with include_superseded, still marked
        # SUPERSEDED, still citing the evidence it was asserted on.
        prior = history[record_a.assertion_id]
        assert prior.status is CurationStatus.SUPERSEDED
        assert prior.superseded_at == record_b.recorded_at
        assert _evidence_of(prior) == ["ev_A"]

        # Nothing vanished: the fact itself is still asserted, with the same
        # subject, predicate and value it always had.
        current = live[record_b.assertion_id]
        assert (current.subject_identity, current.predicate, current.object_value) == (
            subject,
            ATTRIBUTE,
            VALUE,
        )

    def test_the_prior_record_is_invisible_to_an_ordinary_read(
        self, graph: tuple[E2EGraphStore, PlanExecutor, str, Assertion]
    ) -> None:
        """`include_superseded` is doing real work — the history surface is not
        just the live read under another name."""
        store, executor, subject, record_a = graph
        record_b = Assertion.model_validate(
            {
                **_plan_for(
                    _candidate(subject, "ev_B"), str(store.current_epoch())
                ).operations[0].payload,
                "curation_epoch": 0,
            }
        )
        result = ConceptEvolutionPlanner(
            snapshot_version=str(store.current_epoch())
        ).plan_supersession(
            old_assertion=record_a,
            new_assertion=record_b,
            trigger=CurationTrigger.of(kind=TriggerKind.NEW_EVIDENCE, evidence_ids=("ev_B",)),
        )
        assert result.plan is not None
        assert executor.execute(result.plan).outcome is ExecutionOutcome.COMMITTED

        assert record_a.assertion_id not in _by_id(store.assertions_for(subject))
        assert record_a.assertion_id in _by_id(
            store.assertions_for(subject, GraphReadOptions(include_superseded=True))
        )

    def test_the_successor_can_be_minted_from_the_committed_record_alone(
        self, graph: tuple[E2EGraphStore, PlanExecutor, str, Assertion]
    ) -> None:
        """The re-curation path: a caller holding only the graph record, with no
        candidate in hand, can still mint the next record of that fact."""
        store, executor, subject, record_a = graph
        planner = ConceptEvolutionPlanner(snapshot_version=str(store.current_epoch()))
        successor = planner.next_record(
            record_a,
            evidence_refs=(
                EvidenceRef(evidence_id="ev_B", relationship=EvidenceRelationship.SUPPORTS),
            ),
            recorded_at=_LATER,
        )
        result = planner.plan_supersession(
            old_assertion=record_a,
            new_assertion=successor,
            trigger=CurationTrigger.of(kind=TriggerKind.NEW_EVIDENCE, evidence_ids=("ev_B",)),
        )
        assert result.plan is not None
        assert executor.execute(result.plan).outcome is ExecutionOutcome.COMMITTED

        live = _by_id(store.assertions_for(subject))
        history = _by_id(
            store.assertions_for(subject, GraphReadOptions(include_superseded=True))
        )
        assert set(live) == {successor.assertion_id}
        assert set(history) == {record_a.assertion_id, successor.assertion_id}
        assert _evidence_of(history[record_a.assertion_id]) == ["ev_A"]
        assert _evidence_of(live[successor.assertion_id]) == ["ev_B"]

    def test_the_fact_survives_three_successive_evidence_arrivals(
        self, graph: tuple[E2EGraphStore, PlanExecutor, str, Assertion]
    ) -> None:
        """Evolution, not a single hop: each arrival retires exactly its
        predecessor and the chain of records is complete."""
        store, executor, subject, record_a = graph
        planner = ConceptEvolutionPlanner(snapshot_version="0")
        chain = [record_a]
        for index, evidence_id in enumerate(("ev_B", "ev_C", "ev_D"), start=1):
            current = chain[-1]
            successor = planner.next_record(
                current,
                evidence_refs=(
                    EvidenceRef(
                        evidence_id=evidence_id, relationship=EvidenceRelationship.SUPPORTS
                    ),
                ),
                recorded_at=datetime(2026, 9, index, tzinfo=UTC),
            )
            result = ConceptEvolutionPlanner(
                snapshot_version=str(store.current_epoch())
            ).plan_supersession(
                old_assertion=current,
                new_assertion=successor,
                trigger=CurationTrigger.of(
                    kind=TriggerKind.NEW_EVIDENCE, evidence_ids=(evidence_id,)
                ),
            )
            assert result.plan is not None
            assert executor.execute(result.plan).outcome is ExecutionOutcome.COMMITTED
            chain.append(successor)

        history = _by_id(
            store.assertions_for(subject, GraphReadOptions(include_superseded=True))
        )
        assert set(history) == {record.assertion_id for record in chain}
        assert [_evidence_of(history[r.assertion_id]) for r in chain] == [
            ["ev_A"],
            ["ev_B"],
            ["ev_C"],
            ["ev_D"],
        ]
        # Exactly the last record is live; every earlier one is SUPERSEDED.
        assert set(_by_id(store.assertions_for(subject))) == {chain[-1].assertion_id}
        assert [history[r.assertion_id].status for r in chain[:-1]] == [
            CurationStatus.SUPERSEDED
        ] * 3

    def test_a_replay_of_the_committed_record_is_not_a_second_record(
        self, graph: tuple[E2EGraphStore, PlanExecutor, str, Assertion]
    ) -> None:
        """The boundary. Re-planning the SAME candidate with the SAME evidence
        must still mint the same record id, or the fix would have traded one
        silent duplication for another."""
        store, _executor, subject, record_a = graph
        replayed = _plan_for(_candidate(subject, "ev_A"), str(store.current_epoch()))
        assert replayed.operations[0].payload["assertion_id"] == record_a.assertion_id
