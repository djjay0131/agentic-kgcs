"""kgcs: the deterministic Knowledge Graph Curation core (Sprint 1).

The first curation engine: it begins at the `Candidate` boundary and produces
a complete, immutable `CurationPlan` plus its `ValidationDecision`s,
`ResolutionDecision`s, and `AuditRecord`s — and stops there. Nothing is
written to the graph, no entity resolution runs, and there are no embeddings,
LLMs, or probabilistic behavior. Every output is a pure function of the input
candidates and the injected `IdFactory` (the audit stream additionally of the
injected `Clock`), so identical input yields an identical plan.

    Candidate → Validator → Policy → Planner → Audit

The stages are the modules `validation`, `policy`, `planner`, `audit`, wired
by `engine`. `CurationEngine.create(...)` assembles a coherent default; the
determinism primitives (`clock`, `ids`, `scores`) and the in-memory
`AuditSink` (`memory`) are the injectable seams. Contracts themselves live in
`kg_contracts` (frozen); this package only orchestrates over them.
"""

from kgcs.audit import AuditRecorder, AuditSink
from kgcs.clock import Clock, FixedClock, SystemClock
from kgcs.engine import CandidateOutcome, CurationEngine, EngineResult
from kgcs.ids import DerivedIdFactory, IdFactory, UlidIdFactory, is_well_formed_graph_id
from kgcs.memory import InMemoryAuditSink
from kgcs.planner import (
    CurationPlanner,
    PlannedOperation,
    PlanResult,
    ResolvedCandidate,
)
from kgcs.policy import ResolutionPolicy
from kgcs.scores import score_vector
from kgcs.validation import (
    CandidateValidator,
    ContractVersionRule,
    GraphIdWellFormedRule,
    GraphScopeRule,
    ProducerPresentRule,
    RuleBasedValidator,
    RuleViolation,
    SupportedKindRule,
    ValidationRule,
    default_validator,
)

__all__ = [
    # engine
    "CurationEngine",
    "EngineResult",
    "CandidateOutcome",
    # stages
    "CandidateValidator",
    "RuleBasedValidator",
    "ValidationRule",
    "RuleViolation",
    "SupportedKindRule",
    "GraphIdWellFormedRule",
    "GraphScopeRule",
    "ContractVersionRule",
    "ProducerPresentRule",
    "default_validator",
    "ResolutionPolicy",
    "CurationPlanner",
    "PlanResult",
    "PlannedOperation",
    "ResolvedCandidate",
    "AuditRecorder",
    "AuditSink",
    # determinism primitives
    "Clock",
    "SystemClock",
    "FixedClock",
    "IdFactory",
    "DerivedIdFactory",
    "UlidIdFactory",
    "is_well_formed_graph_id",
    "score_vector",
    # in-memory adapters
    "InMemoryAuditSink",
]
