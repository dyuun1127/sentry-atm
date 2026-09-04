"""Isolated deterministic validation of coordinated Emergency Return plans."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from math import cos, radians, sin
from numbers import Real

from sentry_atm.conflict import (
    ConstantVelocityClosestApproachCalculator,
    PairwiseConflictDetector,
)
from sentry_atm.domain import (
    AircraftPerformanceProfile,
    AircraftState,
    ConflictEvent,
    ConflictStatus,
    EmergencyReturnAction,
    EmergencyReturnCandidate,
    EmergencyReturnCandidateBatch,
    EmergencyReturnCandidateValidationResult,
    EmergencyReturnSafetyValidationRun,
    EmergencyReturnValidationReasonCode,
    EntryDelayManeuver,
    ResolutionValidationVerdict,
    SequenceChangeManeuver,
    SpeedManeuver,
)
from sentry_atm.domain.units import (
    as_non_negative_float,
    fpm_to_ft_per_second,
    knots_to_nm_per_second,
)
from sentry_atm.domain.validation import require_identifier


def _as_positive_float(value: Real, *, field_name: str) -> float:
    normalized = as_non_negative_float(value, field_name=field_name)
    if normalized == 0.0:
        raise ValueError(f"{field_name} must be greater than zero")
    return normalized


@dataclass(frozen=True, slots=True)
class EmergencyReturnSafetyValidationProfile:
    """Source-labelled PoC gates for isolated Emergency Return checks."""

    profile_id: str
    horizon_seconds: float
    max_speed_change_kt: float
    max_entry_delay_seconds: float
    maximum_priority_position: int
    stabilized_aircraft_id: str
    source_reference: str

    def __post_init__(self) -> None:
        for field_name in (
            "profile_id",
            "stabilized_aircraft_id",
            "source_reference",
        ):
            object.__setattr__(
                self,
                field_name,
                require_identifier(getattr(self, field_name), field_name=field_name),
            )
        for field_name in (
            "horizon_seconds",
            "max_speed_change_kt",
            "max_entry_delay_seconds",
        ):
            object.__setattr__(
                self,
                field_name,
                _as_positive_float(getattr(self, field_name), field_name=field_name),
            )
        if isinstance(self.maximum_priority_position, bool) or not isinstance(
            self.maximum_priority_position,
            int,
        ):
            raise TypeError("maximum_priority_position must be an integer")
        if self.maximum_priority_position < 1:
            raise ValueError("maximum_priority_position must be at least 1")


POC_EMERGENCY_RETURN_SAFETY_V1_VALIDATION_PROFILE = (
    EmergencyReturnSafetyValidationProfile(
        profile_id="POC_EMERGENCY_RETURN_SAFETY_V1",
        horizon_seconds=120.0,
        max_speed_change_kt=50.0,
        max_entry_delay_seconds=60.0,
        maximum_priority_position=2,
        stabilized_aircraft_id="CIV-A01",
        source_reference="ASM-043 POC EMERGENCY RETURN SAFETY GATES",
    )
)


class IsolatedEmergencyReturnSafetyValidator:
    """Validate each plan on copied Traffic without mutating source Runtime."""

    __slots__ = ("_detector", "_profile")

    def __init__(
        self,
        profile: EmergencyReturnSafetyValidationProfile = (
            POC_EMERGENCY_RETURN_SAFETY_V1_VALIDATION_PROFILE
        ),
        *,
        detector: PairwiseConflictDetector | None = None,
    ) -> None:
        if not isinstance(profile, EmergencyReturnSafetyValidationProfile):
            raise TypeError("profile must be an EmergencyReturnSafetyValidationProfile")
        selected_detector = detector or PairwiseConflictDetector(
            calculator=ConstantVelocityClosestApproachCalculator(
                horizon_seconds=profile.horizon_seconds
            )
        )
        if not isinstance(selected_detector, PairwiseConflictDetector):
            raise TypeError("detector must be a PairwiseConflictDetector")
        if selected_detector.calculator.horizon_seconds != profile.horizon_seconds:
            raise ValueError("detector horizon must match validation profile")
        self._profile = profile
        self._detector = selected_detector

    @property
    def profile(self) -> EmergencyReturnSafetyValidationProfile:
        return self._profile

    @property
    def detector(self) -> PairwiseConflictDetector:
        return self._detector

    def validate(
        self,
        batch: EmergencyReturnCandidateBatch,
        traffic_states: Iterable[AircraftState],
        performance_profiles: Mapping[str, AircraftPerformanceProfile],
    ) -> EmergencyReturnSafetyValidationRun:
        """Return deterministic evidence while preserving every input object."""

        if not isinstance(batch, EmergencyReturnCandidateBatch):
            raise TypeError("batch must be an EmergencyReturnCandidateBatch")
        state_by_id = _validate_states(batch, traffic_states)
        performance_by_id = _validate_performance_profiles(
            performance_profiles,
            state_by_id,
        )
        evaluated_at_utc = next(iter(state_by_id.values())).timestamp_utc
        if batch.generated_at_utc != evaluated_at_utc:
            raise ValueError("Candidate Batch and traffic States must share one timestamp")
        baseline_conflicts = _predicted(self._detector.assess(state_by_id.values()))
        baseline_pairs = {item.pair for item in baseline_conflicts}
        results = tuple(
            self._validate_candidate(
                batch,
                candidate,
                state_by_id=state_by_id,
                performance_by_id=performance_by_id,
                baseline_pairs=baseline_pairs,
            )
            for candidate in batch.candidates
        )
        timestamp_token = evaluated_at_utc.strftime("%Y%m%dT%H%M%S%fZ")
        return EmergencyReturnSafetyValidationRun(
            validation_run_id=(
                f"EMERGENCY-SAFETY-{self._profile.profile_id}-{timestamp_token}-"
                f"{batch.candidate_batch_id}"
            ),
            source_candidate_batch_id=batch.candidate_batch_id,
            evaluated_at_utc=evaluated_at_utc,
            horizon_seconds=self._profile.horizon_seconds,
            validation_profile_id=self._profile.profile_id,
            baseline_conflicts=baseline_conflicts,
            results=results,
        )

    def _validate_candidate(
        self,
        batch: EmergencyReturnCandidateBatch,
        candidate: EmergencyReturnCandidate,
        *,
        state_by_id: dict[str, AircraftState],
        performance_by_id: dict[str, AircraftPerformanceProfile],
        baseline_pairs: set,
    ) -> EmergencyReturnCandidateValidationResult:
        isolated_states = dict(state_by_id)
        for action in candidate.actions:
            isolated_states[action.aircraft_id] = apply_emergency_return_action_to_state(
                isolated_states[action.aircraft_id],
                action,
            )
        predicted_after = _predicted(self._detector.assess(isolated_states.values()))
        new_conflicts = tuple(
            item for item in predicted_after if item.pair not in baseline_pairs
        )
        performance_feasible = all(
            _action_is_performance_feasible(
                action,
                state_by_id[action.aircraft_id],
                performance_by_id[action.aircraft_id],
                self._profile,
            )
            for action in candidate.actions
        )
        emergency_position = candidate.arrival_sequence.index(
            batch.emergency_aircraft_id
        ) + 1
        priority_achieved = (
            emergency_position <= self._profile.maximum_priority_position
        )
        stabilized_preserved = (
            candidate.arrival_sequence[0] == self._profile.stabilized_aircraft_id
            and candidate.preserves_stabilized_arrival
        )
        reasons = _reason_codes(
            candidate,
            new_conflicts=new_conflicts,
            performance_feasible=performance_feasible,
            priority_achieved=priority_achieved,
            stabilized_preserved=stabilized_preserved,
        )
        safe = (
            not candidate.is_baseline
            and not new_conflicts
            and performance_feasible
            and priority_achieved
            and stabilized_preserved
        )
        timestamp_token = batch.generated_at_utc.strftime("%Y%m%dT%H%M%S%fZ")
        return EmergencyReturnCandidateValidationResult(
            validation_result_id=(
                f"EMERGENCY-VALIDATION-{self._profile.profile_id}-{timestamp_token}-"
                f"{candidate.candidate_id}"
            ),
            candidate_id=candidate.candidate_id,
            evaluated_at_utc=batch.generated_at_utc,
            verdict=(
                ResolutionValidationVerdict.SAFE
                if safe
                else ResolutionValidationVerdict.UNSAFE
            ),
            predicted_conflicts_after=predicted_after,
            new_conflicts=new_conflicts,
            performance_feasible=performance_feasible,
            emergency_sequence_position=emergency_position,
            priority_target_achieved=priority_achieved,
            stabilized_arrival_preserved=stabilized_preserved,
            baseline=candidate.is_baseline,
            reason_codes=reasons,
            validation_profile_id=self._profile.profile_id,
        )


def apply_emergency_return_action_to_state(
    state: AircraftState,
    action: EmergencyReturnAction,
) -> AircraftState:
    """Return one action-applied copy; logical sequence actions leave State unchanged."""

    if not isinstance(state, AircraftState):
        raise TypeError("state must be an AircraftState")
    if not isinstance(action, EmergencyReturnAction):
        raise TypeError("action must be an EmergencyReturnAction")
    if action.aircraft_id != state.aircraft_id:
        raise ValueError("action target must match the Aircraft State")
    maneuver = action.maneuver
    if isinstance(maneuver, SpeedManeuver):
        return replace(state, ground_speed_kt=maneuver.target_ground_speed_kt)
    if isinstance(maneuver, EntryDelayManeuver):
        heading_rad = radians(state.heading_deg)
        distance_nm = (
            knots_to_nm_per_second(state.ground_speed_kt) * maneuver.delay_seconds
        )
        altitude_change_ft = (
            fpm_to_ft_per_second(state.vertical_speed_fpm) * maneuver.delay_seconds
        )
        return replace(
            state,
            x_nm=state.x_nm - distance_nm * sin(heading_rad),
            y_nm=state.y_nm - distance_nm * cos(heading_rad),
            altitude_ft=state.altitude_ft - altitude_change_ft,
        )
    if isinstance(maneuver, SequenceChangeManeuver):
        return state
    raise TypeError("action contains an unsupported Emergency Return maneuver")


def _action_is_performance_feasible(
    action: EmergencyReturnAction,
    state: AircraftState,
    performance: AircraftPerformanceProfile,
    profile: EmergencyReturnSafetyValidationProfile,
) -> bool:
    maneuver = action.maneuver
    if isinstance(maneuver, SpeedManeuver):
        return (
            performance.min_speed_kt <= maneuver.target_ground_speed_kt
            <= performance.max_speed_kt
            and abs(maneuver.target_ground_speed_kt - state.ground_speed_kt)
            <= profile.max_speed_change_kt
        )
    if isinstance(maneuver, EntryDelayManeuver):
        return maneuver.delay_seconds <= profile.max_entry_delay_seconds
    return True


def _reason_codes(
    candidate: EmergencyReturnCandidate,
    *,
    new_conflicts: tuple[ConflictEvent, ...],
    performance_feasible: bool,
    priority_achieved: bool,
    stabilized_preserved: bool,
) -> tuple[EmergencyReturnValidationReasonCode, ...]:
    reasons = [
        EmergencyReturnValidationReasonCode.NEW_CONFLICT_DETECTED
        if new_conflicts
        else EmergencyReturnValidationReasonCode.NO_NEW_CONFLICT,
        EmergencyReturnValidationReasonCode.PERFORMANCE_FEASIBLE
        if performance_feasible
        else EmergencyReturnValidationReasonCode.PERFORMANCE_ENVELOPE_EXCEEDED,
        EmergencyReturnValidationReasonCode.PRIORITY_TARGET_ACHIEVED
        if priority_achieved
        else EmergencyReturnValidationReasonCode.PRIORITY_TARGET_NOT_ACHIEVED,
        EmergencyReturnValidationReasonCode.STABILIZED_ARRIVAL_PRESERVED
        if stabilized_preserved
        else EmergencyReturnValidationReasonCode.STABILIZED_ARRIVAL_DISPLACED,
    ]
    if candidate.is_baseline:
        reasons.append(EmergencyReturnValidationReasonCode.NO_ACTION_BASELINE)
    return tuple(reasons)


def _predicted(events: Iterable[ConflictEvent]) -> tuple[ConflictEvent, ...]:
    return tuple(
        sorted(
            (item for item in events if item.status is ConflictStatus.PREDICTED),
            key=lambda item: item.pair.aircraft_ids,
        )
    )


def _validate_states(
    batch: EmergencyReturnCandidateBatch,
    states: Iterable[AircraftState],
) -> dict[str, AircraftState]:
    if isinstance(states, (str, bytes)):
        raise TypeError("traffic_states must be an iterable of AircraftState")
    try:
        materialized = tuple(states)
    except TypeError:
        raise TypeError("traffic_states must be an iterable of AircraftState") from None
    if not materialized or not all(
        isinstance(item, AircraftState) for item in materialized
    ):
        raise TypeError("traffic_states must contain AircraftState instances")
    if len({item.aircraft_id for item in materialized}) != len(materialized):
        raise ValueError("traffic_states must have unique Aircraft IDs")
    if len({item.timestamp_utc for item in materialized}) != 1:
        raise ValueError("traffic_states must share one timestamp")
    required_ids = set().union(
        *(set(candidate.arrival_sequence) for candidate in batch.candidates)
    )
    if not required_ids <= {item.aircraft_id for item in materialized}:
        raise ValueError("traffic_states must contain every Candidate Aircraft")
    return {item.aircraft_id: item for item in materialized}


def _validate_performance_profiles(
    profiles: Mapping[str, AircraftPerformanceProfile],
    state_by_id: Mapping[str, AircraftState],
) -> dict[str, AircraftPerformanceProfile]:
    if not isinstance(profiles, Mapping):
        raise TypeError("performance_profiles must be an Aircraft ID mapping")
    materialized = dict(profiles)
    if set(materialized) != set(state_by_id):
        raise ValueError("performance_profiles must contain exactly the traffic States")
    if not all(
        isinstance(item, AircraftPerformanceProfile) for item in materialized.values()
    ):
        raise TypeError("performance_profiles must contain Performance Profile values")
    return materialized
