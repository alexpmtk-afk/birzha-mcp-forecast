from birzha.domain.forecast import ForecastRecord, HorizonForecast
from birzha.storage.forecast_journal import DuckDBForecastJournal
from birzha.storage.ydb_state import YdbForecastJournal, _safe_table_name


def _forecast() -> ForecastRecord:
    return ForecastRecord(
        forecast_id="fcst_test",
        symbol="Si",
        secid="SiU6",
        created_at_t0="2026-08-28T13:00:00+03:00",
        engine_version="TEST",
        direction="UP",
        signal_strength=0.5,
        control="BUYERS",
        route="TREND",
        horizons=(HorizonForecast(5, "UP", 0.5, 1.2, 0.8),),
        reasons=("test",),
        warnings=(),
        validation_status="TEST",
        reference_price=80.0,
    )


def test_ydb_and_duckdb_use_identical_canonical_forecast_identity() -> None:
    record = _forecast()
    assert YdbForecastJournal.canonical_payload(record) == DuckDBForecastJournal.canonical_payload(record)


def test_ydb_table_names_fail_closed() -> None:
    assert _safe_table_name("forecast_records") == "forecast_records"
    for unsafe in ("forecast-records", "foo/bar", "x;DROP TABLE"):
        try:
            _safe_table_name(unsafe)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe table name accepted: {unsafe}")
