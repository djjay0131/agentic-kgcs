"""E2E scenario 1 — structured source, deterministic auto path (Wave 8).

    KGIS ingestion → candidate ledger → KGCS deterministic curation → executor
    → canonical graph → a visible curation epoch.

Two proofs of the same path:

- `TestRealKgisComposition` drives the *actual* `kgis.IngestPipeline` (structured
  mode: a roster read from an in-memory source) to emit contract-valid
  `Candidate`s into a `MemoryCandidateSink`, then hands the ledger's contents to
  KGCS. This is genuine cross-repo composition over the shared `kg_contracts`
  boundary — KGIS is the real ingestion package, not a stand-in.
- `TestContractShapeComposition` represents the KGIS side by its contract output
  (factory-built candidates whose subject is already a minted identity) so both
  a `CREATE_IDENTITY` *and* an `ATTACH_ASSERTION` auto-apply — proving the
  canonical entity **and** assertion are readable after one epoch.

Both assert §9 law 3 end to end: the store advances only when a `CurationPlan`
is executed, never otherwise.
"""

from __future__ import annotations

from datetime import UTC, datetime

from e2e_harness import (
    REGISTRY_CONFIDENCE_POLICY,
    SENSOR_SHAPE,
    attribute_candidate,
    entity_candidate,
)
from kg_contracts.testing.memory import MemoryCandidateSink, MemoryGraphStore
from kgis import (
    AttributeCandidateBuilder,
    CompositeCandidateBuilder,
    DeterministicIdStrategy,
    EntityCandidateBuilder,
    FieldSpec,
    FixedClock,
    IngestPipeline,
    IterableRecordReader,
    SchemaNormalizer,
    SourceScoring,
)

from kgcs import (
    CurationEngine,
    ExecutionOutcome,
    FixedClock as KgcsFixedClock,
    InMemoryEpochPublisher,
    PlanExecutor,
)

GRAPH = "roster"
_CLOCK = KgcsFixedClock(datetime(2026, 8, 22, tzinfo=UTC))

_ROSTER: tuple[dict[str, str], ...] = (
    {"id": "1", "name": "Ada", "height_cm": "170"},
    {"id": "2", "name": "Grace", "height_cm": "175"},
    {"id": "3", "name": "Alan", "height_cm": "180"},
)


def _kgis_pipeline(sink: MemoryCandidateSink) -> IngestPipeline:
    """A real, fully-deterministic structured `kgis` ingestion job."""
    entity = EntityCandidateBuilder(
        entity_type="Player", namespace="roster", key_field="id", display_name_field="name"
    )
    attributes = AttributeCandidateBuilder(subject=entity, attribute_fields=("height_cm",))
    return IngestPipeline(
        graph_id=GRAPH,
        reader=IterableRecordReader(_ROSTER),
        normalizer=SchemaNormalizer(
            [
                FieldSpec(name="id", type="str", required=True),
                FieldSpec(name="name", type="str"),
                FieldSpec(name="height_cm", type="int"),
            ]
        ),
        builder=CompositeCandidateBuilder([entity, attributes]),
        sink=sink,
        scoring=SourceScoring(source_reliability=0.99),
        ontology_strict=False,
        clock=FixedClock(datetime(2026, 7, 14, tzinfo=UTC)),
        ids=DeterministicIdStrategy(),
        run_id="run-e2e",
        job_id="job-e2e",
    )


def _engine(snapshot_version: str = "0") -> CurationEngine:
    return CurationEngine.create(
        graph_id=GRAPH,
        confidence_policy=REGISTRY_CONFIDENCE_POLICY,
        clock=_CLOCK,
        snapshot_version=snapshot_version,
    )


class TestRealKgisComposition:
    def test_kgis_ingest_then_kgcs_curate_reaches_a_visible_epoch(self) -> None:
        # 1. KGIS ingests a structured roster into the shared candidate ledger.
        ledger = MemoryCandidateSink()
        report = _kgis_pipeline(ledger).run()
        assert report.candidates_submitted > 0

        # The ledger now holds real, contract-valid candidates KGIS produced.
        candidates = [entry.candidate for entry in ledger.ledger_entries()]
        assert {c.candidate_kind for c in candidates} == {"entity", "attribute_assertion"}

        # 2. KGCS curates the ledger's contents deterministically.
        store = MemoryGraphStore()
        publisher = InMemoryEpochPublisher()
        executor = PlanExecutor(store, clock=_CLOCK, epoch_publisher=publisher)
        assert store.current_epoch() == 0  # nothing yet — law 3

        result = _engine().curate(candidates)
        assert result.plan is not None
        record = executor.execute(result.plan)

        # 3. The canonical graph advanced exactly one visible epoch.
        assert record.outcome is ExecutionOutcome.COMMITTED
        assert store.current_epoch() == 1
        assert publisher.published_epoch() == 1

        # Every roster player is a readable canonical entity at the new epoch.
        players = store.find_entities(entity_type="Player")
        assert len(players) == len(_ROSTER)
        assert all(p.curation_epoch == 1 for p in players)

    def test_attributes_defer_pending_er_never_silently_dropped(self) -> None:
        # KGIS attribute candidates carry an EntityRef subject (not a minted id),
        # so KGCS conservatively escalates them off the auto path — an honest
        # deferral, not a silent drop. They stay in the ledger for later ER.
        ledger = MemoryCandidateSink()
        _kgis_pipeline(ledger).run()
        candidates = [entry.candidate for entry in ledger.ledger_entries()]

        result = _engine().curate(candidates)
        attribute_outcomes = [
            o
            for o, c in zip(result.outcomes, candidates)
            if c.candidate_kind == "attribute_assertion"
        ]
        # Resolved (never dropped) but not auto-applied: no operation planned.
        assert attribute_outcomes and all(o.resolution is not None for o in attribute_outcomes)
        assert result.plan is not None
        planned_kinds = {op.type.value for op in result.plan.operations}
        assert planned_kinds == {"CREATE_IDENTITY"}


class TestContractShapeComposition:
    def test_entity_and_assertion_both_commit_and_are_readable(self) -> None:
        # Represent the KGIS side by its contract output so a subject is already
        # a minted identity — then both an entity and an assertion auto-apply.
        shape = SENSOR_SHAPE
        ledger = MemoryCandidateSink()
        store = MemoryGraphStore()
        executor = PlanExecutor(store, clock=_CLOCK)

        # Entity first: submit to the ledger, curate, execute → the minted id.
        entity = entity_candidate(shape, graph_id=GRAPH)
        ledger.submit([entity])
        entity_result = _engine().curate([entity])
        assert entity_result.plan is not None
        assert executor.execute(entity_result.plan).outcome is ExecutionOutcome.COMMITTED
        identity_id = str(entity_result.plan.operations[0].reversal_data["identity_id"])

        # Attribute about that identity: submit, curate, execute → epoch 2.
        attribute = attribute_candidate(
            shape,
            graph_id=GRAPH,
            subject=identity_id,
            value=shape.value_a,
            evidence_id=shape.ev_a,
            cid="cand_sensor_attr_a",
            trace="trace_sensor_a",
        )
        ledger.submit([attribute])
        # The attribute is curated against the *current* snapshot (epoch 1), so
        # its plan-level snapshot precondition matches and it commits at epoch 2.
        attr_result = _engine(snapshot_version=str(store.current_epoch())).curate([attribute])
        assert attr_result.plan is not None
        assert executor.execute(attr_result.plan).outcome is ExecutionOutcome.COMMITTED

        # Both the canonical entity and its assertion are readable.
        assert store.current_epoch() == 2
        assert store.get_entity(identity_id) is not None
        assertions = store.assertions_for(identity_id)
        assert len(assertions) == 1
        assert assertions[0].predicate == shape.attribute
        assert assertions[0].object_value == shape.value_a
        # The committed assertion cites the source evidence (§9 law 9).
        assert shape.ev_a in {ref.evidence_id for ref in assertions[0].evidence_refs}
