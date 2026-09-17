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

from kgcs.advisers import (
    Adviser,
    AdviserAssessment,
    AdviserQuestion,
    AssertionAdviser,
    AssertionRecommendation,
    CompletionError,
    CompletionMiss,
    CompletionPort,
    CompletionRequest,
    CompletionResponse,
    CompletionTimeout,
    ConceptEvolutionAdviser,
    ConceptRecommendation,
    ConflictAdviser,
    ConflictRecommendation,
    CurationOrchestrator,
    FailingCompletionClient,
    IdentityAdviser,
    IdentityRecommendation,
    MalformedCompletionClient,
    OntologyEvolutionAdviser,
    OntologyRecommendation,
    OrchestrationResult,
    RecordedCompletionClient,
    StructuredAdviser,
    TimeoutCompletionClient,
)
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
    Cluster,
    ClusterConstraint,
    ClusterSnapshot,
    ClusterValidation,
    ClusterValidator,
    ConstraintResult,
    DefaultFeatureExtractor,
    DefaultNormalizer,
    DeterministicRuleMatcher,
    EmbeddingChannel,
    ErAction,
    ErDecision,
    ErResolutionPolicy,
    ErRoutingThresholds,
    ExactIdentifierChannel,
    FeatureAgreement,
    GoldenSet,
    IdentityAuthorityConstraint,
    IdentitySignal,
    LabeledPair,
    MatchResult,
    MutuallyExclusiveAttributeConstraint,
    NormalizedEntity,
    NormalizedNameChannel,
    PairFeatures,
    SharedStrongIdentifierRule,
    SourceKeyChannel,
    TemporalConsistencyConstraint,
    TenantBoundaryConstraint,
    UniqueSourceConstraint,
    default_cluster_validator,
    default_source_of,
    evaluate,
    select_survivor,
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
from kgcs.profiles import (
    CurationProfile,
    ErMode,
    FalseMergeCostClass,
    IdentityAuthorityMode,
    ProfileRegistry,
    ProfileScope,
    client_authoritative_profile,
    default_profile,
    default_registry,
    projection_consumer_profile,
)
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
    # cluster validation (Wave 3 / §7.4 step 5)
    "Cluster",
    "ClusterSnapshot",
    "ClusterConstraint",
    "ConstraintResult",
    "ClusterValidation",
    "ClusterValidator",
    "TemporalConsistencyConstraint",
    "UniqueSourceConstraint",
    "MutuallyExclusiveAttributeConstraint",
    "TenantBoundaryConstraint",
    "IdentityAuthorityConstraint",
    "default_cluster_validator",
    "default_source_of",
    "select_survivor",
    # deterministic ER policy gate (Wave 3 / §7.4 step 6)
    "ErAction",
    "ErDecision",
    "ErResolutionPolicy",
    "ErRoutingThresholds",
    # curation profiles (Wave 3 / DG-5)
    "IdentityAuthorityMode",
    "ErMode",
    "FalseMergeCostClass",
    "ProfileScope",
    "CurationProfile",
    "ProfileRegistry",
    "default_profile",
    "projection_consumer_profile",
    "client_authoritative_profile",
    "default_registry",
    # bounded LLM advisers (Wave 4 / DG-2 / DG-4, spec §7.4)
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
    "AdviserQuestion",
    "AdviserAssessment",
    "Adviser",
    "StructuredAdviser",
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
    "CurationOrchestrator",
    "OrchestrationResult",
    "InMemoryEpochPublisher",
    "InMemoryExecutionAuditSink",
]
