"""Canonical archive registry: data belong to instruments, never forecast models."""
from dataclasses import dataclass
from birzha.storage.google_sheets_bridge import INSTRUMENT_TARGETS

@dataclass(frozen=True)
class IndicatorSet:
    key: str
    consumers: tuple[str,...]
    scope: tuple[str,...]
    outputs: tuple[str,...]

# Existing physical p3_* columns are preserved for compatibility. They are
# calculated once in the instrument archive and can be consumed by any model.
from birzha.application.model3_features import EXTRA_COLUMNS
INDICATOR_SETS = (IndicatorSet("bar_volume_structure_v1",("P3_PURNOV_BAR_RESEARCH",),("RI",),tuple(EXTRA_COLUMNS)),)

def archive_contract(symbol):
    key=symbol.strip().upper()
    if key not in INSTRUMENT_TARGETS:raise ValueError("instrument has no approved archive/template")
    target=INSTRUMENT_TARGETS[key]
    if key=="RTSI" and target.spreadsheet_id==INSTRUMENT_TARGETS["RI"].spreadsheet_id:
        raise ValueError("RTSI canonical address conflicts with RI; verify index book before writes")
    return {"symbol":key,"canonical_storage":"GOOGLE_SHEETS","spreadsheet_id":target.spreadsheet_id,
        "spreadsheet_name":target.spreadsheet_name,"candle_sheets":["D1","H1","M15"],
        "indicator_sheets":["D1_INDICATORS","H1_INDICATORS","M15_INDICATORS"],
        "source_cache_is_not_canonical":True,"raw_prices":"PRESERVED_EXACT_CONTRACT",
        "indicator_sets":[{"key":s.key,"consumers":list(s.consumers),"outputs":list(s.outputs)} for s in INDICATOR_SETS if key in s.scope],
        "new_book_policy":"ONLY_NEW_INSTRUMENT_AND_APPROVED_TEMPLATE",
        "source_policy":"EXISTING_ARCHIVE_FIRST_MISSING_ONLY_SHARED_GOVERNOR",
        "m15_source":"M1_THROUGH_EXISTING_MOEX_AGGREGATOR",
        "forecast_admission":"NOT_GRANTED_BY_ARCHIVE"}
