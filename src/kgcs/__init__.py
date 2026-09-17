"""kgcs: the Knowledge Graph Curation Service — deterministic core + executor.

The curation library behind every portfolio knowledge graph. Two halves:

- a **deterministic core** (`validation`, `policy`, `planner`, `audit`, wired
  by `engine`) that begins at the `Candidate` boundary and produces a
  complete, immutable `CurationPlan` plus its `ValidationDecision`s,
  `ResolutionDecision`s, and `AuditRecord`s — and stops there. No graph
  mutation, no entity resolution, no embeddings/LLMs. Every output is a pure
  function of the input candidates and the injected `IdFactory` (the audit
  stream additionally of the injected `Clock`), so identical input yields an
  identical plan.
- a **transaction-aware executor** (`executor`) — the *only* component that
  applies a `CurationPlan` to a `GraphMutationStore`. It checks preconditions,
  rejects stale plans, publishes the curation epoch, fails explicitly on
  operations an adapter cannot apply, records execution audit, and generates
  compensating plans for rollback. Applications never hold a write surface.

    Candidate → Validator → Policy → Planner → Audit → CurationPlan
                                                            ↓
                                          Executor → GraphMutationStore → epoch

`CurationEngine.create(...)` assembles a coherent core; `PlanExecutor` applies
its plans. The determinism primitives (`clock`, `ids`, `scores`) and the
in-memory adapters (`memory`) are the injectable seams. Contracts themselves
live in `kg_contracts` (frozen); this package only orchestrates over them.
"""

from kgcs.audit import AuditRecorder, AuditSink
from kgcs.clock import Clock, FixedClock, SystemClock
from kgcs.engine import CandidateOutcome, CurationEngine, EngineResult
from kgcs.er import (
    BlockingPipeline,
    CalibratedMatcher,
    CalibrationKey,
    CalibrationMetrics,
    CalibrationModel,
    CandidatePair,
    DefaultFeatureExtractor,
    DefaultNormalizer,
    DeterministicRuleMatcher,
    EmbeddingChannel,
    ExactIdentifierChannel,
    FeatureAgreement,
    GoldenSet,
    IdentitySignal,
    LabeledPair,
    MatchResult,
    NormalizedEntity,
    NormalizedNameChannel,
    PairFeatures,
    SharedStrongIdentifierRule,
    SourceKeyChannel,
    evaluate,
)
from kgcs.executor import (
    DEFAULT_SUPPORTED_OPERATIONS,
    INVERSE_OPERATION,
    CompensationResult,
    Compensator,
    EpochPublisher,
    ExecutionAuditSink,
    ExecutionOutcome,
    ExecutionRecord,
    PlanExecutor,
)
from kgcs.ids import DerivedIdFactory, IdFactory, UlidIdFactory, is_well_formed_graph_id
from kgcs.memory import (
    InMemoryAuditSink,
    InMemoryEpochPublisher,
    InMemoryExecutionAuditSink,
)
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
    # executor (write path)
    "PlanExecutor",
    "ExecutionOutcome",
    "ExecutionRecord",
    "ExecutionAuditSink",
    "EpochPublisher",
    "Compensator",
    "CompensationResult",
    "INVERSE_OPERATION",
    "DEFAULT_SUPPORTED_OPERATIONS",
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
    # entity resolution (Wave 2 / ER 5a, spec §7.4)
    "NormalizedEntity",
    "DefaultNormalizer",
    "FeatureAgreement",
    "IdentitySignal",
    "SharedStrongIdentifierRule",
    "CandidatePair",
    "BlockingPipeline",
    "ExactIdentifierChannel",
    "NormalizedNameChannel",
    "SourceKeyChannel",
    "EmbeddingChannel",
    "PairFeatures",
    "DefaultFeatureExtractor",
    "CalibrationKey",
    "MatchResult",
    "DeterministicRuleMatcher",
    "CalibrationModel",
    "CalibratedMatcher",
    "LabeledPair",
    "GoldenSet",
    "CalibrationMetrics",
    "evaluate",
    "InMemoryEpochPublisher",
    "InMemoryExecutionAuditSink",
]
