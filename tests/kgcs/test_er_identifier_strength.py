"""Identifier strength is relative to the entity type the identifier names.

Regression suite for the container-identifier false-merge defect: `issn` and
`isbn` were in `DEFAULT_STRONG_NAMESPACES`, so two *different* papers that
merely appeared in the same journal carried a decisive `AGREE` into the feature
vector and auto-linked — at `STANDARD` outright, and at `HIGH` as soon as their
titles were superficially alike. Both directions are pinned here:

- a container identifier must not manufacture identity for the things *inside*
  the container (nor a contradiction between them), and
- it must remain fully strong for the container entity it genuinely identifies,
  which is what makes this a scoping fix rather than a deletion.
"""

import pytest

from kgcs.er.blocking import CandidatePair
from kgcs.er.cluster import Cluster, MutuallyExclusiveAttributeConstraint
from kgcs.er.features import DefaultFeatureExtractor
from kgcs.er.matcher import CalibrationKey, DeterministicRuleMatcher
from kgcs.er.normalize import (
    DEFAULT_CONTAINER_NAMESPACES,
    DEFAULT_STRONG_NAMESPACES,
    FeatureAgreement,
    NormalizedEntity,
    SharedStrongIdentifierRule,
    run_identity_rules,
)
from kgcs.er.resolution import ErAction, ErResolutionPolicy
from kgcs.profiles import (
    CurationProfile,
    ErMode,
    FalseMergeCostClass,
    IdentityAuthorityMode,
)

_ISSN = "1234-5678"
_PAIR = CandidatePair.of("a", "b")


def _entity(key: str, entity_type: str, name: str, **ids: tuple[str, ...]) -> NormalizedEntity:
    return NormalizedEntity(
        source_key=key,
        graph_id="g1",
        entity_type=entity_type,
        normalized_names=(name,),
        identifiers=dict(ids),
    )


def _profile(cost: FalseMergeCostClass) -> CurationProfile:
    return CurationProfile(
        profile_id="p",
        identity_authority_mode=IdentityAuthorityMode.OPEN,
        er_mode=ErMode.ACTIVE,
        false_merge_cost_class=cost,
        allowable_auto_actions=frozenset({"AUTO_LINK", "RETAIN_SEPARATE"}),
    )


def _decide(
    left: NormalizedEntity,
    right: NormalizedEntity,
    cost: FalseMergeCostClass,
    *,
    rule: SharedStrongIdentifierRule | None = None,
):
    """Score a pair end-to-end and route it, returning (probability, action)."""
    signals = run_identity_rules([rule or SharedStrongIdentifierRule()], left, right)
    features = DefaultFeatureExtractor().extract(_PAIR, left, right, signals=signals)
    key = CalibrationKey(
        graph_id="g1",
        entity_type=left.entity_type,
        source_pair=("s", "s"),
        matcher_version=DeterministicRuleMatcher.matcher_version,
        consequence_class=cost.value,
    )
    result = DeterministicRuleMatcher().score(features, pair=_PAIR, key=key)
    return result.probability, ErResolutionPolicy().decide(result, profile=_profile(cost)).action


# ---------------------------------------------------------------------------
# The defect: a container identifier must not merge the works inside it
# ---------------------------------------------------------------------------


def test_issn_and_isbn_are_not_unconditionally_strong() -> None:
    """The default strong set holds only namespaces that name the entity itself."""
    assert DEFAULT_STRONG_NAMESPACES == frozenset({"doi", "orcid", "vin"})
    assert "issn" not in DEFAULT_STRONG_NAMESPACES
    assert "isbn" not in DEFAULT_STRONG_NAMESPACES
    assert set(DEFAULT_CONTAINER_NAMESPACES) == {"issn", "isbn"}


def test_shared_issn_is_not_identity_agreement_between_two_papers() -> None:
    left = _entity("p1", "Paper", "a survey of deep learning methods", issn=(_ISSN,))
    right = _entity("p2", "Paper", "a survey of deep learning models", issn=(_ISSN,))
    signals = run_identity_rules([SharedStrongIdentifierRule()], left, right)
    # The identifier is still *reported* — suppression must be auditable — but
    # as UNKNOWN, which is neither agreement nor contradiction.
    assert [s.agreement for s in signals] == [FeatureAgreement.UNKNOWN]
    assert signals[0].namespace == "issn"
    assert "container" in (signals[0].detail or "")

    features = DefaultFeatureExtractor().extract(_PAIR, left, right, signals=signals)
    assert features.identifier_agreement is FeatureAgreement.UNKNOWN
    assert features.mutually_exclusive is False


@pytest.mark.parametrize(
    "cost", [FalseMergeCostClass.LOW, FalseMergeCostClass.STANDARD, FalseMergeCostClass.HIGH]
)
def test_two_distinct_papers_sharing_only_an_issn_never_auto_link(
    cost: FalseMergeCostClass,
) -> None:
    """The regression proper. Before the fix this reached p=0.998383 and
    AUTO_LINK even at HIGH (risk 0.001617 inside the 0.002 HIGH budget), and
    AUTO_LINK at STANDARD for titles as unalike as 0.63 similarity."""
    left = _entity("p1", "Paper", "a survey of deep learning methods", issn=(_ISSN,))
    right = _entity("p2", "Paper", "a survey of deep learning models", issn=(_ISSN,))
    probability, action = _decide(left, right, cost)
    assert action is not ErAction.AUTO_LINK
    # No identifier evidence at all: the pair sits in the uncertain middle on
    # name similarity alone, nowhere near any auto-link budget.
    assert probability < 0.9


def test_unrelated_papers_sharing_an_issn_do_not_auto_link_at_standard() -> None:
    left = _entity("p1", "Paper", "attention is all you need", issn=(_ISSN,))
    right = _entity("p2", "Paper", "deep residual learning for image recognition", issn=(_ISSN,))
    probability, action = _decide(left, right, FalseMergeCostClass.STANDARD)
    assert action is not ErAction.AUTO_LINK
    assert probability < 0.5


def test_disjoint_issns_do_not_make_two_papers_mutually_exclusive() -> None:
    """The symmetric defect: a print vs electronic ISSN (or a preprint venue vs
    the journal) is not evidence that two records are different papers."""
    left = _entity("p1", "Paper", "attention is all you need", issn=("1234-5678",))
    right = _entity("p2", "Paper", "attention is all you need", issn=("8765-4321",))
    signals = run_identity_rules([SharedStrongIdentifierRule()], left, right)
    assert all(s.agreement is not FeatureAgreement.CONTRADICT for s in signals)
    features = DefaultFeatureExtractor().extract(_PAIR, left, right, signals=signals)
    assert features.mutually_exclusive is False

    # And the cluster constraint that reads the same rule no longer blocks.
    cluster = Cluster(cluster_id="c1", graph_id="g1", entity_type="Paper", members=("p1", "p2"))
    result = MutuallyExclusiveAttributeConstraint().check(
        cluster, {"p1": left, "p2": right}
    )
    assert result.ok is True


def test_shared_isbn_does_not_merge_two_chapters_of_one_book() -> None:
    left = _entity("c1", "Chapter", "introduction", isbn=("9780000000001",))
    right = _entity("c2", "Chapter", "conclusion", isbn=("9780000000001",))
    _, action = _decide(left, right, FalseMergeCostClass.STANDARD)
    assert action is not ErAction.AUTO_LINK


# ---------------------------------------------------------------------------
# What must keep working: the container identifier still identifies containers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entity_type", ["Journal", "journal", "Serial", "publication_venue"])
def test_shared_issn_still_strongly_identifies_two_journals(entity_type: str) -> None:
    left = _entity("j1", entity_type, "journal of machine learning research", issn=(_ISSN,))
    right = _entity("j2", entity_type, "j. mach. learn. res.", issn=(_ISSN,))
    signals = run_identity_rules([SharedStrongIdentifierRule()], left, right)
    assert [s.agreement for s in signals] == [FeatureAgreement.AGREE]

    probability, action = _decide(left, right, FalseMergeCostClass.STANDARD)
    assert action is ErAction.AUTO_LINK
    assert probability > 0.98


def test_disjoint_issns_still_contradict_between_two_journals() -> None:
    left = _entity("j1", "Journal", "journal of irreproducible results", issn=("1111-1111",))
    right = _entity("j2", "Journal", "journal of irreproducible results", issn=("2222-2222",))
    signals = run_identity_rules([SharedStrongIdentifierRule()], left, right)
    assert [s.agreement for s in signals] == [FeatureAgreement.CONTRADICT]
    features = DefaultFeatureExtractor().extract(_PAIR, left, right, signals=signals)
    assert features.mutually_exclusive is True


def test_shared_isbn_still_strongly_identifies_two_books() -> None:
    left = _entity("b1", "Book", "the art of computer programming", isbn=("9780000000001",))
    right = _entity("b2", "Monograph", "art of computer programming", isbn=("9780000000001",))
    signals = run_identity_rules([SharedStrongIdentifierRule()], left, right)
    assert [s.agreement for s in signals] == [FeatureAgreement.AGREE]


def test_promotion_needs_both_sides_to_be_the_container_type() -> None:
    """A Paper matched against the Journal it appeared in shares an ISSN; that
    is emphatically not evidence the paper *is* the journal."""
    paper = _entity("p1", "Paper", "attention is all you need", issn=(_ISSN,))
    journal = _entity("j1", "Journal", "journal of machine learning research", issn=(_ISSN,))
    signals = run_identity_rules([SharedStrongIdentifierRule()], paper, journal)
    assert [s.agreement for s in signals] == [FeatureAgreement.UNKNOWN]


# ---------------------------------------------------------------------------
# Still a heuristic, still injectable (ADR candidate 0005)
# ---------------------------------------------------------------------------


def test_doi_is_strong_for_papers_regardless_of_the_container_mapping() -> None:
    left = _entity("p1", "Paper", "a", doi=("10.1/abc",))
    right = _entity("p2", "Paper", "b", doi=("10.1/abc",))
    signals = run_identity_rules([SharedStrongIdentifierRule()], left, right)
    assert [s.agreement for s in signals] == [FeatureAgreement.AGREE]


def test_adopter_can_declare_its_own_container_type_vocabulary() -> None:
    left = _entity("v1", "Zeitschrift", "x", issn=(_ISSN,))
    right = _entity("v2", "Zeitschrift", "y", issn=(_ISSN,))
    rule = SharedStrongIdentifierRule(
        container_namespaces={"issn": frozenset({"Zeitschrift"})}
    )
    assert [s.agreement for s in run_identity_rules([rule], left, right)] == [
        FeatureAgreement.AGREE
    ]


def test_adopter_can_disable_container_promotion_entirely() -> None:
    left = _entity("j1", "Journal", "x", issn=(_ISSN,))
    right = _entity("j2", "Journal", "y", issn=(_ISSN,))
    rule = SharedStrongIdentifierRule(container_namespaces={})
    assert run_identity_rules([rule], left, right) == ()


def test_adopter_can_still_force_the_old_unconditionally_strong_behaviour() -> None:
    """The escape hatch is explicit and opt-in, not the default."""
    left = _entity("p1", "Paper", "x", issn=(_ISSN,))
    right = _entity("p2", "Paper", "y", issn=(_ISSN,))
    rule = SharedStrongIdentifierRule(
        strong_namespaces=DEFAULT_STRONG_NAMESPACES | {"issn"}, container_namespaces={}
    )
    assert [s.agreement for s in run_identity_rules([rule], left, right)] == [
        FeatureAgreement.AGREE
    ]
