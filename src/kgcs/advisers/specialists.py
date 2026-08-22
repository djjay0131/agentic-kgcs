"""The bounded specialist advisers (Wave 4, DG-2, spec §7.4 / §7.3).

Each specialist is a thin, data-only subclass of `StructuredAdviser`: it fixes a
recommendation vocabulary (a `StrEnum`), a conservative `insufficient` fallback,
a prompt template id/version, and an instruction — and inherits the shared
render → complete → parse → abstain pipeline unchanged. Every one returns an
`AdviserAssessment` and *only* an `AdviserAssessment`: a recommendation enum
value, cited evidence ids, contradictions, and a confidence/abstain. **None of
them can construct or return a `CurationOperation`, a `GraphMutationBatch`, or a
`CurationPlan`, and none holds a `GraphMutationStore`** (§9 law 16). Their output
is structured evidence that feeds the deterministic policy gate — it decides
nothing itself.

The five capabilities (build plan §DG-2 / Wave 4):

- `IdentityAdviser` — are two entities the same? (`same`/`different`/insufficient)
- `AssertionAdviser` — how does incoming evidence relate to an assertion?
  (`supports`/`contradicts`/`supersedes`/insufficient)
- `ConflictAdviser` — competing assertions: it preserves both by default and may
  advise a preference only as advice, else route to review or gather more
  evidence (never overwrites — §9 law 10).
- `ConceptEvolutionAdviser` — has the conceptual model changed?
  (`merge`/`split`/`relabel`/`same_concept_different_scope`/`no_change`/insufficient)
- `OntologyEvolutionAdviser` — *proposes* an ontology candidate or a promotion
  rationale; it never promotes, so it cannot bypass the PROPOSED → APPROVED →
  OBSERVED → DEPRECATED governance (spec §7.3, §9 law 12).

They share one injected `CompletionPort` and the base machinery; they need not
be distinct model calls.
"""

from enum import StrEnum

from kgcs.advisers.base import StructuredAdviser


def _values(enum: type[StrEnum]) -> frozenset[str]:
    """The value set of a recommendation enum (its permitted recommendations)."""
    return frozenset(member.value for member in enum)


class IdentityRecommendation(StrEnum):
    """Whether two entities are the same (spec §7.4 `ResolutionAssessment`)."""

    SAME = "same"
    DIFFERENT = "different"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class IdentityAdviser(StructuredAdviser):
    """Advises `same` / `different` / `insufficient_evidence` for an entity pair.

    The spec's canonical `ResolutionAssessment` adviser: it compares the cited
    evidence and returns a recommendation with contradictions, never a merge.
    """

    ADVISER_TYPE = "identity"
    ADVISER_VERSION = "identity/1"
    TEMPLATE_ID = "identity_resolution"
    PROMPT_VERSION = "1"
    INSTRUCTION = (
        "You are a bounded identity-resolution adviser. Compare the two entities "
        "using only the cited evidence and recommend whether they are the same "
        "entity, different entities, or that the evidence is insufficient. Cite "
        "the evidence ids you relied on and list any contradictions. You never "
        "merge or write; your output is advice for a deterministic policy gate."
    )
    RECOMMENDATIONS = _values(IdentityRecommendation)
    INSUFFICIENT = IdentityRecommendation.INSUFFICIENT_EVIDENCE.value


class AssertionRecommendation(StrEnum):
    """How incoming evidence relates to a preferred assertion (spec §7.5)."""

    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    SUPERSEDES = "supersedes"
    INSUFFICIENT = "insufficient"


class AssertionAdviser(StructuredAdviser):
    """Advises `supports` / `contradicts` / `supersedes` / `insufficient`.

    Compares incoming evidence against the current preferred and competing
    assertions; it recommends a relationship but never overwrites — supersession
    is advice the policy gate and bitemporal history act on (§9 law 10).
    """

    ADVISER_TYPE = "assertion"
    ADVISER_VERSION = "assertion/1"
    TEMPLATE_ID = "assertion_relation"
    PROMPT_VERSION = "1"
    INSTRUCTION = (
        "You are a bounded assertion adviser. Using only the cited evidence, "
        "recommend whether the incoming evidence supports, contradicts, or "
        "supersedes the current assertion, or is insufficient. Cite evidence ids "
        "and contradictions. You never overwrite an assertion; competing "
        "assertions are preserved."
    )
    RECOMMENDATIONS = _values(AssertionRecommendation)
    INSUFFICIENT = AssertionRecommendation.INSUFFICIENT.value


class ConflictRecommendation(StrEnum):
    """The outcome of comparing competing assertions (spec §7.5, §9 law 10).

    `preserve_both` is the conservative default and the abstain value: an adviser
    that cannot tell never picks a winner. `prefer_subject`/`prefer_other` are
    advice only; `route_review`/`gather_more_evidence` defer to the human path.
    """

    PREFER_SUBJECT = "prefer_subject"
    PREFER_OTHER = "prefer_other"
    PRESERVE_BOTH = "preserve_both"
    ROUTE_REVIEW = "route_review"
    GATHER_MORE_EVIDENCE = "gather_more_evidence"


class ConflictAdviser(StructuredAdviser):
    """Compares competing assertions; preserves both unless advice is warranted.

    Its conservative fallback is `preserve_both` — on any failure or insufficient
    evidence it never destroys competing evidence and never silently prefers one
    side (§9 law 10). A preference is advice only; the policy gate decides.
    """

    ADVISER_TYPE = "conflict"
    ADVISER_VERSION = "conflict/1"
    TEMPLATE_ID = "conflict_comparison"
    PROMPT_VERSION = "1"
    INSTRUCTION = (
        "You are a bounded conflict adviser. Compare the two competing assertions "
        "using only the cited evidence. Preserve both by default; recommend a "
        "preference only when the evidence clearly warrants it, otherwise route to "
        "review or gather more evidence. Cite evidence ids and contradictions. You "
        "never overwrite or delete an assertion."
    )
    RECOMMENDATIONS = _values(ConflictRecommendation)
    INSUFFICIENT = ConflictRecommendation.PRESERVE_BOTH.value


class ConceptRecommendation(StrEnum):
    """How the conceptual model should change as evidence accumulates (DG-3)."""

    MERGE = "merge"
    SPLIT = "split"
    RELABEL = "relabel"
    SAME_CONCEPT_DIFFERENT_SCOPE = "same_concept_different_scope"
    NO_CHANGE = "no_change"
    INSUFFICIENT = "insufficient"


class ConceptEvolutionAdviser(StructuredAdviser):
    """Advises `merge`/`split`/`relabel`/`same_concept_different_scope`/`no_change`.

    Recommends how accumulated evidence changes a concept; the recommendation
    becomes an ordinary resolution decision downstream (never a direct rewrite of
    history — DG-3). Its conservative fallback is `insufficient`.
    """

    ADVISER_TYPE = "concept_evolution"
    ADVISER_VERSION = "concept_evolution/1"
    TEMPLATE_ID = "concept_evolution"
    PROMPT_VERSION = "1"
    INSTRUCTION = (
        "You are a bounded concept-evolution adviser. Using only the cited "
        "evidence, recommend whether two concepts should merge, one should split, "
        "a concept should be relabelled, they are the same concept at a different "
        "scope, or no change is warranted, or that the evidence is insufficient. "
        "Cite evidence ids and contradictions. You never rewrite history; your "
        "recommendation becomes a deterministic resolution decision."
    )
    RECOMMENDATIONS = _values(ConceptRecommendation)
    INSUFFICIENT = ConceptRecommendation.INSUFFICIENT.value


class OntologyRecommendation(StrEnum):
    """An ontology *proposal* — never a promotion (spec §7.3, §9 law 12).

    `propose_candidate` and `promotion_rationale` are proposals that enter the
    governed PROPOSED → APPROVED → OBSERVED lifecycle; they do not promote
    anything. `no_proposal`/`insufficient` are the conservative outcomes.
    """

    PROPOSE_CANDIDATE = "propose_candidate"
    PROMOTION_RATIONALE = "promotion_rationale"
    NO_PROPOSAL = "no_proposal"
    INSUFFICIENT = "insufficient"


class OntologyEvolutionAdviser(StructuredAdviser):
    """Proposes an ontology candidate or a promotion rationale — never promotes.

    Its output is a *proposal* only, so it cannot bypass the PROPOSED → APPROVED
    → OBSERVED → DEPRECATED governance (spec §7.3): promotion remains a governed
    action elsewhere, gated on approval (§9 law 12).
    """

    ADVISER_TYPE = "ontology_evolution"
    ADVISER_VERSION = "ontology_evolution/1"
    TEMPLATE_ID = "ontology_evolution"
    PROMPT_VERSION = "1"
    INSTRUCTION = (
        "You are a bounded ontology-evolution adviser. Using only the cited "
        "evidence, either propose a new ontology candidate, give a rationale for "
        "promoting an existing PROPOSED/APPROVED term, or recommend no proposal, "
        "or state the evidence is insufficient. Cite evidence ids. You never "
        "promote a term; approval governance is not yours to bypass."
    )
    RECOMMENDATIONS = _values(OntologyRecommendation)
    INSUFFICIENT = OntologyRecommendation.INSUFFICIENT.value
