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
from kg_contracts.assertions import Assertion
from kg_contracts.evidence import EvidenceRef, EvidenceRelationship, Provenance, ValidPeriod
from kg_contracts.identity import new_identity_id
from kg_contracts.testing.factories import make_assertion

from kgcs.ids import DerivedIdFactory
from kgcs.records import (
    UnstableRecordValueError,
    assertion_fact_key,
    assertion_record_seed,
    backfill_record_id,
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
        "provenance": _origin("s3://bucket/a.csv"),
    }
    kwargs.update(overrides)
    return record_seed(**kwargs)  # type: ignore[arg-type]


def _origin(locator: str | None, *, source: str = "csv", actor: str = "p") -> Provenance:
    return Provenance(source=source, source_ref=locator, actor=actor)


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
            {"provenance": Provenance(source="csv", source_ref="other.csv", actor="p")},
            {"authority": "some-other-producer"},
        ],
        ids=["object", "evidence", "recorded_at", "valid_period", "origin", "authority"],
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


class TestOriginIsInTheSeedButTheProcessorIsNot:
    """ADR-0021 as revised: *where the claim came from* is record-distinguishing;
    *which pipeline read it* is not.

    Without the origin the fix does not reach any producer that leaves
    `evidence_refs` empty — KGIS's structured/tabular path links evidence into
    a side registry and never populates the field — so the seed's evidence
    component is the constant `[]` there and two sources of one fact collide.
    """

    def test_a_different_source_locator_is_a_different_record(self) -> None:
        assert _seed(provenance=_origin("s3://bucket/b.csv")) != _seed(
            provenance=_origin("s3://bucket/a.csv")
        )

    def test_a_different_source_type_is_a_different_record(self) -> None:
        assert _seed(provenance=_origin("x", source="jdbc")) != _seed(
            provenance=_origin("x", source="csv")
        )

    def test_no_recorded_origin_is_not_the_same_as_an_empty_one(self) -> None:
        """`provenance=None` must not digest like an origin whose fields are
        empty strings — "we do not know where this came from" and "it came from
        nowhere named" are different statements."""
        assert _seed(provenance=None) != _seed(
            provenance=Provenance(source="", source_ref="", actor="")
        )

    def test_a_different_processor_is_the_same_record(self) -> None:
        """`actor`/`model`/`prompt_version` and `authority` stay out, so
        renaming a producer or re-reading one source row with a newer model
        does not fork the record. Pinned so that changing it is a decision."""
        base = make_assertion(
            subject_identity=SUBJECT,
            predicate="proposed_year",
            authority="source-a",
            provenance=Provenance(source="csv", source_ref="a.csv", actor="pipeline-1"),
        )
        other = base.model_copy(
            update={
                "authority": "source-b",
                "provenance": Provenance(
                    source="csv",
                    source_ref="a.csv",
                    actor="pipeline-2",
                    model="gpt-9",
                    prompt_version="7",
                ),
            }
        )
        assert assertion_record_seed(other) == assertion_record_seed(base)

    def test_the_origin_travels_on_the_stored_row(self) -> None:
        """The backfill property: every seed component is on the row itself.

        The planner drops `source_coordinates.fragment` when it builds
        `Provenance`, so the seed reads `source`/`source_ref` and nothing else
        — a field the graph does not keep could not be recomputed from a
        committed record.
        """
        base = make_assertion(subject_identity=SUBJECT, predicate="proposed_year")
        assert assertion_record_seed(base) == record_seed(
            fact_id=assertion_fact_key(base),
            object_value=base.object_value,
            object_identity=base.object_identity,
            valid_period=base.valid_period,
            evidence_refs=base.evidence_refs,
            provenance=base.provenance,
        )


class TestObjectValuesAreRenderedReproducibly:
    """N3: the seed must never digest a memory address.

    `Assertion.object_value` is typed `object | None`, so an arbitrary object
    is type-permitted. `json.dumps(..., default=str)` would render such an
    object as `<Foo object at 0x7f...>` — two runs over the same logical input
    would mint different record ids, silently breaking the repo's central
    determinism guarantee in the one direction nobody notices.
    """

    def test_an_unrenderable_object_value_is_refused_not_digested(self) -> None:
        class Opaque:
            pass

        with pytest.raises(UnstableRecordValueError, match="reproducibly"):
            _seed(object_value=Opaque())

    def test_two_instances_of_one_opaque_class_do_not_merely_differ(self) -> None:
        """The failure this prevents, stated as the thing that must NOT happen:
        two distinct instances must not produce two distinct seeds — they must
        produce no seed at all."""
        class Opaque:
            pass

        seeds = []
        for _ in range(2):
            try:
                seeds.append(_seed(object_value=Opaque()))
            except UnstableRecordValueError:
                pass
        assert seeds == []

    def test_a_datetime_does_not_collide_with_a_string_of_itself(self) -> None:
        moment = datetime(2015, 1, 1, tzinfo=UTC)
        assert _seed(object_value=moment) != _seed(object_value=str(moment))
        assert _seed(object_value=moment) != _seed(object_value=moment.isoformat())

    def test_two_different_datetimes_are_two_different_records(self) -> None:
        assert _seed(object_value=datetime(2015, 1, 1, tzinfo=UTC)) != _seed(
            object_value=datetime(2016, 1, 1, tzinfo=UTC)
        )

    def test_a_tuple_and_the_equivalent_list_are_one_record(self) -> None:
        """Deliberate, and documented. The stored payload goes through
        `model_dump(mode="json")`, which turns a tuple into a list, so they are
        the same record once committed; digesting them apart would mint two ids
        for one stored row."""
        assert _seed(object_value=(1, 2)) == _seed(object_value=[1, 2])


class TestMigrationBackfill:
    """ADR-0021 §Risks: the new id is a pure function of the stored row."""

    def test_backfill_reproduces_exactly_what_the_planner_mints(self) -> None:
        """The property the whole migration procedure rests on. Asserted across
        the module boundary — the planner mints from a *candidate*, the
        backfill recomputes from the *committed record*, and they must agree.
        """
        from kg_contracts.curation import ResolutionDecision
        from kg_contracts.policy import AdjudicationRoute
        from kg_contracts.testing.factories import make_attribute_candidate

        from kgcs.planner import CurationPlanner, ResolvedCandidate

        candidate = make_attribute_candidate(
            graph_id="g1", subject=SUBJECT, attribute="proposed_year", value=2015
        ).model_copy(update={"evidence_refs": _refs("ev_a")})
        resolution = ResolutionDecision(
            candidate_id=candidate.candidate_id,
            resolved_identity=SUBJECT,
            create_new_identity=False,
            route=AdjudicationRoute.AUTO,
            score_vector={"identity": 0.99},
            matcher_version="m1",
            snapshot_version="0",
            trace_id=candidate.trace_id,
        )
        plan = CurationPlanner().plan(
            [ResolvedCandidate(candidate=candidate, resolution=resolution)]
        ).plan
        assert plan is not None, "fixture broken: candidate produced no plan"
        committed = Assertion.model_validate(
            {**plan.operations[0].payload, "curation_epoch": 1}
        )
        assert backfill_record_id(committed) == committed.assertion_id

    def test_the_backfill_mapping_is_not_injective_and_collisions_are_visible(
        self,
    ) -> None:
        """Two legacy rows of one fact with identical record content map to ONE
        new id. They must be merged, not renamed — and a caller can see it
        coming by checking the computed mapping for collisions before writing.
        """
        base = make_assertion(
            subject_identity=SUBJECT, predicate="proposed_year", evidence_refs=_refs("ev_a")
        )
        legacy_one = base.model_copy(update={"assertion_id": "as_legacy_one"})
        legacy_two = base.model_copy(update={"assertion_id": "as_legacy_two"})
        mapping = {a.assertion_id: backfill_record_id(a) for a in (legacy_one, legacy_two)}
        assert len(set(mapping)) == 2
        assert len(set(mapping.values())) == 1  # the collision, detectable

    def test_the_backfill_honours_an_injected_id_factory(self) -> None:
        """A graph written with a non-default factory must be backfilled with
        that factory, or the recomputed ids are wrong everywhere."""
        record = make_assertion(subject_identity=SUBJECT, predicate="proposed_year")
        assert backfill_record_id(record) == backfill_record_id(
            record, id_factory=DerivedIdFactory()
        )
