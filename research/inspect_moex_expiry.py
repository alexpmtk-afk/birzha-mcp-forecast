"""Research-only diagnosis of public official ISS expiry metadata for an expired future."""
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen

secid = "GDM6"
specs = [
    (f"https://iss.moex.com/iss/engines/futures/markets/forts/boards/rfud/securities/{secid}.json", {"iss.only":"securities","iss.meta":"off"}),
    (f"https://iss.moex.com/iss/securities/{secid}.json", {"iss.only":"description","iss.meta":"off"}),
    (f"https://iss.moex.com/iss/securities/{secid}.json", {"iss.meta":"off"}),
]
for base, params in specs:
    url=base+"?"+urlencode(params)
    print("URL",base,flush=True)
    try:
        with urlopen(Request(url,headers={"User-Agent":"Birzha-Research-ReadOnly/1.0"}),timeout=25) as r:
            data=json.loads(r.read())
        for name, table in data.items():
            if not isinstance(table,dict) or "columns" not in table:
                continue
            cols=table["columns"]
            rows=[dict(zip(cols,x)) for x in table.get("data",[])]
            print("TABLE",name,"ROWS",len(rows),"COLS",cols,flush=True)
            for row in rows[:30]:
                if name=="description" and any(t in str(row).upper() for t in ("TRADE","EXPIR","MATDATE","DELDATE","SECID")):
                    print("ENTRY",row,flush=True)
                elif name=="securities" and row.get("SECID")==secid:
                    print("MATCH", {k:v for k,v in row.items() if k in ("SECID","LASTTRADEDATE","LASTDELDATE","MATDATE","SECTYPE")},flush=True)
    except Exception as e:
        print("ERROR",type(e).__name__,str(e)[:100],flush=True)
