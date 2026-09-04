"""Auditable recommendation contracts for coordinated Emergency Return plans."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sentry_atm.domain.emergency_return import EmergencyReturnCandidate
from sentry_atm.domain.emergency_return_validation import (
    EmergencyReturnCandidateValidationResult,
)
from sentry_atm.domain.enums import (
    RecommendationAvailability,
    ResolutionValidationVerdict,
)
from sentry_atm.domain.time_policy import to_utc
from sentry_atm.domain.validation import require_identifier


class EmergencyReturnRecommendationReasonCode(StrEnum):
    """Positive evidence required before an Emergency Return plan can be ranked."""

    VALIDATED_SAFE = "VALIDATED_SAFE"
    NO_NEW_CONFLICT = "NO_NEW_CONFLICT"
    PERFORMANCE_FEASIBLE = "PERFORMANCE_FEASIBLE"
    PRIORITY_TARGET_ACHIEVED = "PRIORITY_TARGET_ACHIEVED"
    STABILIZED_ARRIVAL_PRESERVED = "STABILIZED_ARRIVAL_PRESERVED"


_REQUIRED_POSITIVE_REASONS = frozenset(EmergencyReturnRecommendationReasonCode)


@dataclass(frozen=True, slots=True)
class EmergencyReturnRecommendation:
    """One ranked, validated plan that still requires a controller decision."""

    recommendation_id: str
    rank: int
    candidate: EmergencyReturnCandidate
    validation_result: EmergencyReturnCandidateValidationResult
    generated_at_utc: datetime
    reason_codes: tuple[EmergencyReturnRecommendationReasonCode, ...]
    explanation: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "recommendation_id",
            require_identifier(self.recommendation_id, field_name="recommendation_id"),
        )
        if isinstance(self.rank, bool) or not isinstance(self.rank, int):
            raise TypeError("rank must be an integer")
        if self.rank < 1:
            raise ValueError("rank must be at least 1")
        if not isinstance(self.candidate, EmergencyReturnCandidate):
            raise TypeError("candidate must be an EmergencyReturnCandidate")
        if self.candidate.is_baseline:
            raise ValueError("NO_ACTION baseline cannot be recommended")
        if not isinstance(
            self.validation_result,
            EmergencyReturnCandidateValidationResult,
        ):
            raise TypeError(
                "validation_result must be an EmergencyReturnCandidateValidationResult"
            )
        if self.validation_result.candidate_id != self.candidate.candidate_id:
            raise ValueError("validation_result must reference the recommended Candidate")
        if self.validation_result.verdict is not ResolutionValidationVerdict.SAFE:
            raise ValueError("only a SAFE Emergency Return Candidate can be recommended")
        object.__setattr__(
            self,
            "generated_at_utc",
            to_utc(self.generated_at_utc, field_name="generated_at_utc"),
        )
        if self.generated_at_utc < self.validation_result.evaluated_at_utc:
            raise ValueError("recommendation cannot precede Safety Validation")
        reasons = _normalize_reason_codes(self.reason_codes)
        if set(reasons) != _REQUIRED_POSITIVE_REASONS:
            raise ValueError("reason_codes must contain all positive Safety evidence")
        object.__setattr__(self, "reason_codes", reasons)
        object.__setattr__(
            self,
            "explanation",
            require_identifier(self.explanation, field_name="explanation"),
        )

    @property
    def candidate_id(self) -> str:
        return self.candidate.candidate_id

    @property
    def validation_result_id(self) -> str:
        return self.validation_result.validation_result_id


@dataclass(frozen=True, slots=True)
class EmergencyReturnRecommendationSet:
    """Deterministic ranked outcome for one complete Emergency Return validation."""

    recommendation_set_id: str
    source_exception_id: str
    source_candidate_batch_id: str
    source_validation_run_id: str
    generated_at_utc: datetime
    ranking_policy_id: str
    availability: RecommendationAvailability
    recommendations: tuple[EmergencyReturnRecommendation, ...]

    def __post_init__(self) -> None:
        for field_name in (
            "recommendation_set_id",
            "source_exception_id",
            "source_candidate_batch_id",
            "source_validation_run_id",
            "ranking_policy_id",
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
        object.__setattr__(
            self,
            "availability",
            RecommendationAvailability(self.availability),
        )
        recommendations = _materialize_recommendations(self.recommendations)
        self._validate_recommendations(recommendations)
        object.__setattr__(
            self,
            "recommendations",
            tuple(sorted(recommendations, key=lambda item: item.rank)),
        )

    @property
    def primary_recommendation(self) -> EmergencyReturnRecommendation | None:
        return self.recommendations[0] if self.recommendations else None

    @property
    def alternatives(self) -> tuple[EmergencyReturnRecommendation, ...]:
        return self.recommendations[1:]

    def _validate_recommendations(
        self,
        recommendations: tuple[EmergencyReturnRecommendation, ...],
    ) -> None:
        if self.availability is RecommendationAvailability.AVAILABLE and not recommendations:
            raise ValueError("AVAILABLE outcome requires at least one recommendation")
        if (
            self.availability is RecommendationAvailability.NO_SAFE_CANDIDATE
            and recommendations
        ):
            raise ValueError("NO_SAFE_CANDIDATE outcome must not contain recommendations")
        if not recommendations:
            return
        if {item.rank for item in recommendations} != set(
            range(1, len(recommendations) + 1)
        ):
            raise ValueError("recommendation ranks must be contiguous from 1")
        for field_name, values in (
            (
                "recommendation IDs",
                tuple(item.recommendation_id for item in recommendations),
            ),
            ("Candidate IDs", tuple(item.candidate_id for item in recommendations)),
            (
                "validation result IDs",
                tuple(item.validation_result_id for item in recommendations),
            ),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"{field_name} must be unique")
        if any(item.generated_at_utc != self.generated_at_utc for item in recommendations):
            raise ValueError("recommendations must share the Set generation time")


def _normalize_reason_codes(
    values: Iterable[EmergencyReturnRecommendationReasonCode],
) -> tuple[EmergencyReturnRecommendationReasonCode, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError("reason_codes must be an iterable")
    try:
        normalized = tuple(EmergencyReturnRecommendationReasonCode(item) for item in values)
    except TypeError:
        raise TypeError("reason_codes must be an iterable") from None
    if len(set(normalized)) != len(normalized):
        raise ValueError("reason_codes must be unique")
    reason_order = {
        reason: index for index, reason in enumerate(EmergencyReturnRecommendationReasonCode)
    }
    return tuple(sorted(normalized, key=reason_order.__getitem__))


def _materialize_recommendations(
    values: Iterable[EmergencyReturnRecommendation],
) -> tuple[EmergencyReturnRecommendation, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError("recommendations must be an iterable")
    try:
        materialized = tuple(values)
    except TypeError:
        raise TypeError("recommendations must be an iterable") from None
    if not all(isinstance(item, EmergencyReturnRecommendation) for item in materialized):
        raise TypeError("recommendations must contain EmergencyReturnRecommendation values")
    return materialized
