"""Read a saved snapshot, optionally bind its forecast, print movement facts."""
import argparse
import json
from birzha.application.timeframe_movement import build_timeframe_movement_report, render_timeframe_movement_report
from birzha.application.linked_level_report import build_linked_level_report, render_linked_level_report
if __package__ in (None, ""):
    from g2_price_location import snapshot_from_dict, read_json
else:
    from scripts.g2_price_location import snapshot_from_dict, read_json


def run(snapshot_path, *, forecast_path=None):
    snapshot = snapshot_from_dict(read_json(snapshot_path))
    result = {"movement": build_timeframe_movement_report(snapshot), "levels": None}
    if forecast_path is not None:
        result["levels"] = build_linked_level_report(snapshot, read_json(forecast_path))
    return result


def main():
    parser = argparse.ArgumentParser(description="Сравнение дневного, часового и 15-минутного движения")
    parser.add_argument("--snapshot", required=True); parser.add_argument("--forecast")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()
    try:
        result = run(args.snapshot, forecast_path=args.forecast)
    except (ValueError, TypeError, KeyError, OverflowError, OSError) as exc:
        parser.error(str(exc))
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
    else:
        print(render_timeframe_movement_report(result["movement"]))
        if result["levels"] is not None:
            print("\n" + render_linked_level_report(result["levels"]))


if __name__ == "__main__": main()
