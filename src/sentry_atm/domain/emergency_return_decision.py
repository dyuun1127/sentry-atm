"""Immutable controller audit contracts for Emergency Return recommendations."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sentry_atm.domain.emergency_return import EmergencyReturnCandidate
from sentry_atm.domain.emergency_return_recommendation import (
    EmergencyReturnRecommendation,
)
from sentry_atm.domain.enums import ControllerDecisionType
from sentry_atm.domain.time_policy import to_utc
from sentry_atm.domain.validation import require_identifier


@dataclass(frozen=True, slots=True)
class EmergencyReturnDecisionAuditEntry:
    """One final controller response that does not apply a coordinated plan."""

    decision_id: str
    recommendation_set_id: str
    source_recommendation: EmergencyReturnRecommendation
    decision_type: ControllerDecisionType
    decided_at_utc: datetime
    controller_position_id: str
    rationale: str | None = None
    modified_recommendation: EmergencyReturnRecommendation | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "decision_id",
            "recommendation_set_id",
            "controller_position_id",
        ):
            object.__setattr__(
                self,
                field_name,
                require_identifier(getattr(self, field_name), field_name=field_name),
            )
        if not isinstance(self.source_recommendation, EmergencyReturnRecommendation):
            raise TypeError(
                "source_recommendation must be an EmergencyReturnRecommendation"
            )
        object.__setattr__(self, "decision_type", ControllerDecisionType(self.decision_type))
        object.__setattr__(
            self,
            "decided_at_utc",
            to_utc(self.decided_at_utc, field_name="decided_at_utc"),
        )
        if self.decided_at_utc < self.source_recommendation.generated_at_utc:
            raise ValueError("controller decision cannot precede Recommendation generation")
        rationale = self.rationale
        if rationale is not None:
            rationale = require_identifier(rationale, field_name="rationale")
            object.__setattr__(self, "rationale", rationale)

        if self.decision_type is ControllerDecisionType.MODIFY:
            if rationale is None:
                raise ValueError("MODIFY decision requires a rationale")
            if not isinstance(
                self.modified_recommendation,
                EmergencyReturnRecommendation,
            ):
                raise TypeError(
                    "MODIFY decision requires an Emergency Return alternative"
                )
            if (
                self.modified_recommendation.recommendation_id
                == self.source_recommendation.recommendation_id
            ):
                raise ValueError("MODIFY decision must select a different recommendation")
        else:
            if self.modified_recommendation is not None:
                raise ValueError("only MODIFY can contain a modified recommendation")
            if self.decision_type is ControllerDecisionType.REJECT and rationale is None:
                raise ValueError("REJECT decision requires a rationale")

    @property
    def source_recommendation_id(self) -> str:
        return self.source_recommendation.recommendation_id

    @property
    def source_candidate_id(self) -> str:
        return self.source_recommendation.candidate_id

    @property
    def selected_recommendation(self) -> EmergencyReturnRecommendation | None:
        if self.decision_type is ControllerDecisionType.REJECT:
            return None
        return self.modified_recommendation or self.source_recommendation

    @property
    def selected_candidate_id(self) -> str | None:
        selected = self.selected_recommendation
        return selected.candidate_id if selected is not None else None

    @property
    def authorizes_application(self) -> bool:
        return self.decision_type is ControllerDecisionType.ACCEPT

    @property
    def requires_revalidation(self) -> bool:
        return self.decision_type is ControllerDecisionType.MODIFY

    @property
    def approved_candidate(self) -> EmergencyReturnCandidate | None:
        selected = self.selected_recommendation
        return selected.candidate if self.authorizes_application and selected else None


@dataclass(frozen=True, slots=True)
class EmergencyReturnDecisionAuditLog:
    """Immutable ordered snapshot of Emergency Return controller decisions."""

    audit_log_id: str
    revision: int
    generated_at_utc: datetime
    entries: tuple[EmergencyReturnDecisionAuditEntry, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "audit_log_id",
            require_identifier(self.audit_log_id, field_name="audit_log_id"),
        )
        if isinstance(self.revision, bool) or not isinstance(self.revision, int):
            raise TypeError("revision must be an integer")
        if self.revision < 1:
            raise ValueError("revision must be at least 1")
        object.__setattr__(
            self,
            "generated_at_utc",
            to_utc(self.generated_at_utc, field_name="generated_at_utc"),
        )
        entries = _materialize_entries(self.entries)
        if not entries:
            raise ValueError("entries must not be empty")
        for field_name, values in (
            ("decision IDs", tuple(item.decision_id for item in entries)),
            (
                "Recommendation Set IDs",
                tuple(item.recommendation_set_id for item in entries),
            ),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"{field_name} must be unique")
        if any(item.decided_at_utc > self.generated_at_utc for item in entries):
            raise ValueError("Audit Log generation cannot precede a controller decision")
        object.__setattr__(
            self,
            "entries",
            tuple(sorted(entries, key=lambda item: (item.decided_at_utc, item.decision_id))),
        )

    @property
    def latest_entry(self) -> EmergencyReturnDecisionAuditEntry:
        return self.entries[-1]


def _materialize_entries(
    values: Iterable[EmergencyReturnDecisionAuditEntry],
) -> tuple[EmergencyReturnDecisionAuditEntry, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError("entries must be an iterable")
    try:
        materialized = tuple(values)
    except TypeError:
        raise TypeError("entries must be an iterable") from None
    if not all(isinstance(item, EmergencyReturnDecisionAuditEntry) for item in materialized):
        raise TypeError("entries must contain EmergencyReturnDecisionAuditEntry values")
    return materialized
