from birzha.application.volume_profile import VolumeProfileEngine, profile_from_public_trades
from birzha.domain.volume_profile import PriceVolumePoint


def _points(weights):
    points=[]
    for price, volume in enumerate(weights, start=1):
        if volume:
            points.append(PriceVolumePoint(float(price), float(volume)))
    return points


def test_profile_detects_upper_heavy_p_shape():
    result=VolumeProfileEngine(bins=9).build(_points([1,1,1,2,3,8,12,15,14]))
    assert result.shape == "P"
    assert result.val < result.poc < result.vah
    assert result.total_volume == 57.0


def test_profile_detects_lower_heavy_b_shape():
    result=VolumeProfileEngine(bins=9).build(_points([14,15,12,8,3,2,1,1,1]))
    assert result.shape == "b"


def test_profile_detects_double_distribution():
    result=VolumeProfileEngine(bins=9).build(_points([1,8,12,8,1,7,13,7,1]))
    assert result.shape == "B"
    assert len(result.hvn) >= 2


def test_profile_defaults_to_balanced_d_shape():
    result=VolumeProfileEngine(bins=9).build(_points([3,5,7,9,10,9,7,5,3]))
    assert result.shape == "D"
    assert result.poc > 0



def test_public_trade_profile_uses_latest_session_real_price_quantity():
    rows = [
        {"TRADEDATE":"2026-10-02","PRICE":100.0,"QUANTITY":100.0,"OFFMARKETDEAL":0},
        {"TRADEDATE":"2026-10-03","PRICE":101.0,"QUANTITY":10.0,"OFFMARKETDEAL":0},
        {"TRADEDATE":"2026-10-03","PRICE":102.0,"QUANTITY":30.0,"OFFMARKETDEAL":0},
    ]

    result = profile_from_public_trades(rows, bins=8)

    assert result is not None
    assert result.method == "PUBLIC_TRADES_PRICE_QUANTITY_V1"
    assert result.total_volume == 40.0
    assert 101.0 <= result.poc <= 102.0


def test_public_trade_profile_excludes_offmarket_rows():
    rows = [
        {"TRADEDATE":"2026-10-03","PRICE":100.0,"QUANTITY":10.0,"OFFMARKETDEAL":0},
        {"TRADEDATE":"2026-10-03","PRICE":999.0,"QUANTITY":1000.0,"OFFMARKETDEAL":1},
    ]

    result = profile_from_public_trades(rows, bins=8)

    assert result is not None
    assert result.total_volume == 10.0
    assert result.poc < 200.0
