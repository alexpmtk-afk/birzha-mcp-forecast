from dataclasses import replace

import pytest

from test_production_feature_contracts import snapshot, instrument
from birzha.application.forecast import build_forecast_from_snapshot, ForecastParameters
from birzha.domain.forecast import BASELINE_EVIDENCE_RECORD_VERSION, FORECAST_RECORD_CONTRACT_VERSION
from birzha.domain.snapshot import market_snapshot_id
from birzha.storage.forecast_journal import DuckDBForecastJournal, ForecastCollisionError, _record_from_dict


def test_direction_is_an_estimate_and_never_claims_full_control_route():
    snap=snapshot()
    record=build_forecast_from_snapshot(snap)
    payload=record.to_dict()
    assert record.direction=='UP'
    assert record.record_version==BASELINE_EVIDENCE_RECORD_VERSION
    assert record.decision_status=='BASELINE_DIRECTIONAL_ESTIMATE'
    assert record.control=='UNKNOWN' and record.route=='UNAVAILABLE'
    assert record.abstention_reasons==()
    for name in ('control','route','scenario','probability','control_confidence','route_confidence'):
        assert payload['field_availability'][name]=='UNAVAILABLE'
    assert record.primary_scenario is None and record.confirmation_level is None
    assert record.key_levels
    assert payload['field_availability']['directional_estimate']=='AVAILABLE'


@pytest.mark.parametrize('n',[0,1,5,14,15,19,20])
def test_short_d1_history_abstains_with_machine_readable_reason(n):
    record=build_forecast_from_snapshot(snapshot(n))
    assert record.decision_status=='ABSTAIN'
    assert record.direction=='NEUTRAL' and record.signal_strength==0
    assert record.abstention_reasons
    assert all(h.expected_move_pct is None and h.adverse_move_pct is None for h in record.horizons)
    assert record.to_dict()['field_availability']['directional_estimate']=='UNAVAILABLE'


@pytest.mark.parametrize('field',['d1_atr_price_scale','d1_return_5_atr','d1_return_20_atr','d1_efficiency_20'])
def test_missing_required_value_cannot_be_replaced_by_intraday_strength(field):
    snap=snapshot(60)
    snap=replace(snap,normalized_features=replace(snap.normalized_features,**{field:None}),h1=replace(snap.h1,trend_score=100.))
    record=build_forecast_from_snapshot(snap)
    assert record.decision_status=='ABSTAIN'
    assert any(field in reason for reason in record.abstention_reasons)
    assert record.signal_strength==0


def test_unknown_quality_abstains_despite_computable_values():
    snap=snapshot()
    snap=replace(snap,quality_contract=replace(snap.quality_contract,reasons=('UNEXPLAINED',)))
    record=build_forecast_from_snapshot(snap)
    assert record.decision_status=='ABSTAIN'
    assert any('quality_not_proven' in r for r in record.abstention_reasons)


def test_missing_quality_contract_abstains():
    record=build_forecast_from_snapshot(replace(snapshot(60),quality_contract=None))
    assert 'DATA_QUALITY_CONTRACT_MISSING' in record.abstention_reasons


def test_optional_bad_timeframe_has_no_directional_weight_or_alignment():
    snap=snapshot(60)
    bad=replace(snap,h1=replace(snap.h1,trend_score=-100.,return_5=-100.),normalized_features=replace(snap.normalized_features,h1_return_5_atr=None))
    no_h1=replace(bad,h1=replace(bad.h1,trend_score=0.,return_5=None))
    first=build_forecast_from_snapshot(bad)
    second=build_forecast_from_snapshot(no_h1)
    assert first.direction==second.direction and first.signal_strength==second.signal_strength
    assert 'optional_timeframe_excluded:H1' in first.reasons
    assert first.snapshot_id==market_snapshot_id(bad)
    assert bad.h1.trend_score==-100.  # source snapshot unchanged


def test_eligible_neutral_is_distinct_from_refusal():
    snap=snapshot(60)
    snap=replace(snap,d1=replace(snap.d1,trend_score=0.,return_5=0.),h1=replace(snap.h1,trend_score=0.,return_5=0.),m15=replace(snap.m15,trend_score=0.,return_5=0.))
    record=build_forecast_from_snapshot(snap)
    assert record.direction=='NEUTRAL' and record.decision_status=='BASELINE_NEUTRAL'
    assert record.abstention_reasons==()


def test_immutable_journal_roundtrip_preserves_v2_and_old_payload_bytes():
    journal=DuckDBForecastJournal()
    try:
        new=build_forecast_from_snapshot(snapshot())
        old_snap=snapshot()
        old_snap=replace(old_snap,normalized_features=replace(old_snap.normalized_features,version='NORMALIZED_FEATURES_V1'))
        old=build_forecast_from_snapshot(old_snap)
        old_payload=old.to_dict()
        assert old.record_version==FORECAST_RECORD_CONTRACT_VERSION
        assert 'decision_status' not in old_payload
        assert 'control' not in old_payload['field_availability']
        for record in (old,new):
            original=journal.canonical_payload(record)
            result=journal.append(record)
            restored=journal.get(record.forecast_id)
            assert journal.canonical_payload(restored)==original
            assert journal.append(restored).status=='DUPLICATE_IDENTICAL'
            assert result.payload_hash==original[1]
        changed=replace(new,reasons=new.reasons+('changed_after_result',))
        with pytest.raises(ForecastCollisionError): journal.append(changed)
        assert journal.canonical_payload(journal.get(old.forecast_id))==journal.canonical_payload(old)
    finally:
        journal.close()


def test_refusal_record_roundtrip_preserves_all_reasons():
    record=build_forecast_from_snapshot(snapshot(19))
    restored=_record_from_dict(record.to_dict())
    assert restored.to_dict()==record.to_dict()


@pytest.mark.parametrize('change',[{'control':'BUYERS'},{'route':'TREND'},{'primary_scenario':'invented'},{'entry_levels':(100.,)},{'decision_status':None}])
def test_v2_rejects_fabricated_full_engine_fields(change):
    record=build_forecast_from_snapshot(snapshot())
    with pytest.raises(ValueError): replace(record,**change).to_dict()


def test_abstention_cannot_keep_a_directional_estimate():
    record=build_forecast_from_snapshot(snapshot(19))
    with pytest.raises(ValueError): replace(record,direction='UP').to_dict()


@pytest.mark.parametrize('params',[ForecastParameters(strength_scale=0.),ForecastParameters(direction_threshold=-1.),ForecastParameters(d1_weight=float('nan'))])
def test_invalid_parameters_are_rejected_before_forecast(params):
    with pytest.raises(ValueError): build_forecast_from_snapshot(snapshot(),parameters=params)


def test_identity_and_payload_are_deterministic_including_refusal():
    for n in (19,21,60):
        snap=snapshot(n)
        first=build_forecast_from_snapshot(snap)
        second=build_forecast_from_snapshot(snap)
        assert first.to_dict()==second.to_dict()
        assert first.snapshot_id==market_snapshot_id(snap)


@pytest.mark.parametrize('scale',[0.,-1.])
def test_nonpositive_normalized_scale_abstains(scale):
    snap=snapshot(60)
    snap=replace(snap,normalized_features=replace(snap.normalized_features,d1_atr_price_scale=scale))
    assert 'D1_POSITIVE_ATR_PRICE_SCALE_UNAVAILABLE' in build_forecast_from_snapshot(snap).abstention_reasons


def test_conflicting_quality_count_does_not_certify_input():
    snap=snapshot(60)
    quality=snap.quality_contract
    first=replace(quality.timeframes[0],candles=999)
    snap=replace(snap,quality_contract=replace(quality,timeframes=(first,*quality.timeframes[1:])))
    assert 'D1_QUALITY_COUNT_CONFLICT' in build_forecast_from_snapshot(snap).abstention_reasons


def test_unadmitted_proxy_profile_cannot_influence_baseline_direction():
    from birzha.domain.volume_profile import VolumeProfileResult
    snap=snapshot(21)
    profile=VolumeProfileResult(1.,2.,0.,.7,(),(),'P',(),100.,'CANDLE_VOLUME_PROXY_V1')
    normalized=replace(snap.normalized_features,profile_method=profile.method,profile_is_exact=False,distance_to_poc_atr=1.)
    snap=replace(snap,volume_profile=profile,normalized_features=normalized)
    first=build_forecast_from_snapshot(snap)
    second=build_forecast_from_snapshot(replace(snap,volume_profile=None))
    assert first.signal_strength==second.signal_strength
    assert 'optional_profile_excluded' in first.reasons
    assert first.snapshot_id==market_snapshot_id(snap)
