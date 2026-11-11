"""M1 v2.3 research-only, fail-closed field-name contract for Google Sheets.

This library does not read/write a live workbook and does not authorize any
MCP/Forecast Engine integration. Resolve *every* input and output field from
the actual header row. Column letters are derived only at the API boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence


class SchemaError(ValueError):
    """An ambiguous/missing/unapproved workbook schema; no write is safe."""


MODEL_ID = "M1_GPT_STRUCTURE_CONTROL"
SPECIFICATION_VERSION = "2.3"
RAW_FIELDS = frozenset((
    "record_key", "calendar_date", "trade_session_date", "session_id",
    "bar_start_time", "bar_end_time", "available_at", "available_at_confidence",
    "contract_code", "open", "high", "low", "close", "volume",
    "number_of_trades", "turnover", "is_complete", "source_interval",
    "source_candles", "source_id", "research_status", "normalized_sha256",
    "source_archive_sha256", "capture_day_status", "source_kind", "caveat",
    "source_capture_label", "source_file_id",
))
INDICATOR_CORE_FIELDS = frozenset((
    "record_key", "symbol", "secid", "timeframe", "begin", "end",
    "source_record_key", "source_record_sha256", "source_window_sha256",
    "source_window_count", "window_start", "window_end", "formula_id",
    "calculated_at", "available_at", "available_at_confidence",
    "quality_status", "calendar_status", "source_complete_flag",
    "indicator_status",
))
LEGACY_INDICATOR_FIELDS = frozenset((
    "return_5", "return_10", "return_20", "sma_20", "sma_50",
    "efficiency_ratio_20", "atr_14_pct", "atr_14_sma",
    "volume_ratio_20", "vwap_20_candle_approx", "price_location_20",
    "support_20_observed_min", "resistance_20_observed_max",
    "trend_score_legacy",
))
ATR_RESEARCH_FIELDS = frozenset((
    "atr_14_wilder", "atr_14_wilder_status", "_atr_calc_key",
    "_atr_calc_time", "_atr_calc_contract", "_atr_calc_tr",
    "_atr_calc_valid", "_atr_calc_run_count", "_atr_calc_run_sum",
    "_atr_calc_value", "atr_model_id", "atr_specification_version",
    "atr_formula_id", "atr_algorithm_parameters_sha256",
    "atr_source_record_key", "atr_source_hashes_json",
    "atr_calculation_window_id", "atr_quality_json",
    "_atr_calc_chain_start_key", "_atr_output_source_key",
))
INDICATOR_FIELDS = INDICATOR_CORE_FIELDS | LEGACY_INDICATOR_FIELDS | ATR_RESEARCH_FIELDS
FUTURE_OUTCOME_FIELDS = frozenset(("return_5", "return_10", "return_20"))
RAW_REQUIRED = frozenset((
    "record_key", "bar_start_time", "bar_end_time", "contract_code",
    "open", "high", "low", "close", "is_complete",
))
INDICATOR_REQUIRED = frozenset((
    "record_key", "secid", "timeframe", "source_record_key",
    "source_record_sha256", "formula_id", "indicator_status",
    "atr_14_wilder", "atr_14_wilder_status", "atr_formula_id",
    "atr_source_record_key",
))


@dataclass(frozen=True)
class NamedSchema:
    fields: tuple[str, ...]
    positions: Mapping[str, int]
    kind: str

    def position(self, field: str) -> int:
        if field not in self.positions:
            raise SchemaError(f"SCHEMA_ERROR: absent requested field {field!r}")
        return self.positions[field]

    def api_column(self, field: str) -> str:
        """Transient A1 API coordinate, *not* the meaning of a field."""
        return api_column_from_index(self.position(field))


def api_column_from_index(index: int) -> str:
    if type(index) is not int or index < 0:
        raise SchemaError("SCHEMA_ERROR: invalid column index")
    chars = []
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        chars.append(chr(65 + rem))
    return "".join(reversed(chars))


def resolve_header(
    header: Sequence[object], *, kind: str,
    required: frozenset[str] | None = None,
    allow_versioned_extensions: bool = False,
) -> NamedSchema:
    """Validate an actual Google header; no fixed column orders or aliases.

    Extensions are not silently created; only explicit callers can permit them.
    Existing v2.3 canonical books are expected to contain all known fields,
    but historical subsets can be audited using 'required'.
    """
    known, default_required = (
        (RAW_FIELDS, RAW_REQUIRED) if kind == "raw" else
        (INDICATOR_FIELDS, INDICATOR_REQUIRED) if kind == "indicators" else
        (None, None)
    )
    if known is None:
        raise SchemaError("SCHEMA_ERROR: unknown schema kind")
    if not isinstance(header, (list, tuple)) or not header:
        raise SchemaError("SCHEMA_ERROR: no header row")
    fields = tuple(header)
    if any(not isinstance(x, str) or not x or x != x.strip() for x in fields):
        raise SchemaError("SCHEMA_ERROR: blank, nontext or noncanonical header")
    if len(fields) != len(set(fields)):
        raise SchemaError("SCHEMA_ERROR: duplicated header")
    mandatory = default_required if required is None else required
    if not isinstance(mandatory, frozenset) or not mandatory.issubset(known):
        raise SchemaError("SCHEMA_ERROR: invalid declared mandatory fields")
    if missing := mandatory - set(fields):
        raise SchemaError(f"SCHEMA_ERROR: missing mandatory headers: {sorted(missing)}")
    if unexpected := set(fields) - known:
        if not allow_versioned_extensions:
            raise SchemaError(f"SCHEMA_ERROR: unapproved headers: {sorted(unexpected)}")
    return NamedSchema(fields, {name: i for i, name in enumerate(fields)}, kind)


def read_named_row(schema: NamedSchema, row: Sequence[object]) -> dict[str, object]:
    if not isinstance(row, (list, tuple)) or len(row) > len(schema.fields):
        raise SchemaError("SCHEMA_ERROR: row longer than declared header")
    return {name: row[index] if index < len(row) else ""
            for name, index in schema.positions.items()}


def write_named_row(
    schema: NamedSchema, values: Mapping[str, object], *, required_values: frozenset[str] = frozenset(),
) -> list[object]:
    """Build only a caller-authorized row in the sheet's *actual* order.

    Never blindly write this as an append: the caller must separately verify
    the target identity, deduplicate keys, and back-read the committed row.
    """
    if not isinstance(values, Mapping):
        raise SchemaError("SCHEMA_ERROR: named field mapping required")
    if unknown := set(values) - set(schema.fields):
        raise SchemaError(f"SCHEMA_ERROR: cannot silently create: {sorted(unknown)}")
    if missing := required_values - set(values):
        raise SchemaError(f"SCHEMA_ERROR: missing required values: {sorted(missing)}")
    if any(not isinstance(key, str) for key in values):
        raise SchemaError("SCHEMA_ERROR: invalid value field")
    return [values.get(name, "") for name in schema.fields]


def compare_backread(
    requested_schema: NamedSchema, written: Sequence[Mapping[str, object]],
    actual_header: Sequence[object], actual_rows: Sequence[Sequence[object]],
    *, key_field: str = "record_key",
) -> None:
    """Fail closed on partial write, wrong content, wrong key, or duplicate."""
    current = resolve_header(
        actual_header, kind=requested_schema.kind,
        required=frozenset(requested_schema.fields) &
                 (RAW_FIELDS if requested_schema.kind == "raw" else INDICATOR_FIELDS),
        allow_versioned_extensions=False,
    )
    if set(current.fields) != set(requested_schema.fields):
        raise SchemaError("SCHEMA_ERROR: schema changed during write")
    if key_field not in current.positions:
        raise SchemaError("SCHEMA_ERROR: missing key header")
    existing: dict[object, Mapping[str, object]] = {}
    for row in actual_rows:
        record = read_named_row(current, row)
        k = record[key_field]
        if k not in (None, ""):
            if not isinstance(k, str):
                raise SchemaError("SCHEMA_ERROR: nonstring backread key")
            if k in existing:
                raise SchemaError("SCHEMA_ERROR: duplicate key in backread")
            existing[k] = record
    targets = set()
    for wanted in written:
        if not isinstance(wanted, Mapping):
            raise SchemaError("SCHEMA_ERROR: expected dict for backread")
        k = wanted.get(key_field)
        if not isinstance(k, str) or not k.strip() or k in targets:
            raise SchemaError("SCHEMA_ERROR: missing/duplicate requested key")
        targets.add(k)
        if k not in existing:
            raise SchemaError("SCHEMA_ERROR: committed row not found")
        for field, value in wanted.items():
            if field not in current.positions or existing[k][field] != value:
                raise SchemaError(f"SCHEMA_ERROR: content mismatch at {k!r}/{field!r}")


def select_m1_t0_features(
    schema: NamedSchema, row: Sequence[object], feature_fields: frozenset[str],
) -> dict[str, object]:
    """Reject future outcome/label leakage, even if values are already filled."""
    if not isinstance(feature_fields, frozenset) or not feature_fields:
        raise SchemaError("SCHEMA_ERROR: explicit feature allowlist required")
    if feature_fields & FUTURE_OUTCOME_FIELDS:
        raise SchemaError("SCHEMA_ERROR: future outcomes cannot be T0 features")
    if feature_fields - set(schema.fields):
        raise SchemaError("SCHEMA_ERROR: requested feature missing")
    if schema.kind != "indicators":
        raise SchemaError("SCHEMA_ERROR: features require indicator schema")
    record = read_named_row(schema, row)
    return {field: record[field] for field in sorted(feature_fields)}
