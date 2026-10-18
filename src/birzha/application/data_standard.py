"""Versioned research schema; preserve original candle cells during projection."""
from birzha.application.market_mirror import D1_COLUMNS
SCHEMA_VERSION = "CANONICAL_DATA_V1_RESEARCH"
PROVENANCE_COLUMNS = ["research_status", "normalized_sha256", "source_archive_sha256", "capture_day_status", "source_kind", "caveat", "source_capture_label", "source_file_id"]
SEMANTIC_COLUMNS = ["secid", "board", "engine", "market", "timeframe", "observed_at", "first_seen_at", "volume_unit", "volume_status", "turnover_unit", "turnover_status", "completeness_evidence"]
CANDLE_COLUMNS = D1_COLUMNS + PROVENANCE_COLUMNS + SEMANTIC_COLUMNS
LOGICAL_SHEETS = ("README_STATUS", "CONTRACTS", "SESSIONS", "D1", "H1", "M15", "FLOW", "POSITIONS", "INDICATORS", "QUALITY_AUDIT")

def semantic_metadata(symbol, timeframe, source):
    """Unknown units/receipt are not guessed; index raw zero remains untouched."""
    index = symbol.upper() in {"IMOEX", "RTSI"}
    volume = source.get("volume")
    turnover = source.get("value", source.get("turnover"))
    return dict(secid=source.get("secid", source.get("contract_code", "")),
      board=source.get("board") or "", engine=source.get("engine") or "", market=source.get("market") or "", timeframe=timeframe,
      observed_at=source.get("observed_at") or "", first_seen_at="",
      volume_unit="NOT_APPLICABLE" if index else source.get("volume_unit") or "UNKNOWN",
      volume_status="NOT_APPLICABLE_INDEX" if index else "MISSING" if volume in (None, "") else "SOURCE_RECORDED_UNIT_UNVERIFIED",
      turnover_unit=source.get("turnover_unit") or "UNKNOWN",
      turnover_status="MISSING" if turnover in (None, "") else "SOURCE_RECORDED_UNIT_UNVERIFIED",
      completeness_evidence="SOURCE_FLAG_ONLY_MINUTE_COVERAGE_UNVERIFIED")

def migrate_research_row(header, row, symbol, timeframe):
    """Lossless projection; reject unaccounted custom fields and identity changes."""
    if len(set(header)) != len(header) or not set(D1_COLUMNS).issubset(header):
        raise ValueError("Invalid source candle schema")
    if set(header) - set(CANDLE_COLUMNS) or len(row) > len(header):
        raise ValueError("Unaccounted source columns; explicit mapping required")
    raw = dict(zip(header, row + [""] * (len(header) - len(row))))
    result = {**semantic_metadata(symbol, timeframe, raw), **raw}
    if result["secid"] != result["contract_code"] or result["timeframe"] != timeframe:
        raise ValueError("Wrong exact contract or timeframe")
    return [result.get(k, "") for k in CANDLE_COLUMNS]
