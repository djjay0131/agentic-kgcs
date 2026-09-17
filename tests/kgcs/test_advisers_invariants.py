"""Architectural invariants for the adviser layer (§9 law 16, Wave 4 exit).

The whole point of the wave, as tests: an adviser's public interface cannot
construct or return a `GraphMutationBatch` / `CurationOperation` / `CurationPlan`,
it holds no `GraphMutationStore`, and the orchestrator returns a decision plus
structured assessments — never an operation. These are enforced two ways: the
assessment/result *types* have no field capable of holding a mutation, and the
adviser package *source* imports no mutation surface.
"""

import inspect
from pathlib import Path

import kgcs.advisers as advisers_pkg
from kgcs.advisers.base import AdviserAssessment
from kgcs.advisers.orchestrator import CurationOrchestrator, OrchestrationResult
from kgcs.advisers.specialists import (
    AssertionAdviser,
    ConceptEvolutionAdviser,
    ConflictAdviser,
    IdentityAdviser,
    OntologyEvolutionAdviser,
)

_FORBIDDEN = ("GraphMutationBatch", "GraphMutationStore", "CurationOperation", "CurationPlan")

_ADVISER_CLASSES = (
    IdentityAdviser,
    AssertionAdviser,
    ConflictAdviser,
    ConceptEvolutionAdviser,
    OntologyEvolutionAdviser,
)


def _advisers_source_files() -> list[Path]:
    package_dir = Path(inspect.getfile(advisers_pkg)).parent
    return sorted(package_dir.glob("*.py"))


class TestNoMutationSurfaceInSource:
    def test_adviser_package_imports_no_mutation_surface(self) -> None:
        for path in _advisers_source_files():
            source = path.read_text(encoding="utf-8")
            for symbol in _FORBIDDEN:
                # Allow the words in comments/docstrings (they explain the rule),
                # but never in an import statement.
                for line in source.splitlines():
                    stripped = line.strip()
                    if stripped.startswith(("import ", "from ")):
                        assert symbol not in stripped, f"{path.name} imports {symbol}"


class TestAssessmentTypesHoldNoOperation:
    def test_assessment_field_types_are_inert(self) -> None:
        # No field annotation references a mutation type.
        for field in AdviserAssessment.model_fields.values():
            annotation = str(field.annotation)
            for symbol in _FORBIDDEN:
                assert symbol not in annotation

    def test_assessment_serializes_to_json_primitives_only(self) -> None:
        assessment = AdviserAssessment(
            adviser_type="identity",
            adviser_version="identity/1",
            model_id="m",
            model_version="1",
            prompt_version="1",
            recommendation="same",
            evidence_ids=("ev_1",),
            trace_id="t",
        )
        dumped = assessment.model_dump()
        for value in dumped.values():
            assert value is None or isinstance(value, (str, bool, int, float, tuple, list))

    def test_advisers_hold_no_graph_mutation_store(self) -> None:
        from kgcs.advisers.completion import FailingCompletionClient

        for cls in _ADVISER_CLASSES:
            adviser = cls(port=FailingCompletionClient())
            for _name, value in vars(adviser).items():
                assert type(value).__name__ not in _FORBIDDEN


class TestOrchestratorReturnsDecisionNotOperation:
    def test_result_type_has_no_operation_field(self) -> None:
        for field in OrchestrationResult.model_fields.values():
            annotation = str(field.annotation)
            for symbol in _FORBIDDEN:
                assert symbol not in annotation

    def test_orchestrator_has_no_mutation_method_names(self) -> None:
        method_names = {name for name in dir(CurationOrchestrator) if not name.startswith("__")}
        for suspicious in ("apply", "commit", "mutate", "write", "execute", "batch", "store"):
            assert suspicious not in method_names

    def test_orchestrator_holds_no_mutation_store(self) -> None:
        orch = CurationOrchestrator()
        for value in vars(orch).values():
            assert type(value).__name__ not in _FORBIDDEN
