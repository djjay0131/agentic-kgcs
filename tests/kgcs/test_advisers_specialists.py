"""Focused coverage for each bounded specialist (Wave 4, DG-2).

One test per specialist: a recorded fixture drives it to a representative
recommendation and it cites the supplied evidence. Every specialist returns an
`AdviserAssessment` — never an operation — and shares the same base machinery, so
these tests exercise the recommendation vocabulary and evidence citation, not the
plumbing (covered in `test_advisers_base`).
"""

import json
from typing import cast

from kgcs.advisers.base import AdviserAssessment, AdviserQuestion, StructuredAdviser
from kgcs.advisers.completion import CompletionResponse, RecordedCompletionClient
from kgcs.advisers.specialists import (
    AssertionAdviser,
    AssertionRecommendation,
    ConceptEvolutionAdviser,
    ConceptRecommendation,
    ConflictAdviser,
    ConflictRecommendation,
    IdentityAdviser,
    IdentityRecommendation,
    OntologyEvolutionAdviser,
    OntologyRecommendation,
)


def _drive(
    adviser_cls: type[StructuredAdviser], question: AdviserQuestion, payload: dict[str, object]
) -> AdviserAssessment:
    """Build the specialist, record `payload` for `question`, and assess."""
    # Build the request the adviser will send, to key the fixture by its hash.
    probe = adviser_cls(port=RecordedCompletionClient({}))
    request = probe.build_request(question)
    response = CompletionResponse(
        text=json.dumps(payload), model_id="recorded/echo", model_version="1"
    )
    adviser = adviser_cls(port=RecordedCompletionClient({request.request_hash: response}))
    return adviser.assess(question)


def _question(kind: str) -> AdviserQuestion:
    return AdviserQuestion(
        kind=kind, subject="left", other="right", evidence_ids=("ev_1", "ev_2"), trace_id="t"
    )


def test_identity_adviser_recommends_same_with_citation() -> None:
    assessment = _drive(
        IdentityAdviser,
        _question("identity"),
        {"recommendation": "same", "evidence_ids": ["ev_1"], "contradictions": [], "confidence": 0.9},
    )
    assert assessment.recommendation == IdentityRecommendation.SAME
    assert assessment.evidence_ids == ("ev_1",)
    assert assessment.abstained is False


def test_assertion_adviser_recommends_supersedes() -> None:
    assessment = _drive(
        AssertionAdviser,
        _question("assertion"),
        {"recommendation": "supersedes", "evidence_ids": ["ev_2"], "confidence": 0.7},
    )
    assert assessment.recommendation == AssertionRecommendation.SUPERSEDES
    assert assessment.evidence_ids == ("ev_2",)


def test_conflict_adviser_preserves_both() -> None:
    assessment = _drive(
        ConflictAdviser,
        _question("conflict"),
        {"recommendation": "preserve_both", "evidence_ids": ["ev_1", "ev_2"]},
    )
    assert assessment.recommendation == ConflictRecommendation.PRESERVE_BOTH
    assert set(assessment.evidence_ids) == {"ev_1", "ev_2"}


def test_conflict_adviser_conservative_fallback_is_preserve_both() -> None:
    # On malformed output the conflict adviser never picks a winner.
    probe = ConflictAdviser(port=RecordedCompletionClient({}))
    question = _question("conflict")
    request = probe.build_request(question)
    bad = CompletionResponse(text="not json", model_id="m", model_version="1")
    adviser = ConflictAdviser(port=RecordedCompletionClient({request.request_hash: bad}))
    assessment = adviser.assess(question)
    assert assessment.abstained is True
    assert assessment.recommendation == ConflictRecommendation.PRESERVE_BOTH


def test_concept_evolution_adviser_recommends_split() -> None:
    assessment = _drive(
        ConceptEvolutionAdviser,
        _question("concept"),
        {"recommendation": "split", "evidence_ids": ["ev_1"], "contradictions": ["scope drift"]},
    )
    assert assessment.recommendation == ConceptRecommendation.SPLIT
    assert assessment.contradictions == ("scope drift",)


def test_ontology_evolution_adviser_only_proposes() -> None:
    assessment = _drive(
        OntologyEvolutionAdviser,
        _question("ontology"),
        {"recommendation": "propose_candidate", "evidence_ids": ["ev_1", "ev_2"], "confidence": 0.6},
    )
    assert assessment.recommendation == OntologyRecommendation.PROPOSE_CANDIDATE
    # The vocabulary contains only proposals — no "promote"/"approve" value exists.
    values = {member.value for member in OntologyRecommendation}
    assert "promote" not in values and "approve" not in values


def test_recommendation_is_the_only_structured_output() -> None:
    # Every specialist's assessment type is the same inert model.
    assessment = _drive(IdentityAdviser, _question("identity"), {"recommendation": "different"})
    assert isinstance(cast(object, assessment), AdviserAssessment)
    assert assessment.recommendation == IdentityRecommendation.DIFFERENT
