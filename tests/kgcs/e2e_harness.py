"""Wave 8 end-to-end harness: wire the KGIS→KGCS pipeline once, reuse it.

A plain importable module (like `helpers.py`), not a `conftest.py`, so the E2E
test files can `from e2e_harness import ...`. It holds *test-only* orchestration
— nothing here belongs in `src/kgcs`. Two things earn their place:

- `E2EGraphStore` — the reference `MemoryGraphStore` is a Plan-1 adapter whose
  `apply()` supports only `CREATE_IDENTITY` and `ATTACH_ASSERTION`; the other
  five `CurationOperationType`s raise `NotImplementedError`. To execute a
  *supersession* plan end-to-end (attach the new assertion, then mark the prior
  one `SUPERSEDED`) we need an adapter that also applies `RETRACT_ASSERTION`.
  `E2EGraphStore` adds exactly that, composing the contract's own writer
  primitive (`mark_superseded`) — so the plan → executor → `GraphMutationStore`
  discipline (§9 law 3) is untouched; only the adapter's op coverage widens,
  never the frozen contract. `MERGE`/`SPLIT`/`REASSIGN`/`PROMOTE` are left
  deliberately unsupported so the merge+compensation scenario can prove the
  honest `UNSUPPORTED_OPERATION` rollback path on the reference surface.

- Source *shapes* — the flagship re-curation flow must be domain-neutral, not
  hard-coded to research papers. A `SourceShape` describes one domain's entity
  and its evolving attribute; `PAPER_SHAPE` (a research concept whose claimed
  year is corrected by a second paper) and `SENSOR_SHAPE` (a device whose status
  reading is superseded by a later observation) drive the *same* pipeline, so a
  parametrized flagship test proves the architecture, not the vocabulary.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from kg_contracts.assertions import Assertion, CanonicalEntity, CurationStatus
from kg_contracts.candidates import (
    AttributeAssertionCandidate,
    Candidate,
    EntityCandidate,
)
from kg_contracts.curation import CurationOperationType, Precondition
from kg_contracts.evidence import EvidenceRef, EvidenceRelationship
from kg_contracts.policy import ConfidencePolicy
from kg_contracts.stores import CommitResult, GraphMutationBatch
from kg_contracts.testing.factories import (
    make_attribute_candidate,
    make_entity_candidate,
    make_scores,
)
from kg_contracts.testing.memory import MemoryGraphStore

# The AUTO band's scores: an authoritative structured source reads its rows
# exactly (extraction 1.0) from a reliable registry (source 0.99). Identity
# confidence is intentionally omitted — a structured registry's admission does
# not hinge on it (see REGISTRY_CONFIDENCE_POLICY).
AUTO_SOURCE_RELIABILITY = 0.99

#: An authoritative-structured-registry profile (DG-5): a clean structured read
#: auto-applies without an identity-confidence score, which a research/prose
#: source would still require. Composed onto the *existing* ConfidencePolicy —
#: not a new write path.
REGISTRY_CONFIDENCE_POLICY = ConfidencePolicy(require_identity_confidence_for_auto=False)

#: The op vocabulary an `E2EGraphStore`-backed executor may apply: the Plan-1
#: baseline plus `RETRACT_ASSERTION`, which `E2EGraphStore` implements.
SUPPORTED_WITH_RETRACT: frozenset[CurationOperationType] = frozenset(
    {
        CurationOperationType.CREATE_IDENTITY,
        CurationOperationType.ATTACH_ASSERTION,
        CurationOperationType.RETRACT_ASSERTION,
    }
)

# A fixed instant so committed records are reproducible across runs.
T0 = datetime(2026, 8, 1, tzinfo=UTC)


class E2EGraphStore(MemoryGraphStore):
    """`MemoryGraphStore` widened to also apply `RETRACT_ASSERTION`.

    A test-only adapter (the Wave-0 exit criteria explicitly allow a "test-only
    adapter" to apply plan output). `apply()` handles `CREATE_IDENTITY`,
    `ATTACH_ASSERTION`, and `RETRACT_ASSERTION`; every other op type raises
    `NotImplementedError` exactly like the reference store, so the executor
    still reports `UNSUPPORTED_OPERATION` for merge/split/reassign/promote. It
    keeps the reference store's atomicity (a failed precondition or a mid-batch
    error leaves the store unchanged) and its `entity_version`/epoch semantics.
    """

    def apply(
        self, batch: GraphMutationBatch, preconditions: Sequence[Precondition]
    ) -> CommitResult:
        failed = tuple(p for p in preconditions if not self._precondition_holds(p))
        if failed:
            return CommitResult(
                batch_id=batch.batch_id, committed=False, failed_preconditions=failed
            )

        new_epoch = self._epoch + 1
        self.begin()  # snapshot for rollback-on-error atomicity
        try:
            touched: list[str] = []
            for operation in batch.operations:
                if operation.type is CurationOperationType.CREATE_IDENTITY:
                    entity = CanonicalEntity.model_validate(
                        {**operation.payload, "curation_epoch": new_epoch}
                    )
                    self.put_entity(entity)
                    touched.append(entity.identity_id)
                elif operation.type is CurationOperationType.ATTACH_ASSERTION:
                    assertion = Assertion.model_validate(
                        {**operation.payload, "curation_epoch": new_epoch}
                    )
                    self.put_assertion(assertion)
                    touched.append(assertion.subject_identity)
                elif operation.type is CurationOperationType.RETRACT_ASSERTION:
                    assertion_id = str(operation.payload["assertion_id"])
                    # A supersession plan pins `superseded_at`; a *compensating*
                    # RETRACT (from an ATTACH's reversal_data) does not — rollback
                    # is a soft-undo (status change, history preserved), so a
                    # fixed instant stands in when none is carried.
                    raw = operation.payload.get("superseded_at")
                    superseded_at = datetime.fromisoformat(str(raw)) if raw is not None else T0
                    self.mark_superseded(assertion_id, superseded_at)
                    touched.append(str(operation.payload["subject_identity"]))
                else:
                    raise NotImplementedError(
                        f"{operation.type} is not applied by E2EGraphStore"
                    )
            for subject in touched:
                self._entity_versions[subject] = self._entity_versions.get(subject, 0) + 1
            self._epoch = new_epoch
            self.commit()
        except Exception:
            self.rollback()
            raise
        return CommitResult(batch_id=batch.batch_id, committed=True, new_epoch=new_epoch)


# --- source shapes ----------------------------------------------------------


@dataclass(frozen=True)
class SourceShape:
    """One domain's entity + evolving attribute, for the parametrized flagship.

    `value_a`/`ev_a` are the first source's claim; `value_b`/`ev_b` are the
    second source's contradicting claim that drives re-curation. Nothing here
    knows it is a paper or a sensor — that is the point.
    """

    name: str
    entity_type: str
    entity_key: str
    display_name: str
    attribute: str
    value_a: object
    value_b: object
    ev_a: str
    ev_b: str


#: The FLAGSHIP research-paper shape: Paper A claims a concept was proposed in
#: 2015; Paper B provides evidence it was 2014.
PAPER_SHAPE = SourceShape(
    name="paper",
    entity_type="Concept",
    entity_key="attention-mechanism",
    display_name="Attention Mechanism",
    attribute="proposed_year",
    value_a=2015,
    value_b=2014,
    ev_a="ev_paper_a",
    ev_b="ev_paper_b",
)

#: A NON-paper shape proving domain-neutrality: a device reads "online", then a
#: later observation supersedes it with "offline". Same architectural flow.
SENSOR_SHAPE = SourceShape(
    name="sensor",
    entity_type="Sensor",
    entity_key="sensor-42",
    display_name="Rooftop Sensor 42",
    attribute="status",
    value_a="online",
    value_b="offline",
    ev_a="ev_obs_1",
    ev_b="ev_obs_2",
)


def _pin(candidate: Candidate, *, cid: str, trace: str) -> Candidate:
    """Pin the non-deterministic envelope fields so a run is reproducible."""
    return candidate.model_copy(
        update={"candidate_id": cid, "trace_id": trace, "created_at": T0}
    )


def entity_candidate(shape: SourceShape, *, graph_id: str) -> EntityCandidate:
    """The `EntityCandidate` a structured source emits for this shape's concept."""
    candidate = make_entity_candidate(
        graph_id=graph_id,
        key=shape.entity_key,
        entity_type=shape.entity_type,
        display_name=shape.display_name,
        scores=make_scores(
            extraction_confidence=1.0, source_reliability=AUTO_SOURCE_RELIABILITY
        ),
    )
    return _pin(candidate, cid=f"cand_{shape.name}_entity", trace=f"trace_{shape.name}_a")  # type: ignore[return-value]


def attribute_candidate(
    shape: SourceShape,
    *,
    graph_id: str,
    subject: str,
    value: object,
    evidence_id: str,
    cid: str,
    trace: str,
) -> AttributeAssertionCandidate:
    """An `AttributeAssertionCandidate` about `subject`, citing `evidence_id`.

    The subject is a minted identity id (so the attribute auto-applies), and the
    candidate carries an `EvidenceRef` so the committed assertion cites evidence.
    """
    candidate = make_attribute_candidate(
        graph_id=graph_id,
        subject=subject,
        attribute=shape.attribute,
        value=value,
        scores=make_scores(
            extraction_confidence=1.0, source_reliability=AUTO_SOURCE_RELIABILITY
        ),
    )
    pinned = candidate.model_copy(
        update={
            "candidate_id": cid,
            "trace_id": trace,
            "created_at": T0,
            "evidence_refs": (
                EvidenceRef(evidence_id=evidence_id, relationship=EvidenceRelationship.SUPPORTS),
            ),
        }
    )
    return pinned  # type: ignore[return-value]


def new_assertion(
    *,
    subject: str,
    predicate: str,
    value: object,
    evidence_id: str,
    relationship: EvidenceRelationship,
    assertion_id: str,
    recorded_at: datetime,
) -> Assertion:
    """Build an explicit canonical `Assertion` for the re-curation step.

    Used for the *second* source's claim (the one that supersedes or conflicts
    with the committed assertion). Evidence is cited so the change is traceable.
    """
    from kg_contracts.testing.factories import make_assertion

    refs = (EvidenceRef(evidence_id=evidence_id, relationship=relationship),)
    return make_assertion(
        subject_identity=subject,
        predicate=predicate,
        object_value=value,
        evidence_refs=refs,
        status=CurationStatus.ACTIVE,
        recorded_at=recorded_at,
    ).model_copy(update={"assertion_id": assertion_id})
