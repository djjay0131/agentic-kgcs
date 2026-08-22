"""Bounded LLM curation advisers (Wave 4, spec §7.4 / §7.3, build plan §DG-2/DG-4).

The agentic reasoning layer, built so it *cannot* violate canonical-write
governance. Every adviser is a bounded, evidence-citing specialist that returns
structured advice — an `AdviserAssessment` — and nothing else: no
`CurationOperation`, no `GraphMutationBatch`, no `CurationPlan`, and no
`GraphMutationStore`. Advice feeds the deterministic policy gate; it never
bypasses it (§9 law 16).

    deterministic baseline (Wave 3)  →  select specialists  →  aggregate advice
        →  fold back through the deterministic policy  →  final decision

`completion` is the injected LLM seam (a KGCS-local `CompletionPort`, no provider
SDK); `base` holds the shared render → complete → parse → abstain machinery and
the DG-4 provenance model; `specialists` are the five bounded advisers; and
`orchestrator` runs the deterministic baseline first and folds advice into it,
guaranteeing the baseline stands on any LLM failure (§9 law 1).
"""

from kgcs.advisers.base import (
    Adviser,
    AdviserAssessment,
    AdviserQuestion,
    StructuredAdviser,
)
from kgcs.advisers.completion import (
    CompletionError,
    CompletionMiss,
    CompletionPort,
    CompletionRequest,
    CompletionResponse,
    CompletionTimeout,
    FailingCompletionClient,
    MalformedCompletionClient,
    RecordedCompletionClient,
    TimeoutCompletionClient,
)
from kgcs.advisers.orchestrator import CurationOrchestrator, OrchestrationResult
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

__all__ = [
    # completion seam
    "CompletionRequest",
    "CompletionResponse",
    "CompletionPort",
    "RecordedCompletionClient",
    "FailingCompletionClient",
    "TimeoutCompletionClient",
    "MalformedCompletionClient",
    "CompletionError",
    "CompletionTimeout",
    "CompletionMiss",
    # base machinery + provenance
    "AdviserQuestion",
    "AdviserAssessment",
    "Adviser",
    "StructuredAdviser",
    # specialists + recommendation vocabularies
    "IdentityAdviser",
    "IdentityRecommendation",
    "AssertionAdviser",
    "AssertionRecommendation",
    "ConflictAdviser",
    "ConflictRecommendation",
    "ConceptEvolutionAdviser",
    "ConceptRecommendation",
    "OntologyEvolutionAdviser",
    "OntologyRecommendation",
    # orchestrator
    "CurationOrchestrator",
    "OrchestrationResult",
]
