"""The `kg_eval` seam: KGCS emits metrics with no reverse dependency (Wave 7).

Proves the Wave-7 exit point: KGCS *implements* the `MetricProvider` seam and
emits an honest-null `MetricSnapshot` over the in-memory semantic audit sink; a
downstream harness (`kg_eval`) reads it. A grep confirms there is no `import
kg_eval` anywhere in `src/kgcs`, so no reverse dependency exists.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from kgcs.advisers.completion import CompletionResponse, RecordedCompletionClient
from kgcs.advisers.orchestrator import CurationOrchestrator
from kgcs.advisers.specialists import IdentityAdviser
from kgcs.er.blocking import CandidatePair
from kgcs.er.matcher import CalibrationKey, DeterministicRuleMatcher, MatchResult
from kgcs.executor import ExecutionOutcome, ExecutionRecord
from kgcs.observability import (
    AuditMetricProvider,
    InMemorySemanticAuditSink,
    MetricProvider,
    MetricSnapshot,
    ReplayInputs,
    SemanticAuditBuilder,
)
from kgcs.profiles import default_profile

_TRACE = "trace-provider"
_KEY = CalibrationKey.of(
    graph_id="g1",
    entity_type="Paper",
    source_pair=("s_a", "s_b"),
    matcher_version="rules/1",
    consequence_class="standard",
)


def _mr() -> MatchResult:
    return MatchResult(
        pair=CandidatePair.of("paper/a", "paper/b"),
        probability=0.90,
        matcher_version="rules/1",
        feature_vector={"mutually_exclusive": 0.0},
        calibration_key=_KEY,
    )


def _recorded_adviser(mr: MatchResult) -> IdentityAdviser:
    from kgcs.advisers.orchestrator import _identity_question

    question = _identity_question(mr, evidence_ids=("ev_1",), trace_id=_TRACE)
    request = IdentityAdviser(port=RecordedCompletionClient({})).build_request(question)
    response = CompletionResponse(
        text=json.dumps({"recommendation": "same", "evidence_ids": ["ev_1"]}),
        model_id="recorded/echo",
        model_version="1",
    )
    return IdentityAdviser(port=RecordedCompletionClient({request.request_hash: response}))


def _populate(sink: InMemorySemanticAuditSink) -> None:
    mr = _mr()
    result = CurationOrchestrator(identity_adviser=_recorded_adviser(mr)).resolve(
        mr, profile=default_profile(), evidence_ids=("ev_1",), trace_id=_TRACE
    )
    execution = ExecutionRecord(
        execution_id="ex_1",
        plan_id="pl_1",
        batch_id="mb_1",
        outcome=ExecutionOutcome.COMMITTED,
        recorded_at=datetime(2026, 8, 1, tzinfo=UTC),
        is_compensation=True,  # one rollback in the stream
    )
    record = SemanticAuditBuilder().build(
        result,
        ReplayInputs(match_result=mr, profile=default_profile(), evidence_ids=("ev_1",), trace_id=_TRACE),
        execution=execution,
    )
    sink.record(record)


class TestProviderSeam:
    def test_kgcs_implements_the_metric_provider_protocol(self) -> None:
        provider = AuditMetricProvider(InMemorySemanticAuditSink())
        assert isinstance(provider, MetricProvider)  # structural: KGCS satisfies the seam

    def test_empty_sink_emits_honest_null_snapshot(self) -> None:
        snapshot = AuditMetricProvider(InMemorySemanticAuditSink()).snapshot()
        assert isinstance(snapshot, MetricSnapshot)
        assert snapshot.er.abstention_rate is None
        assert snapshot.curation.rollback_frequency is None
        assert snapshot.curation.count == 0

    def test_provider_emits_metrics_from_the_audit_stream(self) -> None:
        sink = InMemorySemanticAuditSink()
        _populate(sink)
        snapshot = AuditMetricProvider(sink).snapshot()
        # abstention derived from the (non-abstained) adviser assessment
        assert snapshot.er.abstention_rate == 0.0
        # one compensation execution ref in the stream → rollback frequency 1.0
        assert snapshot.curation.rollback_frequency == 1.0
        assert snapshot.curation.count == 1

    def test_provider_emits_label_dependent_er_metrics_when_given_a_golden_set(self) -> None:
        from kgcs.er.features import PairFeatures
        from kgcs.er.matcher import GoldenSet, LabeledPair
        from kgcs.er.normalize import FeatureAgreement

        golden = GoldenSet(
            pairs=(
                LabeledPair(
                    pair=CandidatePair.of("a1", "a2"),
                    features=PairFeatures(identifier_agreement=FeatureAgreement.AGREE, name_similarity=1.0),
                    label=True,
                    key=_KEY,
                ),
            )
        )
        provider = AuditMetricProvider(
            InMemorySemanticAuditSink(), golden=golden, matcher=DeterministicRuleMatcher()
        )
        snapshot = provider.snapshot()
        assert snapshot.er.count == 1
        assert snapshot.er.pairwise_precision is not None

    def test_snapshot_is_json_serializable_across_the_seam(self) -> None:
        sink = InMemorySemanticAuditSink()
        _populate(sink)
        snapshot = AuditMetricProvider(sink).snapshot()
        restored = MetricSnapshot.model_validate_json(snapshot.model_dump_json())
        assert restored == snapshot


class TestNoReverseDependency:
    def test_no_kg_eval_import_anywhere_in_src_kgcs(self) -> None:
        # Scan the whole KGCS source tree — there must be no *import* of kg_eval
        # anywhere; the seam runs the other way (kg_eval imports KGCS, not vice
        # versa). Prose mentions of the name in docstrings are fine; an import
        # statement is the reverse-dependency leak this guards against.
        src = Path(__file__).resolve().parents[2] / "src" / "kgcs"
        assert src.is_dir(), f"source tree not found at {src}"
        offenders: list[str] = []
        for path in src.rglob("*.py"):
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith(("import kg_eval", "from kg_eval")):
                    offenders.append(f"{path}: {stripped}")
        assert offenders == [], f"kg_eval imported in src/kgcs: {offenders}"
