"""Shared test constants and helpers, importable by test modules.

Kept in a plain module (not `conftest.py`) so test files can *import* these:
with pytest's default import mode the test directory is on `sys.path`, so
`from helpers import GRAPH_ID` resolves without any package wiring. `conftest`
holds only fixtures (mirrors the KGIS `scenarios.py` convention).
"""

from kg_contracts.identity import new_identity_id

GRAPH_ID = "g1"


def known_identity() -> str:
    """A minted identity id in `GRAPH_ID`, usable as an assertion subject."""
    return new_identity_id(GRAPH_ID)
