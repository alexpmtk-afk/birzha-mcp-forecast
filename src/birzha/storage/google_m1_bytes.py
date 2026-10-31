"""In-memory monthly M1 bridge, reusing PR289 validation and deterministic ZIP format."""
from dataclasses import dataclass
import json,hashlib
from birzha.storage.monthly_m1_source import _month_data,_package,_read_zip_bytes
from birzha.application.canonical_market_mirror import MirrorConflict

@dataclass
class MemoryZip:
    body: bytes
    def read_bytes(self):return self.body

def validate_zip(body,symbol,secid,month,expected_sha=None):
    if expected_sha and hashlib.sha256(body).hexdigest()!=expected_sha:raise MirrorConflict('M1_ZIP_HASH_MISMATCH')
    payload,manifest,_=_read_zip_bytes(body)
    if payload.get('symbol')!=symbol or payload.get('exact_secid')!=secid or manifest.get('secid')!=secid:raise MirrorConflict('M1_IDENTITY_MISMATCH')
    # Run the same bounded validator as the existing offline monthly builder.
    _,_,_,_,doc=_month_data([MemoryZip(body)],symbol,secid,month,symbol!='IMOEX')
    if len(doc['raw']['M1'])!=len(payload['raw']['M1']):raise MirrorConflict('M1_MONTH_SCOPE_MISMATCH')
    return doc['raw']['M1']

def merge_zip(old,rows,symbol,secid,month):
    old_rows=validate_zip(old,symbol,secid,month) if old else []
    def key(r):return r['begin']
    from birzha.storage.monthly_m1_source import _clock
    normalized=[]
    for row in rows:
        r={k:row.get(k) for k in ('begin','end','open','high','low','close','volume','value')};r['begin']=_clock(r['begin']).isoformat(timespec='seconds');r['end']=_clock(r['end']).isoformat(timespec='seconds');normalized.append(r)
    by={key(r):r for r in old_rows}
    for r in normalized:
        if key(r) in by and by[key(r)]!=r:raise MirrorConflict('M1_REVISION_REQUIRES_REVIEW')
        by[key(r)]=r
    if old and list(sorted(by.values(),key=key))==old_rows:return old,False
    doc={'symbol':symbol,'exact_secid':secid,'source':'OFFICIAL_MOEX_ISS','raw':{'M1':list(by.values())}}
    payload=json.dumps(doc,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
    manifest=json.dumps({'secid':secid,'payload_sha256':hashlib.sha256(payload).hexdigest()}).encode()
    capture=_package(payload,manifest)
    payload,manifest,_,_,_=_month_data([MemoryZip(capture)],symbol,secid,month,symbol!='IMOEX')
    return _package(payload,manifest),True
