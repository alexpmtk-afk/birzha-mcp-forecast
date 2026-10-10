"""Repeat bounded profile research from pinned original saved MOEX pages."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
from birzha.application.candle_profile_research import build_candle_profile_research, render_candle_profile_research
from birzha.application.level_reaction import canonical_bytes, _receipt_time
from birzha.domain.market import Instrument
if __package__ in (None, ""):
    from g2_real_source_level_report import read_source_archive
    from g2_freeze_real_forecast_from_source_receipts import source_series
else:
    from scripts.g2_real_source_level_report import read_source_archive
    from scripts.g2_freeze_real_forecast_from_source_receipts import source_series


def compute(archive_path, expected_archive_sha256, *, analysis_at):
    analysis=_receipt_time(analysis_at)
    with tempfile.TemporaryDirectory(prefix='birzha-profile-research-') as temp:
        root=Path(temp)
        manifest,manifest_sha=read_source_archive(archive_path,expected_archive_sha256,root)
        start,end=_receipt_time(manifest['capture_started_utc']),_receipt_time(manifest['capture_completed_utc'])
        if not start <= end <= analysis:raise ValueError('capture interval exceeds research analysis')
        results=[]
        for entry in sorted(manifest['instruments'],key=lambda e:('SBER','Si','BR','GOLD','IMOEX','RTSI').index(e['market'])):
            if entry['status']!='SOURCE_CAPTURE_COMPLETE':raise ValueError('incomplete source market')
            instrument=Instrument(**entry['instrument'])
            if instrument.symbol!=entry['market'] or instrument.secid!=entry['resolved_secid']:raise ValueError('source instrument mismatch')
            series,pages,seen=source_series(root,entry,'H1',instrument)
            for page in entry['timeframes']['H1']['pages']:
                if not start<=_receipt_time(page['observed_start_utc'])<=_receipt_time(page['observed_end_utc'])<=end:raise ValueError('receipt outside capture')
            report=build_candle_profile_research(series,analysis_at=analysis_at,source_observed_at=seen.isoformat())
            results.append({'market':entry['market'],'source_pages':[{'name':name,'sha256':hashlib.sha256(body).hexdigest()} for name,body in pages], 'report':report})
    body={'version':'SIX_MARKET_CANDLE_PROFILE_RESEARCH_V1','archive_sha256':expected_archive_sha256,
        'manifest_sha256':manifest_sha,'analysis_at':analysis_at,'markets':results,
        'mode':'OFFLINE_RESEARCH_NOT_ISSUANCE','old_records_mutated':False,'predictive_quality':'NOT_MEASURED'}
    return {'report_id':'six_profiles_'+hashlib.sha256(canonical_bytes(body)).hexdigest(),**body}


def render(result):
    lines=['# Исследовательский приблизительный профиль по шести рынкам','',
        'Сохранённый архив, отдельный расчёт; это не новый прогноз и не текущие котировки.']
    for row in result['markets']:
        lines.extend(['','## '+row['market'],'',render_candle_profile_research(row['report'])])
    return '\n'.join(lines)


def main():
    p=argparse.ArgumentParser(description='Исследовательский профиль на исходных сохранённых свечах')
    p.add_argument('--archive',required=True);p.add_argument('--archive-sha256',required=True)
    p.add_argument('--analysis-at',required=True);p.add_argument('--format',choices=('text','json'),default='text')
    args=p.parse_args()
    try:r=compute(args.archive,args.archive_sha256,analysis_at=args.analysis_at)
    except (TypeError,ValueError,KeyError,OverflowError,OSError) as exc:p.error(str(exc))
    print(render(r) if args.format=='text' else json.dumps(r,ensure_ascii=False,allow_nan=False,indent=2))


if __name__=='__main__':main()
