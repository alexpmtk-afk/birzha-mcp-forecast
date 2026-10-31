"""Content-addressed RI M1 capture on the existing governed provider path."""
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import re
import uuid
import zipfile

class MinuteArchiveCapture:
    def __init__(self,directory):self.directory=Path(directory)
    def __call__(self,instrument,candles,from_date,till_date):
        if not re.fullmatch(r"RI[FGHJKMNQUVXZ]\d{1,2}",instrument.secid):return
        if not candles:return
        raw=[asdict(c) for c in candles]
        payload={"symbol":"RI","exact_secid":instrument.secid,"source":"OFFICIAL_MOEX_ISS",
            "range":[from_date,till_date],"historical_first_seen":"UNKNOWN","raw":{"M1":raw}}
        body=json.dumps(payload,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
        content_sha=hashlib.sha256(body).hexdigest()
        self.directory.mkdir(parents=True,exist_ok=True)
        target=self.directory/(instrument.secid+"_"+content_sha+".zip")
        if target.exists():
            with zipfile.ZipFile(target) as z:
                if hashlib.sha256(z.read("source_and_derived.json")).hexdigest()!=content_sha:raise ValueError("existing minute archive corrupted")
            return
        manifest={"secid":instrument.secid,"archive_sha256":content_sha,"range":[from_date,till_date],
            "captured_at_utc":datetime.now(timezone.utc).isoformat(),"historical_first_available_at":"UNKNOWN",
            "source":"EXISTING_GOVERNED_MOEX_PROVIDER_M1"}
        temporary=self.directory/("capture_"+uuid.uuid4().hex+".tmp")
        try:
            with zipfile.ZipFile(temporary,"w",compression=zipfile.ZIP_DEFLATED) as z:
                z.writestr("source_and_derived.json",body)
                z.writestr("manifest.json",json.dumps(manifest,separators=(",",":")))
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
