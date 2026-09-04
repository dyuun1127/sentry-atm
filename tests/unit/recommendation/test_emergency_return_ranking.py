from dataclasses import replace

import pytest

from sentry_atm.domain import (
    EmergencyReturnRecommendationReasonCode,
    EmergencyReturnValidationReasonCode,
    OperationalPriorityExceptionItem,
    OperationalPriorityLevel,
    RecommendationAvailability,
    ResolutionValidationVerdict,
)
from sentry_atm.recommendation import (
    POC_EMERGENCY_RETURN_RECOMMENDATION_V1_RANKING_PROFILE,
    DeterministicEmergencyReturnRecommendationRankingService,
)
from sentry_atm.runtime import GoldenDemoStepOrchestrator, build_golden_demo_runtime


def _inputs():
    runtime = build_golden_demo_runtime()
    runtime.simulation.clock.play()
    step = GoldenDemoStepOrchestrator(runtime).step(240)
    exception = next(
        item
        for item in step.exception_queue_snapshot.items
        if isinstance(item, OperationalPriorityExceptionItem)
        and item.assessment.priority_level is OperationalPriorityLevel.EMERGENCY
    )
    profile_by_id = {item.profile_id: item for item in runtime.performance_profiles}
    metadata_by_id = {
        item.aircraft_id: item.metadata for item in runtime.definition.aircraft
    }
    performance_by_aircraft = {
        state.aircraft_id: profile_by_id[
            metadata_by_id[state.aircraft_id].performance_class
        ]
        for state in step.traffic_snapshot.states
    }
    batch = runtime.emergency_return_candidate_generator.generate(
        exception,
        step.traffic_snapshot.states,
        performance_by_aircraft,
    )
    validation = runtime.emergency_return_safety_validator.validate(
        batch,
        step.traffic_snapshot.states,
        performance_by_aircraft,
    )
    return runtime, batch, validation


def test_golden_safe_plans_are_ranked_by_explicit_cost_without_runtime_mutation() -> None:
    runtime, batch, validation = _inputs()
    traffic_before = runtime.simulation.engine.snapshot()

    result = DeterministicEmergencyReturnRecommendationRankingService().recommend(
        batch,
        validation,
        generated_at_utc=validation.evaluated_at_utc,
    )

    assert result.availability is RecommendationAvailability.AVAILABLE
    assert result.source_exception_id == "EXCEPTION-PRIORITY-MIL-T01"
    assert result.source_candidate_batch_id == batch.candidate_batch_id
    assert result.source_validation_run_id == validation.validation_run_id
    assert result.ranking_policy_id == "POC_EMERGENCY_RETURN_RECOMMENDATION_V1"
    assert tuple(item.candidate_id for item in result.recommendations) == (
        "ER-CAND-B",
        "ER-CAND-A",
    )
    assert tuple(item.rank for item in result.recommendations) == (1, 2)
    assert result.primary_recommendation is result.recommendations[0]
    assert result.alternatives == (result.recommendations[1],)
    assert set(result.recommendations[0].reason_codes) == set(
        EmergencyReturnRecommendationReasonCode
    )
    assert "Controller decision required; not applied" in (
        result.recommendations[0].explanation
    )
    assert runtime.simulation.engine.snapshot() == traffic_before


def test_ranking_is_deterministic_and_independent_of_input_order() -> None:
    _, batch, validation = _inputs()
    service = DeterministicEmergencyReturnRecommendationRankingService()

    first = service.recommend(
        replace(batch, candidates=tuple(reversed(batch.candidates))),
        replace(validation, results=tuple(reversed(validation.results))),
        generated_at_utc=validation.evaluated_at_utc,
    )
    second = service.recommend(
        batch,
        validation,
        generated_at_utc=validation.evaluated_at_utc,
    )

    assert first == second
    assert service.profile is POC_EMERGENCY_RETURN_RECOMMENDATION_V1_RANKING_PROFILE


def test_unsafe_and_no_action_candidates_are_never_recommended() -> None:
    _, batch, validation = _inputs()

    result = DeterministicEmergencyReturnRecommendationRankingService().recommend(
        batch,
        validation,
        generated_at_utc=validation.evaluated_at_utc,
    )

    assert {item.candidate_id for item in result.recommendations}.isdisjoint(
        {"ER-CAND-C", "ER-CAND-D"}
    )


def test_no_safe_candidate_is_an_explicit_empty_outcome() -> None:
    _, batch, validation = _inputs()
    unsafe_results = tuple(
        _make_unsafe(item) if item.is_safe else item for item in validation.results
    )

    result = DeterministicEmergencyReturnRecommendationRankingService().recommend(
        batch,
        replace(validation, results=unsafe_results),
        generated_at_utc=validation.evaluated_at_utc,
    )

    assert result.availability is RecommendationAvailability.NO_SAFE_CANDIDATE
    assert result.recommendations == ()
    assert result.primary_recommendation is None


def test_ranking_rejects_incomplete_or_mismatched_validation_sources() -> None:
    _, batch, validation = _inputs()
    service = DeterministicEmergencyReturnRecommendationRankingService()

    with pytest.raises(ValueError, match="reference candidate_batch"):
        service.recommend(
            batch,
            replace(validation, source_candidate_batch_id="OTHER-BATCH"),
            generated_at_utc=validation.evaluated_at_utc,
        )
    with pytest.raises(ValueError, match="every Candidate exactly once"):
        service.recommend(
            batch,
            replace(validation, results=validation.results[:-1]),
            generated_at_utc=validation.evaluated_at_utc,
        )
    with pytest.raises(ValueError, match="cannot precede Safety Validation"):
        service.recommend(
            batch,
            validation,
            generated_at_utc=validation.evaluated_at_utc.replace(year=2025),
        )


def test_ranking_requires_emergency_return_contract_inputs() -> None:
    _, batch, validation = _inputs()
    service = DeterministicEmergencyReturnRecommendationRankingService()

    with pytest.raises(TypeError, match="EmergencyReturnCandidateBatch"):
        service.recommend(  # type: ignore[arg-type]
            "batch",
            validation,
            generated_at_utc=validation.evaluated_at_utc,
        )
    with pytest.raises(TypeError, match="EmergencyReturnSafetyValidationRun"):
        service.recommend(  # type: ignore[arg-type]
            batch,
            "validation",
            generated_at_utc=validation.evaluated_at_utc,
        )


def _make_unsafe(result):
    reasons = tuple(
        EmergencyReturnValidationReasonCode.STABILIZED_ARRIVAL_DISPLACED
        if reason is EmergencyReturnValidationReasonCode.STABILIZED_ARRIVAL_PRESERVED
        else reason
        for reason in result.reason_codes
    )
    return replace(
        result,
        verdict=ResolutionValidationVerdict.UNSAFE,
        stabilized_arrival_preserved=False,
        reason_codes=reasons,
    )
