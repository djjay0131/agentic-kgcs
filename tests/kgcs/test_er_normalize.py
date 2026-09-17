"""ER stage 1: normalization is deterministic, idempotent, and never merges;
identity rules emit explainable agreement/contradiction signals."""

from kg_contracts.identity import EntityRef
from kg_contracts.testing.factories import make_entity_candidate

from kgcs.er.normalize import (
    DefaultNormalizer,
    FeatureAgreement,
    SharedStrongIdentifierRule,
    normalize_name,
    run_identity_rules,
)


def _paper(*, key: str, doi: str | None = None, orcid: str | None = None, name: str | None = None):
    aliases = [EntityRef(entity_type="Paper", namespace="src", key=key)]
    if doi is not None:
        aliases.append(EntityRef(entity_type="Paper", namespace="doi", key=doi))
    if orcid is not None:
        aliases.append(EntityRef(entity_type="Paper", namespace="orcid", key=orcid))
    return make_entity_candidate(
        entity_type="Paper",
        key=key,
        semantic_key=f"paper/src/{key}",
        aliases=tuple(aliases),
        display_name=name,
    )


def test_normalize_name_is_idempotent_and_collapses() -> None:
    once = normalize_name("  Álvaro   José  ")
    assert once == normalize_name(once)
    assert once == "álvaro josé"


def test_normalization_is_deterministic() -> None:
    normalizer = DefaultNormalizer()
    candidate = _paper(key="p1", doi="10.1/ABC", name="A Study")
    assert normalizer.normalize(candidate) == normalizer.normalize(candidate)


def test_normalization_is_idempotent_on_names_and_ids() -> None:
    normalizer = DefaultNormalizer()
    first = normalizer.normalize(_paper(key="p1", doi="HTTPS://doi.org/10.1/ABC", name="A  Study"))
    # Feed the already-normalized forms back in; they must not change again.
    second = normalizer.normalize(
        _paper(key="p1", doi=first.identifiers["doi"][0], name=first.normalized_names[0])
    )
    assert first.normalized_names == second.normalized_names
    assert first.identifiers["doi"] == second.identifiers["doi"]


def test_doi_normalization_strips_resolver_and_casefolds() -> None:
    normalizer = DefaultNormalizer()
    normalized = normalizer.normalize(_paper(key="p1", doi="HTTPS://doi.org/10.1/ABC"))
    assert normalized.identifiers["doi"] == ("10.1/abc",)


def test_orcid_normalization_keeps_digits_and_checksum() -> None:
    normalizer = DefaultNormalizer()
    normalized = normalizer.normalize(_paper(key="p1", orcid="0000-0002-1825-009X"))
    assert normalized.identifiers["orcid"] == ("000000021825009X",)


def test_normalization_produces_one_view_per_entity_never_merges() -> None:
    normalizer = DefaultNormalizer()
    left = normalizer.normalize(_paper(key="p1", doi="10.1/ABC"))
    right = normalizer.normalize(_paper(key="p2", doi="10.1/ABC"))
    # Same DOI, but two distinct normalized entities — normalization does not
    # collapse them into one.
    assert left.source_key != right.source_key
    assert left.identifiers["doi"] == right.identifiers["doi"]


def test_shared_strong_identifier_rule_agrees() -> None:
    normalizer = DefaultNormalizer()
    rule = SharedStrongIdentifierRule()
    left = normalizer.normalize(_paper(key="p1", doi="10.1/ABC"))
    right = normalizer.normalize(_paper(key="p2", doi="https://doi.org/10.1/abc"))
    signals = run_identity_rules([rule], left, right)
    assert len(signals) == 1
    assert signals[0].namespace == "doi"
    assert signals[0].agreement is FeatureAgreement.AGREE


def test_shared_strong_identifier_rule_contradicts_on_disjoint() -> None:
    normalizer = DefaultNormalizer()
    rule = SharedStrongIdentifierRule()
    left = normalizer.normalize(_paper(key="p1", doi="10.1/ABC"))
    right = normalizer.normalize(_paper(key="p2", doi="10.1/XYZ"))
    signals = run_identity_rules([rule], left, right)
    assert signals[0].agreement is FeatureAgreement.CONTRADICT


def test_identity_rule_is_silent_when_identifier_absent_on_a_side() -> None:
    normalizer = DefaultNormalizer()
    rule = SharedStrongIdentifierRule()
    left = normalizer.normalize(_paper(key="p1", doi="10.1/ABC"))
    right = normalizer.normalize(_paper(key="p2"))  # no doi
    assert run_identity_rules([rule], left, right) == ()
