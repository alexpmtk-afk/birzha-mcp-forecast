"""Actual resolver/parser/collector and native Google HTTP transport; network replaced only."""
from copy import deepcopy
from datetime import datetime,timedelta
import json
from urllib.parse import unquote
import httpx
import pytest
from birzha.application.google_first_archive import GoogleFirstArchive,MSK
from birzha.application.market_data import MarketDataService
from birzha.providers.moex_iss import MoexIssClient,IssResponse
from birzha.providers.moex_history import MoexHistoricalFutureResolver
from birzha.providers.moex_resolver import MoexDirectInstrumentResolver
from birzha.storage.google_archive_transport import GoogleArchiveTransport,AUDIT
from birzha.storage.google_sheets_bridge import GoogleSheetsBridge,INSTRUMENT_FOLDER_IDS
from test_google_first_archive import Book

SYMBOLS=('RI','Si','BR','GOLD','SBER','IMOEX')
CODES={'2025-01-15':('RIH5','SiH5','BRG5','GDH5','SBER','IMOEX'),'2026-10-29':('RIZ6','SiZ6','BRX6','GDZ6','SBER','IMOEX')}
def table(name,rows):
    keys=list(rows[0]) if rows else []
    return {name:{'columns':keys,'data':[[r[k] for k in keys] for r in rows]}}

class OfficialShape(MoexIssClient):
    def __init__(self,symbol,day):
        self.symbol=symbol;self.day=day;self.candle_requests=0;self.metadata_requests=[]
        self.code=CODES[day][SYMBOLS.index(symbol)]
    def _request(self,path,params):
        self.metadata_requests.append((path,dict(params)))
        if path.startswith('/history/'):
            assert params['assetcode']==('RTS' if self.symbol=='RI' else self.symbol)
            assert params['date']<self.day
            data=table('history',[{'TRADEDATE':params['date'],'SECID':self.code,'ASSETCODE':params['assetcode'],'BOARDID':'RFUD','VOLUME':100,'NUMTRADES':10,'LASTTRADEDATE':'2027-03-18'}])
        elif path==f'/securities/{self.symbol}.json':
            assert self.symbol in ('SBER','IMOEX')
            data=table('boards',[{'secid':self.symbol,'boardid':'TQBR' if self.symbol=='SBER' else 'SNDX','engine':'stock','market':'shares' if self.symbol=='SBER' else 'index','is_primary':1,'has_candles':1}])
        elif path.endswith('/candles.json'):
            assert '/'+self.code+'/' in path
            assert params['from']==params['till']==self.day
            self.candle_requests+=1
            start=datetime.fromisoformat(self.day+'T10:00:00')
            interval=params['interval'];n=15 if interval==1 else 1
            rows=[]
            for i in range(n):
                begin=start+timedelta(minutes=i)
                end=begin+timedelta(seconds=59 if interval==1 else 3599 if interval==60 else 50399)
                rows.append(dict(open=100,close=101,high=102,low=99,value=None if self.symbol=='IMOEX' else 10,volume=None if self.symbol=='IMOEX' else 5,begin=begin.isoformat(),end=end.isoformat()))
            data=table('candles',rows)
            data.update(table('candles.cursor',[{'INDEX':0,'TOTAL':n,'PAGESIZE':100}]))
        elif path.endswith(f'/securities/{self.symbol}.json'):
            data=table('securities',[{'SECID':self.symbol,'LOTSIZE':10,'MINSTEP':0.01,'CURRENCYID':'RUB'}])
        else:raise AssertionError(path)
        return IssResponse(200,{},json.dumps(data).encode())

def service(book,symbol,day):
    provider=OfficialShape(symbol,day)
    market=MarketDataService(provider,MoexDirectInstrumentResolver(provider),MoexHistoricalFutureResolver(provider))
    return GoogleFirstArchive(book,market,'a'*40,True,now=lambda:datetime(2026,11,1,tzinfo=MSK)),provider

@pytest.mark.parametrize('symbol',SYMBOLS)
@pytest.mark.parametrize('tf',('D1','H1','M15'))
@pytest.mark.parametrize('day',tuple(CODES))
def test_one_real_resolver_parser_pipeline_for_all_instruments(symbol,tf,day):
    book=Book();collector,provider=service(book,symbol,day)
    indicators=deepcopy(book.indicators)
    result=collector.collect(symbol=symbol,timeframe=tf,day=day,dry_run=False)
    assert result['status']=='READBACK_PASS' and result['added_rows']==1
    assert result['secid']==CODES[day][SYMBOLS.index(symbol)]
    before=deepcopy(book.data[tf]);calls=provider.candle_requests
    assert collector.collect(symbol=symbol,timeframe=tf,day=day,dry_run=False)['status']=='REUSED'
    assert calls==provider.candle_requests==1 and book.data[tf]==before
    assert book.indicators==indicators
    assert before[1][13]==('' if symbol=='IMOEX' else 75 if tf=='M15' else 5)

class GoogleREST:
    def __init__(self,symbol,tf):
        self.symbol,self.target=GoogleSheetsBridge.target_for_symbol(symbol)
        self.rows=Book().data
        self.rows.update({'SYNC_STATUS':[['key','value'],['google_first_scan_cursor|'+tf,'2025-01-15']],'QUALITY_AUDIT':[AUDIT],'CONTRACTS':[['symbol']]})
        self.sids={name:i for i,name in enumerate(self.rows,1)}
        self.calls=[]
    def __call__(self,request):
        self.calls.append((request.method,str(request.url)))
        assert request.headers['Authorization']=='Bearer isolated'
        path=unquote(request.url.path)
        if request.method=='GET' and request.url.host=='www.googleapis.com':
            return httpx.Response(200,json={'id':self.target.spreadsheet_id,'name':self.target.spreadsheet_name,'mimeType':'application/vnd.google-apps.spreadsheet','parents':[INSTRUMENT_FOLDER_IDS[self.symbol]],'trashed':False})
        if request.method=='GET' and '/values/' not in path:
            return httpx.Response(200,json={'spreadsheetId':self.target.spreadsheet_id,'sheets':[{'properties':{'title':name,'sheetId':sid,'sheetType':'GRID','gridProperties':{'rowCount':100,'columnCount':len(self.rows[name][0])}}} for name,sid in self.sids.items()]})
        if request.method=='GET':
            sheet=path.split('/values/')[1].split('!')[0].strip("'")
            return httpx.Response(200,json={'values':self.rows[sheet]})
        assert request.method=='POST' and path.endswith(':batchUpdate')
        for req in json.loads(request.content)['requests']:
            assert set(req)<= {'appendCells','updateCells'}
            op=req.get('appendCells') or req['updateCells'];sid=op.get('sheetId',op.get('start',{}).get('sheetId'))
            name=next(k for k,v in self.sids.items() if v==sid)
            converted=[[next(iter(cell.get('userEnteredValue',{}).values()),'') for cell in row['values']] for row in op['rows']]
            if 'appendCells' in req:self.rows[name].extend(converted)
            else:
                for offset,row in enumerate(converted):self.rows[name][op['start']['rowIndex']+offset]=row
        return httpx.Response(200,json={})

@pytest.mark.parametrize('symbol',SYMBOLS)
@pytest.mark.parametrize('tf',('D1','H1'))
def test_cold_native_google_coverage_scan_append_and_reopen_without_duckdb(symbol,tf):
    rest=GoogleREST(symbol,tf)
    def transport():return GoogleArchiveTransport(lambda:'isolated',transport=httpx.MockTransport(rest))
    collector,provider=service(transport(),symbol,'2025-01-15')
    assert collector.coverage(symbol=symbol,timeframes=(tf,))['timeframes'][tf]['scan_cursor']=='2025-01-15'
    result=collector.sync(symbol=symbol,from_date='2025-01-15',till_date='2025-01-15',timeframes=(tf,),dry_run=False,scan_mode=True)
    assert result['status']=='PASS' and result['scan_cursor']=='2025-01-16'
    # Entirely new process state: use the cloud receipt, never another MOEX call.
    collector,unused_source=service(transport(),symbol,'2025-01-15')
    assert collector.collect(symbol=symbol,timeframe=tf,day='2025-01-15',dry_run=False)['status']=='REUSED'
    assert provider.candle_requests==1 and unused_source.candle_requests==0
    assert len(rest.rows[tf])==2
