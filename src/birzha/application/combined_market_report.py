"""Bound read-only movement, frozen levels and explicitly later observations."""
import hashlib
from birzha.application.forecast_decision_report import build_forecast_decision_report, render_forecast_decision_report
from birzha.application.level_reaction import canonical_bytes
from birzha.application.linked_level_report import build_linked_level_report, render_linked_level_report
from birzha.application.timeframe_movement import build_timeframe_movement_report, render_timeframe_movement_report

COMBINED_REPORT_VERSION = "COMBINED_MARKET_FACTS_V2"


def build_combined_market_report(snapshot, forecast_payload, *, series=None, observation_at=None, schedule_bytes=None):
    """Recompute both children from one original snapshot and a bound forecast.

    Later candles are passed only to the level observer. This is content
    consistency, not a signature check, forecast issuance or quality score.
    """
    levels = build_linked_level_report(snapshot, forecast_payload, series=series,
        observation_at=observation_at, schedule_bytes=schedule_bytes)
    decision = build_forecast_decision_report(forecast_payload)
    if decision["forecast_sha256"] != levels["forecast_sha256"]:
        raise ValueError("decision and levels forecast binding mismatch")
    movement = build_timeframe_movement_report(snapshot)
    for key in ("snapshot_id", "snapshot_sha256", "snapshot_contract_version", "symbol", "secid"):
        if movement[key] != levels[key]:
            raise ValueError("movement and levels source binding mismatch")
    if movement["t0"] != levels["created_at_t0"]:
        raise ValueError("movement and levels T0 mismatch")
    body = {"version": COMBINED_REPORT_VERSION, "forecast_id": levels["forecast_id"],
        "forecast_sha256": levels["forecast_sha256"], "snapshot_id": levels["snapshot_id"],
        "snapshot_sha256": levels["snapshot_sha256"], "snapshot_contract_version": levels["snapshot_contract_version"],
        "symbol": snapshot.symbol, "secid": snapshot.secid, "t0": snapshot.as_of,
        "decision_at_t0": decision, "movement_at_t0": movement, "level_report": levels,
        "observation_at": observation_at, "later_data_used_for_movement": False,
        "forecast_mutated": False, "source_authentication": "NOT_ESTABLISHED_BY_THIS_MODULE",
        "alignment_score": None, "control": "UNKNOWN", "predictive_quality": "NOT_MEASURED"}
    return {"report_id": "combined_market_" + hashlib.sha256(canonical_bytes(body)).hexdigest(), **body}


def render_combined_market_report(report):
    movement = render_timeframe_movement_report(report["movement_at_t0"])
    levels = render_linked_level_report(report["level_report"])
    # Demote existing headings without hiding the later-observation boundary.
    movement = "\n".join("#" + line if line.startswith("#") else line for line in movement.splitlines())
    levels = "\n".join("#" + line if line.startswith("#") else line for line in levels.splitlines())
    return "\n".join(["# Движение цены и реакции на уровни", "",
        "Движение и положение уровней рассчитаны по одному исходному снимку при записи прогноза.",
        "Последующие свечи относятся только к разделу после записи; они не меняют прежние сведения.",
        "", render_forecast_decision_report(report["decision_at_t0"]) if "decision_at_t0" in report else "Исходное решение в этой старой версии отчёта не представлено.",
        "", movement, "", levels])
