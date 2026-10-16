"""Issuance cutover can be tested without contacting market or changing journals."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from birzha.application.forecast import ENGINE_VERSION
from birzha.application.forecast_issuance_policy import (
    ISSUANCE_POLICY_APPROVED,
    ISSUANCE_POLICY_LEGACY,
    UnapprovedForecastIssuanceError,
    assert_forecast_issuance_allowed,
    blocked_forecast_result,
)
from birzha.application.journal import ForecastJournalService
from birzha.application.market_analysis import MarketAnalysisService
from birzha.config import Settings


class ForbiddenBuilder:
    calls = 0
    def build(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("provider or legacy forecast reached")


class ReadableStore:
    storage_scope = "test_memory"
    def __init__(self):
        self.appends = 0
        self.historical = SimpleNamespace(
            forecast_id="fcst_old_unchanged",
            engine_version="BIRZHA_FORECAST_BASELINE_V0_4_SCENARIOS",
            to_dict=lambda: {"forecast_id": "fcst_old_unchanged"},
        )
    def append(self, record):
        self.appends += 1
        raise AssertionError("no new historical forecast allowed")
    def get(self, forecast_id):
        return self.historical if forecast_id == "fcst_old_unchanged" else None
    def list_recent(self, *, limit=20, symbol=None):
        return [self.historical]


def test_approved_only_rejects_v04_v06_and_synthetic_versions():
    for version in (
        ENGINE_VERSION, "BIRZHA_FORECAST_BASELINE_V0_6_EVIDENCE_DECISION",
        ENGINE_VERSION + ":CAL:aggressive", "FAKE_UNREVIEWED_NEW_MODEL",
    ):
        with pytest.raises(UnapprovedForecastIssuanceError) as error:
            assert_forecast_issuance_allowed(version, policy=ISSUANCE_POLICY_APPROVED)
        report = blocked_forecast_result(error.value)
        assert report["code"] == "METHOD_NOT_APPROVED_FOR_NEW_FORECAST"
        assert report["issued"] is False
        assert report["stored"] is False


def test_journal_blocks_before_any_fetch_or_write_and_old_records_remain_readable():
    builder = ForbiddenBuilder()
    store = ReadableStore()
    service = ForecastJournalService(builder, store, issuance_policy=ISSUANCE_POLICY_APPROVED)
    with pytest.raises(UnapprovedForecastIssuanceError):
        service.create_and_save("SBER")
    assert builder.calls == 0
    assert store.appends == 0
    assert service.get("fcst_old_unchanged") == {"forecast_id": "fcst_old_unchanged"}
    assert service.list_recent()["count"] == 1


class ForbiddenHistory:
    calls = 0
    def sync(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("unapproved method must not sync history")


def test_analysis_run_core_returns_blocked_without_history_fetch():
    history = ForbiddenHistory()
    service = MarketAnalysisService(
        history=history,
        journal=ForecastJournalService(ForbiddenBuilder(), ReadableStore(),
                                       issuance_policy=ISSUANCE_POLICY_APPROVED),
    )
    result = service.run()
    assert result["status"] == "BLOCKED"
    assert result["history_synced"] is False
    assert result["issued"] is False
    assert result["items"] == []
    assert history.calls == 0


def test_config_has_legacy_default_and_explicit_cutover_mode(monkeypatch):
    monkeypatch.delenv("BIRZHA_FORECAST_ISSUANCE_POLICY", raising=False)
    assert Settings.from_env().forecast_issuance_policy == ISSUANCE_POLICY_LEGACY
    monkeypatch.setenv("BIRZHA_FORECAST_ISSUANCE_POLICY", "approved_only")
    assert Settings.from_env().forecast_issuance_policy == ISSUANCE_POLICY_APPROVED
    for invalid in ["", "force", "allow_all", "legacy_compat,approved_only"]:
        monkeypatch.setenv("BIRZHA_FORECAST_ISSUANCE_POLICY", invalid)
        with pytest.raises(ValueError, match="BIRZHA_FORECAST_ISSUANCE_POLICY"):
            Settings.from_env()


def test_legacy_compat_mode_kept_for_separately_authorized_cutover():
    assert_forecast_issuance_allowed(ENGINE_VERSION, policy=ISSUANCE_POLICY_LEGACY)


def test_no_implicit_approval_from_read_only_registry():
    from birzha.application.method_registry import read_method_decisions
    assert read_method_decisions()["approved_predictive_methods"] == []


def test_forecast_service_preview_stays_research_only_not_an_approved_issuance():
    # The lower-level ForecastService.build method remains available for
    # historical evaluations; guard is enforced at the operational MCP
    # endpoint plus direct ForecastJournalService for durable issuance.
    from birzha.application.forecast import ForecastService
    assert callable(ForecastService.build)
    assert Settings().forecast_issuance_policy == "legacy_compat"
