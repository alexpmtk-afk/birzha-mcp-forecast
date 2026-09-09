from birzha.application.volume_profile import VolumeProfileEngine
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
