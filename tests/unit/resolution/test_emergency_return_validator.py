from dataclasses import replace

import pytest

from sentry_atm.domain import (
    EmergencyReturnValidationReasonCode,
    EntryDelayManeuver,
    OperationalPriorityExceptionItem,
    OperationalPriorityLevel,
    ResolutionValidationVerdict,
    SpeedManeuver,
)
from sentry_atm.resolution import (
    EmergencyReturnSafetyValidationProfile,
    IsolatedEmergencyReturnSafetyValidator,
    apply_emergency_return_action_to_state,
)
from sentry_atm.runtime import GoldenDemoStepOrchestrator, build_golden_demo_runtime


def _inputs():
    runtime = build_golden_demo_runtime()
    runtime.simulation.clock.play()
    result = GoldenDemoStepOrchestrator(runtime).step(240)
    exception = next(
        item
        for item in result.exception_queue_snapshot.items
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
        for state in result.traffic_snapshot.states
    }
    batch = runtime.emergency_return_candidate_generator.generate(
        exception,
        result.traffic_snapshot.states,
        performance_by_aircraft,
    )
    return batch, result.traffic_snapshot.states, performance_by_aircraft


def test_golden_validation_is_isolated_and_returns_explainable_verdicts() -> None:
    batch, states, profiles = _inputs()
    original_states = states

    run = IsolatedEmergencyReturnSafetyValidator().validate(
        batch,
        reversed(states),
        dict(reversed(tuple(profiles.items()))),
    )

    assert states == original_states
    assert run.source_candidate_batch_id == batch.candidate_batch_id
    assert run.validation_profile_id == "POC_EMERGENCY_RETURN_SAFETY_V1"
    assert run.horizon_seconds == 120.0
    assert tuple(item.pair.aircraft_ids for item in run.baseline_conflicts) == (
        ("CIV-A03", "MIL-F01"),
    )
    assert tuple(item.candidate_id for item in run.results) == (
        "ER-CAND-A",
        "ER-CAND-B",
        "ER-CAND-C",
        "ER-CAND-D",
    )
    assert tuple(item.verdict for item in run.results) == (
        ResolutionValidationVerdict.SAFE,
        ResolutionValidationVerdict.SAFE,
        ResolutionValidationVerdict.UNSAFE,
        ResolutionValidationVerdict.UNSAFE,
    )
    assert tuple(item.emergency_sequence_position for item in run.results) == (2, 2, 1, 5)
    assert all(item.new_conflicts == () for item in run.results)
    assert all(item.performance_feasible for item in run.results)
    assert tuple(item.candidate_id for item in run.safe_results) == (
        "ER-CAND-A",
        "ER-CAND-B",
    )


def test_validation_reasons_distinguish_displacement_and_unmet_priority() -> None:
    batch, states, profiles = _inputs()

    run = IsolatedEmergencyReturnSafetyValidator().validate(batch, states, profiles)
    immediate = run.results[2]
    baseline = run.results[3]

    assert EmergencyReturnValidationReasonCode.STABILIZED_ARRIVAL_DISPLACED in (
        immediate.reason_codes
    )
    assert EmergencyReturnValidationReasonCode.PRIORITY_TARGET_ACHIEVED in (
        immediate.reason_codes
    )
    assert EmergencyReturnValidationReasonCode.PRIORITY_TARGET_NOT_ACHIEVED in (
        baseline.reason_codes
    )
    assert EmergencyReturnValidationReasonCode.NO_ACTION_BASELINE in baseline.reason_codes


def test_validation_is_deterministic_and_rejects_timestamp_mismatch() -> None:
    batch, states, profiles = _inputs()
    validator = IsolatedEmergencyReturnSafetyValidator()

    assert validator.validate(batch, states, profiles) == validator.validate(
        batch,
        states,
        profiles,
    )
    with pytest.raises(ValueError, match="share one timestamp"):
        validator.validate(
            replace(batch, generated_at_utc=batch.generated_at_utc.replace(year=2027)),
            states,
            profiles,
        )


def test_validation_profile_and_input_contracts_are_enforced() -> None:
    batch, states, profiles = _inputs()
    with pytest.raises(TypeError, match="EmergencyReturnCandidateBatch"):
        IsolatedEmergencyReturnSafetyValidator().validate(  # type: ignore[arg-type]
            "batch",
            states,
            profiles,
        )
    with pytest.raises(ValueError, match="exactly"):
        IsolatedEmergencyReturnSafetyValidator().validate(
            batch,
            states,
            {"MIL-T01": profiles["MIL-T01"]},
        )
    with pytest.raises(TypeError, match="EmergencyReturnSafetyValidationProfile"):
        IsolatedEmergencyReturnSafetyValidator("profile")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="detector horizon"):
        IsolatedEmergencyReturnSafetyValidator(
            EmergencyReturnSafetyValidationProfile(
                profile_id="TEST",
                horizon_seconds=60.0,
                max_speed_change_kt=40.0,
                max_entry_delay_seconds=40.0,
                maximum_priority_position=2,
                stabilized_aircraft_id="CIV-A01",
                source_reference="TEST INPUT",
            ),
            detector=IsolatedEmergencyReturnSafetyValidator().detector,
        )


def test_speed_outside_performance_envelope_is_unsafe() -> None:
    batch, states, profiles = _inputs()
    protected = batch.candidates[0]
    speed_index = next(
        index
        for index, item in enumerate(protected.actions)
        if isinstance(item.maneuver, SpeedManeuver)
    )
    actions = list(protected.actions)
    actions[speed_index] = replace(
        actions[speed_index],
        maneuver=SpeedManeuver(target_ground_speed_kt=1.0),
    )
    candidates = (
        replace(protected, actions=tuple(actions)),
        *batch.candidates[1:],
    )

    result = IsolatedEmergencyReturnSafetyValidator().validate(
        replace(batch, candidates=candidates),
        states,
        profiles,
    ).results[0]

    assert result.verdict is ResolutionValidationVerdict.UNSAFE
    assert not result.performance_feasible
    assert (
        EmergencyReturnValidationReasonCode.PERFORMANCE_ENVELOPE_EXCEEDED
        in result.reason_codes
    )


def test_action_application_returns_copies_and_checks_target_identity() -> None:
    batch, states, _ = _inputs()
    state_by_id = {item.aircraft_id: item for item in states}
    protected = batch.candidates[0]
    speed_action = next(
        item for item in protected.actions if isinstance(item.maneuver, SpeedManeuver)
    )
    delay_action = next(
        item for item in protected.actions if isinstance(item.maneuver, EntryDelayManeuver)
    )

    speed_state = apply_emergency_return_action_to_state(
        state_by_id[speed_action.aircraft_id],
        speed_action,
    )
    delay_state = apply_emergency_return_action_to_state(
        state_by_id[delay_action.aircraft_id],
        delay_action,
    )

    assert speed_state.ground_speed_kt == 220.0
    assert state_by_id[speed_action.aircraft_id].ground_speed_kt == 250.0
    assert delay_state != state_by_id[delay_action.aircraft_id]
    with pytest.raises(ValueError, match="target"):
        apply_emergency_return_action_to_state(
            state_by_id["MIL-T01"],
            speed_action,
        )
