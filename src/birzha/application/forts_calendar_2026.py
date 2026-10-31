"""Documented MOEX FORTS 2026 trading-date mapping, not first-receipt evidence.

Sources: https://www.moex.com/n95564 ; https://www.moex.com/n96571 ;
https://www.moex.com/ru/derivatives/weekend-session .
Only the published 2026 policy is implemented. Unknown years fail closed.
"""
from datetime import date,timedelta
VERSION="MOEX_FORTS_2026_PUBLISHED_EXTRA_SESSIONS_V1"
EXTRA_HOLIDAYS={date(2026,2,23),date(2026,5,1),date(2026,6,12),date(2026,11,4)}
CLOSED={date(2026,1,1),date(2026,1,2),date(2026,1,7),date(2026,3,8),date(2026,5,9),date(2026,12,31)}
EXCLUDED_WEEKENDS=set()
for m,days in [(1,[3,4,10,11]),(2,[14,15]),(3,[7,8,21,22]),(5,[9,10]),(6,[20,21]),(8,[1,2,15,16]),(9,[12,13]),(10,[24,25]),(12,[5,6])]:
    EXCLUDED_WEEKENDS.update(date(2026,m,d) for d in days)

def kind(day):
    if day.year!=2026:raise ValueError("documented FORTS calendar only covers 2026")
    if day in CLOSED or day in EXCLUDED_WEEKENDS:return "CLOSED"
    if day in EXTRA_HOLIDAYS or day.weekday()>=5:return "EXTRA"
    return "PRIMARY"

def trade_day(civil_day):
    state=kind(civil_day)
    if state=="CLOSED":raise ValueError("source bar on published closed date")
    day=civil_day
    while kind(day)!="PRIMARY":
        day+=timedelta(days=1)
    return day

def primary_days(start,end):
    result=[];d=start
    while d<=end:
        if kind(d)=="PRIMARY":result.append(d)
        d+=timedelta(days=1)
    return result

