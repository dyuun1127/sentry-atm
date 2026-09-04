from dataclasses import replace

import pytest

from sentry_atm.domain import (
    EntryDelayManeuver,
    ExceptionStatus,
    OperationalPriorityExceptionItem,
    OperationalPriorityLevel,
    SequenceChangeManeuver,
    SpeedManeuver,
)
from sentry_atm.resolution import (
    POC_EMERGENCY_RETURN_V1_GENERATION_PROFILE,
    DeterministicEmergencyReturnCandidateGenerator,
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
        state.aircraft_id: profile_by_id[metadata_by_id[state.aircraft_id].performance_class]
        for state in result.traffic_snapshot.states
    }
    return exception, result.traffic_snapshot.states, performance_by_aircraft


def test_golden_generator_builds_four_unvalidated_coordinated_alternatives() -> None:
    exception, states, profiles = _inputs()
    original_states = states

    batch = DeterministicEmergencyReturnCandidateGenerator().generate(
        exception,
        reversed(states),
        dict(reversed(tuple(profiles.items()))),
    )

    assert states == original_states
    assert batch.source_exception_id == "EXCEPTION-PRIORITY-MIL-T01"
    assert batch.source_priority_assessment_id == exception.assessment.priority_assessment_id
    assert batch.emergency_aircraft_id == "MIL-T01"
    assert batch.generator_profile_id == "POC_EMERGENCY_RETURN_V1"
    assert tuple(item.candidate_id for item in batch.candidates) == (
        "ER-CAND-A",
        "ER-CAND-B",
        "ER-CAND-C",
        "ER-CAND-D",
    )

    protected, sequence_only, immediate, baseline = batch.candidates
    assert protected.arrival_sequence == (
        "CIV-A01",
        "MIL-T01",
        "CIV-A02",
        "MIL-F02",
        "CIV-A03",
    )
    assert protected.preserves_stabilized_arrival
    assert tuple(type(item.maneuver) for item in protected.actions) == (
        SequenceChangeManeuver,
        SpeedManeuver,
        EntryDelayManeuver,
    )
    assert protected.actions[0].aircraft_id == "MIL-T01"
    assert protected.actions[0].maneuver.target_sequence_position == 2  # type: ignore[union-attr]
    assert protected.actions[1].aircraft_id == "CIV-A02"
    assert protected.actions[1].maneuver.target_ground_speed_kt == 220.0  # type: ignore[union-attr]
    assert protected.actions[2].aircraft_id == "MIL-F02"
    assert protected.actions[2].maneuver.delay_seconds == 30.0  # type: ignore[union-attr]
    assert sequence_only.arrival_sequence == protected.arrival_sequence
    assert len(sequence_only.actions) == 1
    assert immediate.arrival_sequence[0] == "MIL-T01"
    assert not immediate.preserves_stabilized_arrival
    assert baseline.arrival_sequence == (
        "CIV-A01",
        "CIV-A02",
        "MIL-F02",
        "CIV-A03",
        "MIL-T01",
    )
    assert baseline.actions == ()
    assert baseline.is_baseline


def test_generator_is_deterministic_and_source_ids_are_stable() -> None:
    inputs = _inputs()
    generator = DeterministicEmergencyReturnCandidateGenerator()

    first = generator.generate(*inputs)
    second = generator.generate(*inputs)

    assert first == second
    assert first.candidate_batch_id.startswith(
        "EMERGENCY-RETURN-POC_EMERGENCY_RETURN_V1-20260901T030400"
    )
    assert generator.profile is POC_EMERGENCY_RETURN_V1_GENERATION_PROFILE


def test_generator_rejects_wrong_or_inactive_priority_exception() -> None:
    exception, states, profiles = _inputs()
    generator = DeterministicEmergencyReturnCandidateGenerator()
    with pytest.raises(TypeError, match="OperationalPriorityExceptionItem"):
        generator.generate("exception", states, profiles)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="resolved"):
        generator.generate(
            replace(exception, status=ExceptionStatus.RESOLVED),
            states,
            profiles,
        )
    with pytest.raises(ValueError, match="EMERGENCY"):
        generator.generate(
            replace(
                exception,
                assessment=replace(
                    exception.assessment,
                    priority_level=OperationalPriorityLevel.ATTENTION,
                    priority_score=40.0,
                ),
            ),
            states,
            profiles,
        )


def test_generator_validates_complete_synchronized_inputs() -> None:
    exception, states, profiles = _inputs()
    generator = DeterministicEmergencyReturnCandidateGenerator()
    with pytest.raises(ValueError, match="arrival-sequence"):
        generator.generate(
            exception,
            tuple(item for item in states if item.aircraft_id != "CIV-A01"),
            profiles,
        )
    with pytest.raises(ValueError, match="exactly"):
        generator.generate(exception, states, {"MIL-T01": profiles["MIL-T01"]})
    with pytest.raises(TypeError, match="mapping"):
        generator.generate(exception, states, [])  # type: ignore[arg-type]
