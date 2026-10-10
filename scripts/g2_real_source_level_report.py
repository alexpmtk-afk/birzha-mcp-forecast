"""Offline repeat of level facts on pinned original MOEX pages; never issuance."""
import argparse
import io
from datetime import timezone
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import zipfile

from birzha.application.features import TimeframeFeatureEngine
from birzha.application.normalized_features import NormalizedFeatureEngine
from birzha.application.forecast import build_forecast_from_snapshot
from birzha.application.linked_level_report import build_linked_level_report, render_linked_level_report
from birzha.application.level_reaction import canonical_bytes, _receipt_time
from birzha.domain.market import Instrument
from birzha.domain.snapshot import MarketSnapshot, DataQualityContract, TimeframeQuality
if __package__ in (None, ""):
    from g2_freeze_real_forecast_from_source_receipts import source_series, contiguous_m15, EXPECTED_SOURCE
    from g2_price_location import _unique_pairs
else:
    from scripts.g2_freeze_real_forecast_from_source_receipts import source_series, contiguous_m15, EXPECTED_SOURCE
    from scripts.g2_price_location import _unique_pairs

VERSION = "G2_OFFLINE_REAL_SOURCE_LEVEL_REPORT_V1"
MARKETS = ("SBER", "Si", "BR", "GOLD", "IMOEX", "RTSI")


def _json(body):
    return json.loads(body, object_pairs_hook=_unique_pairs)


def read_source_archive(path, expected_sha256, target):
    body=Path(path).read_bytes()
    if sha256(body).hexdigest()!=expected_sha256:
        raise ValueError("original archive SHA256 mismatch")
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        names=archive.namelist()
        if len(names)!=len(set(names)) or len(names)>100 or sum(i.file_size for i in archive.infolist())>64*1024*1024:
            raise ValueError("duplicate or oversized archive")
        if any(Path(n).name!=n or "/" in n or "\\" in n or ":" in n for n in names):
            raise ValueError("archive must contain plain filenames only")
        manifest_bytes=archive.read("manifest.json")
        manifest=_json(manifest_bytes)
        if manifest.get("schema")!=EXPECTED_SOURCE or manifest.get("origin")!="REAL_MOEX_PUBLIC_ISS_CAPTURE":
            raise ValueError("unsupported original-source manifest")
        entries=manifest.get("instruments",[])
        if sorted(manifest.get("requested_markets",[]))!=sorted(MARKETS) or sorted(e.get("market") for e in entries)!=sorted(MARKETS):
            raise ValueError("exact six-market inventory required")
        if manifest.get("failed_markets") or sorted(manifest.get("complete_markets",[]))!=sorted(MARKETS):
            raise ValueError("incomplete original capture")
        listed=manifest["all_payload_files"]
        expected_names=[p["name"] for p in listed]
        if len(expected_names)!=len(set(expected_names)) or set(names)!={"manifest.json",*expected_names}:
            raise ValueError("archive inventory mismatch")
        consumed=[]
        for entry in entries:
            for tf in ("D1","H1","M1"):
                meta=entry["timeframes"][tf]
                if meta.get("timeframe")!=tf:
                    raise ValueError("timeframe metadata mismatch")
                consumed.extend(p["path"] for p in meta["pages"])
        if len(consumed)!=len(set(consumed)) or set(consumed)!=set(expected_names):
            raise ValueError("unbound or duplicated source page")
        for item in listed:
            payload=archive.read(item["name"])
            if sha256(payload).hexdigest()!=item["sha256"]:
                raise ValueError("manifest/page SHA mismatch")
            table=_json(payload)["candles"]
            columns=table["columns"]
            if len(columns)!=len(set(columns)) or any(len(row)!=len(columns) for row in table["data"]):
                raise ValueError("ambiguous candle columns or row width")
            (target/item["name"]).write_bytes(payload)
    return manifest,sha256(manifest_bytes).hexdigest()


def compute(archive_path, expected_archive_sha256, *, analysis_at):
    analysis=_receipt_time(analysis_at)
    with tempfile.TemporaryDirectory(prefix="birzha-level-offline-") as temp:
        root=Path(temp)
        manifest,manifest_sha=read_source_archive(archive_path,expected_archive_sha256,root)
        capture_start=_receipt_time(manifest["capture_started_utc"])
        capture_end=_receipt_time(manifest["capture_completed_utc"])
        if not capture_start<=capture_end<=analysis:
            raise ValueError("capture interval exceeds offline analysis time")
        results=[]
        for entry in manifest["instruments"]:
            if entry["status"]!="SOURCE_CAPTURE_COMPLETE":
                raise ValueError("incomplete market")
            instrument=Instrument(**entry["instrument"])
            if "CANDLES" not in instrument.data_capabilities:
                raise ValueError("instrument does not declare candle capability")
            if instrument.symbol!=entry["market"] or instrument.secid!=entry["resolved_secid"]:
                raise ValueError("instrument identity mismatch")
            series={}; receipts=[]
            for tf in ("D1","H1","M1"):
                series[tf],_,seen=source_series(root,entry,tf,instrument)
                receipts.append(seen)
                for page in entry["timeframes"][tf]["pages"]:
                    if not capture_start<=_receipt_time(page["observed_start_utc"])<=_receipt_time(page["observed_end_utc"])<=capture_end:
                        raise ValueError("page receipt lies outside declared capture")
            series["M15"],discarded=contiguous_m15(series["M1"])
            counts={tf:series[tf].count for tf in ("D1","H1","M15")}
            if any(n<50 for n in counts.values()):
                raise ValueError("insufficient completed source bars")
            engine=TimeframeFeatureEngine()
            states={tf:engine.build(series[tf]) for tf in counts}
            warnings=("OFFLINE_REPEAT_NOT_ORIGINAL_ISSUANCE","SOURCE_CALENDAR_NOT_INDEPENDENTLY_VERIFIED",f"M15_INCOMPLETE_BUCKETS_EXCLUDED={discarded}")
            snap=MarketSnapshot(symbol=instrument.symbol,secid=instrument.secid,as_of=analysis_at,
                source="SAVED_MOEX_BYTES_OFFLINE_REPEAT",d1=states["D1"],h1=states["H1"],m15=states["M15"],
                data_quality="DEGRADED",warnings=warnings,
                quality_contract=DataQualityContract("DATA_QUALITY_CONTRACT_V2","DEGRADED",
                    tuple(TimeframeQuality(tf,counts[tf],50,series[tf].candles[-1].end,"PASS") for tf in counts),"NOT_REQUESTED",warnings),
                normalized_features=NormalizedFeatureEngine().build(d1=states["D1"],h1=states["H1"],m15=states["M15"],flow=None,volume_profile=None,instrument=instrument))
            forecast=build_forecast_from_snapshot(snap).to_dict()
            linked=build_linked_level_report(snap,forecast)
            latest=_receipt_time(series["M15"].candles[-1].end)
            results.append({"market":instrument.symbol,"secid":instrument.secid,"counts":counts,
                "source_observed_at":max(receipts).astimezone(timezone.utc).isoformat(),
                "last_price_at":series["M15"].candles[-1].end,"price_age_seconds":(analysis-latest).total_seconds(),
                "discarded_m15_buckets":discarded,"snapshot":snap.to_dict(),"repeat_record":forecast,"report":linked})
    body={"version":VERSION,"mode":"OFFLINE_REPEAT_ON_SAVED_REAL_PROVIDER_PAGES",
        "archive_sha256":expected_archive_sha256,"manifest_sha256":manifest_sha,"analysis_at":analysis_at,
        "source_origin_declared":manifest["origin"],"source_capture_completed_at":manifest["capture_completed_utc"],
        "provider_authentication":"NOT_INDEPENDENTLY_ATTESTED","future_observations_supplied":False,
        "prospective_issuance":False,"old_forecasts_modified":False,"predictive_quality_measured":False,
        "markets":results}
    return {"run_id":"real_levels_"+sha256(canonical_bytes(body)).hexdigest(),**body}


def render_summary(result):
    lines=["# Проверка отчёта на сохранённых ценах биржи","",
        "Отдельный повторный расчёт по реальным сохранённым ответам. Это не прежний прогноз и не текущие котировки.",
        "Старые прогнозы не изменены. Последующие наблюдения не переданы; точность прогноза не измеряется.",
        f"Данные получены: {result['source_capture_completed_at']}; повторный расчёт: {result['analysis_at']}.","",
        "| Рынок | Точный инструмент | Сохранённая цена | Последняя свеча цены | Уровень выше | Уровень ниже |","|---|---|---:|---|---:|---:|"]
    for item in result['markets']:
        report=item['report']['at_t0']
        def nearby(key):
            near=report[key]
            return 'нет среди допущенных' if near is None else str(near['price'])
        lines.append('| '+' | '.join((item['market'],item['secid'],str(report['reference']['price']),item['last_price_at'],nearby('nearest_above'),nearby('nearest_below')))+' |')
    lines.extend(['','Все шесть рынков проверены. Неполные 15-минутные группы исключены явно.','Календарная полнота и подлинность времён поставщика независимо не удостоверены.'])
    return '\n'.join(lines)


def publish(result, output_dir):
    root=Path(output_dir)
    root.mkdir(parents=True,exist_ok=False)
    for item in result['markets']:
        folder=root/item['market'];folder.mkdir()
        for name,payload in (("snapshot.json",item['snapshot']),("offline_repeat_record.json",item['repeat_record']),("report.json",item['report'])):
            (folder/name).write_bytes(canonical_bytes(payload)+b"\n")
        heading="Повторный расчёт на сохранённых реальных ценах. Не исходный выпуск прогноза и не текущие котировки.\n\n"
        (folder/'report.md').write_text(heading+render_linked_level_report(item['report'])+'\n',encoding='utf-8')
    (root/'result.json').write_bytes(canonical_bytes(result)+b"\n")
    (root/'summary.md').write_text(render_summary(result)+'\n',encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description="Повторная проверка отчёта по сохранённым реальным ценам")
    parser.add_argument('--archive',required=True);parser.add_argument('--expected-archive-sha256',required=True)
    parser.add_argument('--analysis-at',required=True);parser.add_argument('--output-dir',required=True)
    args=parser.parse_args()
    try:
        result=compute(args.archive,args.expected_archive_sha256,analysis_at=args.analysis_at)
        publish(result,args.output_dir)
    except (ValueError,KeyError,TypeError,OSError,zipfile.BadZipFile) as exc:
        parser.error(str(exc))
    print(render_summary(result))


if __name__=='__main__':main()
