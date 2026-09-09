from birzha.application.forecast import _profile_adjustment
from birzha.domain.snapshot import MarketSnapshot, TimeframeState
from birzha.domain.volume_profile import VolumeProfileResult

def st(tf, price):
    return TimeframeState(tf,60,price,0.01,0.02,0.03,price,price,0.5,0.02,1.0,1.0)

def test_profile_adjustment_uses_value_area_location():
    profile=VolumeProfileResult(100,105,95,0.7,(),(),"P",(),1000,"TEST")
    snap=MarketSnapshot("SBER","SBER","2026-09-01","TEST",st("D1",110),st("H1",110),st("M15",110),"PASS",(),volume_profile=profile)
    assert _profile_adjustment(snap) > 0.4
