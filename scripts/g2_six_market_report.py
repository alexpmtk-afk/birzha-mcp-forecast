"""Read original exports listed in a bounded six-market input manifest."""
import argparse
import json
from pathlib import Path
from birzha.application.six_market_report import build_six_market_report, render_six_market_report, MARKETS
if __package__ in (None, ""):
    from g2_combined_market_report import load_inputs
    from g2_price_location import read_json
else:
    from scripts.g2_combined_market_report import load_inputs
    from scripts.g2_price_location import read_json


def run(manifest_path):
    manifest_path = Path(manifest_path).resolve()
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict) or set(manifest) != {"version", "evidence_mode", "markets"} or manifest["version"] != "SIX_MARKET_REPORT_INPUTS_V1":
        raise ValueError("explicit supported six-market input manifest required")
    entries = manifest["markets"]
    if not isinstance(entries, list) or len(entries) > 6:
        raise ValueError("at most six market entries required")
    def path(value):
        if not isinstance(value, str) or not value or Path(value).is_absolute():
            raise ValueError("nonempty relative input path required")
        result = (manifest_path.parent / value).resolve()
        if not result.is_relative_to(manifest_path.parent):
            raise ValueError("input path escapes manifest directory")
        return result
    inputs = {}
    for entry in entries:
        if not isinstance(entry, dict) or not {"market", "snapshot", "forecast"}.issubset(entry) or set(entry) - {"market", "snapshot", "forecast", "candles", "observation_at", "schedule"}:
            raise ValueError("invalid original market input entry")
        market = entry["market"]
        if not isinstance(market,str) or market not in MARKETS or market in inputs:
            raise ValueError("unknown or duplicate market input")
        inputs[market] = load_inputs(path(entry["snapshot"]),path(entry["forecast"]),
            candles_path=path(entry["candles"]) if "candles" in entry else None,
            observation_at=entry.get("observation_at"),
            schedule_path=path(entry["schedule"]) if "schedule" in entry else None)
    return build_six_market_report(inputs,evidence_mode=manifest["evidence_mode"])


def main():
    parser = argparse.ArgumentParser(description="Сводка исходных решений и наличия данных по шести рынкам")
    parser.add_argument("--manifest",required=True)
    parser.add_argument("--format",choices=("text","json"),default="text")
    args = parser.parse_args()
    try:
        report = run(args.manifest)
    except (TypeError,ValueError,KeyError,OverflowError,OSError) as exc:
        parser.error(str(exc))
    print(render_six_market_report(report) if args.format == "text" else json.dumps(report,ensure_ascii=False,allow_nan=False,indent=2))


if __name__ == "__main__":main()
