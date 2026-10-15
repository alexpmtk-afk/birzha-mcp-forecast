"""One bounded BR D1 MOEX source continuation, isolated from installed HOME.
Read-only source capture for 13 dates; no history sync, no forecasting.
"""
from __future__ import annotations
import csv,hashlib,json
from datetime import date
from pathlib import Path
from birzha.application.market_data import MarketDataService

TARGET={
 "2026-09-28":"BRV6","2026-09-29":"BRV6","2026-09-30":"BRX6",
 "2026-10-01":"BRX6","2026-10-02":"BRX6","2026-10-05":"BRX6",
 "2026-10-06":"BRX6","2026-10-07":"BRX6","2026-10-08":"BRX6",
 "2026-10-09":"BRX6","2026-10-12":"BRX6","2026-10-13":"BRX6",
 "2026-10-14":"BRX6"
}
def run():
 dest=Path("br_d1_source_20260928_20261014")
 dest.mkdir(exist_ok=False)
 market=MarketDataService.default()
 rows=[];receipts=[]
 for day,expected_secid in TARGET.items():
  inst=market.resolve("BR",as_of=date.fromisoformat(day))
  if inst.secid!=expected_secid or inst.symbol!="BR" or inst.asset_class!="future":
   raise RuntimeError(f"Contract mismatch {day}: got {inst.secid}, expected {expected_secid}")
  series=market.candles_for_instrument(inst,timeframe="D1",from_date=day,till_date=day,completed_only=True)
  candles=[c for c in series.candles if c.begin[:10]==day and c.completed is True]
  if len(candles)!=1:raise RuntimeError(f"Expected exactly one completed BR D1: {day}, got {len(candles)}")
  c=candles[0]
  if not(0<c.low<=min(c.open,c.close)<=max(c.open,c.close)<=c.high):raise RuntimeError(f"Bad OHLC {day}")
  if c.volume is not None and c.volume<0:raise RuntimeError(f"Bad volume {day}")
  key="BR|D1|"+inst.secid+"|"+c.begin
  row=[key,day,"","",c.begin,c.end,"","UNKNOWN",inst.secid,c.open,c.high,c.low,c.close,c.volume,"",c.value,"TRUE","D1",1,"MOEX_ISS"]
  assert len(row)==20
  rows.append(row)
  receipts.append({"secid":inst.secid,"date":day,"start":c.begin,"end":c.end,"source":"MOEX_ISS","calendar_attested":False,"pit_attested":False})
 with (dest/"verified_d1_rows.csv").open("w",newline="",encoding="utf-8") as fh:csv.writer(fh).writerows(rows)
 (dest/"source_manifest.json").write_text(json.dumps({"instrument":"BR","timeframe":"D1","range":["2026-09-28","2026-10-14"],"expected_rows":len(TARGET),"actual_rows":len(rows),"records":receipts,"calendar":"NOT_ATTESTED","first_receipt":"UNKNOWN","production_changes":"NONE"},indent=2,ensure_ascii=False)+"\n")
 with (dest/"SHA256SUMS.txt").open("w") as fh:
  for name in ["verified_d1_rows.csv","source_manifest.json"]:
   sha=hashlib.sha256((dest/name).read_bytes()).hexdigest();fh.write(f"{sha}  {name}\n")
 print(json.dumps({"status":"D1_SOURCE_COLLECTED","rows":len(rows),"contracts":sorted({r[8] for r in rows}),"first":rows[0][0],"last":rows[-1][0]}))
if __name__=="__main__":run()
