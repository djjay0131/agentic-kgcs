"""ADR-0021: fact identity and record identity are two different things.

`kgcs.records` is the seam. These tests pin the two properties the whole fix
rests on — a fact key is invariant under everything that distinguishes one
record of that fact from another, and a record seed reacts to each of those
things — plus the one property that keeps replay working: the seed holds no
clock, so re-observing the same claim from the same evidence at a later
instant is the same record, not a new one.
"""

from datetime import UTC, datetime

import pytest
from kg_contracts.evidence import EvidenceRef, EvidenceRelationship, ValidPeriod
from kg_contracts.identity import new_identity_id
from kg_contracts.testing.factories import make_assertion

from kgcs.records import (
    assertion_fact_key,
    assertion_record_seed,
    fact_key,
    record_seed,
)

SUBJECT = new_identity_id("g1")
OTHER_SUBJECT = new_identity_id("g1")


def _refs(*evidence_ids: str) -> tuple[EvidenceRef, ...]:
    return tuple(
        EvidenceRef(evidence_id=e, relationship=EvidenceRelationship.SUPPORTS)
        for e in evidence_ids
    )


def _seed(**overrides: object) -> str:
    kwargs: dict[str, object] = {
        "fact_id": fact_key(SUBJECT, "proposed_year"),
        "object_value": 2015,
        "object_identity": None,
        "valid_period": None,
        "evidence_refs": _refs("ev_a"),
    }
    kwargs.update(overrides)
    return record_seed(**kwargs)  # type: ignore[arg-type]


class TestFactKey:
    def test_the_same_slot_always_yields_the_same_fact_key(self) -> None:
        assert fact_key(SUBJECT, "proposed_year") == fact_key(SUBJECT, "proposed_year")

    def test_a_different_subject_is_a_different_fact(self) -> None:
        assert fact_key(SUBJECT, "proposed_year") != fact_key(OTHER_SUBJECT, "proposed_year")

    def test_a_different_predicate_is_a_different_fact(self) -> None:
        assert fact_key(SUBJECT, "proposed_year") != fact_key(SUBJECT, "founded_year")

    def test_the_subject_predicate_boundary_cannot_be_smudged(self) -> None:
        """`("ab", "c")` and `("a", "bc")` must not digest to one key.

        Naive concatenation collides here; the NUL join is what prevents it.
        Written with literal strings because the point is the *joining*, not
        the values.
        """
        assert fact_key("ab", "c") != fact_key("a", "bc")

    @pytest.mark.parametrize(
        "update",
        [
            {"object_value": 2014},
            {"evidence_refs": _refs("ev_b")},
            {"recorded_at": datetime(2030, 1, 1, tzinfo=UTC)},
            {"valid_period": ValidPeriod(valid_from=datetime(2020, 1, 1, tzinfo=UTC))},
        ],
        ids=["object", "evidence", "recorded_at", "valid_period"],
    )
    def test_the_fact_key_is_invariant_under_record_differences(
        self, update: dict[str, object]
    ) -> None:
        """Everything that makes two records differ leaves the fact unchanged.

        This is the half of the fix that says an evidence-free identity is
        *correct*: the same fact from two sources is one fact.
        """
        base = make_assertion(subject_identity=SUBJECT, predicate="proposed_year")
        variant = base.model_copy(update=update)
        assert assertion_fact_key(variant) == assertion_fact_key(base)


class TestRecordSeed:
    def test_identical_content_yields_an_identical_seed(self) -> None:
        assert _seed() == _seed()

    def test_new_evidence_is_a_new_record(self) -> None:
        assert _seed(evidence_refs=_refs("ev_b")) != _seed(evidence_refs=_refs("ev_a"))

    def test_additional_evidence_is_a_new_record(self) -> None:
        assert _seed(evidence_refs=_refs("ev_a", "ev_b")) != _seed(evidence_refs=_refs("ev_a"))

    def test_evidence_order_is_observable(self) -> None:
        """Order, not set membership. Comparing sets here would go blind to a
        real difference — the citation list is ordered in the plan too."""
        assert _seed(evidence_refs=_refs("ev_a", "ev_b")) != _seed(
            evidence_refs=_refs("ev_b", "ev_a")
        )

    def test_the_evidence_relationship_is_part_of_the_record(self) -> None:
        supports = _refs("ev_a")
        contradicts = (
            EvidenceRef(evidence_id="ev_a", relationship=EvidenceRelationship.CONTRADICTS),
        )
        assert _seed(evidence_refs=supports) != _seed(evidence_refs=contradicts)

    def test_a_different_object_value_is_a_different_record(self) -> None:
        assert _seed(object_value=2014) != _seed(object_value=2015)

    def test_a_different_object_identity_is_a_different_record(self) -> None:
        a = _seed(object_value=None, object_identity=SUBJECT)
        b = _seed(object_value=None, object_identity=OTHER_SUBJECT)
        assert a != b

    def test_a_different_valid_period_is_a_different_record(self) -> None:
        period = ValidPeriod(valid_from=datetime(2020, 1, 1, tzinfo=UTC))
        assert _seed(valid_period=period) != _seed(valid_period=None)

    def test_an_absent_valid_period_reads_as_the_open_interval(self) -> None:
        assert _seed(valid_period=None) == _seed(valid_period=ValidPeriod())

    def test_a_different_fact_is_a_different_record(self) -> None:
        assert _seed(fact_id=fact_key(SUBJECT, "founded_year")) != _seed()

    def test_the_seed_holds_no_clock(self) -> None:
        """A later re-observation of the same claim from the same evidence is a
        replay, not new knowledge — so `recorded_at` is not in the seed.

        Without this the planner would stop being clock-free and a producer
        that lets `created_at` default to `now()` would mint a fresh record on
        every run.
        """
        base = make_assertion(
            subject_identity=SUBJECT,
            predicate="proposed_year",
            object_value=2015,
            evidence_refs=_refs("ev_a"),
            recorded_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        later = base.model_copy(update={"recorded_at": datetime(2026, 9, 1, tzinfo=UTC)})
        assert assertion_record_seed(later) == assertion_record_seed(base)

    def test_the_authority_is_not_in_the_seed(self) -> None:
        """Two authorities citing the same evidence for the same claim make one
        record, deliberately (ADR-0021 §Tradeoffs). Pinned so that changing it
        is a decision, not a drift."""
        base = make_assertion(
            subject_identity=SUBJECT, predicate="proposed_year", authority="source-a"
        )
        other = base.model_copy(update={"authority": "source-b"})
        assert assertion_record_seed(other) == assertion_record_seed(base)
