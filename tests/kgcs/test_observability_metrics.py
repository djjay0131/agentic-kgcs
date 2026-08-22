"""Honest-null evaluation metrics (ADR-0009, §9 law 9).

Proves the Wave-7 honest-null exit criterion: empty or insufficient evaluation
data yields explicit `None` metrics with a count of 0 — never a fabricated
`0.0`/`1.0` and never a promotion claim. Also proves the ER metrics reuse the
matcher's own labeled evaluation, the cluster metrics are honest-null on empty
denominators, and the curation metrics are pure functions of their inputs.
"""

from datetime import UTC, datetime

from kg_contracts.curation import ReviewAction, ReviewDecision
from kgcs.er.blocking import CandidatePair
from kgcs.er.features import PairFeatures
from kgcs.er.matcher import (
    CalibrationKey,
    DeterministicRuleMatcher,
    GoldenSet,
    LabeledPair,
)
from kgcs.er.normalize import FeatureAgreement
from kgcs.executor import ExecutionOutcome, ExecutionRecord
from kgcs.observability import (
    CurationMetrics,
    ErMetrics,
    cluster_precision_recall,
    curation_metrics,
    er_metrics,
)


def _key() -> CalibrationKey:
    return CalibrationKey.of(
        graph_id="g1",
        entity_type="Paper",
        source_pair=("s_a", "s_b"),
        matcher_version="rules/1",
        consequence_class="standard",
    )


def _labeled(left: str, right: str, *, agree: bool, label: bool) -> LabeledPair:
    features = PairFeatures(
        identifier_agreement=FeatureAgreement.AGREE if agree else FeatureAgreement.CONTRADICT,
        name_similarity=1.0 if agree else 0.0,
    )
    return LabeledPair(pair=CandidatePair.of(left, right), features=features, label=label, key=_key())


def _golden() -> GoldenSet:
    # Two true matches with agreeing identifiers, two true non-matches with
    # contradicting identifiers — the rule matcher scores these correctly.
    return GoldenSet(
        pairs=(
            _labeled("a1", "a2", agree=True, label=True),
            _labeled("b1", "b2", agree=True, label=True),
            _labeled("c1", "c2", agree=False, label=False),
            _labeled("d1", "d2", agree=False, label=False),
        )
    )


class TestErMetricsHonestNull:
    def test_empty_everything_is_all_none_with_zero_count(self) -> None:
        metrics = er_metrics()
        assert isinstance(metrics, ErMetrics)
        assert metrics.count == 0
        assert metrics.pairwise_precision is None
        assert metrics.pairwise_recall is None
        assert metrics.cluster_precision is None
        assert metrics.false_merge_rate is None
        assert metrics.calibration_error is None
        assert metrics.abstention_rate is None

    def test_no_matcher_means_pairwise_metrics_stay_none(self) -> None:
        metrics = er_metrics(golden=_golden())  # golden but no matcher
        assert metrics.pairwise_precision is None
        assert metrics.count == 0

    def test_labeled_golden_set_yields_real_pairwise_metrics(self) -> None:
        metrics = er_metrics(matcher=DeterministicRuleMatcher(), golden=_golden(), threshold=0.5)
        assert metrics.count == 4
        assert metrics.pairwise_precision == 1.0
        assert metrics.pairwise_recall == 1.0
        assert metrics.false_merge_rate == 0.0  # a measured zero, from real data
        assert metrics.calibration_error is not None


class TestClusterMetrics:
    def test_empty_clusters_are_honest_null(self) -> None:
        precision, recall = cluster_precision_recall([], [])
        assert precision is None
        assert recall is None

    def test_perfect_clustering_scores_one(self) -> None:
        predicted = [frozenset({"a", "b"}), frozenset({"c"})]
        gold = [frozenset({"a", "b"}), frozenset({"c"})]
        precision, recall = cluster_precision_recall(predicted, gold)
        assert precision == 1.0
        assert recall == 1.0

    def test_over_merge_lowers_precision(self) -> None:
        predicted = [frozenset({"a", "b", "c"})]  # merged a wrong member in
        gold = [frozenset({"a", "b"}), frozenset({"c"})]
        precision, recall = cluster_precision_recall(predicted, gold)
        assert precision is not None and precision < 1.0
        assert recall == 1.0  # the true pair (a,b) is still together


class TestAbstentionRate:
    def test_abstention_rate_from_audit_stream(self) -> None:
        from kgcs.advisers.completion import FailingCompletionClient
        from kgcs.advisers.orchestrator import CurationOrchestrator
        from kgcs.advisers.specialists import IdentityAdviser
        from kgcs.er.matcher import MatchResult
        from kgcs.observability import ReplayInputs, SemanticAuditBuilder
        from kgcs.profiles import default_profile

        mr = MatchResult(
            pair=CandidatePair.of("paper/a", "paper/b"),
            probability=0.90,
            matcher_version="rules/1",
            feature_vector={"mutually_exclusive": 0.0},
            calibration_key=_key(),
        )
        # A failing adviser yields one abstained assessment; the baseline stands.
        result = CurationOrchestrator(
            identity_adviser=IdentityAdviser(port=FailingCompletionClient())
        ).resolve(mr, profile=default_profile(), evidence_ids=("ev_1",), trace_id="t")
        record = SemanticAuditBuilder().build(
            result, ReplayInputs(match_result=mr, profile=default_profile(), trace_id="t")
        )
        assert record.assessments[0].abstained is True
        metrics = er_metrics([record])
        assert metrics.abstention_rate == 1.0

    def test_abstention_none_when_no_assessments(self) -> None:
        metrics = er_metrics(records=[])
        assert metrics.abstention_rate is None


class TestCurationMetricsHonestNull:
    def test_empty_streams_are_all_none(self) -> None:
        metrics = curation_metrics()
        assert isinstance(metrics, CurationMetrics)
        assert metrics.count == 0
        assert metrics.review_yield is None
        assert metrics.review_agreement_rate is None
        assert metrics.rollback_frequency is None
        assert metrics.mean_queue_age_hours is None
        assert metrics.time_to_canonicalization_hours is None

    def test_rollback_frequency_from_executions(self) -> None:
        executions = [
            _exec("pl_1", is_compensation=False),
            _exec("pl_2", is_compensation=True),
            _exec("pl_3", is_compensation=False),
            _exec("pl_4", is_compensation=False),
        ]
        metrics = curation_metrics(executions=executions)
        assert metrics.rollback_frequency == 0.25

    def test_review_agreement_rate_from_decisions(self) -> None:
        decisions = [
            _decision(ReviewAction.APPROVE),
            _decision(ReviewAction.APPROVE),
            _decision(ReviewAction.REJECT),
            _decision(ReviewAction.SPLIT),
        ]
        metrics = curation_metrics(review_decisions=decisions)
        assert metrics.review_agreement_rate == 0.5

    def test_means_are_none_on_empty_series_not_zero(self) -> None:
        metrics = curation_metrics(queue_ages_hours=(), canonicalization_latencies_hours=())
        assert metrics.mean_queue_age_hours is None
        assert metrics.time_to_canonicalization_hours is None

    def test_means_computed_when_present(self) -> None:
        metrics = curation_metrics(
            queue_ages_hours=(2.0, 4.0), canonicalization_latencies_hours=(10.0,)
        )
        assert metrics.mean_queue_age_hours == 3.0
        assert metrics.time_to_canonicalization_hours == 10.0

    def test_metrics_are_pure_functions_of_inputs(self) -> None:
        executions = [_exec("pl_1", is_compensation=True), _exec("pl_2", is_compensation=False)]
        first = curation_metrics(executions=executions)
        second = curation_metrics(executions=executions)
        assert first.model_dump_json() == second.model_dump_json()


def _exec(plan_id: str, *, is_compensation: bool) -> ExecutionRecord:
    return ExecutionRecord(
        execution_id=f"ex_{plan_id}",
        plan_id=plan_id,
        batch_id=None,
        outcome=ExecutionOutcome.COMMITTED,
        recorded_at=datetime(2026, 8, 1, tzinfo=UTC),
        is_compensation=is_compensation,
    )


def _decision(action: ReviewAction) -> ReviewDecision:
    payload = {"members": ["a", "b"]} if action is ReviewAction.EDIT else None
    return ReviewDecision(
        item_id="rv_1",
        action=action,
        actor="alice",
        edited_payload=payload,
        decided_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
