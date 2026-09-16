"""Shared fixtures for the KGCS curation-core tests.

Score presets pin candidates to a known `AdjudicationRoute` so routing is not
re-derived in every test, and `fixed_clock` / `engine` give a deterministic
engine whose plans and audit records are byte-reproducible.
"""

from datetime import UTC, datetime

import pytest
from kg_contracts.candidates import CandidateScores
from kg_contracts.testing.factories import make_scores

from helpers import GRAPH_ID
from kgcs import CurationEngine, FixedClock


@pytest.fixture
def auto_scores() -> CandidateScores:
    """Scores that route AUTO (high extraction, source, and identity)."""
    return make_scores(extraction_confidence=0.99, source_reliability=0.99, identity_confidence=0.99)


@pytest.fixture
def assess_scores() -> CandidateScores:
    """Scores that route LLM_ASSESS (extraction below the AUTO floor)."""
    return make_scores(extraction_confidence=0.85, source_reliability=0.99, identity_confidence=0.99)


@pytest.fixture
def human_scores() -> CandidateScores:
    """Scores that route HUMAN (extraction below the assess floor)."""
    return make_scores(extraction_confidence=0.50, source_reliability=0.99, identity_confidence=0.99)


@pytest.fixture
def fixed_clock() -> FixedClock:
    return FixedClock(datetime(2026, 7, 17, tzinfo=UTC))


@pytest.fixture
def engine(fixed_clock: FixedClock) -> CurationEngine:
    """A deterministic engine bound to `GRAPH_ID` (derived ids, fixed clock)."""
    return CurationEngine.create(graph_id=GRAPH_ID, clock=fixed_clock)
