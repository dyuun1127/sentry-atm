"""Authorized Emergency Return application and T+260 recovery evidence."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from sentry_atm.domain import (
    AircraftState,
    ControllerDecisionType,
    EmergencyReturnCandidate,
    EmergencyReturnCandidateValidationResult,
    EmergencyReturnDecisionAuditEntry,
    EmergencyReturnRecommendationSet,
    EmergencyStatus,
    EmergencyType,
    ExceptionStatus,
    FlightPhase,
    OperationalPriorityLevel,
    RiskLevel,
)
from sentry_atm.resolution import apply_emergency_return_action_to_state
from sentry_atm.runtime.application_orchestrator import _synthetic_runtime_for_aircraft
from sentry_atm.runtime.orchestrator import GoldenDemoStepOrchestrator, GoldenDemoStepResult
from sentry_atm.simulation import TrafficSnapshot

_APPLICATION_AT_SECONDS = 240
_RECOVERY_AT_SECONDS = 260
_EXPECTED_EMERGENCY_AIRCRAFT_ID = "MIL-T01"


@dataclass(frozen=True, slots=True)
class GoldenDemoEmergencyReturnApplicationResult:
    """Evidence that an audited, freshly SAFE plan was applied at T+240."""

    application_id: str
    source_decision_id: str
    applied_at_utc: datetime
    emergency_aircraft_id: str
    decision_entry: EmergencyReturnDecisionAuditEntry
    candidate: EmergencyReturnCandidate
    safety_validation: EmergencyReturnCandidateValidationResult
    source_step_result: GoldenDemoStepResult
    before_states: tuple[AircraftState, ...]
    applied_states: tuple[AircraftState, ...]
    application_snapshot: TrafficSnapshot


@dataclass(frozen=True, slots=True)
class GoldenDemoEmergencyRecoveryResult:
    """T+260 recovery evidence that retains unrelated residual conflicts."""

    recovery_id: str
    source_application_id: str
    completed_at_utc: datetime
    application_result: GoldenDemoEmergencyReturnApplicationResult
    recovery_state: AircraftState
    recovery_step_result: GoldenDemoStepResult

    @property
    def remaining_high_critical_pairs(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            item.pair.aircraft_ids
            for item in self.recovery_step_result.risk_assessments
            if item.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
        )

    @property
    def emergency_exception_status(self) -> ExceptionStatus:
        emergency_id = self.application_result.emergency_aircraft_id
        item = next(
            (
                entry
                for entry in self.recovery_step_result.exception_queue_snapshot.items
                if entry.subject_aircraft_ids == (emergency_id,)
            ),
            None,
        )
        if item is None:  # pragma: no cover - Golden queue lifecycle invariant
            raise ValueError("Emergency Return Exception is missing at recovery")
        return item.status

    @property
    def recovery_complete(self) -> bool:
        emergency_id = self.application_result.emergency_aircraft_id
        emergency_priority = next(
            item
            for item in self.recovery_step_result.priority_assessments
            if item.aircraft_id == emergency_id
        )
        emergency_conflict_clear = all(
            emergency_id not in pair for pair in self.remaining_high_critical_pairs
        )
        return (
            self.recovery_state.emergency_status is EmergencyStatus.NONE
            and emergency_priority.priority_level is OperationalPriorityLevel.ROUTINE
            and self.emergency_exception_status is ExceptionStatus.RESOLVED
            and emergency_conflict_clear
        )


class GoldenDemoEmergencyReturnApplicationOrchestrator:
    """Apply at T+240, then independently complete recovery at T+260."""

    __slots__ = (
        "_last_application_result",
        "_last_recovery_result",
        "_observed_reset_count",
        "_steps",
    )

    def __init__(self, steps: GoldenDemoStepOrchestrator) -> None:
        if not isinstance(steps, GoldenDemoStepOrchestrator):
            raise TypeError("steps must be a GoldenDemoStepOrchestrator")
        self._steps = steps
        self._observed_reset_count = steps.runtime.simulation.clock.reset_count
        self._last_application_result: (
            GoldenDemoEmergencyReturnApplicationResult | None
        ) = None
        self._last_recovery_result: GoldenDemoEmergencyRecoveryResult | None = None

    @property
    def step_orchestrator(self) -> GoldenDemoStepOrchestrator:
        return self._steps

    @property
    def last_application_result(
        self,
    ) -> GoldenDemoEmergencyReturnApplicationResult | None:
        self._synchronize_reset()
        return self._last_application_result

    @property
    def last_recovery_result(self) -> GoldenDemoEmergencyRecoveryResult | None:
        self._synchronize_reset()
        return self._last_recovery_result

    def apply(
        self,
        fresh_recommendations: EmergencyReturnRecommendationSet,
    ) -> GoldenDemoEmergencyReturnApplicationResult:
        """Freshly revalidate and apply one audited choice without advancing Clock."""

        if not isinstance(fresh_recommendations, EmergencyReturnRecommendationSet):
            raise TypeError(
                "fresh_recommendations must be an EmergencyReturnRecommendationSet"
            )
        self._synchronize_reset()
        if self._last_application_result is not None:
            raise ValueError("an Emergency Return application already exists for this Run")

        runtime = self._steps.runtime
        clock = runtime.simulation.clock
        source_step = self._steps.last_result
        if source_step is None:
            raise ValueError("a T+240 Golden Demo Step is required")
        expected_time = clock.start_time_utc + timedelta(seconds=_APPLICATION_AT_SECONDS)
        if source_step.timestamp_utc != clock.current_time_utc:
            raise ValueError("the latest Golden Demo Step must match the current Clock")
        if source_step.timestamp_utc != expected_time:
            raise ValueError("Emergency Return application must run at T+240 seconds")

        audit_log = runtime.emergency_return_decision_service.last_audit_log
        if audit_log is None:
            raise ValueError("an Emergency Return controller decision is required")
        decision = audit_log.latest_entry
        if decision.decision_type is ControllerDecisionType.REJECT:
            raise ValueError("a rejected Emergency Return plan cannot be applied")
        selected = decision.selected_recommendation
        if selected is None:  # pragma: no cover - Decision invariant
            raise ValueError("the controller decision must select a recommendation")
        if fresh_recommendations.recommendation_set_id != decision.recommendation_set_id:
            raise ValueError("fresh Recommendations must match the audited Set")
        fresh = next(
            (
                item
                for item in fresh_recommendations.recommendations
                if item.candidate_id == selected.candidate_id
            ),
            None,
        )
        if fresh is None:
            raise ValueError("the audited Candidate is not SAFE in fresh validation")

        candidate = fresh.candidate
        if not any(
            action.aircraft_id == _EXPECTED_EMERGENCY_AIRCRAFT_ID
            for action in candidate.actions
        ):
            raise ValueError("Emergency Return plan must act on MIL-T01")
        before_by_id = {
            item.aircraft_id: item for item in source_step.traffic_snapshot.states
        }
        prepared_states: list[AircraftState] = []
        for action in candidate.actions:
            state = apply_emergency_return_action_to_state(
                before_by_id[action.aircraft_id],
                action,
            )
            if action.aircraft_id == _EXPECTED_EMERGENCY_AIRCRAFT_ID:
                state = replace(
                    state,
                    emergency_status=EmergencyStatus.DECLARED,
                    emergency_type=EmergencyType.PRIORITY_RETURN,
                    flight_phase=FlightPhase.APPROACH,
                )
            prepared_states.append(state)

        for state in prepared_states:
            _synthetic_runtime_for_aircraft(
                runtime.simulation.engine.runtimes,
                state.aircraft_id,
            ).apply_state_anchor(state)
        application_snapshot = runtime.simulation.engine.snapshot()
        result = GoldenDemoEmergencyReturnApplicationResult(
            application_id=f"GOLDEN-EMERGENCY-APPLICATION-{_APPLICATION_AT_SECONDS:012d}",
            source_decision_id=decision.decision_id,
            applied_at_utc=source_step.timestamp_utc,
            emergency_aircraft_id=_EXPECTED_EMERGENCY_AIRCRAFT_ID,
            decision_entry=decision,
            candidate=candidate,
            safety_validation=fresh.validation_result,
            source_step_result=source_step,
            before_states=tuple(
                before_by_id[item.aircraft_id] for item in candidate.actions
            ),
            applied_states=tuple(prepared_states),
            application_snapshot=application_snapshot,
        )
        self._last_application_result = result
        return result

    def complete_recovery(self) -> GoldenDemoEmergencyRecoveryResult:
        """Advance the applied plan to T+260 and calculate final recovery evidence."""

        self._synchronize_reset()
        application = self._last_application_result
        if application is None:
            raise ValueError("an applied Emergency Return plan is required")
        if self._last_recovery_result is not None:
            raise ValueError("Emergency Return recovery already exists for this Run")

        runtime = self._steps.runtime
        clock = runtime.simulation.clock
        expected_time = clock.start_time_utc + timedelta(seconds=_APPLICATION_AT_SECONDS)
        if clock.current_time_utc != expected_time:
            raise ValueError("Emergency Return recovery must begin at T+240 seconds")
        runtime.simulation.engine.tick(
            _RECOVERY_AT_SECONDS - _APPLICATION_AT_SECONDS
        )
        emergency_runtime = _synthetic_runtime_for_aircraft(
            runtime.simulation.engine.runtimes,
            application.emergency_aircraft_id,
        )
        recovery_before = emergency_runtime.current_state
        if recovery_before is None:  # pragma: no cover - Golden aircraft is active
            raise ValueError("Emergency Aircraft must be active at T+260")
        recovery_state = replace(
            recovery_before,
            emergency_status=EmergencyStatus.NONE,
            emergency_type=None,
            flight_phase=FlightPhase.FINAL,
        )
        emergency_runtime.apply_state_anchor(recovery_state)
        recovery_step = self._steps.step(0)
        result = GoldenDemoEmergencyRecoveryResult(
            recovery_id=f"GOLDEN-EMERGENCY-RECOVERY-{_RECOVERY_AT_SECONDS:012d}",
            source_application_id=application.application_id,
            completed_at_utc=recovery_step.timestamp_utc,
            application_result=application,
            recovery_state=recovery_state,
            recovery_step_result=recovery_step,
        )
        if not result.recovery_complete:
            raise ValueError("Emergency Return recovery conditions were not satisfied")
        self._last_recovery_result = result
        return result

    def _synchronize_reset(self) -> None:
        reset_count = self._steps.runtime.simulation.clock.reset_count
        if reset_count == self._observed_reset_count:
            return
        self._last_application_result = None
        self._last_recovery_result = None
        self._observed_reset_count = reset_count
