"""JSON-ready Golden Demo Session views and a read-only in-process API."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from sentry_atm.api.controller_decision import ControllerDecisionAuditLogReadModel
from sentry_atm.api.exception_queue import ExceptionQueueSnapshotReadModel
from sentry_atm.api.recommendation import ResolutionRecommendationSetReadModel
from sentry_atm.domain import (
    POC_TERMINAL_V1_RULE_PROFILE,
    AircraftState,
    AltitudeManeuver,
    CandidateSafetyValidationResult,
    ConflictEvent,
    ConflictRiskAssessment,
    ConflictStatus,
    ControllerDecisionType,
    EmergencyReturnCandidateBatch,
    EmergencyReturnDecisionAuditLog,
    EmergencyReturnRecommendationSet,
    EmergencyReturnSafetyValidationRun,
    EntryDelayManeuver,
    ExceptionStatus,
    HeadingManeuver,
    OperationalPriorityExceptionItem,
    OperationalPriorityLevel,
    ResolutionCandidate,
    ResolutionManeuver,
    RiskLevel,
    SeparationMinimum,
    SequenceChangeManeuver,
    SpeedManeuver,
)
from sentry_atm.domain.time_policy import to_utc
from sentry_atm.scenario import (
    EmergencyDeclaredPayload,
    EntryConformanceDeviationPayload,
    ScenarioDefinition,
    ScenarioEvent,
    ScenarioEventType,
)
from sentry_atm.simulation import TrafficSnapshot

if TYPE_CHECKING:
    from sentry_atm.runtime.application_orchestrator import (
        GoldenDemoApprovedManeuverOrchestrator,
    )
    from sentry_atm.runtime.emergency_return_application_orchestrator import (
        GoldenDemoEmergencyRecoveryResult,
        GoldenDemoEmergencyReturnApplicationOrchestrator,
        GoldenDemoEmergencyReturnApplicationResult,
    )
    from sentry_atm.runtime.modified_application_orchestrator import (
        GoldenDemoValidatedModifiedManeuverApplicationOrchestrator,
    )
    from sentry_atm.runtime.modified_revalidation_orchestrator import (
        GoldenDemoModifiedManeuverRevalidationOrchestrator,
    )


def _utc_text(value: datetime) -> str:
    return to_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


class GoldenDemoSessionStage(StrEnum):
    """Presentation stages derived only from completed backend evidence."""

    READY = "READY"
    MONITORING = "MONITORING"
    DEVIATION_DETECTED = "DEVIATION_DETECTED"
    CONFLICT_DETECTED = "CONFLICT_DETECTED"
    RECOMMENDATION_AVAILABLE = "RECOMMENDATION_AVAILABLE"
    DECISION_ACCEPTED = "DECISION_ACCEPTED"
    DECISION_MODIFIED = "DECISION_MODIFIED"
    MODIFICATION_REVALIDATED = "MODIFICATION_REVALIDATED"
    DECISION_REJECTED = "DECISION_REJECTED"
    CONFLICT_RESOLVED = "CONFLICT_RESOLVED"
    EMERGENCY_DECLARED = "EMERGENCY_DECLARED"
    EMERGENCY_DECISION_ACCEPTED = "EMERGENCY_DECISION_ACCEPTED"
    EMERGENCY_DECISION_MODIFIED = "EMERGENCY_DECISION_MODIFIED"
    EMERGENCY_DECISION_REJECTED = "EMERGENCY_DECISION_REJECTED"
    EMERGENCY_RETURN_APPLIED = "EMERGENCY_RETURN_APPLIED"
    EMERGENCY_RECOVERED = "EMERGENCY_RECOVERED"


class GoldenDemoSessionCommand(StrEnum):
    """Fixed commands accepted by the deterministic Session boundary."""

    START = "START"
    ADVANCE_TO_CONFLICT = "ADVANCE_TO_CONFLICT"
    GENERATE_RECOMMENDATION = "GENERATE_RECOMMENDATION"
    ACCEPT_RECOMMENDATION = "ACCEPT_RECOMMENDATION"
    MODIFY_RECOMMENDATION = "MODIFY_RECOMMENDATION"
    REVALIDATE_MODIFIED_MANEUVER = "REVALIDATE_MODIFIED_MANEUVER"
    APPLY_VALIDATED_MODIFIED_MANEUVER = "APPLY_VALIDATED_MODIFIED_MANEUVER"
    REJECT_RECOMMENDATION = "REJECT_RECOMMENDATION"
    APPLY_APPROVED_MANEUVER = "APPLY_APPROVED_MANEUVER"
    ADVANCE_TO_EMERGENCY = "ADVANCE_TO_EMERGENCY"
    ACCEPT_EMERGENCY_RETURN = "ACCEPT_EMERGENCY_RETURN"
    MODIFY_EMERGENCY_RETURN = "MODIFY_EMERGENCY_RETURN"
    REJECT_EMERGENCY_RETURN = "REJECT_EMERGENCY_RETURN"
    APPLY_EMERGENCY_RETURN = "APPLY_EMERGENCY_RETURN"
    COMPLETE_EMERGENCY_RECOVERY = "COMPLETE_EMERGENCY_RECOVERY"
    RESET = "RESET"


class GoldenDemoSessionCommandValidationError(ValueError):
    """A well-formed Session request whose decision inputs are invalid."""


@dataclass(frozen=True, slots=True)
class GoldenDemoAircraftReadModel:
    """Metadata-enriched current Aircraft State for map and table views."""

    aircraft_id: str
    aircraft_type: str
    category: str
    source: str
    timestamp_utc: str
    x_nm: float
    y_nm: float
    altitude_ft: float
    ground_speed_kt: float
    heading_deg: float
    vertical_speed_fpm: float
    flight_phase: str
    emergency_status: str
    emergency_type: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "aircraft_id": self.aircraft_id,
            "aircraft_type": self.aircraft_type,
            "category": self.category,
            "source": self.source,
            "timestamp_utc": self.timestamp_utc,
            "x_nm": self.x_nm,
            "y_nm": self.y_nm,
            "altitude_ft": self.altitude_ft,
            "ground_speed_kt": self.ground_speed_kt,
            "heading_deg": self.heading_deg,
            "vertical_speed_fpm": self.vertical_speed_fpm,
            "flight_phase": self.flight_phase,
            "emergency_status": self.emergency_status,
            "emergency_type": self.emergency_type,
        }


@dataclass(frozen=True, slots=True)
class GoldenDemoRevalidationReadModel:
    """Compact post-application evidence for the original Golden Conflict."""

    application_step_id: str
    source_decision_step_id: str
    application_source: str
    source_modified_revalidation_step_id: str | None
    authorization_id: str | None
    authorized_at_utc: str | None
    applied_maneuver_type: str
    applied_aircraft_id: str
    before_altitude_ft: float
    applied_altitude_ft: float
    prediction_run_id: str
    conflict_run_id: str
    conflict_id: str
    aircraft_ids: tuple[str, str]
    conflict_status: str
    risk_level: str
    risk_score: float
    tcpa_seconds: float
    horizontal_separation_nm: float
    vertical_separation_ft: float
    source_exception_status: str
    resolved: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "application_step_id": self.application_step_id,
            "source_decision_step_id": self.source_decision_step_id,
            "application_source": self.application_source,
            "source_modified_revalidation_step_id": (
                self.source_modified_revalidation_step_id
            ),
            "authorization_id": self.authorization_id,
            "authorized_at_utc": self.authorized_at_utc,
            "applied_maneuver_type": self.applied_maneuver_type,
            "applied_aircraft_id": self.applied_aircraft_id,
            "before_altitude_ft": self.before_altitude_ft,
            "applied_altitude_ft": self.applied_altitude_ft,
            "prediction_run_id": self.prediction_run_id,
            "conflict_run_id": self.conflict_run_id,
            "conflict_id": self.conflict_id,
            "aircraft_ids": list(self.aircraft_ids),
            "conflict_status": self.conflict_status,
            "risk_level": self.risk_level,
            "risk_score": self.risk_score,
            "tcpa_seconds": self.tcpa_seconds,
            "horizontal_separation_nm": self.horizontal_separation_nm,
            "vertical_separation_ft": self.vertical_separation_ft,
            "source_exception_status": self.source_exception_status,
            "resolved": self.resolved,
        }


@dataclass(frozen=True, slots=True)
class GoldenDemoConflictEvidenceReadModel:
    """Explainable baseline evidence for the Golden Demo's primary conflict."""

    conflict_id: str
    aircraft_ids: tuple[str, str]
    status: str
    evaluated_at_utc: str
    closest_approach_time_utc: str
    tcpa_seconds: float
    horizontal_separation_nm: float
    vertical_separation_ft: float
    horizontal_threshold_nm: float
    vertical_threshold_ft: float
    horizontal_separation_ratio: float
    vertical_separation_ratio: float
    risk_score: float
    risk_level: str
    risk_reason_codes: tuple[str, ...]
    rule_profile_id: str
    risk_policy_profile_id: str

    def to_dict(self) -> dict[str, object]:
        return {
            "conflict_id": self.conflict_id,
            "aircraft_ids": list(self.aircraft_ids),
            "status": self.status,
            "evaluated_at_utc": self.evaluated_at_utc,
            "closest_approach_time_utc": self.closest_approach_time_utc,
            "tcpa_seconds": self.tcpa_seconds,
            "horizontal_separation_nm": self.horizontal_separation_nm,
            "vertical_separation_ft": self.vertical_separation_ft,
            "horizontal_threshold_nm": self.horizontal_threshold_nm,
            "vertical_threshold_ft": self.vertical_threshold_ft,
            "horizontal_separation_ratio": self.horizontal_separation_ratio,
            "vertical_separation_ratio": self.vertical_separation_ratio,
            "risk_score": self.risk_score,
            "risk_level": self.risk_level,
            "risk_reason_codes": list(self.risk_reason_codes),
            "rule_profile_id": self.rule_profile_id,
            "risk_policy_profile_id": self.risk_policy_profile_id,
        }


@dataclass(frozen=True, slots=True)
class GoldenDemoDeviationReadModel:
    """Expected-versus-actual evidence for the emitted entry deviation."""

    event_id: str
    detected_at_utc: str
    aircraft_id: str
    expected_entry_point: str
    expected_altitude_ft: float
    actual_altitude_ft: float
    vertical_deviation_ft: float
    expected_heading_deg: float
    actual_heading_deg: float
    heading_deviation_deg: float
    lateral_deviation_nm: float
    time_deviation_seconds: float

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "detected_at_utc": self.detected_at_utc,
            "aircraft_id": self.aircraft_id,
            "expected_entry_point": self.expected_entry_point,
            "expected_altitude_ft": self.expected_altitude_ft,
            "actual_altitude_ft": self.actual_altitude_ft,
            "vertical_deviation_ft": self.vertical_deviation_ft,
            "expected_heading_deg": self.expected_heading_deg,
            "actual_heading_deg": self.actual_heading_deg,
            "heading_deviation_deg": self.heading_deviation_deg,
            "lateral_deviation_nm": self.lateral_deviation_nm,
            "time_deviation_seconds": self.time_deviation_seconds,
        }


@dataclass(frozen=True, slots=True)
class GoldenDemoEmergencyReadModel:
    """Declared emergency joined with its independent operational priority evidence."""

    event_id: str
    declared_at_utc: str
    aircraft_id: str
    emergency_type: str
    reason_category: str
    priority_assessment_id: str
    priority_level: str
    priority_score: float
    reason_codes: tuple[str, ...]
    source_event_ids: tuple[str, ...]
    queue_exception_id: str | None
    queue_rank: int | None

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "declared_at_utc": self.declared_at_utc,
            "aircraft_id": self.aircraft_id,
            "emergency_type": self.emergency_type,
            "reason_category": self.reason_category,
            "priority_assessment_id": self.priority_assessment_id,
            "priority_level": self.priority_level,
            "priority_score": self.priority_score,
            "reason_codes": list(self.reason_codes),
            "source_event_ids": list(self.source_event_ids),
            "queue_exception_id": self.queue_exception_id,
            "queue_rank": self.queue_rank,
        }


@dataclass(frozen=True, slots=True)
class GoldenDemoEmergencyReturnActionReadModel:
    """JSON-ready action in one unvalidated emergency-return plan."""

    aircraft_id: str
    maneuver_type: str
    target_ground_speed_kt: float | None
    delay_seconds: float | None
    target_sequence_position: int | None

    def to_dict(self) -> dict[str, object]:
        return {
            "aircraft_id": self.aircraft_id,
            "maneuver_type": self.maneuver_type,
            "target_ground_speed_kt": self.target_ground_speed_kt,
            "delay_seconds": self.delay_seconds,
            "target_sequence_position": self.target_sequence_position,
        }


@dataclass(frozen=True, slots=True)
class GoldenDemoEmergencyReturnCandidateReadModel:
    """One coordinated alternative joined with isolated validation evidence."""

    candidate_id: str
    strategy: str
    arrival_sequence: tuple[str, ...]
    actions: tuple[GoldenDemoEmergencyReturnActionReadModel, ...]
    preserves_stabilized_arrival: bool
    estimated_delay_seconds: float
    operational_cost_score: float
    baseline: bool
    verdict: str
    predicted_conflict_aircraft_ids: tuple[tuple[str, str], ...]
    new_conflict_aircraft_ids: tuple[tuple[str, str], ...]
    performance_feasible: bool
    emergency_sequence_position: int
    priority_target_achieved: bool
    stabilized_arrival_preserved: bool
    reason_codes: tuple[str, ...]
    recommendation_rank: int | None
    recommendation_explanation: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "strategy": self.strategy,
            "arrival_sequence": list(self.arrival_sequence),
            "actions": [item.to_dict() for item in self.actions],
            "preserves_stabilized_arrival": self.preserves_stabilized_arrival,
            "estimated_delay_seconds": self.estimated_delay_seconds,
            "operational_cost_score": self.operational_cost_score,
            "baseline": self.baseline,
            "validation_status": self.verdict,
            "verdict": self.verdict,
            "predicted_conflict_aircraft_ids": [
                list(item) for item in self.predicted_conflict_aircraft_ids
            ],
            "new_conflict_aircraft_ids": [
                list(item) for item in self.new_conflict_aircraft_ids
            ],
            "performance_feasible": self.performance_feasible,
            "emergency_sequence_position": self.emergency_sequence_position,
            "priority_target_achieved": self.priority_target_achieved,
            "stabilized_arrival_preserved": self.stabilized_arrival_preserved,
            "reason_codes": list(self.reason_codes),
            "recommended": self.recommendation_rank is not None,
            "recommendation_rank": self.recommendation_rank,
            "recommendation_explanation": self.recommendation_explanation,
        }


@dataclass(frozen=True, slots=True)
class GoldenDemoEmergencyReturnBatchReadModel:
    """Presentation projection of deterministic emergency-return alternatives."""

    candidate_batch_id: str
    source_exception_id: str
    source_priority_assessment_id: str
    emergency_aircraft_id: str
    generated_at_utc: str
    generator_profile_id: str
    validation_run_id: str
    validation_profile_id: str
    validation_horizon_seconds: float
    baseline_conflict_aircraft_ids: tuple[tuple[str, str], ...]
    recommendation_set_id: str
    ranking_policy_id: str
    recommendation_availability: str
    primary_recommendation_candidate_id: str | None
    candidates: tuple[GoldenDemoEmergencyReturnCandidateReadModel, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_batch_id": self.candidate_batch_id,
            "source_exception_id": self.source_exception_id,
            "source_priority_assessment_id": self.source_priority_assessment_id,
            "emergency_aircraft_id": self.emergency_aircraft_id,
            "generated_at_utc": self.generated_at_utc,
            "generator_profile_id": self.generator_profile_id,
            "validation_run_id": self.validation_run_id,
            "validation_profile_id": self.validation_profile_id,
            "validation_horizon_seconds": self.validation_horizon_seconds,
            "baseline_conflict_aircraft_ids": [
                list(item) for item in self.baseline_conflict_aircraft_ids
            ],
            "recommendation_set_id": self.recommendation_set_id,
            "ranking_policy_id": self.ranking_policy_id,
            "recommendation_availability": self.recommendation_availability,
            "primary_recommendation_candidate_id": (
                self.primary_recommendation_candidate_id
            ),
            "candidate_count": len(self.candidates),
            "candidates": [item.to_dict() for item in self.candidates],
        }


@dataclass(frozen=True, slots=True)
class GoldenDemoEmergencyReturnDecisionReadModel:
    """JSON-ready audit evidence for one Emergency Return controller decision."""

    audit_log_id: str
    revision: int
    decision_id: str
    recommendation_set_id: str
    source_recommendation_id: str
    source_candidate_id: str
    selected_recommendation_id: str | None
    selected_candidate_id: str | None
    decision_type: str
    decided_at_utc: str
    controller_position_id: str
    rationale: str | None
    authorizes_application: bool
    requires_revalidation: bool
    applied: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "audit_log_id": self.audit_log_id,
            "revision": self.revision,
            "decision_id": self.decision_id,
            "recommendation_set_id": self.recommendation_set_id,
            "source_recommendation_id": self.source_recommendation_id,
            "source_candidate_id": self.source_candidate_id,
            "selected_recommendation_id": self.selected_recommendation_id,
            "selected_candidate_id": self.selected_candidate_id,
            "decision_type": self.decision_type,
            "decided_at_utc": self.decided_at_utc,
            "controller_position_id": self.controller_position_id,
            "rationale": self.rationale,
            "authorizes_application": self.authorizes_application,
            "requires_revalidation": self.requires_revalidation,
            "applied": self.applied,
        }


@dataclass(frozen=True, slots=True)
class GoldenDemoEmergencyReturnApplicationReadModel:
    """Applied emergency plan and optional T+260 recovery evidence."""

    application_id: str
    source_decision_id: str
    applied_at_utc: str
    emergency_aircraft_id: str
    selected_candidate_id: str
    decision_type: str
    validation_verdict: str
    actions: tuple[GoldenDemoEmergencyReturnActionReadModel, ...]
    recovery_id: str | None
    completed_at_utc: str | None
    emergency_status_after: str
    flight_phase_after: str
    emergency_exception_status: str | None
    remaining_high_critical_pairs: tuple[tuple[str, str], ...]
    recovery_complete: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "application_id": self.application_id,
            "source_decision_id": self.source_decision_id,
            "applied_at_utc": self.applied_at_utc,
            "emergency_aircraft_id": self.emergency_aircraft_id,
            "selected_candidate_id": self.selected_candidate_id,
            "decision_type": self.decision_type,
            "validation_verdict": self.validation_verdict,
            "actions": [item.to_dict() for item in self.actions],
            "recovery_id": self.recovery_id,
            "completed_at_utc": self.completed_at_utc,
            "emergency_status_after": self.emergency_status_after,
            "flight_phase_after": self.flight_phase_after,
            "emergency_exception_status": self.emergency_exception_status,
            "remaining_high_critical_pairs": [
                list(item) for item in self.remaining_high_critical_pairs
            ],
            "recovery_complete": self.recovery_complete,
        }


@dataclass(frozen=True, slots=True)
class GoldenDemoCandidateComparisonReadModel:
    """One candidate joined with its isolated Safety Validation evidence."""

    candidate_id: str
    target_aircraft_id: str | None
    maneuver_type: str
    target_heading_deg: float | None
    target_altitude_ft: float | None
    target_ground_speed_kt: float | None
    delay_seconds: float | None
    target_sequence_position: int | None
    operational_cost_score: float
    verdict: str
    primary_conflict_status: str
    primary_horizontal_separation_nm: float
    primary_vertical_separation_ft: float
    secondary_conflict_aircraft_ids: tuple[tuple[str, str], ...]
    performance_feasible: bool
    rule_violation_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]
    validation_profile_id: str
    recommended: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "target_aircraft_id": self.target_aircraft_id,
            "maneuver_type": self.maneuver_type,
            "target_heading_deg": self.target_heading_deg,
            "target_altitude_ft": self.target_altitude_ft,
            "target_ground_speed_kt": self.target_ground_speed_kt,
            "delay_seconds": self.delay_seconds,
            "target_sequence_position": self.target_sequence_position,
            "operational_cost_score": self.operational_cost_score,
            "verdict": self.verdict,
            "primary_conflict_status": self.primary_conflict_status,
            "primary_horizontal_separation_nm": self.primary_horizontal_separation_nm,
            "primary_vertical_separation_ft": self.primary_vertical_separation_ft,
            "secondary_conflict_aircraft_ids": [
                list(aircraft_ids) for aircraft_ids in self.secondary_conflict_aircraft_ids
            ],
            "performance_feasible": self.performance_feasible,
            "rule_violation_ids": list(self.rule_violation_ids),
            "reason_codes": list(self.reason_codes),
            "validation_profile_id": self.validation_profile_id,
            "recommended": self.recommended,
        }


@dataclass(frozen=True, slots=True)
class GoldenDemoModifiedRevalidationReadModel:
    """JSON-ready isolated validation evidence for a modified Maneuver."""

    revalidation_step_id: str
    source_decision_step_id: str
    candidate_id: str
    evaluated_at_utc: str
    validation_run_id: str
    verdict: str
    primary_conflict_status: str
    primary_horizontal_separation_nm: float
    primary_vertical_separation_ft: float
    tcpa_seconds: float
    secondary_conflict_aircraft_ids: tuple[tuple[str, str], ...]
    performance_feasible: bool
    rule_violation_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]
    validation_profile_id: str
    safe_to_apply: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "revalidation_step_id": self.revalidation_step_id,
            "source_decision_step_id": self.source_decision_step_id,
            "candidate_id": self.candidate_id,
            "evaluated_at_utc": self.evaluated_at_utc,
            "validation_run_id": self.validation_run_id,
            "verdict": self.verdict,
            "primary_conflict_status": self.primary_conflict_status,
            "primary_horizontal_separation_nm": self.primary_horizontal_separation_nm,
            "primary_vertical_separation_ft": self.primary_vertical_separation_ft,
            "tcpa_seconds": self.tcpa_seconds,
            "secondary_conflict_aircraft_ids": [
                list(aircraft_ids) for aircraft_ids in self.secondary_conflict_aircraft_ids
            ],
            "performance_feasible": self.performance_feasible,
            "rule_violation_ids": list(self.rule_violation_ids),
            "reason_codes": list(self.reason_codes),
            "validation_profile_id": self.validation_profile_id,
            "safe_to_apply": self.safe_to_apply,
        }


@dataclass(frozen=True, slots=True)
class GoldenDemoSessionReadModel:
    """One complete JSON-compatible view of current Golden Demo backend state."""

    session_id: str
    scenario_id: str
    run_number: int
    stage: GoldenDemoSessionStage
    clock_state: str
    simulation_time_utc: str
    elapsed_seconds: float
    traffic: tuple[GoldenDemoAircraftReadModel, ...]
    active_exception_count: int
    step_id: str | None
    resolution_step_id: str | None
    decision_step_id: str | None
    application_step_id: str | None
    primary_conflict: GoldenDemoConflictEvidenceReadModel | None
    deviation: GoldenDemoDeviationReadModel | None
    emergency: GoldenDemoEmergencyReadModel | None
    emergency_return_candidates: GoldenDemoEmergencyReturnBatchReadModel | None
    emergency_return_decision: GoldenDemoEmergencyReturnDecisionReadModel | None
    emergency_return_application: GoldenDemoEmergencyReturnApplicationReadModel | None
    candidate_comparisons: tuple[GoldenDemoCandidateComparisonReadModel, ...]
    exception_queue: ExceptionQueueSnapshotReadModel | None
    recommendation: ResolutionRecommendationSetReadModel | None
    controller_decision: ControllerDecisionAuditLogReadModel | None
    modified_revalidation: GoldenDemoModifiedRevalidationReadModel | None
    revalidation: GoldenDemoRevalidationReadModel | None

    def to_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "scenario_id": self.scenario_id,
            "run_number": self.run_number,
            "stage": self.stage.value,
            "clock_state": self.clock_state,
            "simulation_time_utc": self.simulation_time_utc,
            "elapsed_seconds": self.elapsed_seconds,
            "traffic_count": len(self.traffic),
            "active_exception_count": self.active_exception_count,
            "step_id": self.step_id,
            "resolution_step_id": self.resolution_step_id,
            "decision_step_id": self.decision_step_id,
            "application_step_id": self.application_step_id,
            "primary_conflict": (
                self.primary_conflict.to_dict() if self.primary_conflict is not None else None
            ),
            "deviation": self.deviation.to_dict() if self.deviation is not None else None,
            "emergency": self.emergency.to_dict() if self.emergency is not None else None,
            "emergency_return_candidates": (
                self.emergency_return_candidates.to_dict()
                if self.emergency_return_candidates is not None
                else None
            ),
            "emergency_return_decision": (
                self.emergency_return_decision.to_dict()
                if self.emergency_return_decision is not None
                else None
            ),
            "emergency_return_application": (
                self.emergency_return_application.to_dict()
                if self.emergency_return_application is not None
                else None
            ),
            "candidate_comparisons": [
                item.to_dict() for item in self.candidate_comparisons
            ],
            "traffic": [item.to_dict() for item in self.traffic],
            "exception_queue": (
                self.exception_queue.to_dict() if self.exception_queue is not None else None
            ),
            "recommendation": (
                self.recommendation.to_dict() if self.recommendation is not None else None
            ),
            "controller_decision": (
                self.controller_decision.to_dict() if self.controller_decision is not None else None
            ),
            "modified_revalidation": (
                self.modified_revalidation.to_dict()
                if self.modified_revalidation is not None
                else None
            ),
            "revalidation": (
                self.revalidation.to_dict() if self.revalidation is not None else None
            ),
        }


@runtime_checkable
class GoldenDemoSessionApiContract(Protocol):
    """Synchronous read-only Session API for presentation adapters."""

    def get_current(self) -> GoldenDemoSessionReadModel: ...


@runtime_checkable
class GoldenDemoSessionCommandApiContract(Protocol):
    """Synchronous command API consumed by transport adapters."""

    @property
    def read_api(self) -> GoldenDemoSessionApiContract: ...

    def execute(
        self,
        command: GoldenDemoSessionCommand,
        *,
        rationale: str | None = None,
        modified_maneuver: ResolutionManeuver | None = None,
        modified_emergency_candidate_id: str | None = None,
    ) -> GoldenDemoSessionReadModel: ...


class InProcessGoldenDemoSessionApi:
    """Project the current Orchestrator chain without owning its lifecycle."""

    __slots__ = (
        "_application_orchestrator",
        "_emergency_return_application_orchestrator",
        "_modified_application_orchestrator",
        "_modified_revalidation_orchestrator",
    )

    def __init__(
        self,
        application_orchestrator: "GoldenDemoApprovedManeuverOrchestrator",
        modified_revalidation_orchestrator: (
            "GoldenDemoModifiedManeuverRevalidationOrchestrator"
        ),
        modified_application_orchestrator: (
            "GoldenDemoValidatedModifiedManeuverApplicationOrchestrator"
        ),
        emergency_return_application_orchestrator: (
            "GoldenDemoEmergencyReturnApplicationOrchestrator"
        ),
    ) -> None:
        from sentry_atm.runtime.application_orchestrator import (
            GoldenDemoApprovedManeuverOrchestrator,
        )
        from sentry_atm.runtime.emergency_return_application_orchestrator import (
            GoldenDemoEmergencyReturnApplicationOrchestrator,
        )
        from sentry_atm.runtime.modified_application_orchestrator import (
            GoldenDemoValidatedModifiedManeuverApplicationOrchestrator,
        )
        from sentry_atm.runtime.modified_revalidation_orchestrator import (
            GoldenDemoModifiedManeuverRevalidationOrchestrator,
        )

        if not isinstance(application_orchestrator, GoldenDemoApprovedManeuverOrchestrator):
            raise TypeError(
                "application_orchestrator must be a GoldenDemoApprovedManeuverOrchestrator"
            )
        self._application_orchestrator = application_orchestrator
        if not isinstance(
            modified_revalidation_orchestrator,
            GoldenDemoModifiedManeuverRevalidationOrchestrator,
        ):
            raise TypeError(
                "modified_revalidation_orchestrator must be a "
                "GoldenDemoModifiedManeuverRevalidationOrchestrator"
            )
        if (
            modified_revalidation_orchestrator.decision_orchestrator
            is not application_orchestrator.decision_orchestrator
        ):
            raise ValueError("Session Orchestrators must share one Controller Decision source")
        self._modified_revalidation_orchestrator = modified_revalidation_orchestrator
        if not isinstance(
            modified_application_orchestrator,
            GoldenDemoValidatedModifiedManeuverApplicationOrchestrator,
        ):
            raise TypeError(
                "modified_application_orchestrator must be a "
                "GoldenDemoValidatedModifiedManeuverApplicationOrchestrator"
            )
        if (
            modified_application_orchestrator.modified_revalidation_orchestrator
            is not modified_revalidation_orchestrator
        ):
            raise ValueError(
                "Session Orchestrators must share one Modified Revalidation source"
            )
        self._modified_application_orchestrator = modified_application_orchestrator
        if not isinstance(
            emergency_return_application_orchestrator,
            GoldenDemoEmergencyReturnApplicationOrchestrator,
        ):
            raise TypeError(
                "emergency_return_application_orchestrator must be a "
                "GoldenDemoEmergencyReturnApplicationOrchestrator"
            )
        if (
            emergency_return_application_orchestrator.step_orchestrator
            is not application_orchestrator.decision_orchestrator
            .resolution_orchestrator.step_orchestrator
        ):
            raise ValueError("Session Orchestrators must share one Step source")
        self._emergency_return_application_orchestrator = (
            emergency_return_application_orchestrator
        )

    @property
    def application_orchestrator(self) -> "GoldenDemoApprovedManeuverOrchestrator":
        return self._application_orchestrator

    @property
    def modified_revalidation_orchestrator(
        self,
    ) -> "GoldenDemoModifiedManeuverRevalidationOrchestrator":
        return self._modified_revalidation_orchestrator

    @property
    def modified_application_orchestrator(
        self,
    ) -> "GoldenDemoValidatedModifiedManeuverApplicationOrchestrator":
        return self._modified_application_orchestrator

    @property
    def emergency_return_application_orchestrator(
        self,
    ) -> "GoldenDemoEmergencyReturnApplicationOrchestrator":
        return self._emergency_return_application_orchestrator

    def get_current(self) -> GoldenDemoSessionReadModel:
        application = self._application_orchestrator
        approved_application_result = application.last_result
        modified_revalidation_result = self._modified_revalidation_orchestrator.last_result
        modified_application_result = self._modified_application_orchestrator.last_result
        application_result = modified_application_result or approved_application_result
        emergency_application_result = (
            self._emergency_return_application_orchestrator.last_application_result
        )
        emergency_recovery_result = (
            self._emergency_return_application_orchestrator.last_recovery_result
        )
        decision = application.decision_orchestrator
        decision_result = decision.last_result
        resolution = decision.resolution_orchestrator
        resolution_result = resolution.last_result
        steps = resolution.step_orchestrator
        step_result = steps.last_result
        runtime = steps.runtime
        clock = runtime.simulation.clock

        traffic_snapshot = _latest_traffic_snapshot(
            application_result=application_result,
            step_result=step_result,
            fallback=runtime.simulation.engine.snapshot(),
        )
        if emergency_application_result is not None:
            traffic_snapshot = emergency_application_result.application_snapshot
        if emergency_recovery_result is not None:
            traffic_snapshot = emergency_recovery_result.recovery_step_result.traffic_snapshot
        queue = runtime.exception_queue_api.get_current(include_resolved=True)
        emergency_source_step = (
            emergency_application_result.source_step_result
            if emergency_application_result is not None
            else step_result
        )
        emergency_return_batch = _build_emergency_return_candidates(
            step_result=emergency_source_step,
            runtime=runtime,
        )
        emergency_return_validation = (
            runtime.emergency_return_safety_validator.validate(
                emergency_return_batch,
                emergency_source_step.traffic_snapshot.states,
                _performance_by_aircraft(
                    step_result=emergency_source_step,
                    runtime=runtime,
                ),
            )
            if emergency_return_batch is not None
            else None
        )
        emergency_return_recommendations = (
            runtime.emergency_return_recommendation_service.recommend(
                emergency_return_batch,
                emergency_return_validation,
                generated_at_utc=emergency_return_validation.evaluated_at_utc,
            )
            if emergency_return_batch is not None
            and emergency_return_validation is not None
            else None
        )
        recommendation = runtime.recommendation_api.get_current()
        controller_decision = runtime.controller_decision_api.get_current()
        emergency_return_decision = _map_emergency_return_decision(
            runtime.emergency_return_decision_service.last_audit_log,
            applied=emergency_application_result is not None,
        )
        return GoldenDemoSessionReadModel(
            session_id=f"{runtime.definition.scenario_id}-RUN-{clock.reset_count:06d}",
            scenario_id=runtime.definition.scenario_id,
            run_number=clock.reset_count,
            stage=_stage(
                step_result=step_result,
                resolution_result=resolution_result,
                decision_result=decision_result,
                modified_revalidation_result=modified_revalidation_result,
                application_result=application_result,
                emergency_return_decision=emergency_return_decision,
                emergency_application_result=emergency_application_result,
                emergency_recovery_result=emergency_recovery_result,
            ),
            clock_state=clock.state.value,
            simulation_time_utc=_utc_text(clock.current_time_utc),
            elapsed_seconds=clock.elapsed_seconds,
            traffic=_map_traffic(traffic_snapshot, runtime.definition),
            active_exception_count=queue.active_count if queue is not None else 0,
            step_id=step_result.step_id if step_result is not None else None,
            resolution_step_id=(
                resolution_result.resolution_step_id if resolution_result is not None else None
            ),
            decision_step_id=(
                decision_result.decision_step_id if decision_result is not None else None
            ),
            application_step_id=(
                application_result.application_step_id if application_result is not None else None
            ),
            primary_conflict=_map_primary_conflict(
                step_result=step_result,
                resolution_result=resolution_result,
            ),
            deviation=_map_deviation(
                step_result=step_result,
                scenario_events=runtime.simulation.timeline.events,
            ),
            emergency=_map_emergency(
                step_result=emergency_source_step,
                scenario_events=runtime.simulation.timeline.events,
                queue=queue,
            ),
            emergency_return_candidates=(
                _map_emergency_return_batch(
                    emergency_return_batch,
                    emergency_return_validation,
                    emergency_return_recommendations,
                )
                if emergency_return_batch is not None
                and emergency_return_validation is not None
                and emergency_return_recommendations is not None
                else None
            ),
            emergency_return_decision=emergency_return_decision,
            emergency_return_application=(
                _map_emergency_return_application(
                    emergency_application_result,
                    emergency_recovery_result,
                )
                if emergency_application_result is not None
                else None
            ),
            candidate_comparisons=_map_candidate_comparisons(resolution_result),
            exception_queue=queue,
            recommendation=recommendation,
            controller_decision=controller_decision,
            modified_revalidation=(
                _map_modified_revalidation(modified_revalidation_result)
                if modified_revalidation_result is not None
                else None
            ),
            revalidation=(
                _map_revalidation(application_result) if application_result is not None else None
            ),
        )

    def get_emergency_return_recommendation_set(
        self,
    ) -> EmergencyReturnRecommendationSet | None:
        """Rebuild the current deterministic T+240 Set for a command boundary."""

        decision = self._application_orchestrator.decision_orchestrator
        steps = decision.resolution_orchestrator.step_orchestrator
        step_result = steps.last_result
        runtime = steps.runtime
        batch = _build_emergency_return_candidates(
            step_result=step_result,
            runtime=runtime,
        )
        if batch is None or step_result is None:
            return None
        validation = runtime.emergency_return_safety_validator.validate(
            batch,
            step_result.traffic_snapshot.states,
            _performance_by_aircraft(step_result=step_result, runtime=runtime),
        )
        return runtime.emergency_return_recommendation_service.recommend(
            batch,
            validation,
            generated_at_utc=validation.evaluated_at_utc,
        )


def _stage(
    *,
    step_result,
    resolution_result,
    decision_result,
    modified_revalidation_result,
    application_result,
    emergency_return_decision=None,
    emergency_application_result=None,
    emergency_recovery_result=None,
) -> GoldenDemoSessionStage:
    if emergency_recovery_result is not None:
        return GoldenDemoSessionStage.EMERGENCY_RECOVERED
    if emergency_application_result is not None:
        return GoldenDemoSessionStage.EMERGENCY_RETURN_APPLIED
    if emergency_return_decision is not None:
        if emergency_return_decision.decision_type == ControllerDecisionType.MODIFY.value:
            return GoldenDemoSessionStage.EMERGENCY_DECISION_MODIFIED
        if emergency_return_decision.decision_type == ControllerDecisionType.REJECT.value:
            return GoldenDemoSessionStage.EMERGENCY_DECISION_REJECTED
        return GoldenDemoSessionStage.EMERGENCY_DECISION_ACCEPTED
    if step_result is not None and any(
        item.priority_level is OperationalPriorityLevel.EMERGENCY
        for item in step_result.priority_assessments
    ):
        return GoldenDemoSessionStage.EMERGENCY_DECLARED
    if application_result is not None:
        return GoldenDemoSessionStage.CONFLICT_RESOLVED
    if modified_revalidation_result is not None:
        return GoldenDemoSessionStage.MODIFICATION_REVALIDATED
    if decision_result is not None:
        decision_type = decision_result.decision_entry.decision_type
        if decision_type is ControllerDecisionType.MODIFY:
            return GoldenDemoSessionStage.DECISION_MODIFIED
        if decision_type is ControllerDecisionType.REJECT:
            return GoldenDemoSessionStage.DECISION_REJECTED
        return GoldenDemoSessionStage.DECISION_ACCEPTED
    if resolution_result is not None:
        return GoldenDemoSessionStage.RECOMMENDATION_AVAILABLE
    if step_result is None:
        return GoldenDemoSessionStage.READY
    if any(
        item.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
        for item in step_result.risk_assessments
    ):
        return GoldenDemoSessionStage.CONFLICT_DETECTED
    if any(
        item.priority_level is not OperationalPriorityLevel.ROUTINE
        for item in step_result.priority_assessments
    ):
        return GoldenDemoSessionStage.DEVIATION_DETECTED
    return GoldenDemoSessionStage.MONITORING


def _latest_traffic_snapshot(*, application_result, step_result, fallback):
    if application_result is None:
        return step_result.traffic_snapshot if step_result is not None else fallback
    if step_result is None:
        return application_result.traffic_snapshot
    if (
        step_result.traffic_snapshot.timestamp_utc
        > application_result.traffic_snapshot.timestamp_utc
    ):
        return step_result.traffic_snapshot
    return application_result.traffic_snapshot


def _map_traffic(
    snapshot: TrafficSnapshot,
    definition: ScenarioDefinition,
) -> tuple[GoldenDemoAircraftReadModel, ...]:
    metadata_by_id = {item.aircraft_id: item.metadata for item in definition.aircraft}
    return tuple(
        _map_aircraft(state, metadata_by_id[state.aircraft_id]) for state in snapshot.states
    )


def _map_aircraft(state: AircraftState, metadata) -> GoldenDemoAircraftReadModel:
    return GoldenDemoAircraftReadModel(
        aircraft_id=state.aircraft_id,
        aircraft_type=metadata.aircraft_type,
        category=metadata.category.value,
        source=state.source.value,
        timestamp_utc=_utc_text(state.timestamp_utc),
        x_nm=state.x_nm,
        y_nm=state.y_nm,
        altitude_ft=state.altitude_ft,
        ground_speed_kt=state.ground_speed_kt,
        heading_deg=state.heading_deg,
        vertical_speed_fpm=state.vertical_speed_fpm,
        flight_phase=state.flight_phase.value,
        emergency_status=state.emergency_status.value,
        emergency_type=(state.emergency_type.value if state.emergency_type is not None else None),
    )


def _map_deviation(
    *,
    step_result,
    scenario_events: tuple[ScenarioEvent, ...],
) -> GoldenDemoDeviationReadModel | None:
    if step_result is None:
        return None
    event = next(
        (
            item
            for item in scenario_events
            if item.event_type is ScenarioEventType.ENTRY_CONFORMANCE_DEVIATION
            and item.scheduled_time_utc <= step_result.timestamp_utc
        ),
        None,
    )
    if event is None or not isinstance(event.payload, EntryConformanceDeviationPayload):
        return None
    state = next(
        item
        for item in step_result.traffic_snapshot.states
        if item.aircraft_id == event.target_aircraft_id
    )
    payload = event.payload
    return GoldenDemoDeviationReadModel(
        event_id=event.event_id,
        detected_at_utc=_utc_text(event.scheduled_time_utc),
        aircraft_id=event.target_aircraft_id,
        expected_entry_point=payload.expected_entry_point,
        expected_altitude_ft=payload.expected_altitude_ft,
        actual_altitude_ft=payload.actual_altitude_ft,
        vertical_deviation_ft=payload.actual_altitude_ft - payload.expected_altitude_ft,
        expected_heading_deg=payload.expected_heading_deg,
        actual_heading_deg=state.heading_deg,
        heading_deviation_deg=_signed_heading_delta(
            state.heading_deg,
            payload.expected_heading_deg,
        ),
        lateral_deviation_nm=payload.lateral_deviation_nm,
        time_deviation_seconds=payload.time_deviation_seconds,
    )


def _map_emergency(
    *,
    step_result,
    scenario_events: tuple[ScenarioEvent, ...],
    queue: ExceptionQueueSnapshotReadModel | None,
) -> GoldenDemoEmergencyReadModel | None:
    if step_result is None:
        return None
    event = next(
        (
            item
            for item in scenario_events
            if item.event_type is ScenarioEventType.EMERGENCY_DECLARED
            and item.scheduled_time_utc <= step_result.timestamp_utc
        ),
        None,
    )
    if event is None or not isinstance(event.payload, EmergencyDeclaredPayload):
        return None
    assessment = next(
        (
            item
            for item in step_result.priority_assessments
            if item.aircraft_id == event.target_aircraft_id
            and item.priority_level is OperationalPriorityLevel.EMERGENCY
        ),
        None,
    )
    if assessment is None:
        return None
    queue_item = None
    queue_rank = None
    if queue is not None:
        for rank, item in enumerate(queue.items, start=1):
            if item.subject_aircraft_ids == (event.target_aircraft_id,):
                queue_item = item
                queue_rank = rank
                break
    payload = event.payload
    return GoldenDemoEmergencyReadModel(
        event_id=event.event_id,
        declared_at_utc=_utc_text(event.scheduled_time_utc),
        aircraft_id=event.target_aircraft_id,
        emergency_type=payload.emergency_type.value,
        reason_category=payload.reason_category.value,
        priority_assessment_id=assessment.priority_assessment_id,
        priority_level=assessment.priority_level.value,
        priority_score=assessment.priority_score,
        reason_codes=tuple(item.value for item in assessment.reason_codes),
        source_event_ids=assessment.source_event_ids,
        queue_exception_id=queue_item.exception_id if queue_item is not None else None,
        queue_rank=queue_rank,
    )


def _build_emergency_return_candidates(*, step_result, runtime):
    if step_result is None:
        return None
    exception = next(
        (
            item
            for item in step_result.exception_queue_snapshot.items
            if isinstance(item, OperationalPriorityExceptionItem)
            and item.assessment.priority_level is OperationalPriorityLevel.EMERGENCY
        ),
        None,
    )
    if exception is None:
        return None
    return runtime.emergency_return_candidate_generator.generate(
        exception,
        step_result.traffic_snapshot.states,
        _performance_by_aircraft(step_result=step_result, runtime=runtime),
    )


def _performance_by_aircraft(*, step_result, runtime):
    profile_by_id = {item.profile_id: item for item in runtime.performance_profiles}
    metadata_by_id = {
        item.aircraft_id: item.metadata for item in runtime.definition.aircraft
    }
    return {
        state.aircraft_id: profile_by_id[
            metadata_by_id[state.aircraft_id].performance_class
        ]
        for state in step_result.traffic_snapshot.states
    }


def _map_emergency_return_batch(
    batch: EmergencyReturnCandidateBatch,
    validation: EmergencyReturnSafetyValidationRun,
    recommendations: EmergencyReturnRecommendationSet,
) -> GoldenDemoEmergencyReturnBatchReadModel:
    validation_by_candidate = {
        item.candidate_id: item for item in validation.results
    }
    recommendation_by_candidate = {
        item.candidate_id: item for item in recommendations.recommendations
    }
    return GoldenDemoEmergencyReturnBatchReadModel(
        candidate_batch_id=batch.candidate_batch_id,
        source_exception_id=batch.source_exception_id,
        source_priority_assessment_id=batch.source_priority_assessment_id,
        emergency_aircraft_id=batch.emergency_aircraft_id,
        generated_at_utc=_utc_text(batch.generated_at_utc),
        generator_profile_id=batch.generator_profile_id,
        validation_run_id=validation.validation_run_id,
        validation_profile_id=validation.validation_profile_id,
        validation_horizon_seconds=validation.horizon_seconds,
        baseline_conflict_aircraft_ids=tuple(
            item.pair.aircraft_ids for item in validation.baseline_conflicts
        ),
        recommendation_set_id=recommendations.recommendation_set_id,
        ranking_policy_id=recommendations.ranking_policy_id,
        recommendation_availability=recommendations.availability.value,
        primary_recommendation_candidate_id=(
            recommendations.primary_recommendation.candidate_id
            if recommendations.primary_recommendation is not None
            else None
        ),
        candidates=tuple(
            GoldenDemoEmergencyReturnCandidateReadModel(
                candidate_id=candidate.candidate_id,
                strategy=candidate.strategy.value,
                arrival_sequence=candidate.arrival_sequence,
                actions=tuple(
                    GoldenDemoEmergencyReturnActionReadModel(
                        aircraft_id=action.aircraft_id,
                        maneuver_type=action.maneuver.maneuver_type.value,
                        target_ground_speed_kt=(
                            action.maneuver.target_ground_speed_kt
                            if isinstance(action.maneuver, SpeedManeuver)
                            else None
                        ),
                        delay_seconds=(
                            action.maneuver.delay_seconds
                            if isinstance(action.maneuver, EntryDelayManeuver)
                            else None
                        ),
                        target_sequence_position=(
                            action.maneuver.target_sequence_position
                            if isinstance(action.maneuver, SequenceChangeManeuver)
                            else None
                        ),
                    )
                    for action in candidate.actions
                ),
                preserves_stabilized_arrival=candidate.preserves_stabilized_arrival,
                estimated_delay_seconds=candidate.cost.estimated_delay_seconds,
                operational_cost_score=candidate.cost.operational_cost_score,
                baseline=candidate.is_baseline,
                verdict=validation_by_candidate[candidate.candidate_id].verdict.value,
                predicted_conflict_aircraft_ids=tuple(
                    item.pair.aircraft_ids
                    for item in validation_by_candidate[
                        candidate.candidate_id
                    ].predicted_conflicts_after
                ),
                new_conflict_aircraft_ids=tuple(
                    item.pair.aircraft_ids
                    for item in validation_by_candidate[
                        candidate.candidate_id
                    ].new_conflicts
                ),
                performance_feasible=validation_by_candidate[
                    candidate.candidate_id
                ].performance_feasible,
                emergency_sequence_position=validation_by_candidate[
                    candidate.candidate_id
                ].emergency_sequence_position,
                priority_target_achieved=validation_by_candidate[
                    candidate.candidate_id
                ].priority_target_achieved,
                stabilized_arrival_preserved=validation_by_candidate[
                    candidate.candidate_id
                ].stabilized_arrival_preserved,
                reason_codes=tuple(
                    item.value
                    for item in validation_by_candidate[
                        candidate.candidate_id
                    ].reason_codes
                ),
                recommendation_rank=(
                    recommendation_by_candidate[candidate.candidate_id].rank
                    if candidate.candidate_id in recommendation_by_candidate
                    else None
                ),
                recommendation_explanation=(
                    recommendation_by_candidate[candidate.candidate_id].explanation
                    if candidate.candidate_id in recommendation_by_candidate
                    else None
                ),
            )
            for candidate in batch.candidates
        ),
    )


def _signed_heading_delta(actual_heading_deg: float, expected_heading_deg: float) -> float:
    delta = (actual_heading_deg - expected_heading_deg + 180.0) % 360.0 - 180.0
    return 180.0 if delta == -180.0 else delta


def _map_emergency_return_decision(
    audit_log: EmergencyReturnDecisionAuditLog | None,
    *,
    applied: bool = False,
) -> GoldenDemoEmergencyReturnDecisionReadModel | None:
    if audit_log is None:
        return None
    entry = audit_log.latest_entry
    selected = entry.selected_recommendation
    return GoldenDemoEmergencyReturnDecisionReadModel(
        audit_log_id=audit_log.audit_log_id,
        revision=audit_log.revision,
        decision_id=entry.decision_id,
        recommendation_set_id=entry.recommendation_set_id,
        source_recommendation_id=entry.source_recommendation_id,
        source_candidate_id=entry.source_candidate_id,
        selected_recommendation_id=(
            selected.recommendation_id if selected is not None else None
        ),
        selected_candidate_id=entry.selected_candidate_id,
        decision_type=entry.decision_type.value,
        decided_at_utc=_utc_text(entry.decided_at_utc),
        controller_position_id=entry.controller_position_id,
        rationale=entry.rationale,
        authorizes_application=entry.authorizes_application,
        requires_revalidation=entry.requires_revalidation,
        applied=applied,
    )


def _map_emergency_return_application(
    application: "GoldenDemoEmergencyReturnApplicationResult",
    recovery: "GoldenDemoEmergencyRecoveryResult | None",
) -> GoldenDemoEmergencyReturnApplicationReadModel:
    final_state = (
        recovery.recovery_state
        if recovery is not None
        else next(
            item
            for item in application.applied_states
            if item.aircraft_id == application.emergency_aircraft_id
        )
    )
    return GoldenDemoEmergencyReturnApplicationReadModel(
        application_id=application.application_id,
        source_decision_id=application.source_decision_id,
        applied_at_utc=_utc_text(application.applied_at_utc),
        emergency_aircraft_id=application.emergency_aircraft_id,
        selected_candidate_id=application.candidate.candidate_id,
        decision_type=application.decision_entry.decision_type.value,
        validation_verdict=application.safety_validation.verdict.value,
        actions=tuple(
            GoldenDemoEmergencyReturnActionReadModel(
                aircraft_id=action.aircraft_id,
                maneuver_type=action.maneuver.maneuver_type.value,
                target_ground_speed_kt=(
                    action.maneuver.target_ground_speed_kt
                    if isinstance(action.maneuver, SpeedManeuver)
                    else None
                ),
                delay_seconds=(
                    action.maneuver.delay_seconds
                    if isinstance(action.maneuver, EntryDelayManeuver)
                    else None
                ),
                target_sequence_position=(
                    action.maneuver.target_sequence_position
                    if isinstance(action.maneuver, SequenceChangeManeuver)
                    else None
                ),
            )
            for action in application.candidate.actions
        ),
        recovery_id=recovery.recovery_id if recovery is not None else None,
        completed_at_utc=(
            _utc_text(recovery.completed_at_utc) if recovery is not None else None
        ),
        emergency_status_after=final_state.emergency_status.value,
        flight_phase_after=final_state.flight_phase.value,
        emergency_exception_status=(
            recovery.emergency_exception_status.value if recovery is not None else None
        ),
        remaining_high_critical_pairs=(
            recovery.remaining_high_critical_pairs if recovery is not None else ()
        ),
        recovery_complete=recovery.recovery_complete if recovery is not None else False,
    )


def _map_candidate_comparisons(
    resolution_result,
) -> tuple[GoldenDemoCandidateComparisonReadModel, ...]:
    if resolution_result is None:
        return ()
    result_by_id = {
        item.candidate_id: item for item in resolution_result.validation_run.results
    }
    primary_recommendation = resolution_result.recommendation_set.primary_recommendation
    primary_candidate_id = (
        primary_recommendation.candidate_id if primary_recommendation is not None else None
    )
    return tuple(
        _map_candidate_comparison(
            candidate,
            result_by_id[candidate.candidate_id],
            recommended=candidate.candidate_id == primary_candidate_id,
        )
        for candidate in resolution_result.candidate_batch.candidates
    )


def _map_candidate_comparison(
    candidate: ResolutionCandidate,
    validation: CandidateSafetyValidationResult,
    *,
    recommended: bool,
) -> GoldenDemoCandidateComparisonReadModel:
    maneuver = candidate.maneuver
    return GoldenDemoCandidateComparisonReadModel(
        candidate_id=candidate.candidate_id,
        target_aircraft_id=candidate.target_aircraft_id,
        maneuver_type=candidate.maneuver_type.value,
        target_heading_deg=(
            maneuver.target_heading_deg if isinstance(maneuver, HeadingManeuver) else None
        ),
        target_altitude_ft=(
            maneuver.target_altitude_ft if isinstance(maneuver, AltitudeManeuver) else None
        ),
        target_ground_speed_kt=(
            maneuver.target_ground_speed_kt if isinstance(maneuver, SpeedManeuver) else None
        ),
        delay_seconds=(
            maneuver.delay_seconds if isinstance(maneuver, EntryDelayManeuver) else None
        ),
        target_sequence_position=(
            maneuver.target_sequence_position
            if isinstance(maneuver, SequenceChangeManeuver)
            else None
        ),
        operational_cost_score=candidate.cost.operational_cost_score,
        verdict=validation.verdict.value,
        primary_conflict_status=validation.primary_conflict.status.value,
        primary_horizontal_separation_nm=(
            validation.primary_conflict.minimum_separation.horizontal_nm
        ),
        primary_vertical_separation_ft=(
            validation.primary_conflict.minimum_separation.vertical_ft
        ),
        secondary_conflict_aircraft_ids=tuple(
            item.pair.aircraft_ids for item in validation.secondary_conflicts
        ),
        performance_feasible=validation.performance_feasible,
        rule_violation_ids=tuple(item.rule_id for item in validation.rule_violations),
        reason_codes=tuple(item.value for item in validation.reason_codes),
        validation_profile_id=validation.validation_profile_id,
        recommended=recommended,
    )


def _map_modified_revalidation(result) -> GoldenDemoModifiedRevalidationReadModel:
    validation = result.validation_result
    primary = validation.primary_conflict
    return GoldenDemoModifiedRevalidationReadModel(
        revalidation_step_id=result.revalidation_step_id,
        source_decision_step_id=result.source_decision_step_id,
        candidate_id=result.modified_candidate.candidate_id,
        evaluated_at_utc=_utc_text(validation.evaluated_at_utc),
        validation_run_id=result.validation_run.validation_run_id,
        verdict=validation.verdict.value,
        primary_conflict_status=primary.status.value,
        primary_horizontal_separation_nm=primary.minimum_separation.horizontal_nm,
        primary_vertical_separation_ft=primary.minimum_separation.vertical_ft,
        tcpa_seconds=primary.tcpa_seconds,
        secondary_conflict_aircraft_ids=tuple(
            item.pair.aircraft_ids for item in validation.secondary_conflicts
        ),
        performance_feasible=validation.performance_feasible,
        rule_violation_ids=tuple(item.rule_id for item in validation.rule_violations),
        reason_codes=tuple(item.value for item in validation.reason_codes),
        validation_profile_id=validation.validation_profile_id,
        safe_to_apply=validation.is_safe,
    )


def _map_primary_conflict(
    *,
    step_result,
    resolution_result,
) -> GoldenDemoConflictEvidenceReadModel | None:
    """Keep the pre-maneuver conflict evidence stable through the decision chain."""

    risk = (
        resolution_result.source_exception.assessment
        if resolution_result is not None
        else _highest_actionable_risk(step_result)
    )
    if risk is None:
        return None

    event = _matching_conflict_event(step_result, risk)
    rule = POC_TERMINAL_V1_RULE_PROFILE
    minimum_horizontal_nm = (
        event.minimum_separation.horizontal_nm
        if event is not None
        else risk.horizontal_separation_ratio * rule.horizontal_threshold_nm
    )
    minimum_vertical_ft = (
        event.minimum_separation.vertical_ft
        if event is not None
        else risk.vertical_separation_ratio * rule.vertical_threshold_ft
    )
    closest_approach_time = (
        event.closest_approach_time_utc
        if event is not None
        else risk.evaluated_at_utc + timedelta(seconds=risk.tcpa_seconds)
    )
    status = (
        event.status
        if event is not None
        else rule.classify(
            SeparationMinimum(minimum_horizontal_nm, minimum_vertical_ft)
        )
    )
    return GoldenDemoConflictEvidenceReadModel(
        conflict_id=risk.conflict_id,
        aircraft_ids=risk.pair.aircraft_ids,
        status=status.value,
        evaluated_at_utc=_utc_text(risk.evaluated_at_utc),
        closest_approach_time_utc=_utc_text(closest_approach_time),
        tcpa_seconds=risk.tcpa_seconds,
        horizontal_separation_nm=minimum_horizontal_nm,
        vertical_separation_ft=minimum_vertical_ft,
        horizontal_threshold_nm=rule.horizontal_threshold_nm,
        vertical_threshold_ft=rule.vertical_threshold_ft,
        horizontal_separation_ratio=risk.horizontal_separation_ratio,
        vertical_separation_ratio=risk.vertical_separation_ratio,
        risk_score=risk.risk_score,
        risk_level=risk.risk_level.value,
        risk_reason_codes=tuple(item.value for item in risk.reason_codes),
        rule_profile_id=rule.profile_id,
        risk_policy_profile_id=risk.policy_profile_id,
    )


def _highest_actionable_risk(step_result) -> ConflictRiskAssessment | None:
    if step_result is None:
        return None
    actionable = tuple(
        item
        for item in step_result.risk_assessments
        if item.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
    )
    if not actionable:
        return None
    return sorted(
        actionable,
        key=lambda item: (-item.risk_score, item.tcpa_seconds, item.conflict_id),
    )[0]


def _matching_conflict_event(
    step_result,
    risk: ConflictRiskAssessment,
) -> ConflictEvent | None:
    if step_result is None or step_result.conflict_run is None:
        return None
    return next(
        (
            item
            for item in step_result.conflict_run.assessments
            if item.conflict_id == risk.conflict_id
        ),
        None,
    )


def _map_revalidation(application_result) -> GoldenDemoRevalidationReadModel:
    conflict = application_result.primary_conflict_after_application
    risk = next(item for item in application_result.risk_assessments if item.pair == conflict.pair)
    source_exception = next(
        item
        for item in application_result.exception_queue_snapshot.items
        if item.subject_aircraft_ids == conflict.pair.aircraft_ids
    )
    resolved = (
        conflict.status is ConflictStatus.SAFE
        and risk.risk_level is RiskLevel.LOW
        and source_exception.status is ExceptionStatus.RESOLVED
    )
    modified_revalidation = getattr(application_result, "modified_revalidation", None)
    if modified_revalidation is None:
        candidate = application_result.decision_entry.approved_candidate
        application_source = "ACCEPTED_RECOMMENDATION"
        source_modified_revalidation_step_id = None
        authorization_id = None
        authorized_at_utc = None
    else:
        candidate = modified_revalidation.modified_candidate
        application_source = "REVALIDATED_MODIFICATION"
        source_modified_revalidation_step_id = (
            application_result.source_revalidation_step_id
        )
        authorization_id = application_result.authorization_id
        authorized_at_utc = _utc_text(application_result.authorized_at_utc)
    return GoldenDemoRevalidationReadModel(
        application_step_id=application_result.application_step_id,
        source_decision_step_id=application_result.source_decision_step_id,
        application_source=application_source,
        source_modified_revalidation_step_id=source_modified_revalidation_step_id,
        authorization_id=authorization_id,
        authorized_at_utc=authorized_at_utc,
        applied_maneuver_type=candidate.maneuver.maneuver_type.value,
        applied_aircraft_id=application_result.applied_state.aircraft_id,
        before_altitude_ft=application_result.before_state.altitude_ft,
        applied_altitude_ft=application_result.applied_state.altitude_ft,
        prediction_run_id=application_result.prediction_run.prediction_run_id,
        conflict_run_id=application_result.conflict_run.assessment_run_id,
        conflict_id=conflict.conflict_id,
        aircraft_ids=conflict.pair.aircraft_ids,
        conflict_status=conflict.status.value,
        risk_level=risk.risk_level.value,
        risk_score=risk.risk_score,
        tcpa_seconds=conflict.tcpa_seconds,
        horizontal_separation_nm=conflict.minimum_separation.horizontal_nm,
        vertical_separation_ft=conflict.minimum_separation.vertical_ft,
        source_exception_status=source_exception.status.value,
        resolved=resolved,
    )
