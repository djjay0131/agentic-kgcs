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
    DEFAULT_SCOPED_NAMESPACES,
    DEFAULT_STRONG_NAMESPACES,
    FeatureAgreement,
    NamespaceScope,
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


def test_only_type_agnostic_namespaces_are_unconditionally_strong() -> None:
    """The strong set holds only namespaces strong for *whatever* carries them."""
    assert DEFAULT_STRONG_NAMESPACES == frozenset({"doi", "vin"})
    # Each of these names one *kind* of thing, so each is scoped, not strong.
    assert set(DEFAULT_SCOPED_NAMESPACES) == {"orcid", "issn", "isbn"}
    for namespace in ("orcid", "issn", "isbn"):
        assert namespace not in DEFAULT_STRONG_NAMESPACES


def test_a_proceedings_series_type_is_not_an_issn_subject() -> None:
    """MAJOR-1 regression: a proceedings *series* carries one ISSN across
    unrelated conferences (LNCS 0302-9743), so promoting `issn` for a venue or
    conference type rebuilds the very false merge this scoping prevents."""
    subjects = DEFAULT_SCOPED_NAMESPACES["issn"].subjects
    assert subjects == frozenset({"journal", "serial", "periodical"})
    for forbidden in ("venue", "publicationvenue", "conference", "proceedings"):
        assert forbidden not in subjects


def test_two_different_conferences_sharing_a_series_issn_never_auto_link() -> None:
    lncs = "0302-9743"
    left = _entity("v1", "Venue", "international conference on machine learning", issn=(lncs,))
    right = _entity("v2", "Venue", "international conference on computer vision", issn=(lncs,))
    for cost in FalseMergeCostClass:
        probability, action = _decide(left, right, cost)
        assert action is not ErAction.AUTO_LINK
        assert probability < 0.9


def test_shared_issn_is_not_identity_agreement_between_two_papers() -> None:
    left = _entity("p1", "Paper", "a survey of deep learning methods", issn=(_ISSN,))
    right = _entity("p2", "Paper", "a survey of deep learning models", issn=(_ISSN,))
    signals = run_identity_rules([SharedStrongIdentifierRule()], left, right)
    # The identifier is still *reported* — suppression must be auditable — but
    # as UNKNOWN, which is neither agreement nor contradiction.
    assert [s.agreement for s in signals] == [FeatureAgreement.UNKNOWN]
    assert signals[0].namespace == "issn"
    assert "not weighed as identity evidence" in (signals[0].detail or "")

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


@pytest.mark.parametrize("entity_type", ["Journal", "journal", "Serial", "PERIODICAL"])
def test_shared_issn_still_strongly_identifies_two_journals(entity_type: str) -> None:
    left = _entity("j1", entity_type, "journal of machine learning research", issn=(_ISSN,))
    right = _entity("j2", entity_type, "j. mach. learn. res.", issn=(_ISSN,))
    signals = run_identity_rules([SharedStrongIdentifierRule()], left, right)
    assert [s.agreement for s in signals] == [FeatureAgreement.AGREE]

    probability, action = _decide(left, right, FalseMergeCostClass.STANDARD)
    assert action is ErAction.AUTO_LINK
    assert probability > 0.98


def test_a_journals_print_and_electronic_issn_do_not_contradict() -> None:
    """MAJOR-2 regression. An earlier revision of this suite asserted the
    opposite and so pinned the defect green: JMLR's print ISSN 1532-4435 and
    its e-ISSN 1533-7928 identify *one* journal — which is precisely why
    ISSN-L exists — and read as a contradiction, scoring p=0.000001 and having
    the cluster rejected. `contradicts=False` is what fixes it."""
    assert DEFAULT_SCOPED_NAMESPACES["issn"].contradicts is False
    left = _entity("j1", "Journal", "journal of machine learning research", issn=("1532-4435",))
    right = _entity("j2", "Journal", "j. mach. learn. res.", issn=("1533-7928",))
    signals = run_identity_rules([SharedStrongIdentifierRule()], left, right)
    assert [s.agreement for s in signals] == [FeatureAgreement.UNKNOWN]

    features = DefaultFeatureExtractor().extract(_PAIR, left, right, signals=signals)
    assert features.mutually_exclusive is False

    # The pair is left to be judged on its other evidence: the score is exactly
    # what it would be if neither record carried an ISSN, rather than the
    # p=0.000001 RETAIN_SEPARATE the CONTRADICT drove it to.
    probability, action = _decide(left, right, FalseMergeCostClass.STANDARD)
    bare_left = _entity("j1", "Journal", "journal of machine learning research")
    bare_right = _entity("j2", "Journal", "j. mach. learn. res.")
    bare_probability, _ = _decide(bare_left, bare_right, FalseMergeCostClass.STANDARD)
    assert probability == bare_probability
    assert probability > 0.3
    assert action is ErAction.GATHER_MORE_EVIDENCE

    cluster = Cluster(cluster_id="c1", graph_id="g1", entity_type="Journal", members=("j1", "j2"))
    assert MutuallyExclusiveAttributeConstraint().check(
        cluster, {"j1": left, "j2": right}
    ).ok is True


def test_a_books_isbn10_and_isbn13_do_not_contradict() -> None:
    """The same defect for books: 0201896834 and 9780201896831 are the same
    book under two ISBN standards, and one book also carries a distinct ISBN
    per format."""
    assert DEFAULT_SCOPED_NAMESPACES["isbn"].contradicts is False
    left = _entity("b1", "Book", "the art of computer programming", isbn=("0201896834",))
    right = _entity("b2", "Book", "the art of computer programming", isbn=("9780201896831",))
    signals = run_identity_rules([SharedStrongIdentifierRule()], left, right)
    assert [s.agreement for s in signals] == [FeatureAgreement.UNKNOWN]
    features = DefaultFeatureExtractor().extract(_PAIR, left, right, signals=signals)
    assert features.mutually_exclusive is False


def test_disjoint_orcids_do_still_contradict_between_two_people() -> None:
    """`contradicts=False` is not blanket: ORCID issues one value per person,
    so two different ORCIDs on two Person records remain decisive."""
    assert DEFAULT_SCOPED_NAMESPACES["orcid"].contradicts is True
    left = _entity("a1", "Person", "john smith", orcid=("0000000218250009",))
    right = _entity("a2", "Person", "john smith", orcid=("0000000321250117",))
    signals = run_identity_rules([SharedStrongIdentifierRule()], left, right)
    assert [s.agreement for s in signals] == [FeatureAgreement.CONTRADICT]
    features = DefaultFeatureExtractor().extract(_PAIR, left, right, signals=signals)
    assert features.mutually_exclusive is True


def test_shared_isbn_still_strongly_identifies_two_books() -> None:
    left = _entity("b1", "Book", "the art of computer programming", isbn=("9780000000001",))
    right = _entity("b2", "Monograph", "art of computer programming", isbn=("9780000000001",))
    signals = run_identity_rules([SharedStrongIdentifierRule()], left, right)
    assert [s.agreement for s in signals] == [FeatureAgreement.AGREE]


def test_promotion_needs_both_sides_to_be_the_subject_type() -> None:
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


def test_adopter_can_declare_its_own_subject_type_vocabulary() -> None:
    left = _entity("v1", "Zeitschrift", "x", issn=(_ISSN,))
    right = _entity("v2", "Zeitschrift", "y", issn=(_ISSN,))
    rule = SharedStrongIdentifierRule(
        scoped_namespaces={"issn": NamespaceScope(subjects={"Zeitschrift"}, contradicts=False)}
    )
    assert [s.agreement for s in run_identity_rules([rule], left, right)] == [
        FeatureAgreement.AGREE
    ]


def test_subjects_are_stored_normalized_on_the_public_mapping() -> None:
    """A consumer reading the exported default gets type keys already in the
    form `_type_key` produces, rather than having to re-derive it."""
    assert DEFAULT_SCOPED_NAMESPACES["orcid"].subjects == frozenset(
        {"person", "author", "researcher", "contributor", "creator"}
    )
    assert NamespaceScope(subjects={"Publication_Venue", "JOURNAL"}).subjects == frozenset(
        {"publicationvenue", "journal"}
    )


def test_adopter_can_disable_scoping_entirely() -> None:
    left = _entity("j1", "Journal", "x", issn=(_ISSN,))
    right = _entity("j2", "Journal", "y", issn=(_ISSN,))
    rule = SharedStrongIdentifierRule(scoped_namespaces={})
    assert run_identity_rules([rule], left, right) == ()


def test_adopter_can_still_force_the_old_unconditionally_strong_behaviour() -> None:
    """The escape hatch is explicit and opt-in, not the default. Membership in
    `strong_namespaces` outranks any scope for the same namespace, so the
    faithful restore of the pre-ADR-0017 default needs no second argument."""
    left = _entity("p1", "Paper", "x", issn=(_ISSN,))
    right = _entity("p2", "Paper", "y", issn=(_ISSN,))
    rule = SharedStrongIdentifierRule(
        strong_namespaces=DEFAULT_STRONG_NAMESPACES | {"issn", "isbn", "orcid"}
    )
    assert [s.agreement for s in run_identity_rules([rule], left, right)] == [
        FeatureAgreement.AGREE
    ]


# ---------------------------------------------------------------------------
# ORCID: the same defect class, one namespace over (review MAJOR-3)
# ---------------------------------------------------------------------------


def test_two_different_papers_by_one_author_never_auto_link_on_orcid() -> None:
    """An ORCID names a *person*. Bibliographic ingest routinely copies an
    author's ORCID onto the work record, where a shared value means "same
    author", not "same paper" — byte-identical in mechanism to the ISSN
    defect, and it scored p=0.998383/AUTO_LINK before this scoping."""
    orcid = ("000000021825009x",)
    left = _entity("p1", "Paper", "a survey of deep learning methods", orcid=orcid)
    right = _entity("p2", "Paper", "a survey of deep learning models", orcid=orcid)
    signals = run_identity_rules([SharedStrongIdentifierRule()], left, right)
    assert [s.agreement for s in signals] == [FeatureAgreement.UNKNOWN]
    for cost in FalseMergeCostClass:
        probability, action = _decide(left, right, cost)
        assert action is not ErAction.AUTO_LINK
        assert probability < 0.9


@pytest.mark.parametrize("entity_type", ["Person", "Author", "Researcher", "contributor"])
def test_shared_orcid_still_strongly_identifies_two_people(entity_type: str) -> None:
    orcid = ("000000021825009x",)
    left = _entity("a1", entity_type, "j. smith", orcid=orcid)
    right = _entity("a2", entity_type, "john smith", orcid=orcid)
    signals = run_identity_rules([SharedStrongIdentifierRule()], left, right)
    assert [s.agreement for s in signals] == [FeatureAgreement.AGREE]
    _, action = _decide(left, right, FalseMergeCostClass.STANDARD)
    assert action is ErAction.AUTO_LINK


def test_an_author_orcid_does_not_merge_a_person_into_their_paper() -> None:
    orcid = ("000000021825009x",)
    person = _entity("a1", "Person", "john smith", orcid=orcid)
    paper = _entity("p1", "Paper", "a survey of deep learning methods", orcid=orcid)
    signals = run_identity_rules([SharedStrongIdentifierRule()], person, paper)
    assert [s.agreement for s in signals] == [FeatureAgreement.UNKNOWN]


# ---------------------------------------------------------------------------
# The suppression must hold on every channel, not just the signal (MAJOR-4)
# ---------------------------------------------------------------------------


def test_a_suppressed_identifier_cannot_re_enter_through_attribute_rarity() -> None:
    """`attribute_rarity` carries weight +2.0 and previously counted *every*
    shared namespace. With a rarity_index injected, a shared ISSN between two
    Papers lifted them 0.604806 -> 0.918754 after the signal channel had
    already refused it — the same false merge by a second route."""
    left = _entity("p1", "Paper", "a survey of deep learning methods", issn=(_ISSN,))
    right = _entity("p2", "Paper", "a survey of deep learning models", issn=(_ISSN,))
    signals = run_identity_rules([SharedStrongIdentifierRule()], left, right)
    extractor = DefaultFeatureExtractor(rarity_index={_ISSN: 1})
    assert extractor.extract(_PAIR, left, right, signals=signals).attribute_rarity is None

    # Identical to the pair carrying no ISSN at all: suppression is total.
    bare_left = _entity("p1", "Paper", "a survey of deep learning methods")
    bare_right = _entity("p2", "Paper", "a survey of deep learning models")
    assert extractor.extract(_PAIR, left, right, signals=signals).to_vector() == (
        extractor.extract(_PAIR, bare_left, bare_right).to_vector()
    )


def test_rarity_still_counts_a_shared_rare_identifier_the_rules_are_silent_on() -> None:
    """Scoping must not gut the feature: a weak namespace no identity rule
    speaks to is exactly what `attribute_rarity` exists to weigh."""
    left = _entity("p1", "Paper", "x", internal_key=("rare-value",))
    right = _entity("p2", "Paper", "y", internal_key=("rare-value",))
    signals = run_identity_rules([SharedStrongIdentifierRule()], left, right)
    assert signals == ()
    extractor = DefaultFeatureExtractor(rarity_index={"rare-value": 2})
    assert extractor.extract(_PAIR, left, right, signals=signals).attribute_rarity == 0.5


def test_rarity_still_counts_a_shared_issn_between_two_journals() -> None:
    left = _entity("j1", "Journal", "x", issn=(_ISSN,))
    right = _entity("j2", "Journal", "y", issn=(_ISSN,))
    signals = run_identity_rules([SharedStrongIdentifierRule()], left, right)
    extractor = DefaultFeatureExtractor(rarity_index={_ISSN: 4})
    assert extractor.extract(_PAIR, left, right, signals=signals).attribute_rarity == 0.25
