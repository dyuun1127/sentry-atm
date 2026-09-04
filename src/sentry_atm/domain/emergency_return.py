"""Domain contracts for coordinated emergency-return candidate plans."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sentry_atm.domain.resolution import (
    CandidateCostEstimate,
    EntryDelayManeuver,
    SequenceChangeManeuver,
    SpeedManeuver,
)
from sentry_atm.domain.time_policy import to_utc
from sentry_atm.domain.validation import require_identifier


class EmergencyReturnStrategy(StrEnum):
    """Stable strategy labels that do not imply a Safety verdict."""

    PROTECTED_PRIORITY_RETURN = "PROTECTED_PRIORITY_RETURN"
    PRIORITY_SEQUENCE_ONLY = "PRIORITY_SEQUENCE_ONLY"
    IMMEDIATE_LEAD = "IMMEDIATE_LEAD"
    NO_ACTION = "NO_ACTION"


type EmergencyReturnManeuver = (
    SequenceChangeManeuver | SpeedManeuver | EntryDelayManeuver
)


@dataclass(frozen=True, slots=True)
class EmergencyReturnAction:
    """One Aircraft action inside a coordinated, not-yet-approved plan."""

    aircraft_id: str
    maneuver: EmergencyReturnManeuver

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "aircraft_id",
            require_identifier(self.aircraft_id, field_name="aircraft_id"),
        )
        if not isinstance(
            self.maneuver,
            (SequenceChangeManeuver, SpeedManeuver, EntryDelayManeuver),
        ):
            raise TypeError("maneuver must be a supported Emergency Return Maneuver")


@dataclass(frozen=True, slots=True)
class EmergencyReturnCandidate:
    """One coordinated return plan awaiting isolated Safety Validation."""

    candidate_id: str
    strategy: EmergencyReturnStrategy
    arrival_sequence: tuple[str, ...]
    actions: tuple[EmergencyReturnAction, ...]
    preserves_stabilized_arrival: bool
    cost: CandidateCostEstimate

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "candidate_id",
            require_identifier(self.candidate_id, field_name="candidate_id"),
        )
        object.__setattr__(self, "strategy", EmergencyReturnStrategy(self.strategy))
        sequence = _materialize_identifiers(
            self.arrival_sequence,
            field_name="arrival_sequence",
        )
        if not sequence:
            raise ValueError("arrival_sequence must not be empty")
        if len(set(sequence)) != len(sequence):
            raise ValueError("arrival_sequence Aircraft IDs must be unique")
        object.__setattr__(self, "arrival_sequence", sequence)
        actions = _materialize_actions(self.actions)
        action_ids = tuple(item.aircraft_id for item in actions)
        if len(set(action_ids)) != len(action_ids):
            raise ValueError("actions must target unique Aircraft")
        if any(item not in sequence for item in action_ids):
            raise ValueError("action Aircraft must belong to arrival_sequence")
        object.__setattr__(self, "actions", actions)
        if not isinstance(self.preserves_stabilized_arrival, bool):
            raise TypeError("preserves_stabilized_arrival must be a bool")
        if not isinstance(self.cost, CandidateCostEstimate):
            raise TypeError("cost must be a CandidateCostEstimate")
        if self.strategy is EmergencyReturnStrategy.NO_ACTION:
            if actions:
                raise ValueError("NO_ACTION candidate must not contain actions")
            if not self.cost.is_zero:
                raise ValueError("NO_ACTION candidate must have zero estimated cost")
        elif not actions:
            raise ValueError("actionable Emergency Return candidate must contain actions")

    @property
    def is_baseline(self) -> bool:
        return self.strategy is EmergencyReturnStrategy.NO_ACTION


@dataclass(frozen=True, slots=True)
class EmergencyReturnCandidateBatch:
    """Deterministic candidates generated from one Emergency Priority exception."""

    candidate_batch_id: str
    source_exception_id: str
    source_priority_assessment_id: str
    emergency_aircraft_id: str
    generated_at_utc: datetime
    generator_profile_id: str
    candidates: tuple[EmergencyReturnCandidate, ...]

    def __post_init__(self) -> None:
        for field_name in (
            "candidate_batch_id",
            "source_exception_id",
            "source_priority_assessment_id",
            "emergency_aircraft_id",
            "generator_profile_id",
        ):
            object.__setattr__(
                self,
                field_name,
                require_identifier(getattr(self, field_name), field_name=field_name),
            )
        object.__setattr__(
            self,
            "generated_at_utc",
            to_utc(self.generated_at_utc, field_name="generated_at_utc"),
        )
        candidates = _materialize_candidates(self.candidates)
        if not candidates:
            raise ValueError("candidates must not be empty")
        candidate_ids = tuple(item.candidate_id for item in candidates)
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("candidate IDs must be unique")
        if sum(item.is_baseline for item in candidates) != 1:
            raise ValueError("candidates must contain exactly one NO_ACTION baseline")
        if any(
            self.emergency_aircraft_id not in item.arrival_sequence
            for item in candidates
        ):
            raise ValueError("every candidate must sequence the Emergency Aircraft")
        object.__setattr__(
            self,
            "candidates",
            tuple(sorted(candidates, key=lambda item: item.candidate_id)),
        )

    @property
    def actionable_candidates(self) -> tuple[EmergencyReturnCandidate, ...]:
        return tuple(item for item in self.candidates if not item.is_baseline)

    @property
    def baseline_candidate(self) -> EmergencyReturnCandidate:
        return next(item for item in self.candidates if item.is_baseline)


def _materialize_identifiers(values: Iterable[str], *, field_name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{field_name} must be an iterable of identifiers")
    try:
        materialized = tuple(values)
    except TypeError:
        raise TypeError(f"{field_name} must be an iterable of identifiers") from None
    return tuple(
        require_identifier(value, field_name=f"{field_name} item")
        for value in materialized
    )


def _materialize_actions(
    values: Iterable[EmergencyReturnAction],
) -> tuple[EmergencyReturnAction, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError("actions must be an iterable of EmergencyReturnAction instances")
    try:
        materialized = tuple(values)
    except TypeError:
        raise TypeError(
            "actions must be an iterable of EmergencyReturnAction instances"
        ) from None
    if not all(isinstance(value, EmergencyReturnAction) for value in materialized):
        raise TypeError("actions must contain only EmergencyReturnAction instances")
    return materialized


def _materialize_candidates(
    values: Iterable[EmergencyReturnCandidate],
) -> tuple[EmergencyReturnCandidate, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError("candidates must be an iterable of EmergencyReturnCandidate instances")
    try:
        materialized = tuple(values)
    except TypeError:
        raise TypeError(
            "candidates must be an iterable of EmergencyReturnCandidate instances"
        ) from None
    if not all(isinstance(value, EmergencyReturnCandidate) for value in materialized):
        raise TypeError(
            "candidates must contain only EmergencyReturnCandidate instances"
        )
    return materialized
