"""Bounded native Google archive transport: existing sheets + one ZIP/SECID/month.

No local files, no createSheet/copySheet/clear/delete endpoints, no write retry.
"""
import hashlib,json,time
from datetime import datetime,timezone
from urllib.parse import quote
import httpx
from birzha.storage.native_canonical_sheets import NativeCanonicalSheets
from birzha.storage.google_sheets_bridge import GoogleSheetsBridge,INSTRUMENT_FOLDER_IDS
from birzha.application.canonical_market_mirror import MirrorConflict,digest
from birzha.storage.google_m1_bytes import merge_zip,validate_zip
from birzha.storage.monthly_m1_source import MAX_SOURCE_BYTES

INDEX=['contract_secid','month','first_m1_msk','last_m1_msk','m1_rows','volume_contracts','source_file_id','source_archive','zip_sha256','ohlcv_integrity','limitations']
AUDIT=['audit_id','scope','status','description','checked_at','evidence_ref','affected_rows','before_sha','after_sha','limitation','next_action','owner','source_sheet','source_payload_json']

class GoogleArchiveTransport(NativeCanonicalSheets):
    def _validate_named_target(self,symbol,sheet):
        # Base transport admits only its fixed allowlist. Extend it narrowly by
        # validating the same pinned book/folder/grid contract for new service tabs.
        if sheet not in ('QUALITY_AUDIT','CONTRACTS','M1_SOURCE_INDEX'):
            return super()._validate_named_target(symbol,sheet)
        symbol,target=GoogleSheetsBridge.target_for_symbol(symbol)
        meta=self._request('GET',f'https://www.googleapis.com/drive/v3/files/{target.spreadsheet_id}',params={'fields':'id,name,mimeType,parents,trashed'})
        if meta!={'id':target.spreadsheet_id,'name':target.spreadsheet_name,'mimeType':'application/vnd.google-apps.spreadsheet','parents':[INSTRUMENT_FOLDER_IDS[symbol]],'trashed':False}:raise MirrorConflict('CANONICAL_BOOK_IDENTITY_MISMATCH')
        book=self._request('GET',f'https://sheets.googleapis.com/v4/spreadsheets/{target.spreadsheet_id}',params={'fields':'spreadsheetId,sheets.properties'})
        found=[s['properties'] for s in book.get('sheets',[]) if s.get('properties',{}).get('title')==sheet]
        if book.get('spreadsheetId')!=target.spreadsheet_id or len(found)!=1:raise MirrorConflict('EXISTING_SERVICE_SHEET_REQUIRED')
        p=found[0];g=p.get('gridProperties',{})
        if p.get('sheetType','GRID')!='GRID' or not 1<=g.get('rowCount',0)<=100000 or not 1<=g.get('columnCount',0)<=40:raise MirrorConflict('SERVICE_GRID_UNSUPPORTED')
        self._sheets[(symbol,sheet)]=p

    def validate_service_target(self,symbol,sheet):
        if sheet not in ('SYNC_STATUS','QUALITY_AUDIT','CONTRACTS','M1_SOURCE_INDEX'):raise MirrorConflict('ARCHIVE_SERVICE_SHEET_UNSUPPORTED')
        self._validate_named_target(symbol,sheet)

    def append_quality(self,symbol,event):
        self.validate_service_target(symbol,'QUALITY_AUDIT');old=self.read_service_rows(symbol,'QUALITY_AUDIT')
        if not old or old[0]!=AUDIT:raise MirrorConflict('QUALITY_AUDIT_SCHEMA_MISMATCH')
        row=[event.get(k,'') for k in AUDIT]
        prior=[r for r in old[1:] if r[0]==row[0]]
        if prior:
            if prior!=[row]:raise MirrorConflict('QUALITY_AUDIT_KEY_CONFLICT')
            return
        self.append_rows(symbol,'QUALITY_AUDIT',[row])
        if self.read_service_rows(symbol,'QUALITY_AUDIT')!=old+[row]:raise MirrorConflict('QUALITY_AUDIT_READBACK_FAILED')

    def _media(self,method,url,*,body=None,content_type=None):
        if body is not None and len(body)>MAX_SOURCE_BYTES:raise MirrorConflict('ZIP_TRANSFER_BOUND')
        if not isinstance(self._transport,httpx.MockTransport):time.sleep(max(0,1.1-(time.monotonic()-self._last_request)))
        self._last_request=time.monotonic()
        try:
            headers={'Authorization':'Bearer '+self._access_token()}
            if content_type:headers['Content-Type']=content_type
            with httpx.Client(transport=self._transport,timeout=60,follow_redirects=False) as c:
                r=c.request(method,url,content=body,headers=headers)
        except Exception:raise MirrorConflict('DRIVE_MEDIA_TRANSPORT_FAILED_NO_RETRY') from None
        if not r.is_success or len(r.content)>MAX_SOURCE_BYTES:raise MirrorConflict('DRIVE_MEDIA_REFUSED_OR_TOO_LARGE')
        return r.content

    def _folder(self,symbol):
        symbol,_=GoogleSheetsBridge.target_for_symbol(symbol)
        root=INSTRUMENT_FOLDER_IDS[symbol]
        r=self._request('GET','https://www.googleapis.com/drive/v3/files',params={'q':f"'{root}' in parents and name = '01_M1_SOURCE' and mimeType = 'application/vnd.google-apps.folder' and trashed = false",'fields':'files(id,name,parents),nextPageToken','pageSize':100})
        files=r.get('files',[])
        if r.get('nextPageToken') or len(files)!=1 or files[0].get('parents')!=[root]:raise MirrorConflict('EXISTING_UNIQUE_M1_SOURCE_FOLDER_REQUIRED')
        return files[0]['id']

    def validate_m1(self,symbol,secid,month):
        self.validate_service_target(symbol,'M1_SOURCE_INDEX')
        rows=self.read_service_rows(symbol,'M1_SOURCE_INDEX')
        if not rows or rows[0]!=INDEX:raise MirrorConflict('M1_INDEX_SCHEMA_MISMATCH')
        keys=[tuple(r[:2]) for r in rows[1:] if any(x!='' for x in r)]
        if len(keys)!=len(set(keys)):raise MirrorConflict('M1_INDEX_DUPLICATE_KEYS')
        return self._folder(symbol)

    def _file(self,symbol,secid,month):
        folder=self.validate_m1(symbol,secid,month)
        name=f'{symbol}_{secid}_{month}_SOURCE_M1_OHLCV.zip'
        r=self._request('GET','https://www.googleapis.com/drive/v3/files',params={'q':f"'{folder}' in parents and name = '{name}' and trashed = false",'fields':'files(id,name,mimeType,parents,headRevisionId),nextPageToken','pageSize':100})
        files=r.get('files',[])
        if r.get('nextPageToken') or len(files)>1:raise MirrorConflict('DUPLICATE_MONTHLY_M1_FILES')
        if files and (files[0].get('parents')!=[folder] or files[0].get('mimeType')!='application/zip'):raise MirrorConflict('M1_FILE_IDENTITY_MISMATCH')
        return folder,name,(files[0] if files else None)

    def read_m1(self,symbol,secid,month):
        folder,name,f=self._file(symbol,secid,month)
        rows=self.read_service_rows(symbol,'M1_SOURCE_INDEX');matches=[r for r in rows[1:] if r[:2]==[secid,month]]
        if not f:
            if matches:raise MirrorConflict('M1_INDEX_FILE_MISSING')
            return None
        if matches and matches[0][6]!=f['id']:raise MirrorConflict('M1_INDEX_FILE_ID_MISMATCH')
        body=self._media('GET',f"https://www.googleapis.com/drive/v3/files/{f['id']}?alt=media")
        sha=hashlib.sha256(body).hexdigest();validate_zip(body,symbol,secid,month,matches[0][8] if matches else None)
        return {'body':body,'sha256':sha,'file_id':f['id'],'head':f.get('headRevisionId'),'indexed':bool(matches)}

    def publish_m1(self,symbol,secid,month,rows):
        folder,name,f=self._file(symbol,secid,month);old=self.read_m1(symbol,secid,month)
        body,changed=merge_zip(old['body'] if old else None,rows,symbol,secid,month)
        sha=hashlib.sha256(body).hexdigest()
        if changed:
            if f:
                if not old.get('head'):raise MirrorConflict('OLD_REVISION_ID_REQUIRED')
                # Keep old source revision before changing the same canonical file.
                self._request('PATCH',f"https://www.googleapis.com/drive/v3/files/{f['id']}/revisions/{old['head']}",json={'keepForever':True})
                fresh=self._request('GET',f"https://www.googleapis.com/drive/v3/files/{f['id']}",params={'fields':'headRevisionId'})
                if fresh.get('headRevisionId')!=old['head']:raise MirrorConflict('M1_CONCURRENT_REVISION')
                self._media('PATCH',f"https://www.googleapis.com/upload/drive/v3/files/{f['id']}?uploadType=media&keepRevisionForever=true",body=body,content_type='application/zip')
                file_id=f['id']
            else:
                boundary='birzha_m1_upload'
                metadata=json.dumps({'name':name,'mimeType':'application/zip','parents':[folder]}).encode()
                multi=b'--'+boundary.encode()+b'\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n'+metadata+b'\r\n--'+boundary.encode()+b'\r\nContent-Type: application/zip\r\n\r\n'+body+b'\r\n--'+boundary.encode()+b'--\r\n'
                answer=self._media('POST','https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id',body=multi,content_type='multipart/related; boundary='+boundary)
                file_id=json.loads(answer)['id']
        else:file_id=old['file_id']
        actual=self._media('GET',f'https://www.googleapis.com/drive/v3/files/{file_id}?alt=media')
        if actual!=body:raise MirrorConflict('M1_ZIP_READBACK_FAILED')
        minutes=validate_zip(actual,symbol,secid,month,sha)
        before=self.read_service_rows(symbol,'M1_SOURCE_INDEX');matches=[(i,r) for i,r in enumerate(before[1:],1) if r[:2]==[secid,month]]
        volumes=[r.get('volume') for r in minutes]
        row=[secid,month,minutes[0]['begin'],minutes[-1]['begin'],len(minutes),sum(volumes) if all(v is not None for v in volumes) else '',file_id,f'https://drive.google.com/file/d/{file_id}/view',sha,'OHLCV_VALIDATED','SESSION_AND_PIT_UNVERIFIED']
        if matches:
            i,prior=matches[0];row[7]=prior[7] # preserve existing hyperlink formula
            edits=[(i,j,v) for j,v in enumerate(row) if j!=7 and v!=prior[j]]
            if edits:self._patch_index(symbol,edits)
            expected=[r[:] for r in before];expected[i]=row
        else:
            self.append_rows(symbol,'M1_SOURCE_INDEX',[row]);expected=before+[row]
        if self.read_service_rows(symbol,'M1_SOURCE_INDEX')!=expected:raise MirrorConflict('M1_INDEX_READBACK_FAILED')
        return {'file_id':file_id,'sha256':sha,'changed':changed}

    def _patch_index(self,symbol,edits):
        _,target=GoogleSheetsBridge.target_for_symbol(symbol);sid=self._sheets[(symbol,'M1_SOURCE_INDEX')]['sheetId']
        def cell(v):return {'userEnteredValue':{'numberValue':v}} if type(v) in (int,float) else {'userEnteredValue':{'stringValue':str(v)}}
        self._request('POST',f'https://sheets.googleapis.com/v4/spreadsheets/{target.spreadsheet_id}:batchUpdate',json={'requests':[{'updateCells':{'start':{'sheetId':sid,'rowIndex':r,'columnIndex':c},'rows':[{'values':[cell(v)]}],'fields':'userEnteredValue'}} for r,c,v in edits]})
