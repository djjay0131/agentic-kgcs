# Tech Context — agentic-kgcs

Python >=3.11, depends on agentic-kgis distribution (kg_contracts).
pytest, ruff, mypy --strict, hatchling, src/ layout, venv at .venv.
Dev install: .venv/bin/pip install -e ../agentic-kgis -e '.[dev]'
Tests run against kg_contracts.testing.memory_store.MemoryGraphStore —
no graph infrastructure needed.
GitHub: djjay0131/agentic-kgcs (private).
