"""Deterministic ranking of validated coordinated Emergency Return plans."""

from datetime import datetime

from sentry_atm.domain import (
    EmergencyReturnCandidate,
    EmergencyReturnCandidateBatch,
    EmergencyReturnRecommendation,
    EmergencyReturnRecommendationReasonCode,
    EmergencyReturnRecommendationSet,
    EmergencyReturnSafetyValidationRun,
    RecommendationAvailability,
)
from sentry_atm.domain.time_policy import to_utc
from sentry_atm.recommendation.profile import (
    POC_EMERGENCY_RETURN_RECOMMENDATION_V1_RANKING_PROFILE,
    RecommendationRankingProfile,
)

_POSITIVE_REASONS = tuple(EmergencyReturnRecommendationReasonCode)


class DeterministicEmergencyReturnRecommendationRankingService:
    """Rank only SAFE coordinated plans without applying any candidate action."""

    __slots__ = ("_profile",)

    def __init__(
        self,
        profile: RecommendationRankingProfile = (
            POC_EMERGENCY_RETURN_RECOMMENDATION_V1_RANKING_PROFILE
        ),
    ) -> None:
        if not isinstance(profile, RecommendationRankingProfile):
            raise TypeError("profile must be a RecommendationRankingProfile")
        self._profile = profile

    @property
    def profile(self) -> RecommendationRankingProfile:
        return self._profile

    def recommend(
        self,
        candidate_batch: EmergencyReturnCandidateBatch,
        validation_run: EmergencyReturnSafetyValidationRun,
        *,
        generated_at_utc: datetime,
    ) -> EmergencyReturnRecommendationSet:
        """Return cost-ranked SAFE plans tied to one complete validation run."""

        if not isinstance(candidate_batch, EmergencyReturnCandidateBatch):
            raise TypeError("candidate_batch must be an EmergencyReturnCandidateBatch")
        if not isinstance(validation_run, EmergencyReturnSafetyValidationRun):
            raise TypeError(
                "validation_run must be an EmergencyReturnSafetyValidationRun"
            )
        generated_at = to_utc(generated_at_utc, field_name="generated_at_utc")
        self._validate_sources(candidate_batch, validation_run, generated_at)

        validation_by_id = {
            result.candidate_id: result for result in validation_run.results
        }
        safe_candidates = tuple(
            candidate
            for candidate in candidate_batch.actionable_candidates
            if validation_by_id[candidate.candidate_id].is_safe
        )
        selected = tuple(sorted(safe_candidates, key=_ranking_key))[
            : self._profile.max_recommendations
        ]
        timestamp_token = generated_at.strftime("%Y%m%dT%H%M%S%fZ")
        recommendations = tuple(
            EmergencyReturnRecommendation(
                recommendation_id=(
                    f"EMERGENCY-RECOMMENDATION-{self._profile.profile_id}-"
                    f"{timestamp_token}-{candidate.candidate_id}"
                ),
                rank=rank,
                candidate=candidate,
                validation_result=validation_by_id[candidate.candidate_id],
                generated_at_utc=generated_at,
                reason_codes=_POSITIVE_REASONS,
                explanation=_explanation(candidate),
            )
            for rank, candidate in enumerate(selected, start=1)
        )
        availability = (
            RecommendationAvailability.AVAILABLE
            if recommendations
            else RecommendationAvailability.NO_SAFE_CANDIDATE
        )
        return EmergencyReturnRecommendationSet(
            recommendation_set_id=(
                f"EMERGENCY-RECOMMENDATION-SET-{self._profile.profile_id}-"
                f"{timestamp_token}-{candidate_batch.candidate_batch_id}"
            ),
            source_exception_id=candidate_batch.source_exception_id,
            source_candidate_batch_id=candidate_batch.candidate_batch_id,
            source_validation_run_id=validation_run.validation_run_id,
            generated_at_utc=generated_at,
            ranking_policy_id=self._profile.profile_id,
            availability=availability,
            recommendations=recommendations,
        )

    @staticmethod
    def _validate_sources(
        candidate_batch: EmergencyReturnCandidateBatch,
        validation_run: EmergencyReturnSafetyValidationRun,
        generated_at_utc: datetime,
    ) -> None:
        if validation_run.source_candidate_batch_id != candidate_batch.candidate_batch_id:
            raise ValueError("validation_run must reference candidate_batch")
        candidate_ids = {item.candidate_id for item in candidate_batch.candidates}
        validation_ids = {item.candidate_id for item in validation_run.results}
        if validation_ids != candidate_ids:
            raise ValueError("validation_run must contain every Candidate exactly once")
        if validation_run.evaluated_at_utc < candidate_batch.generated_at_utc:
            raise ValueError("Safety Validation cannot precede Candidate generation")
        if generated_at_utc < validation_run.evaluated_at_utc:
            raise ValueError("Recommendation generation cannot precede Safety Validation")


def _ranking_key(candidate: EmergencyReturnCandidate) -> tuple[float, float, float, str]:
    return (
        candidate.cost.operational_cost_score,
        candidate.cost.estimated_delay_seconds,
        candidate.cost.estimated_path_extension_nm,
        candidate.candidate_id,
    )


def _explanation(candidate: EmergencyReturnCandidate) -> str:
    cost = candidate.cost
    sequence = " -> ".join(candidate.arrival_sequence)
    return (
        f"Validated safe coordinated plan: {candidate.strategy.value}; no new conflict, "
        "performance feasible, emergency priority target achieved, stabilized arrival "
        f"preserved. Sequence {sequence}. Cost score {cost.operational_cost_score:.1f}, "
        f"delay {cost.estimated_delay_seconds:.1f} s, path extension "
        f"{cost.estimated_path_extension_nm:.1f} NM. Controller decision required; not applied."
    )
