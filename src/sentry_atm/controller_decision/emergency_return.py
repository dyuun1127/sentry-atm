"""Deterministic Human-in-the-loop audit service for Emergency Return plans."""

from datetime import datetime

from sentry_atm.domain import (
    ControllerDecisionType,
    EmergencyReturnDecisionAuditEntry,
    EmergencyReturnDecisionAuditLog,
    EmergencyReturnRecommendationSet,
    RecommendationAvailability,
)
from sentry_atm.domain.time_policy import to_utc
from sentry_atm.domain.validation import require_identifier


class DeterministicEmergencyReturnDecisionService:
    """Record one final decision per Set without changing Aircraft Runtime."""

    __slots__ = ("_entries_by_set", "_last_audit_log", "_revision")

    def __init__(self) -> None:
        self._entries_by_set: dict[str, EmergencyReturnDecisionAuditEntry] = {}
        self._last_audit_log: EmergencyReturnDecisionAuditLog | None = None
        self._revision = 0

    @property
    def revision(self) -> int:
        return self._revision

    @property
    def last_audit_log(self) -> EmergencyReturnDecisionAuditLog | None:
        return self._last_audit_log

    def decide(
        self,
        recommendation_set: EmergencyReturnRecommendationSet,
        decision_type: ControllerDecisionType,
        *,
        decided_at_utc: datetime,
        controller_position_id: str,
        rationale: str | None = None,
        modified_recommendation_id: str | None = None,
    ) -> EmergencyReturnDecisionAuditLog:
        """Validate and append an immutable final decision."""

        if not isinstance(recommendation_set, EmergencyReturnRecommendationSet):
            raise TypeError(
                "recommendation_set must be an EmergencyReturnRecommendationSet"
            )
        selected_type = ControllerDecisionType(decision_type)
        decided_at = self._normalize_operation_time(decided_at_utc)
        position_id = require_identifier(
            controller_position_id,
            field_name="controller_position_id",
        )
        set_id = recommendation_set.recommendation_set_id
        if set_id in self._entries_by_set:
            raise ValueError("Recommendation Set already has a final controller decision")
        if recommendation_set.availability is not RecommendationAvailability.AVAILABLE:
            raise ValueError("an AVAILABLE Emergency Return Recommendation is required")
        source = recommendation_set.primary_recommendation
        if source is None:  # pragma: no cover - Domain availability invariant
            raise ValueError("a primary Emergency Return Recommendation is required")

        modified = None
        if selected_type is ControllerDecisionType.MODIFY:
            normalized_id = require_identifier(
                modified_recommendation_id,
                field_name="modified_recommendation_id",
            )
            modified = next(
                (
                    item
                    for item in recommendation_set.recommendations
                    if item.recommendation_id == normalized_id
                ),
                None,
            )
            if modified is None:
                raise KeyError(
                    "modified_recommendation_id does not belong to Recommendation Set"
                )
        elif modified_recommendation_id is not None:
            raise ValueError("only MODIFY can select an alternative recommendation")

        revision = self._revision + 1
        timestamp_token = decided_at.strftime("%Y%m%dT%H%M%S%fZ")
        entry = EmergencyReturnDecisionAuditEntry(
            decision_id=(
                f"EMERGENCY-DECISION-{timestamp_token}-{revision:06d}-{set_id}"
            ),
            recommendation_set_id=set_id,
            source_recommendation=source,
            decision_type=selected_type,
            decided_at_utc=decided_at,
            controller_position_id=position_id,
            rationale=rationale,
            modified_recommendation=modified,
        )
        entries = (
            (*self._last_audit_log.entries, entry)
            if self._last_audit_log is not None
            else (entry,)
        )
        audit_log = EmergencyReturnDecisionAuditLog(
            audit_log_id=f"EMERGENCY-CONTROLLER-AUDIT-{timestamp_token}-{revision:06d}",
            revision=revision,
            generated_at_utc=decided_at,
            entries=entries,
        )
        self._entries_by_set = {**self._entries_by_set, set_id: entry}
        self._last_audit_log = audit_log
        self._revision = revision
        return audit_log

    def reset(self) -> None:
        self._entries_by_set.clear()
        self._last_audit_log = None
        self._revision = 0

    def _normalize_operation_time(self, value: datetime) -> datetime:
        normalized = to_utc(value, field_name="decided_at_utc")
        if (
            self._last_audit_log is not None
            and normalized < self._last_audit_log.generated_at_utc
        ):
            raise ValueError("decision time must not precede the last Audit Log")
        return normalized
