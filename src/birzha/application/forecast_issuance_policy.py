"""Prospective issuance gate backed by versioned method decisions.

This gate does not alter historical records or disable research evaluation.
The default legacy mode preserves currently installed endpoint behavior until
the owner separately authorizes runtime cutover to approved_only.
"""
from __future__ import annotations

from birzha.application.method_registry import read_method_decisions

ISSUANCE_POLICY_LEGACY = "legacy_compat"
ISSUANCE_POLICY_APPROVED = "approved_only"
ALLOWED_ISSUANCE_POLICIES = (ISSUANCE_POLICY_LEGACY, ISSUANCE_POLICY_APPROVED)


class UnapprovedForecastIssuanceError(ValueError):
    """A proposed new forecast must not be created with an unapproved method."""

    def __init__(self, engine_version: str):
        self.engine_version = engine_version
        self.code = "METHOD_NOT_APPROVED_FOR_NEW_FORECAST"
        super().__init__(self.code)


def assert_forecast_issuance_allowed(
    engine_version: str,
    *,
    policy: str,
) -> None:
    """Fail before snapshot/provider and before any Forecast Journal operation."""
    if policy not in ALLOWED_ISSUANCE_POLICIES:
        raise ValueError("unknown forecast issuance policy")
    if not isinstance(engine_version, str) or not engine_version.strip():
        raise UnapprovedForecastIssuanceError(str(engine_version))
    if policy == ISSUANCE_POLICY_LEGACY:
        # Backward compatible as initially shipped; NOT research acceptance.
        return
    approved = read_method_decisions()["approved_predictive_methods"]
    if engine_version not in approved:
        raise UnapprovedForecastIssuanceError(engine_version)


def blocked_forecast_result(exc: UnapprovedForecastIssuanceError) -> dict[str, object]:
    return {
        "status": "BLOCKED",
        "code": exc.code,
        "engine_version": exc.engine_version,
        "issued": False,
        "stored": False,
        "prior_journal_records_unchanged": True,
        "message_ru": "Метод не допущен для создания новых прогнозов. Старые прогнозы доступны для чтения.",
    }
