"""Compare optional V2 formulas against a frozen Google-only research snapshot.

Output is aggregate evidence only; never writes source, HOME, Google or forecasts.
"""
import argparse
import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path
import openpyxl
from openpyxl.utils.datetime import to_excel
from birzha.application.canonical_data_derivatives import indicator_rows, INDICATOR_COLUMNS, FEATURES
from birzha.application.data_standard import migrate_research_row, CANDLE_COLUMNS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--indicators', type=Path, required=True)
    parser.add_argument('--symbol', choices=['SBER','Si','BR','GOLD','IMOEX','RTSI'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Existing evidence must not be replaced')
    source_sha = hashlib.sha256(args.source.read_bytes()).hexdigest()
    book = openpyxl.load_workbook(args.source, read_only=True, data_only=False)
    frames = {}
    for tf in ('D1','H1','M15'):
        values = iter(book[tf].iter_rows(values_only=True))
        header = list(next(values))
        while header and header[-1] is None:
            header.pop()
        rows = []
        for row in values:
            if any(value is not None for value in row):
                # Native FORMULA/SERIAL_NUMBER read semantics, not altered source cells.
                raw = [to_excel(value) if isinstance(value,(dt.datetime,dt.date,dt.time)) else value for value in row[:len(header)]]
                rows.append(migrate_research_row(header, raw, args.symbol, tf))
        frames[tf] = [CANDLE_COLUMNS,*rows]
    book.close()
    actual, version = indicator_rows(args.symbol, frames)
    by_key = {row[6]:dict(zip(INDICATOR_COLUMNS,row)) for row in actual}
    if len(by_key) != len(actual):
        raise ValueError('Duplicate source identity')
    checked = 0
    numeric = 0
    differences = []
    seen = set()
    for line in gzip.open(args.indicators,'rt',encoding='utf-8'):
        old = json.loads(line)
        if old['input_scope'] != 'GOOGLE_SNAPSHOT' or old['symbol'] != args.symbol:
            continue
        key = old['source_record_key']
        if key in seen or key not in by_key:
            raise ValueError('Missing or duplicate frozen comparison identity')
        seen.add(key)
        new = by_key[key]
        checked += 1
        for field in (*FEATURES,'atr_14_sma'):
            if old[field] != new[field]:
                differences.append([key,field])
            if old[field] is not None:
                numeric += 1
        if old['indicator_status'] != json.loads(new['indicator_status']):
            differences.append([key,'indicator_status'])
        if old['source_window_count'] != new['source_window_count']:
            differences.append([key,'source_window_count'])
    if checked != len(actual) or not checked:
        raise ValueError('Incomplete comparison')
    if hashlib.sha256(args.source.read_bytes()).hexdigest() != source_sha:
        raise ValueError('Source changed during comparison')
    result = dict(schema='OPTIONAL_DERIVED_V2_REAL_SNAPSHOT_REPLAY',symbol=args.symbol,rows=checked,numeric_values_compared=numeric,difference_count=len(differences),source_unchanged=True,source_sha256=source_sha,formula_version=version,google_write=False,home_write=False,real_moex_source_fetch=False,historical_forecast_admission=False)
    # Do not publish raw rows or private payloads in the aggregate evidence.
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
    if differences:
        raise ValueError('Optional formula replay differs; see aggregate evidence')


if __name__ == '__main__':
    main()