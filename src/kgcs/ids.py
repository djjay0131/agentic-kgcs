"""Injected ID minting: `IdFactory`, `DerivedIdFactory`, `UlidIdFactory`.

Every ID the curation core stamps — a minted canonical identity, an
operation, a plan, an audit record — is decided *here*, behind an injected
port, for one reason: `kg_contracts`' own models default their IDs to
`"…_" + new_ulid()`, and a ULID embeds a millisecond timestamp and 80 random
bits. Left to the defaults, two runs over the same candidate would produce
plans that differ in every ID, and "identical input produces an identical
plan" — the sprint's central determinism guarantee — would be false.

So the core never lets a contract model default its own ID. It passes one in,
minted by an `IdFactory`. The **default** factory (`DerivedIdFactory`) is a
pure function of stable candidate content: replaying the same candidates
yields byte-identical IDs with nothing injected, no fixed seed, no clock.
`UlidIdFactory` is the escape hatch for callers who explicitly want globally
unique, time-sortable IDs and do not need replay.

A derived ID is `sha256(seed)` truncated and Crockford-base32 encoded. That
alphabet (`0-9A-HJKMNP-TV-Z`, no I/L/O/U) is exactly the character class a
canonical identity ULID must match (`kg_contracts.identity`), so a derived
identity ID passes `is_identity_id` for free. Collision resistance is 128
bits — the same order as a real ULID's 80 random bits, and far past anything
a single curation batch can reach.
"""

import hashlib
from typing import Protocol, runtime_checkable

from kg_contracts.identity import is_identity_id, new_identity_id

# Crockford's base32 alphabet: the identity-ULID character class exactly
# (`kg_contracts.identity` validates the ULID segment as `[0-9A-Z]{26}`, and
# every one of these 32 symbols is in that set). Excludes I/L/O/U.
_CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

# A canonical identity ULID segment is exactly 26 base32 chars (130 bits of
# capacity holding our 128-bit digest prefix).
_ULID_LEN = 26
_DIGEST_BYTES = 16


def _encode_base32(data: bytes, length: int) -> str:
    """Encode `data` as `length` Crockford-base32 characters, MSB first."""
    value = int.from_bytes(data, byteorder="big")
    chars = ["0"] * length
    for i in range(length - 1, -1, -1):
        chars[i] = _CROCKFORD_ALPHABET[value & 0x1F]
        value >>= 5
    return "".join(chars)


def _derive_token(seed: str) -> str:
    """A stable 26-char Crockford-base32 token for `seed` (pure, no time)."""
    digest = hashlib.sha256(seed.encode("utf-8")).digest()[:_DIGEST_BYTES]
    return _encode_base32(digest, _ULID_LEN)


def is_well_formed_graph_id(graph_id: str) -> bool:
    """Return True iff `graph_id` can anchor a canonical identity ID.

    Reuses the contract's own `is_identity_id` regex (via a placeholder ULID
    segment) rather than re-deriving the graph-id grammar here — the single
    source of truth for "what graph IDs are legal" stays in `kg_contracts`.
    """
    return is_identity_id(f"kg://{graph_id}/identity/{'0' * _ULID_LEN}")


@runtime_checkable
class IdFactory(Protocol):
    """Mints every ID the curation core stamps onto its outputs.

    `seed` on the non-identity methods is a caller-supplied string built from
    stable candidate content; a deterministic factory maps equal seeds to
    equal IDs, which is what makes replay byte-identical.
    """

    def identity_id(self, graph_id: str, seed: str) -> str:
        """Mint a canonical identity ID (`kg://<graph_id>/identity/<ulid>`)."""
        ...

    def operation_id(self, seed: str) -> str:
        """Mint a `CurationOperation.operation_id` (`op_…`)."""
        ...

    def assertion_id(self, seed: str) -> str:
        """Mint an `Assertion.assertion_id` (`as_…`) for an operation payload."""
        ...

    def plan_id(self, seed: str) -> str:
        """Mint a `CurationPlan.plan_id` (`pl_…`)."""
        ...

    def audit_id(self, seed: str) -> str:
        """Mint an `AuditRecord.audit_id` (`au_…`)."""
        ...


class DerivedIdFactory:
    """Deterministic `IdFactory`: every ID is a pure hash of its seed.

    The default the engine wires in. No state, no clock, no randomness —
    replaying the same candidates through the same engine yields the same
    IDs, so two plans over identical input are byte-for-byte equal. Prefixes
    (`op_`/`pl_`/`au_`) mirror the contract defaults so a derived ID reads
    the same as a ULID one in a log.
    """

    def identity_id(self, graph_id: str, seed: str) -> str:
        if not is_well_formed_graph_id(graph_id):
            raise ValueError(
                f"cannot mint identity id: graph_id {graph_id!r} is not well-formed"
            )
        return f"kg://{graph_id}/identity/{_derive_token(seed)}"

    def operation_id(self, seed: str) -> str:
        return "op_" + _derive_token(seed)

    def assertion_id(self, seed: str) -> str:
        return "as_" + _derive_token(seed)

    def plan_id(self, seed: str) -> str:
        return "pl_" + _derive_token(seed)

    def audit_id(self, seed: str) -> str:
        return "au_" + _derive_token(seed)


class UlidIdFactory:
    """Non-deterministic `IdFactory`: ULID-backed, globally unique, sortable.

    For callers that want fresh time-sortable IDs and do not need replay. The
    `seed` arguments are ignored — every call mints a new ULID. Deliberately
    NOT the engine default: using it forfeits "identical input → identical
    plan".
    """

    def identity_id(self, graph_id: str, seed: str) -> str:
        return new_identity_id(graph_id)

    def operation_id(self, seed: str) -> str:
        return "op_" + _new_ulid()

    def assertion_id(self, seed: str) -> str:
        return "as_" + _new_ulid()

    def plan_id(self, seed: str) -> str:
        return "pl_" + _new_ulid()

    def audit_id(self, seed: str) -> str:
        return "au_" + _new_ulid()


def _new_ulid() -> str:
    """A fresh ULID via the contract's minter (import kept local to the seam)."""
    # `new_identity_id` is the only public ULID surface in `kg_contracts`;
    # slice the ULID segment out of a throwaway identity id so `UlidIdFactory`
    # does not reach into the contract's private `_ulid` module.
    return new_identity_id("kgcs").rsplit("/", 1)[1]
