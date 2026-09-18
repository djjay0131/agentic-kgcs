"""Entity resolution substrate (spec §7.4, ADR-0007): Wave 2 / ER 5a.

The deterministic and calibratable core of ER, built *before* any LLM adviser
(a later wave) and free of heavy dependencies — pure Python + pydantic v2, no
embeddings backend required. The four stages, each a small injectable port with
a deterministic reference implementation:

    normalize → block → extract features → match (calibrated probability)
        → validate cluster → deterministic policy gate

Nothing here merges entities: normalization produces canonical forms, identity
rules emit explainable signals, blocking proposes recall-oriented candidate
pairs, features are typed and honest-null, the matcher returns a *calibrated
probability* — never a merge decision, and never a fixed cosine threshold —
cluster validation rejects invalid transitive closures (Wave 3, §7.4 step 5),
and the deterministic policy gate (Wave 3, §7.4 step 6) chooses an `ErAction`
with no LLM in the loop.
"""

from kgcs.er.blocking import (
    BlockingChannel,
    BlockingPipeline,
    CandidatePair,
    EmbeddingChannel,
    ExactIdentifierChannel,
    NormalizedNameChannel,
    PairProducer,
    SourceKeyChannel,
    VectorIndex,
)
from kgcs.er.cluster import (
    Cluster,
    ClusterConstraint,
    ClusterSnapshot,
    ClusterValidation,
    ClusterValidator,
    ConstraintResult,
    IdentityAuthorityConstraint,
    MutuallyExclusiveAttributeConstraint,
    TemporalConsistencyConstraint,
    TenantBoundaryConstraint,
    UniqueSourceConstraint,
    default_cluster_validator,
    default_source_of,
    select_survivor,
)
from kgcs.er.features import (
    FEATURE_KEYS,
    DefaultFeatureExtractor,
    FeatureExtractor,
    PairFeatures,
    cosine_similarity,
    jaro_similarity,
    jaro_winkler_similarity,
)
from kgcs.er.matcher import (
    CalibratedMatcher,
    CalibrationEntry,
    CalibrationKey,
    CalibrationMetrics,
    CalibrationModel,
    DeterministicRuleMatcher,
    GoldenSet,
    LabeledPair,
    Matcher,
    MatchResult,
    calibrate_logistic,
    evaluate,
    linear_score,
    sigmoid,
)
from kgcs.er.normalize import (
    DEFAULT_CONTAINER_NAMESPACES,
    DEFAULT_STRONG_NAMESPACES,
    DefaultNormalizer,
    FeatureAgreement,
    IdentityRule,
    IdentitySignal,
    NormalizableEntity,
    NormalizedEntity,
    Normalizer,
    SharedStrongIdentifierRule,
    TypeHook,
    normalize_name,
    run_identity_rules,
)
from kgcs.er.resolution import (
    ErAction,
    ErDecision,
    ErResolutionPolicy,
    ErRoutingThresholds,
)

__all__ = [
    # normalize (2A)
    "NormalizedEntity",
    "NormalizableEntity",
    "Normalizer",
    "DefaultNormalizer",
    "TypeHook",
    "FeatureAgreement",
    "IdentitySignal",
    "IdentityRule",
    "SharedStrongIdentifierRule",
    "run_identity_rules",
    "normalize_name",
    "DEFAULT_CONTAINER_NAMESPACES",
    "DEFAULT_STRONG_NAMESPACES",
    # blocking (2B)
    "CandidatePair",
    "BlockingChannel",
    "PairProducer",
    "VectorIndex",
    "ExactIdentifierChannel",
    "NormalizedNameChannel",
    "SourceKeyChannel",
    "EmbeddingChannel",
    "BlockingPipeline",
    # features (2C)
    "PairFeatures",
    "FeatureExtractor",
    "DefaultFeatureExtractor",
    "FEATURE_KEYS",
    "jaro_similarity",
    "jaro_winkler_similarity",
    "cosine_similarity",
    # matcher (2D)
    "CalibrationKey",
    "MatchResult",
    "Matcher",
    "DeterministicRuleMatcher",
    "CalibrationEntry",
    "CalibrationModel",
    "CalibratedMatcher",
    "LabeledPair",
    "GoldenSet",
    "CalibrationMetrics",
    "evaluate",
    "calibrate_logistic",
    "sigmoid",
    "linear_score",
    # cluster validation (stage 5, §7.4 step 5)
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
    # deterministic policy gate (stage 6, §7.4 step 6)
    "ErAction",
    "ErDecision",
    "ErResolutionPolicy",
    "ErRoutingThresholds",
]
