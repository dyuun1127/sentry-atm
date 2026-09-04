"""Deterministic generation of coordinated Emergency Return candidates."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from sentry_atm.domain import (
    AircraftPerformanceProfile,
    AircraftState,
    CandidateCostEstimate,
    EmergencyReturnAction,
    EmergencyReturnCandidate,
    EmergencyReturnCandidateBatch,
    EmergencyReturnStrategy,
    EntryDelayManeuver,
    ExceptionStatus,
    OperationalPriorityExceptionItem,
    OperationalPriorityLevel,
    SequenceChangeManeuver,
    SpeedManeuver,
)
from sentry_atm.domain.units import as_non_negative_float
from sentry_atm.domain.validation import require_identifier


@dataclass(frozen=True, slots=True)
class EmergencyReturnCandidateGenerationProfile:
    """Source-labelled Golden Demo inputs for emergency flow alternatives."""

    profile_id: str
    initial_arrival_sequence: tuple[str, ...]
    stabilized_aircraft_id: str
    speed_control_aircraft_id: str
    delay_control_aircraft_id: str
    emergency_target_position: int
    speed_reduction_kt: float
    entry_delay_seconds: float
    source_reference: str

    def __post_init__(self) -> None:
        for field_name in (
            "profile_id",
            "stabilized_aircraft_id",
            "speed_control_aircraft_id",
            "delay_control_aircraft_id",
            "source_reference",
        ):
            object.__setattr__(
                self,
                field_name,
                require_identifier(getattr(self, field_name), field_name=field_name),
            )
        if isinstance(self.initial_arrival_sequence, (str, bytes)):
            raise TypeError("initial_arrival_sequence must be an iterable of identifiers")
        sequence = tuple(
            require_identifier(item, field_name="initial_arrival_sequence item")
            for item in self.initial_arrival_sequence
        )
        if not sequence:
            raise ValueError("initial_arrival_sequence must not be empty")
        if len(set(sequence)) != len(sequence):
            raise ValueError("initial_arrival_sequence Aircraft IDs must be unique")
        required_ids = {
            self.stabilized_aircraft_id,
            self.speed_control_aircraft_id,
            self.delay_control_aircraft_id,
        }
        if not required_ids <= set(sequence):
            raise ValueError("profile control Aircraft must belong to initial_arrival_sequence")
        object.__setattr__(self, "initial_arrival_sequence", sequence)
        if isinstance(self.emergency_target_position, bool) or not isinstance(
            self.emergency_target_position,
            int,
        ):
            raise TypeError("emergency_target_position must be an integer")
        if not 1 <= self.emergency_target_position <= len(sequence):
            raise ValueError("emergency_target_position must belong to arrival sequence")
        for field_name in ("speed_reduction_kt", "entry_delay_seconds"):
            normalized = as_non_negative_float(
                getattr(self, field_name),
                field_name=field_name,
            )
            if normalized == 0.0:
                raise ValueError(f"{field_name} must be greater than zero")
            object.__setattr__(self, field_name, normalized)


POC_EMERGENCY_RETURN_V1_GENERATION_PROFILE = (
    EmergencyReturnCandidateGenerationProfile(
        profile_id="POC_EMERGENCY_RETURN_V1",
        initial_arrival_sequence=(
            "CIV-A01",
            "CIV-A02",
            "MIL-F02",
            "CIV-A03",
            "MIL-T01",
        ),
        stabilized_aircraft_id="CIV-A01",
        speed_control_aircraft_id="CIV-A02",
        delay_control_aircraft_id="MIL-F02",
        emergency_target_position=2,
        speed_reduction_kt=30.0,
        entry_delay_seconds=30.0,
        source_reference="ASM-042 POC EMERGENCY RETURN GENERATION INPUTS",
    )
)


class DeterministicEmergencyReturnCandidateGenerator:
    """Generate fixed alternatives without validating or applying any action."""

    __slots__ = ("_profile",)

    def __init__(
        self,
        profile: EmergencyReturnCandidateGenerationProfile = (
            POC_EMERGENCY_RETURN_V1_GENERATION_PROFILE
        ),
    ) -> None:
        if not isinstance(profile, EmergencyReturnCandidateGenerationProfile):
            raise TypeError(
                "profile must be an EmergencyReturnCandidateGenerationProfile"
            )
        self._profile = profile

    @property
    def profile(self) -> EmergencyReturnCandidateGenerationProfile:
        return self._profile

    def generate(
        self,
        exception: OperationalPriorityExceptionItem,
        states: Iterable[AircraftState],
        performance_profiles: Mapping[str, AircraftPerformanceProfile],
    ) -> EmergencyReturnCandidateBatch:
        """Return input-order-independent candidates while leaving inputs untouched."""

        _validate_exception(exception)
        state_by_id = _validate_states(exception, states, self._profile)
        profile_by_id = _validate_performance_profiles(
            performance_profiles,
            state_by_id,
        )
        emergency_id = exception.assessment.aircraft_id
        generated_at_utc = state_by_id[emergency_id].timestamp_utc
        if exception.assessment.evaluated_at_utc > generated_at_utc:
            raise ValueError("Priority assessment must not be newer than Candidate states")

        initial = self._profile.initial_arrival_sequence
        protected = _move_to_position(
            initial,
            emergency_id,
            self._profile.emergency_target_position,
        )
        immediate = _move_to_position(initial, emergency_id, 1)
        speed_state = state_by_id[self._profile.speed_control_aircraft_id]
        speed_profile = profile_by_id[self._profile.speed_control_aircraft_id]
        reduced_speed = max(
            speed_profile.min_speed_kt,
            speed_state.ground_speed_kt - self._profile.speed_reduction_kt,
        )
        candidates = (
            EmergencyReturnCandidate(
                candidate_id="ER-CAND-A",
                strategy=EmergencyReturnStrategy.PROTECTED_PRIORITY_RETURN,
                arrival_sequence=protected,
                actions=(
                    EmergencyReturnAction(
                        emergency_id,
                        SequenceChangeManeuver(self._profile.emergency_target_position),
                    ),
                    EmergencyReturnAction(
                        self._profile.speed_control_aircraft_id,
                        SpeedManeuver(reduced_speed),
                    ),
                    EmergencyReturnAction(
                        self._profile.delay_control_aircraft_id,
                        EntryDelayManeuver(self._profile.entry_delay_seconds),
                    ),
                ),
                preserves_stabilized_arrival=True,
                cost=CandidateCostEstimate(
                    estimated_delay_seconds=self._profile.entry_delay_seconds,
                    operational_cost_score=20.0,
                ),
            ),
            EmergencyReturnCandidate(
                candidate_id="ER-CAND-B",
                strategy=EmergencyReturnStrategy.PRIORITY_SEQUENCE_ONLY,
                arrival_sequence=protected,
                actions=(
                    EmergencyReturnAction(
                        emergency_id,
                        SequenceChangeManeuver(self._profile.emergency_target_position),
                    ),
                ),
                preserves_stabilized_arrival=True,
                cost=CandidateCostEstimate(operational_cost_score=5.0),
            ),
            EmergencyReturnCandidate(
                candidate_id="ER-CAND-C",
                strategy=EmergencyReturnStrategy.IMMEDIATE_LEAD,
                arrival_sequence=immediate,
                actions=(
                    EmergencyReturnAction(
                        emergency_id,
                        SequenceChangeManeuver(1),
                    ),
                ),
                preserves_stabilized_arrival=False,
                cost=CandidateCostEstimate(operational_cost_score=35.0),
            ),
            EmergencyReturnCandidate(
                candidate_id="ER-CAND-D",
                strategy=EmergencyReturnStrategy.NO_ACTION,
                arrival_sequence=initial,
                actions=(),
                preserves_stabilized_arrival=True,
                cost=CandidateCostEstimate(),
            ),
        )
        timestamp_token = generated_at_utc.strftime("%Y%m%dT%H%M%S%fZ")
        return EmergencyReturnCandidateBatch(
            candidate_batch_id=(
                f"EMERGENCY-RETURN-{self._profile.profile_id}-{timestamp_token}-"
                f"{emergency_id}"
            ),
            source_exception_id=exception.exception_id,
            source_priority_assessment_id=exception.assessment.priority_assessment_id,
            emergency_aircraft_id=emergency_id,
            generated_at_utc=generated_at_utc,
            generator_profile_id=self._profile.profile_id,
            candidates=candidates,
        )


def _validate_exception(exception: OperationalPriorityExceptionItem) -> None:
    if not isinstance(exception, OperationalPriorityExceptionItem):
        raise TypeError("exception must be an OperationalPriorityExceptionItem")
    if exception.status is ExceptionStatus.RESOLVED:
        raise ValueError("resolved Operational Priority Exception cannot generate Candidates")
    if exception.assessment.priority_level is not OperationalPriorityLevel.EMERGENCY:
        raise ValueError("only an EMERGENCY Priority Exception can generate Candidates")


def _validate_states(
    exception: OperationalPriorityExceptionItem,
    states: Iterable[AircraftState],
    profile: EmergencyReturnCandidateGenerationProfile,
) -> dict[str, AircraftState]:
    if isinstance(states, (str, bytes)):
        raise TypeError("states must be an iterable of AircraftState instances")
    try:
        materialized = tuple(states)
    except TypeError:
        raise TypeError("states must be an iterable of AircraftState instances") from None
    if not all(isinstance(state, AircraftState) for state in materialized):
        raise TypeError("states must contain only AircraftState instances")
    state_ids = tuple(state.aircraft_id for state in materialized)
    if len(set(state_ids)) != len(state_ids):
        raise ValueError("states must have unique Aircraft IDs")
    required_ids = {*profile.initial_arrival_sequence, exception.assessment.aircraft_id}
    if not required_ids <= set(state_ids):
        raise ValueError("states must contain the Emergency and arrival-sequence Aircraft")
    if len({state.timestamp_utc for state in materialized}) != 1:
        raise ValueError("states must share one timestamp")
    return {state.aircraft_id: state for state in materialized}


def _validate_performance_profiles(
    profiles: Mapping[str, AircraftPerformanceProfile],
    state_by_id: Mapping[str, AircraftState],
) -> dict[str, AircraftPerformanceProfile]:
    if not isinstance(profiles, Mapping):
        raise TypeError("performance_profiles must be an Aircraft ID mapping")
    materialized = dict(profiles)
    if set(materialized) != set(state_by_id):
        raise ValueError("performance_profiles must contain exactly the Candidate states")
    if not all(
        isinstance(value, AircraftPerformanceProfile)
        for value in materialized.values()
    ):
        raise TypeError(
            "performance_profiles must contain AircraftPerformanceProfile values"
        )
    return materialized


def _move_to_position(
    sequence: tuple[str, ...],
    aircraft_id: str,
    one_based_position: int,
) -> tuple[str, ...]:
    normalized_id = require_identifier(aircraft_id, field_name="aircraft_id")
    if normalized_id not in sequence:
        raise ValueError("Emergency Aircraft must belong to initial_arrival_sequence")
    remaining = [item for item in sequence if item != normalized_id]
    remaining.insert(one_based_position - 1, normalized_id)
    return tuple(remaining)
