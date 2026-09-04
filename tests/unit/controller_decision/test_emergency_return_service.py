import pytest

from sentry_atm.controller_decision import DeterministicEmergencyReturnDecisionService
from sentry_atm.domain import (
    ControllerDecisionType,
    OperationalPriorityExceptionItem,
    OperationalPriorityLevel,
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
    recommendations = runtime.emergency_return_recommendation_service.recommend(
        batch,
        validation,
        generated_at_utc=validation.evaluated_at_utc,
    )
    return runtime, recommendations


def test_accept_audits_primary_without_applying_runtime() -> None:
    runtime, recommendations = _inputs()
    service = DeterministicEmergencyReturnDecisionService()
    before = runtime.simulation.engine.snapshot()

    log = service.decide(
        recommendations,
        ControllerDecisionType.ACCEPT,
        decided_at_utc=recommendations.generated_at_utc,
        controller_position_id="RKTU-DEMO-CONTROLLER",
    )

    entry = log.latest_entry
    assert entry.source_candidate_id == "ER-CAND-B"
    assert entry.selected_candidate_id == "ER-CAND-B"
    assert entry.authorizes_application
    assert not entry.requires_revalidation
    assert entry.approved_candidate is recommendations.primary_recommendation.candidate
    assert runtime.simulation.engine.snapshot() == before


def test_modify_audits_validated_alternative_and_requires_revalidation() -> None:
    runtime, recommendations = _inputs()
    alternative = recommendations.alternatives[0]
    before = runtime.simulation.engine.snapshot()

    log = DeterministicEmergencyReturnDecisionService().decide(
        recommendations,
        ControllerDecisionType.MODIFY,
        decided_at_utc=recommendations.generated_at_utc,
        controller_position_id="RKTU-DEMO-CONTROLLER",
        rationale="Use protected coordination for surrounding arrivals",
        modified_recommendation_id=alternative.recommendation_id,
    )

    entry = log.latest_entry
    assert entry.source_candidate_id == "ER-CAND-B"
    assert entry.selected_candidate_id == "ER-CAND-A"
    assert not entry.authorizes_application
    assert entry.requires_revalidation
    assert entry.approved_candidate is None
    assert runtime.simulation.engine.snapshot() == before


def test_reject_audits_reason_without_selecting_or_applying_a_plan() -> None:
    runtime, recommendations = _inputs()
    before = runtime.simulation.engine.snapshot()

    log = DeterministicEmergencyReturnDecisionService().decide(
        recommendations,
        ControllerDecisionType.REJECT,
        decided_at_utc=recommendations.generated_at_utc,
        controller_position_id="RKTU-DEMO-CONTROLLER",
        rationale="Coordinate an external recovery procedure",
    )

    entry = log.latest_entry
    assert entry.selected_recommendation is None
    assert entry.selected_candidate_id is None
    assert not entry.authorizes_application
    assert not entry.requires_revalidation
    assert runtime.simulation.engine.snapshot() == before


def test_service_rejects_duplicate_and_invalid_modified_selection_atomically() -> None:
    _, recommendations = _inputs()
    service = DeterministicEmergencyReturnDecisionService()

    with pytest.raises(KeyError, match="does not belong"):
        service.decide(
            recommendations,
            ControllerDecisionType.MODIFY,
            decided_at_utc=recommendations.generated_at_utc,
            controller_position_id="RKTU-DEMO-CONTROLLER",
            rationale="Invalid alternative",
            modified_recommendation_id="MISSING",
        )
    assert service.revision == 0
    assert service.last_audit_log is None

    service.decide(
        recommendations,
        ControllerDecisionType.REJECT,
        decided_at_utc=recommendations.generated_at_utc,
        controller_position_id="RKTU-DEMO-CONTROLLER",
        rationale="Use another procedure",
    )
    with pytest.raises(ValueError, match="already has"):
        service.decide(
            recommendations,
            ControllerDecisionType.ACCEPT,
            decided_at_utc=recommendations.generated_at_utc,
            controller_position_id="RKTU-DEMO-CONTROLLER",
        )
    assert service.revision == 1


def test_modify_requires_a_different_safe_alternative_and_rationale() -> None:
    _, recommendations = _inputs()
    service = DeterministicEmergencyReturnDecisionService()
    primary = recommendations.primary_recommendation
    assert primary is not None

    with pytest.raises(ValueError, match="different recommendation"):
        service.decide(
            recommendations,
            ControllerDecisionType.MODIFY,
            decided_at_utc=recommendations.generated_at_utc,
            controller_position_id="RKTU-DEMO-CONTROLLER",
            rationale="No actual change",
            modified_recommendation_id=primary.recommendation_id,
        )
    with pytest.raises(ValueError, match="rationale"):
        service.decide(
            recommendations,
            ControllerDecisionType.MODIFY,
            decided_at_utc=recommendations.generated_at_utc,
            controller_position_id="RKTU-DEMO-CONTROLLER",
            rationale="",
            modified_recommendation_id=recommendations.alternatives[0].recommendation_id,
        )


def test_reset_clears_emergency_decision_audit_state() -> None:
    _, recommendations = _inputs()
    service = DeterministicEmergencyReturnDecisionService()
    service.decide(
        recommendations,
        ControllerDecisionType.ACCEPT,
        decided_at_utc=recommendations.generated_at_utc,
        controller_position_id="RKTU-DEMO-CONTROLLER",
    )

    service.reset()

    assert service.revision == 0
    assert service.last_audit_log is None
