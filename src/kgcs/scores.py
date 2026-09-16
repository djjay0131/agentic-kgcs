"""Projecting `CandidateScores` into a decision's `score_vector`.

Both `ResolutionDecision.score_vector` and `AuditRecord.score_vector` are
`dict[str, float]`: the spec's rule (`kg_contracts.curation`) is that a
single stored final confidence cannot reproduce a decision, so the *whole*
score set travels with every decision. This module is the one place that
flattens `CandidateScores` into that dict, so resolution and audit can never
disagree about what a candidate's scores were.

Two rules make the projection honest and deterministic:

- **Insertion order is fixed** (extraction, source, then the optionals in a
  fixed order, then policy risk). Equal input always yields an equal dict in
  an equal order — replay-stable.
- **`None` is omitted, never zero-filled.** An unknown `identity_confidence`
  is left out of the vector entirely rather than recorded as `0.0`. Honest
  absence is not the same fact as measured-zero, and `ConfidencePolicy`
  itself treats a missing identity score as *blocking* AUTO, not as a
  passing zero — the vector must not launder the one into the other.
"""

from kg_contracts.candidates import CandidateScores

# Optional score fields, in the fixed order they appear in `CandidateScores`.
_OPTIONAL_FIELDS = ("identity_confidence", "assertion_confidence", "corroboration_score")


def score_vector(scores: CandidateScores) -> dict[str, float]:
    """Flatten `scores` into a decision's `score_vector` (non-empty, ordered).

    Always carries the two required scores and `policy_risk`; carries each
    optional score only when it is set. The result is never empty (both
    `ResolutionDecision` and `AuditRecord` require a non-empty vector), and
    is byte-identical for equal input.
    """
    vector: dict[str, float] = {
        "extraction_confidence": scores.extraction_confidence,
        "source_reliability": scores.source_reliability,
    }
    for field in _OPTIONAL_FIELDS:
        value = getattr(scores, field)
        if value is not None:
            vector[field] = value
    vector["policy_risk"] = scores.policy_risk
    return vector
