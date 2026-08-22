"""Reusable contract suites for the ports `kgcs` defines.

Mirrors `kgis.testing` and `kg_contracts.testing`: a subclassable suite per
port, so every implementation — the in-memory reference here, a durable
backend in Plan 2, an adopter's own adapter — is held to the same rules with
zero infrastructure.
"""

from kgcs.testing.contract import AuditSinkContract, CandidateValidatorContract

__all__ = ["AuditSinkContract", "CandidateValidatorContract"]
