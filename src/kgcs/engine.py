"""The curation engine: the whole Sprint-1 pipeline, wired from injected parts.

    Candidate → Validator → Policy → Planner → Audit
                                          ↓
                                     EngineResult

`CurationEngine` owns no policy of its own — no clock, no ids, no scoring, no
graph reference (it could not mutate a graph if it tried; it holds nothing
that can). It only sequences the four ports it was handed. That is what makes
every stage independently testable and the whole run deterministic: wire in a
`DerivedIdFactory` and a `FixedClock` and two runs over the same candidates
produce an identical plan and identical audit records.

Two guarantees live in the sequencing itself, not in any single stage:

- **A validation failure never reaches the policy.** The loop resolves a
  candidate only after its `ValidationDecision` is `valid`; an invalid
  candidate gets an outcome with `resolution=None` and the policy is never
  called for it. Fail-closed is cheap and unbypassable (governance
  principle 1).
- **Nothing is written to the graph.** The engine's only side effect is
  optionally appending audit records to an injected `AuditSink`. The
  `CurationPlan` it returns is a description of what *would* happen, applied
  by nobody in Sprint 1.

Construct one directly for full control, or use `CurationEngine.create(...)`,
which wires all four stages from one `IdFactory` and one `Clock` so their
snapshot and policy versions cannot drift apart.
"""

from dataclasses import dataclass
from typing import Sequence

from kg_contracts.candidates import Candidate
from kg_contracts.curation import AuditRecord, CurationPlan, ResolutionDecision, ValidationDecision
from kg_contracts.policy import ConfidencePolicy

from kgcs.audit import DEFAULT_DECIDED_BY, AuditRecorder, AuditSink
from kgcs.clock import Clock
from kgcs.ids import DerivedIdFactory, IdFactory
from kgcs.planner import CurationPlanner, PlanResult, ResolvedCandidate
from kgcs.policy import DEFAULT_SNAPSHOT_VERSION, ResolutionPolicy
from kgcs.validation import CandidateValidator, default_validator


@dataclass(frozen=True)
class CandidateOutcome:
    """The per-candidate audit trail of stages 1-2, in one immutable record.

    `resolution` is `None` exactly when `validation.valid` is `False`: an
    invalid candidate never reached the policy. Together with the engine's
    plan and audit records, these outcomes make every decision explainable —
    the candidate id, its validation verdict and reasons, and (when valid)
    its full resolution decision all survive here.
    """

    candidate_id: str
    validation: ValidationDecision
    resolution: ResolutionDecision | None


@dataclass(frozen=True)
class EngineResult:
    """The complete, immutable output of one curation run.

    `plan` is `None` when nothing auto-applied. `audit_records` has one entry
    per planned operation, in operation order. `outcomes` has one entry per
    input candidate, in input order.
    """

    outcomes: tuple[CandidateOutcome, ...]
    plan: CurationPlan | None
    audit_records: tuple[AuditRecord, ...]

    @property
    def validation_decisions(self) -> tuple[ValidationDecision, ...]:
        """Every candidate's validation decision, in input order."""
        return tuple(o.validation for o in self.outcomes)

    @property
    def resolution_decisions(self) -> tuple[ResolutionDecision, ...]:
        """Resolution decisions for the candidates that were validated, in order."""
        return tuple(o.resolution for o in self.outcomes if o.resolution is not None)


class CurationEngine:
    """Sequences validator → policy → planner → audit over a candidate batch.

    Every collaborator is injected; the engine adds only orchestration and
    the fail-closed ordering. Reusable and side-effect-free except for the
    optional audit sink.
    """

    def __init__(
        self,
        *,
        validator: CandidateValidator,
        policy: ResolutionPolicy,
        planner: CurationPlanner,
        audit_recorder: AuditRecorder,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self._validator = validator
        self._policy = policy
        self._planner = planner
        self._audit_recorder = audit_recorder
        self._audit_sink = audit_sink

    @classmethod
    def create(
        cls,
        *,
        graph_id: str | None = None,
        confidence_policy: ConfidencePolicy | None = None,
        id_factory: IdFactory | None = None,
        clock: Clock | None = None,
        snapshot_version: str = DEFAULT_SNAPSHOT_VERSION,
        decided_by: str = DEFAULT_DECIDED_BY,
        audit_sink: AuditSink | None = None,
    ) -> "CurationEngine":
        """Wire a coherent engine from one id factory and one clock.

        A single `IdFactory` (deterministic by default) threads through
        resolution, planning, and audit, and one `snapshot_version` /
        `policy_version` pair is shared by every stage — so a plan's snapshot
        and an audit record's policy version can never disagree. Bind
        `graph_id` to reject cross-graph candidates at validation.
        """
        ids = id_factory or DerivedIdFactory()
        policy_model = confidence_policy or ConfidencePolicy()
        policy = ResolutionPolicy(
            confidence_policy=policy_model,
            id_factory=ids,
            snapshot_version=snapshot_version,
        )
        planner = CurationPlanner(
            id_factory=ids,
            snapshot_version=snapshot_version,
            policy_version=policy_model.policy_version,
        )
        audit_recorder = AuditRecorder.create(
            clock=clock,
            id_factory=ids,
            decided_by=decided_by,
            policy_version=policy_model.policy_version,
        )
        return cls(
            validator=default_validator(graph_id=graph_id),
            policy=policy,
            planner=planner,
            audit_recorder=audit_recorder,
            audit_sink=audit_sink,
        )

    def curate(self, candidates: Sequence[Candidate]) -> EngineResult:
        """Run the full pipeline over a batch and return the immutable result.

        Validation gates policy per candidate; the surviving resolved
        candidates are planned as one batch; the plan's operations are
        audited; and, if a sink was injected, the audit records are appended
        to it. The graph is never touched.
        """
        outcomes: list[CandidateOutcome] = []
        resolved: list[ResolvedCandidate] = []
        for candidate in candidates:
            validation = self._validator.validate(candidate)
            if not validation.valid:
                outcomes.append(
                    CandidateOutcome(
                        candidate_id=candidate.candidate_id,
                        validation=validation,
                        resolution=None,
                    )
                )
                continue
            resolution = self._policy.resolve(candidate)
            outcomes.append(
                CandidateOutcome(
                    candidate_id=candidate.candidate_id,
                    validation=validation,
                    resolution=resolution,
                )
            )
            resolved.append(ResolvedCandidate(candidate=candidate, resolution=resolution))

        plan_result: PlanResult = self._planner.plan(resolved)
        audit_records = self._audit_recorder.build(plan_result.planned_operations)
        if self._audit_sink is not None:
            for record in audit_records:
                self._audit_sink.record(record)

        return EngineResult(
            outcomes=tuple(outcomes),
            plan=plan_result.plan,
            audit_records=audit_records,
        )
