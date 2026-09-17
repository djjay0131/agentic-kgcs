"""Re-curation & concept/ontology evolution (Wave 5, DG-1 + DG-3, §9 laws 8, 10-12).

Makes canonical knowledge evolve as new evidence accumulates, without ever
rewriting history. Four seams:

- `triggers` (DG-1) — immutable `CurationTrigger`s that enqueue re-evaluation
  work; they never mutate canonical data, and handling is idempotent and
  trace-linked.
- `targeting` — a `DependencyIndex` port answering "what could this evidence
  affect?" so re-curation is incremental, never a full sweep (§9 law 11).
- `evolution` (DG-3) — a `ConceptEvolutionPlanner` turning a decision + trigger
  into a compensable, bitemporal `CurationPlan` (merge/split/supersede/…);
  supersession marks the old record `SUPERSEDED`, never deletes (§9 law 10).
- `ontology` — a governed `OntologyLifecycle` where promotion cannot skip
  PROPOSED→APPROVED→OBSERVED (§9 law 12).
"""

from kgcs.recuration.evolution import (
    AssertionReassignment,
    ConceptEvolutionPlanner,
    EvolutionKind,
    EvolutionResult,
)
from kgcs.recuration.ontology import (
    IllegalOntologyTransition,
    OntologyLifecycle,
    OntologyPromotionRefused,
    OntologyTerm,
    OntologyTermState,
    TermKind,
    is_legal_transition,
)
from kgcs.recuration.router import EvolutionRouter
from kgcs.recuration.targeting import DependencyIndex, InMemoryDependencyIndex
from kgcs.recuration.triggers import (
    CurationTrigger,
    InMemoryTriggerQueue,
    TriggerKind,
    TriggerQueue,
    VersionContext,
    merge_evidence,
    trigger_provenance,
)

__all__ = [
    # triggers (DG-1)
    "TriggerKind",
    "VersionContext",
    "CurationTrigger",
    "TriggerQueue",
    "InMemoryTriggerQueue",
    "merge_evidence",
    "trigger_provenance",
    # targeting
    "DependencyIndex",
    "InMemoryDependencyIndex",
    # concept evolution (DG-3)
    "EvolutionKind",
    "AssertionReassignment",
    "EvolutionResult",
    "ConceptEvolutionPlanner",
    "EvolutionRouter",
    # ontology lifecycle
    "OntologyTermState",
    "TermKind",
    "OntologyTerm",
    "OntologyLifecycle",
    "IllegalOntologyTransition",
    "OntologyPromotionRefused",
    "is_legal_transition",
]
