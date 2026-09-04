"""Auditable contracts for isolated Emergency Return Safety Validation."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sentry_atm.domain.conflict import ConflictEvent
from sentry_atm.domain.enums import ConflictStatus, ResolutionValidationVerdict
from sentry_atm.domain.time_policy import to_utc
from sentry_atm.domain.units import as_non_negative_float
from sentry_atm.domain.validation import require_identifier


class EmergencyReturnValidationReasonCode(StrEnum):
    """Stable reasons for one Emergency Return validation verdict."""

    NO_NEW_CONFLICT = "NO_NEW_CONFLICT"
    NEW_CONFLICT_DETECTED = "NEW_CONFLICT_DETECTED"
    PERFORMANCE_FEASIBLE = "PERFORMANCE_FEASIBLE"
    PERFORMANCE_ENVELOPE_EXCEEDED = "PERFORMANCE_ENVELOPE_EXCEEDED"
    PRIORITY_TARGET_ACHIEVED = "PRIORITY_TARGET_ACHIEVED"
    PRIORITY_TARGET_NOT_ACHIEVED = "PRIORITY_TARGET_NOT_ACHIEVED"
    STABILIZED_ARRIVAL_PRESERVED = "STABILIZED_ARRIVAL_PRESERVED"
    STABILIZED_ARRIVAL_DISPLACED = "STABILIZED_ARRIVAL_DISPLACED"
    NO_ACTION_BASELINE = "NO_ACTION_BASELINE"


@dataclass(frozen=True, slots=True)
class EmergencyReturnCandidateValidationResult:
    """Evidence for one Candidate evaluated on an isolated Traffic copy."""

    validation_result_id: str
    candidate_id: str
    evaluated_at_utc: datetime
    verdict: ResolutionValidationVerdict
    predicted_conflicts_after: tuple[ConflictEvent, ...]
    new_conflicts: tuple[ConflictEvent, ...]
    performance_feasible: bool
    emergency_sequence_position: int
    priority_target_achieved: bool
    stabilized_arrival_preserved: bool
    baseline: bool
    reason_codes: tuple[EmergencyReturnValidationReasonCode, ...]
    validation_profile_id: str

    def __post_init__(self) -> None:
        for field_name in (
            "validation_result_id",
            "candidate_id",
            "validation_profile_id",
        ):
            object.__setattr__(
                self,
                field_name,
                require_identifier(getattr(self, field_name), field_name=field_name),
            )
        object.__setattr__(
            self,
            "evaluated_at_utc",
            to_utc(self.evaluated_at_utc, field_name="evaluated_at_utc"),
        )
        object.__setattr__(self, "verdict", ResolutionValidationVerdict(self.verdict))
        if self.verdict is ResolutionValidationVerdict.INEFFECTIVE:
            raise ValueError("Emergency Return verdict must be SAFE or UNSAFE")
        conflicts_after = _materialize_conflicts(
            self.predicted_conflicts_after,
            field_name="predicted_conflicts_after",
        )
        new_conflicts = _materialize_conflicts(
            self.new_conflicts,
            field_name="new_conflicts",
        )
        after_pairs = {item.pair for item in conflicts_after}
        if any(item.pair not in after_pairs for item in new_conflicts):
            raise ValueError("new_conflicts must be a subset of predicted_conflicts_after")
        object.__setattr__(self, "predicted_conflicts_after", conflicts_after)
        object.__setattr__(self, "new_conflicts", new_conflicts)
        for field_name in (
            "performance_feasible",
            "priority_target_achieved",
            "stabilized_arrival_preserved",
            "baseline",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise TypeError(f"{field_name} must be a bool")
        if isinstance(self.emergency_sequence_position, bool) or not isinstance(
            self.emergency_sequence_position,
            int,
        ):
            raise TypeError("emergency_sequence_position must be an integer")
        if self.emergency_sequence_position < 1:
            raise ValueError("emergency_sequence_position must be at least 1")
        reasons = _materialize_reasons(self.reason_codes)
        object.__setattr__(self, "reason_codes", reasons)
        self._validate_consistency()

    @property
    def is_safe(self) -> bool:
        return self.verdict is ResolutionValidationVerdict.SAFE

    def _validate_consistency(self) -> None:
        reasons = set(self.reason_codes)
        _require_reason_pair(
            reasons,
            positive=EmergencyReturnValidationReasonCode.NO_NEW_CONFLICT,
            negative=EmergencyReturnValidationReasonCode.NEW_CONFLICT_DETECTED,
            positive_evidence=not self.new_conflicts,
        )
        _require_reason_pair(
            reasons,
            positive=EmergencyReturnValidationReasonCode.PERFORMANCE_FEASIBLE,
            negative=EmergencyReturnValidationReasonCode.PERFORMANCE_ENVELOPE_EXCEEDED,
            positive_evidence=self.performance_feasible,
        )
        _require_reason_pair(
            reasons,
            positive=EmergencyReturnValidationReasonCode.PRIORITY_TARGET_ACHIEVED,
            negative=EmergencyReturnValidationReasonCode.PRIORITY_TARGET_NOT_ACHIEVED,
            positive_evidence=self.priority_target_achieved,
        )
        _require_reason_pair(
            reasons,
            positive=EmergencyReturnValidationReasonCode.STABILIZED_ARRIVAL_PRESERVED,
            negative=EmergencyReturnValidationReasonCode.STABILIZED_ARRIVAL_DISPLACED,
            positive_evidence=self.stabilized_arrival_preserved,
        )
        if (EmergencyReturnValidationReasonCode.NO_ACTION_BASELINE in reasons) is not self.baseline:
            raise ValueError("NO_ACTION_BASELINE must match baseline evidence")
        has_failure = (
            bool(self.new_conflicts)
            or not self.performance_feasible
            or not self.priority_target_achieved
            or not self.stabilized_arrival_preserved
            or self.baseline
        )
        if (self.verdict is ResolutionValidationVerdict.SAFE) is has_failure:
            raise ValueError("SAFE verdict requires all Emergency Return gates to pass")


@dataclass(frozen=True, slots=True)
class EmergencyReturnSafetyValidationRun:
    """Deterministically ordered validation evidence for one Candidate Batch."""

    validation_run_id: str
    source_candidate_batch_id: str
    evaluated_at_utc: datetime
    horizon_seconds: float
    validation_profile_id: str
    baseline_conflicts: tuple[ConflictEvent, ...]
    results: tuple[EmergencyReturnCandidateValidationResult, ...]

    def __post_init__(self) -> None:
        for field_name in (
            "validation_run_id",
            "source_candidate_batch_id",
            "validation_profile_id",
        ):
            object.__setattr__(
                self,
                field_name,
                require_identifier(getattr(self, field_name), field_name=field_name),
            )
        object.__setattr__(
            self,
            "evaluated_at_utc",
            to_utc(self.evaluated_at_utc, field_name="evaluated_at_utc"),
        )
        horizon = as_non_negative_float(self.horizon_seconds, field_name="horizon_seconds")
        if horizon == 0.0:
            raise ValueError("horizon_seconds must be greater than zero")
        object.__setattr__(self, "horizon_seconds", horizon)
        baseline = _materialize_conflicts(
            self.baseline_conflicts,
            field_name="baseline_conflicts",
        )
        results = _materialize_results(self.results)
        if not results:
            raise ValueError("results must not be empty")
        if len({item.candidate_id for item in results}) != len(results):
            raise ValueError("validated candidate IDs must be unique")
        if any(item.evaluated_at_utc != self.evaluated_at_utc for item in results):
            raise ValueError("results must share run evaluated_at_utc")
        if any(item.validation_profile_id != self.validation_profile_id for item in results):
            raise ValueError("results must use run validation_profile_id")
        object.__setattr__(self, "baseline_conflicts", baseline)
        object.__setattr__(
            self,
            "results",
            tuple(sorted(results, key=lambda item: item.candidate_id)),
        )

    @property
    def safe_results(self) -> tuple[EmergencyReturnCandidateValidationResult, ...]:
        return tuple(item for item in self.results if item.is_safe)


def _materialize_conflicts(
    values: Iterable[ConflictEvent],
    *,
    field_name: str,
) -> tuple[ConflictEvent, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{field_name} must be an iterable of ConflictEvent")
    try:
        materialized = tuple(values)
    except TypeError:
        raise TypeError(f"{field_name} must be an iterable of ConflictEvent") from None
    if not all(isinstance(item, ConflictEvent) for item in materialized):
        raise TypeError(f"{field_name} must contain only ConflictEvent instances")
    if any(item.status is not ConflictStatus.PREDICTED for item in materialized):
        raise ValueError(f"{field_name} must contain only PREDICTED Conflicts")
    if len({item.pair for item in materialized}) != len(materialized):
        raise ValueError(f"{field_name} Conflict Pairs must be unique")
    return tuple(sorted(materialized, key=lambda item: item.pair.aircraft_ids))


def _materialize_reasons(
    values: Iterable[EmergencyReturnValidationReasonCode],
) -> tuple[EmergencyReturnValidationReasonCode, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError("reason_codes must be an iterable")
    try:
        normalized = tuple(EmergencyReturnValidationReasonCode(item) for item in values)
    except TypeError:
        raise TypeError("reason_codes must be an iterable") from None
    if len(set(normalized)) != len(normalized):
        raise ValueError("reason_codes must be unique")
    return normalized


def _materialize_results(
    values: Iterable[EmergencyReturnCandidateValidationResult],
) -> tuple[EmergencyReturnCandidateValidationResult, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError("results must be an iterable")
    try:
        materialized = tuple(values)
    except TypeError:
        raise TypeError("results must be an iterable") from None
    if not all(
        isinstance(item, EmergencyReturnCandidateValidationResult)
        for item in materialized
    ):
        raise TypeError("results must contain validation result instances")
    return materialized


def _require_reason_pair(
    reasons: set[EmergencyReturnValidationReasonCode],
    *,
    positive: EmergencyReturnValidationReasonCode,
    negative: EmergencyReturnValidationReasonCode,
    positive_evidence: bool,
) -> None:
    if (positive in reasons) is not positive_evidence:
        raise ValueError(f"{positive.value} must match validation evidence")
    if (negative in reasons) is positive_evidence:
        raise ValueError(f"{negative.value} must match validation evidence")
