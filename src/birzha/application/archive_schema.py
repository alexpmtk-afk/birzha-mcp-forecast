"""Pure existing candle schema; no persistent store imports."""
D1_COLUMNS = [
    "record_key", "calendar_date", "trade_session_date", "session_id",
    "bar_start_time", "bar_end_time", "available_at", "available_at_confidence",
    "contract_code", "open", "high", "low", "close", "volume",
    "number_of_trades", "turnover", "is_complete", "source_interval",
    "source_candles", "source_id",
]

# Existing compact source layout observed in the canonical book, 2026-11-01.
# Accept this exact layout without adding/removing columns or inventing metadata.
COMPACT_CANDLE_COLUMNS = (
    "record_key", "calendar_date", "trade_session_date", "bar_start_time",
    "bar_end_time", "available_at", "contract_code", "open", "high", "low",
    "close", "volume", "is_complete", "source_id", "research_status",
    "normalized_sha256", "caveat", "source_file_id",
)
