"""Build a combined market view from original exports, without storage writes."""
import argparse
import json
from pathlib import Path
from birzha.application.combined_market_report import build_combined_market_report, render_combined_market_report
from birzha.domain.market import Instrument, Candle, CandleSeries
if __package__ in (None, ""):
    from g2_price_location import snapshot_from_dict, read_json
else:
    from scripts.g2_price_location import snapshot_from_dict, read_json


def run(snapshot_path, forecast_path, *, candles_path=None, observation_at=None, schedule_path=None):
    snapshot=snapshot_from_dict(read_json(snapshot_path))
    forecast=read_json(forecast_path)
    series=None
    if candles_path is not None:
        payload=read_json(candles_path)
        series=CandleSeries(Instrument(**payload["instrument"]),payload["timeframe"],tuple(Candle(**item) for item in payload["candles"]),payload.get("source","MOEX_ISS"))
        if "count" in payload and payload["count"]!=series.count:
            raise ValueError("source count conflict")
    schedule=Path(schedule_path).read_bytes() if schedule_path is not None else None
    return build_combined_market_report(snapshot,forecast,series=series,observation_at=observation_at,schedule_bytes=schedule)


def main():
    parser=argparse.ArgumentParser(description="Единый отчёт движения, уровней и последующих наблюдений")
    parser.add_argument("--snapshot",required=True);parser.add_argument("--forecast",required=True)
    parser.add_argument("--candles");parser.add_argument("--observation-at");parser.add_argument("--schedule")
    parser.add_argument("--format",choices=("json","text"),default="text")
    args=parser.parse_args()
    try:
        report=run(args.snapshot,args.forecast,candles_path=args.candles,observation_at=args.observation_at,schedule_path=args.schedule)
    except (TypeError,ValueError,KeyError,OverflowError,OSError) as exc:
        parser.error(str(exc))
    print(render_combined_market_report(report) if args.format=="text" else json.dumps(report,ensure_ascii=False,allow_nan=False,indent=2))


if __name__=="__main__":main()
