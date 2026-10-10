"""Read an exported snapshot and print price location facts; no network or writes."""
import argparse
import json
from birzha.application.price_location import build_price_location_report, render_price_location_report
from birzha.domain.snapshot import MarketSnapshot, TimeframeState, TimeframeQuality, DataQualityContract, MARKET_SNAPSHOT_CONTRACT_VERSION
from birzha.domain.normalized_features import NormalizedFeatureSet
from birzha.domain.volume_profile import VolumeProfileResult, VolumeBin
from birzha.domain.flow import MarketFlowSnapshot, ClientOpenInterest
from pathlib import Path

def _unique_pairs(items):
    result={}
    for key,value in items:
        if key in result:
            raise ValueError("duplicate JSON field: " + key)
        result[key]=value
    return result


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"),object_pairs_hook=_unique_pairs)


def _profile(data):
    if data is None:
        return None
    return VolumeProfileResult(**{**data,"hvn":tuple(data["hvn"]),"lvn":tuple(data["lvn"]),"bins":tuple(VolumeBin(**i) for i in data["bins"])})


def snapshot_from_dict(data):
    if data.get("contract_version") != MARKET_SNAPSHOT_CONTRACT_VERSION:
        raise ValueError("explicit MARKET_SNAPSHOT_V2 export required")
    quality=data.get("quality_contract")
    if quality is not None:
        quality=DataQualityContract(**{**quality,"timeframes":tuple(TimeframeQuality(**i) for i in quality["timeframes"]),"reasons":tuple(quality["reasons"])})
    flow=data.get("flow")
    if flow is not None:
        flow=MarketFlowSnapshot(**{**flow,"warnings":tuple(flow["warnings"]),"volume_profile":_profile(flow.get("volume_profile")),**{k:ClientOpenInterest(**flow[k]) if flow.get(k) is not None else None for k in ("individuals","legal_entities")}})
    normalized=data.get("normalized_features")
    snapshot=MarketSnapshot(**{**data,**{tf:TimeframeState(**data[tf]) for tf in ("d1","h1","m15")},"warnings":tuple(data["warnings"]),"quality_contract":quality,"flow":flow,"volume_profile":_profile(data.get("volume_profile")),"normalized_features":NormalizedFeatureSet(**normalized) if normalized is not None else None})
    if snapshot.to_dict() != data:
        raise ValueError("export must preserve the complete snapshot payload")
    return snapshot


def run(path):
    return build_price_location_report(snapshot_from_dict(read_json(path)))


def main():
    parser=argparse.ArgumentParser(description="Положение цены относительно допущенных уровней")
    parser.add_argument("--snapshot",required=True)
    parser.add_argument("--format",choices=("json","text"),default="json")
    args=parser.parse_args()
    try:
        report=run(args.snapshot)
    except (ValueError,TypeError,KeyError,OverflowError) as exc:
        parser.error(str(exc))
    print(render_price_location_report(report) if args.format=="text" else json.dumps(report,ensure_ascii=False,allow_nan=False,indent=2))


if __name__=="__main__":
    main()
