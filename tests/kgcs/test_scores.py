"""The `score_vector` projection: order, honest-null omission, non-emptiness."""

from kg_contracts.testing.factories import make_scores

from kgcs import score_vector


def test_required_scores_and_risk_always_present() -> None:
    vector = score_vector(make_scores(extraction_confidence=0.9, source_reliability=0.8))
    assert vector == {
        "extraction_confidence": 0.9,
        "source_reliability": 0.8,
        "policy_risk": 0.0,
    }


def test_none_optionals_are_omitted_not_zero_filled() -> None:
    """A missing identity score is absent from the vector, never recorded as 0.0."""
    vector = score_vector(make_scores(identity_confidence=None))
    assert "identity_confidence" not in vector


def test_present_optionals_are_included_in_fixed_order() -> None:
    vector = score_vector(
        make_scores(
            extraction_confidence=0.9,
            source_reliability=0.8,
            identity_confidence=0.7,
            assertion_confidence=0.6,
            corroboration_score=0.5,
            policy_risk=0.1,
        )
    )
    assert list(vector) == [
        "extraction_confidence",
        "source_reliability",
        "identity_confidence",
        "assertion_confidence",
        "corroboration_score",
        "policy_risk",
    ]


def test_projection_is_never_empty() -> None:
    assert score_vector(make_scores()) != {}
