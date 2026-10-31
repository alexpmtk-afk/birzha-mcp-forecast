"""Read verified, configured RI minute archives; no download or extraction."""
from __future__ import annotations
from collections import defaultdict
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib
import json
import math
import re
import zipfile
from birzha.application.model3_features import number, sha

MOSCOW=ZoneInfo("Europe/Moscow")
def clock(value):
    dt=datetime.fromisoformat(str(value).replace("Z","+00:00"))
    return (dt.replace(tzinfo=MOSCOW) if dt.tzinfo is None else dt.astimezone(MOSCOW)).strftime("%Y-%m-%d %H:%M:%S")

def load_minutes(directory, from_date, till_date):
    if not directory:return {}, "UNAVAILABLE_NOT_CONFIGURED"
    folder=Path(directory)
    if not folder.is_dir():raise ValueError("configured minute archive directory unavailable")
    found={};total=0;files=sorted(folder.glob("*.zip"))
    if len(files)>240:raise ValueError("minute archive file bound")
    for path in files:
        with zipfile.ZipFile(path) as z:
            names=z.namelist(); payloads=[n for n in names if n.endswith("source_and_derived.json")]; manifests=[n for n in names if n.endswith("manifest.json")]
            if len(payloads)!=1 or len(manifests)!=1:raise ValueError("minute archive manifest/payload ambiguity")
            if z.getinfo(payloads[0]).file_size>128_000_000 or z.getinfo(manifests[0]).file_size>1_000_000:raise ValueError("minute archive size bound")
            body=z.read(payloads[0]);total+=len(body)
            if total>512_000_000:raise ValueError("minute archive total size bound")
            d=json.loads(body);m=json.loads(z.read(manifests[0]))
            if m.get("archive_sha256") not in {hashlib.sha256(body).hexdigest(),hashlib.sha256(json.dumps(d,sort_keys=True,separators=(",",":")).encode()).hexdigest()}:raise ValueError("minute archive SHA mismatch")
            sec=d.get("exact_secid")
            if not re.fullmatch(r"RI[FGHJKMNQUVXZ]\d{1,2}",str(sec)) or m.get("secid")!=sec:raise ValueError("minute archive exact RI identity")
            # Keep earlier records as causal sign context, never materialize future outputs.
            for r in d.get("raw",{}).get("M1",[]):
                t=clock(r["begin"])
                if t[:10]>till_date:continue
                if not all(number(r.get(k)) for k in ("open","high","low","close","volume")):raise ValueError("invalid minute OHLCV")
                if not r["low"]<=min(r["open"],r["close"])<=max(r["open"],r["close"])<=r["high"] or r["volume"]<0:raise ValueError("inconsistent minute")
                key=(sec,t)
                entry={**r,"begin":t,"source_archive_sha256":m["archive_sha256"]}
                if key in found and any(found[key][k]!=entry[k] for k in ("open","high","low","close","volume","end")):raise ValueError("conflicting minute revision")
                found[key]=entry
                if len(found)>500_000:raise ValueError("minute row bound")
    return found,"HASH_VERIFIED_RECONSTRUCTED_NOT_PIT"

def minute_groups(minutes, from_date, till_date):
    groups=defaultdict(list);previous={}
    for (sec,t),r in sorted(minutes.items()):
        prev=previous.get(sec);sign=0
        if prev and prev[0][:10]==t[:10]:sign=(r["close"]>prev[1])-(r["close"]<prev[1])
        previous[sec]=(t,r["close"])
        if not from_date<=t[:10]<=till_date:continue
        for tf,bucket in (("H1",t[:13]+":00:00"),("M15",t[:14]+f"{int(t[14:16])//15*15:02d}"+":00")):
            groups[(sec,tf,bucket)].append((t,r,sign*r["volume"]))
    return groups

def aggregate(rs):
    rows=sorted(rs,key=lambda x:x[0])
    return dict(open=rows[0][1]["open"],high=max(x[1]["high"] for x in rows),low=min(x[1]["low"] for x in rows),close=rows[-1][1]["close"],volume=sum(x[1]["volume"] for x in rows),count=len(rows))

def profiles(groups, targets):
    result={}
    for key,rs in groups.items():
        if key not in targets:continue
        a=aggregate(rs);hist=defaultdict(float)
        for t,r,delta in rs:
            lo=math.floor(r["low"]/100);hi=math.floor(r["high"]/100)
            if hi-lo>10000:raise ValueError("profile bin bound")
            for q in range(lo,hi+1):hist[q]+=r["volume"]/(hi-lo+1)
        if not a["volume"]:continue
        if not math.isclose(sum(hist.values()),a["volume"],rel_tol=1e-10,abs_tol=1e-7):raise ValueError("profile volume conservation")
        poc=min(hist,key=lambda q:(-hist[q],q));lo=hi=poc;mass=hist[poc]
        while mass<a["volume"]*.7-1e-7:
            lv=hist.get(lo-1,-1);rv=hist.get(hi+1,-1)
            if lv<0 and rv<0:break
            if lv>=rv:lo-=1;mass+=hist.get(lo,0)
            else:hi+=1;mass+=hist.get(hi,0)
        result[key]={**a,"delta":sum(x[2] for x in rs),"poc":poc*100+50,"val":lo*100,"vah":(hi+1)*100,
            "hvn":[q*100+50 for q in sorted(hist) if hist[q]>hist.get(q-1,-1) and hist[q]>hist.get(q+1,-1)],
            "lvn":[q*100+50 for q in sorted(hist) if q-1 in hist and q+1 in hist and hist[q]<hist[q-1] and hist[q]<hist[q+1]],
            "vwap":sum((x[1]["high"]+x[1]["low"]+x[1]["close"])/3*x[1]["volume"] for x in rs)/a["volume"]}
    return result
