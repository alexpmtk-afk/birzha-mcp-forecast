from copy import deepcopy
import hashlib,json
import pytest
from birzha.storage.google_archive_transport import GoogleArchiveTransport,INDEX
from birzha.storage.google_m1_bytes import merge_zip
from test_google_first_archive import Market
from dataclasses import asdict
from datetime import date

class Drive(GoogleArchiveTransport):
    def __init__(self):
        self.files={};self.index=[INDEX[:]];self.calls=[];self._sheets={('RI','M1_SOURCE_INDEX'):{'sheetId':12}};self.revision='r1'
    def validate_m1(self,*a):return 'folder'
    def _file(self,s,sec,m):
        return 'folder',f'{s}_{sec}_{m}_SOURCE_M1_OHLCV.zip',({'id':'zip-id','headRevisionId':self.revision} if self.files else None)
    def read_service_rows(self,s,sheet):return deepcopy(self.index)
    def append_rows(self,s,t,rows):self.index.extend(deepcopy(rows))
    def _request(self,method,url,**kw):
        self.calls.append((method,url,kw))
        if method=='GET':return {'headRevisionId':self.revision}
        for r in kw.get('json',{}).get('requests',[]):
            u=r['updateCells'];row=u['start']['rowIndex'];col=u['start']['columnIndex'];v=u['rows'][0]['values'][0]['userEnteredValue'];self.index[row][col]=next(iter(v.values()))
        return {}
    def _media(self,method,url,**kw):
        self.calls.append((method,url,{}))
        if method=='GET':return self.files['zip-id']
        if method=='POST':
            body=kw['body'];start=body.index(b'PK\x03\x04');end=body.rfind(b'\r\n--birzha_m1_upload--');self.files['zip-id']=body[start:end];return b'{"id":"zip-id"}'
        self.files['zip-id']=kw['body'];self.revision='r2';return b'{"id":"zip-id"}'

def minutes():
    m=Market();ins=m.resolve('RI',date(2026,10,29));return [asdict(c) for c in m.candles_for_instrument(ins,timeframe='M1',from_date='2026-10-29',till_date='2026-10-29').candles]

def test_native_zip_upload_readback_catalog_repeat_then_same_id_revision():
    d=Drive();rows=minutes();first=d.publish_m1('RI','RIZ6','2026-10',rows)
    assert first['changed'] and len(d.index)==2
    uploads=sum(m in ('POST','PATCH') and '/upload/' in u for m,u,_ in d.calls)
    assert d.publish_m1('RI','RIZ6','2026-10',rows)['changed'] is False
    assert sum(m in ('POST','PATCH') and '/upload/' in u for m,u,_ in d.calls)==uploads
    extra=dict(rows[-1]);extra['begin']='2026-10-29T10:15:00+03:00';extra['end']='2026-10-29T10:15:59+03:00'
    second=d.publish_m1('RI','RIZ6','2026-10',rows+[extra])
    assert second['file_id']==first['file_id'] and len(d.index)==2 and d.index[1][4]==16
    assert any(m=='PATCH' and '/revisions/r1' in u and kw['json']['keepForever'] is True for m,u,kw in d.calls)
    assert d.index[1][8]==hashlib.sha256(d.files['zip-id']).hexdigest()

def test_index_checksum_mismatch_never_republishes():
    d=Drive();rows=minutes();d.publish_m1('RI','RIZ6','2026-10',rows);d.index[1][8]='f'*64
    count=len(d.calls)
    with pytest.raises(Exception,match='HASH_MISMATCH'):d.publish_m1('RI','RIZ6','2026-10',rows)
    assert not any(m in ('POST','PATCH') for m,u,k in d.calls[count:])
