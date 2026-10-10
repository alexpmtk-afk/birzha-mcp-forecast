"""Synthetic archives only: source labels are claims, not provider authentication."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import zipfile
import pytest
from test_g2_freeze_real_forecast_from_source_receipts import source_fixture
from scripts.g2_real_source_level_report import compute, publish, MARKETS

AT='2026-10-10T20:00:00Z'


def archive(tmp_path,mode=None):
    entries=[];payloads={};listed=[]
    for market in MARKETS:
        root=tmp_path/market
        entry=source_fixture(root)
        entry['market']=market;entry['resolved_secid']=market
        entry['instrument']['symbol']=market;entry['instrument']['secid']=market
        entry['instrument']['data_capabilities']=['CANDLES','TRADING_CALENDAR','VOLUME']
        for tf,meta in entry['timeframes'].items():
            meta['exact_secid']=market
            for page in meta['pages']:
                original=page['path'];name=original.replace('SBER_SBER_',market+'_'+market+'_')
                page['path']=name
                payloads[name]=(root/original).read_bytes()
                listed.append({'name':name,'sha256':page['sha256']})
        entries.append(entry)
    manifest={'schema':'G2_SIX_MARKET_LIVE_SOURCE_RECEIPT_V1','origin':'REAL_MOEX_PUBLIC_ISS_CAPTURE',
              'requested_markets':list(MARKETS),'complete_markets':list(MARKETS),'failed_markets':[],
              'capture_started_utc':'2026-10-10T15:00:00Z','capture_completed_utc':'2026-10-10T15:00:10Z',
              'instruments':entries,'all_payload_files':listed}
    if mode=='missing_market':manifest['instruments'].pop()
    if mode=='duplicate_market':manifest['instruments'][-1]=deepcopy(entries[0])
    if mode=='missing_complete':manifest['complete_markets'].pop()
    if mode=='failed':manifest['failed_markets']=['SBER']
    if mode=='page_sha':payloads[next(iter(payloads))]=b'{}'
    if mode=='wrong_secid':entries[0]['resolved_secid']='OTHER'
    if mode=='missing_capability':entries[0]['instrument']['data_capabilities']=[]
    if mode=='future_receipt':entries[0]['timeframes']['D1']['pages'][0]['observed_end_utc']='2026-10-11T15:00:00Z'
    if mode=='reversed_capture':manifest['capture_started_utc']='2026-10-11T00:00:00Z'
    if mode=='wrong_tf':entries[0]['timeframes']['D1']['timeframe']='H1'
    if mode=='extra_page':payloads['extra.json']=b'{}'
    if mode=='unbound':entries[0]['timeframes']['D1']['pages']=[]
    if mode=='duplicate_inventory':listed.append(deepcopy(listed[0]))
    body=json.dumps(manifest).encode()
    if mode=='duplicate_json':body=body.replace(b'{',b'{"schema":"bad",',1)
    path=tmp_path/'source.zip'
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('manifest.json',body)
        for name,payload in payloads.items():z.writestr(name,payload)
        if mode=='traversal':z.writestr('../outside.json',b'{}')
    return path,hashlib.sha256(path.read_bytes()).hexdigest()


def test_six_reports_preserve_sources_and_explicit_repeat_mode(tmp_path):
    path,sha=archive(tmp_path);before=path.read_bytes()
    result=compute(path,sha,analysis_at=AT)
    assert len(result['markets'])==6
    assert result['prospective_issuance'] is False and result['predictive_quality_measured'] is False
    assert result['provider_authentication']=='NOT_INDEPENDENTLY_ATTESTED'
    for item in result['markets']:
        assert item['snapshot']['as_of']==AT
        assert item['report']['after_t0'] is None
        assert item['report']['at_t0']['reference']['price'] is not None
        assert item['repeat_record']['created_at_t0']==AT
        assert item['price_age_seconds']>0
    assert path.read_bytes()==before
    assert result==compute(path,sha,analysis_at=AT)


@pytest.mark.parametrize('mode',['missing_market','duplicate_market','missing_complete','failed','page_sha','wrong_secid','future_receipt','reversed_capture','wrong_tf','extra_page','unbound','duplicate_inventory','duplicate_json','traversal','missing_capability'])
def test_corruption_inventory_and_time_fail_closed(tmp_path,mode):
    path,sha=archive(tmp_path,mode)
    with pytest.raises((ValueError,KeyError)):compute(path,sha,analysis_at=AT)


def test_wrong_archive_pin_refused(tmp_path):
    path,_=archive(tmp_path)
    with pytest.raises(ValueError,match='SHA256'):compute(path,'0'*64,analysis_at=AT)


@pytest.mark.parametrize('at',['2026-10-09T20:00:00Z','2026-10-10T20:00:00'])
def test_backdated_or_unknown_analysis_time_refused(tmp_path,at):
    path,sha=archive(tmp_path)
    with pytest.raises(ValueError):compute(path,sha,analysis_at=at)


def test_publish_new_folder_only_and_recheck_native_command(tmp_path):
    from scripts.g2_linked_level_report import run
    path,sha=archive(tmp_path);result=compute(path,sha,analysis_at=AT)
    dest=tmp_path/'reports';publish(result,dest)
    assert (dest/'summary.md').is_file()
    item=result['markets'][0]
    assert run(dest/item['market']/'snapshot.json',dest/item['market']/'offline_repeat_record.json')==item['report']
    before=(dest/'result.json').read_bytes()
    with pytest.raises(FileExistsError):publish(result,dest)
    assert (dest/'result.json').read_bytes()==before
