from datetime import UTC, datetime

import pytest

from sentry_atm.domain import (
    CandidateCostEstimate,
    EmergencyReturnAction,
    EmergencyReturnCandidate,
    EmergencyReturnCandidateBatch,
    EmergencyReturnStrategy,
    HeadingManeuver,
    SequenceChangeManeuver,
    SpeedManeuver,
)

NOW = datetime(2026, 9, 1, 3, 4, tzinfo=UTC)
SEQUENCE = ("CIV-A01", "MIL-T01", "CIV-A02")


def _candidate(
    candidate_id: str = "ER-CAND-A",
    *,
    strategy: EmergencyReturnStrategy = (
        EmergencyReturnStrategy.PROTECTED_PRIORITY_RETURN
    ),
    actions: tuple[EmergencyReturnAction, ...] | None = None,
    cost: CandidateCostEstimate | None = None,
) -> EmergencyReturnCandidate:
    return EmergencyReturnCandidate(
        candidate_id=candidate_id,
        strategy=strategy,
        arrival_sequence=SEQUENCE,
        actions=(EmergencyReturnAction("MIL-T01", SequenceChangeManeuver(2)),)
        if actions is None
        else actions,
        preserves_stabilized_arrival=True,
        cost=cost if cost is not None else CandidateCostEstimate(),
    )


def _baseline() -> EmergencyReturnCandidate:
    return _candidate(
        "ER-CAND-D",
        strategy=EmergencyReturnStrategy.NO_ACTION,
        actions=(),
    )


def test_candidate_normalizes_identity_and_preserves_ordered_actions() -> None:
    actions = (
        EmergencyReturnAction(" MIL-T01 ", SequenceChangeManeuver(2)),
        EmergencyReturnAction("CIV-A02", SpeedManeuver(220)),
    )

    candidate = _candidate(" ER-CAND-A ", actions=actions)

    assert candidate.candidate_id == "ER-CAND-A"
    assert candidate.arrival_sequence == SEQUENCE
    assert tuple(item.aircraft_id for item in candidate.actions) == (
        "MIL-T01",
        "CIV-A02",
    )
    assert not candidate.is_baseline


def test_no_action_is_the_only_empty_zero_cost_candidate() -> None:
    assert _baseline().is_baseline
    with pytest.raises(ValueError, match="must not contain actions"):
        _candidate(
            strategy=EmergencyReturnStrategy.NO_ACTION,
        )
    with pytest.raises(ValueError, match="must contain actions"):
        _candidate(actions=())
    with pytest.raises(ValueError, match="zero estimated cost"):
        _candidate(
            strategy=EmergencyReturnStrategy.NO_ACTION,
            actions=(),
            cost=CandidateCostEstimate(operational_cost_score=1),
        )


def test_candidate_rejects_unsupported_duplicate_or_out_of_sequence_actions() -> None:
    with pytest.raises(TypeError, match="supported Emergency Return Maneuver"):
        EmergencyReturnAction("MIL-T01", HeadingManeuver(180))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unique Aircraft"):
        _candidate(
            actions=(
                EmergencyReturnAction("MIL-T01", SequenceChangeManeuver(2)),
                EmergencyReturnAction("MIL-T01", SpeedManeuver(200)),
            )
        )
    with pytest.raises(ValueError, match="belong to arrival_sequence"):
        _candidate(actions=(EmergencyReturnAction("OTHER", SpeedManeuver(200)),))


def test_batch_sorts_candidates_and_requires_one_baseline() -> None:
    batch = EmergencyReturnCandidateBatch(
        candidate_batch_id=" BATCH-1 ",
        source_exception_id="EXCEPTION-1",
        source_priority_assessment_id="PRIORITY-1",
        emergency_aircraft_id="MIL-T01",
        generated_at_utc=NOW,
        generator_profile_id="PROFILE-1",
        candidates=(_baseline(), _candidate()),
    )

    assert batch.candidate_batch_id == "BATCH-1"
    assert tuple(item.candidate_id for item in batch.candidates) == (
        "ER-CAND-A",
        "ER-CAND-D",
    )
    assert batch.actionable_candidates == (batch.candidates[0],)
    assert batch.baseline_candidate == batch.candidates[1]
    with pytest.raises(ValueError, match="exactly one"):
        EmergencyReturnCandidateBatch(
            candidate_batch_id="BATCH-2",
            source_exception_id="EXCEPTION-1",
            source_priority_assessment_id="PRIORITY-1",
            emergency_aircraft_id="MIL-T01",
            generated_at_utc=NOW,
            generator_profile_id="PROFILE-1",
            candidates=(_candidate(),),
        )
