"""Retired RTSI index must never masquerade as the RI futures archive."""
import pytest

from birzha.application.instrument_archive_registry import archive_contract
from birzha.application.orchestration_plan import CORE_SYMBOLS
from birzha.application.session_data_rules import session_data_rules
from birzha.application.validation_readiness import CORE_VALIDATION_SYMBOLS
from birzha.storage.google_sheets_bridge import INSTRUMENT_TARGETS, INSTRUMENT_FOLDER_IDS, GoogleSheetsBridge


def test_rtsi_not_active_and_ri_has_one_canonical_google_book():
    assert "RTSI" not in INSTRUMENT_TARGETS
    assert "RTSI" not in INSTRUMENT_FOLDER_IDS
    assert "RI" in INSTRUMENT_TARGETS
    ri = archive_contract("RI")
    assert ri["drive_folder_id"] == "1v0ZYWbxp5vuH8WIuQtTcg6qBfX1b69Ez"
    assert ri["instrument_kind"] == "RTS_FUTURES_EXACT_CONTRACTS"
    assert ri["contract_list_sheet"] == "CONTRACTS"
    assert ri["spreadsheet_id"] == "1PuPv7Nv_Dpj-KTECPHNHecj34HrR9K5y53OwRcu7RVY"
    assert ri["spreadsheet_name"] == "MOEX_HISTDATA_RI_MCP_CANONICAL"
    assert "RTSI" not in CORE_SYMBOLS
    assert "RI" in CORE_SYMBOLS
    assert "RTSI" not in CORE_VALIDATION_SYMBOLS
    assert "RI" in CORE_VALIDATION_SYMBOLS
    with pytest.raises(ValueError, match="retired"):
        archive_contract("RTSI")
    with pytest.raises(ValueError, match="unsupported"):
        GoogleSheetsBridge.target_for_symbol("RTSI")


def test_published_rules_point_to_ri_and_never_to_rtsi_index():
    rules = session_data_rules()
    books = rules["canonical_google_books"]
    assert "RI" in books and "RTSI" not in books
    assert books["RI"]["name"] == "MOEX_HISTDATA_RI_MCP_CANONICAL"
    assert "RTSI" in rules["retired_index_symbols"]
    assert rules["canonical_drive_folder_ids"]["RI"] == "1v0ZYWbxp5vuH8WIuQtTcg6qBfX1b69Ez"
    assert rules["active_instruments"][-1] == "RI"


def test_authorized_ydb_backfill_no_longer_accepts_rtsi():
    import argparse
    import scripts.run_authorized_ydb_backfill as backfill

    assert "RI" in backfill.CORE_SYMBOLS
    assert "RTSI" not in backfill.CORE_SYMBOLS
    assert backfill._active_symbols("SBER, RI") == ("SBER", "RI")
    for excluded in ("RTSI", "SBER,rtsi"):
        with pytest.raises(argparse.ArgumentTypeError, match="retired"):
            backfill._active_symbols(excluded)
