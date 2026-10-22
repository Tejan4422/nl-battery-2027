import numpy as np
import pandas as pd
import pytest

from pipeline.profiles import hour_index, profile_payload, sun_elevation, synthetic_load, synthetic_solar


@pytest.fixture(scope="module")
def idx():
    return hour_index(2025)


def test_normalised(idx):
    for f in (synthetic_load, synthetic_solar):
        v = f(idx)
        assert len(v) == 8760
        assert v.sum() == pytest.approx(1.0)
        assert (v >= 0).all()


def test_solar_zero_at_night_and_peaks_at_noon(idx):
    v = synthetic_solar(idx)
    local = idx.tz_convert("Europe/Amsterdam")
    assert v[local.hour == 2].max() == 0
    june = (local.month == 6)
    by_hour = pd.Series(v[june]).groupby(local.hour[june]).sum()
    assert by_hour.idxmax() in (13, 14)  # solar noon is ~13:40 CEST


def test_solar_summer_much_larger_than_winter(idx):
    v = synthetic_solar(idx)
    m = idx.tz_convert("Europe/Amsterdam").month
    assert v[m == 6].sum() > 5 * v[m == 12].sum()


def test_load_winter_higher_and_evening_peak(idx):
    v = synthetic_load(idx)
    local = idx.tz_convert("Europe/Amsterdam")
    assert v[local.month == 1].sum() > v[local.month == 7].sum()
    by_hour = pd.Series(v).groupby(local.hour).sum()
    assert by_hour.idxmax() in (18, 19, 20)
    assert by_hour.idxmin() in (2, 3, 4)


def test_sun_elevation_known_value():
    # De Bilt, summer solstice, solar noon ~11:40 UTC: elevation ~ 90 - 52.1 + 23.44 = 61.3
    t = pd.DatetimeIndex([pd.Timestamp("2025-06-21 11:40", tz="UTC")])
    assert sun_elevation(t)[0] == pytest.approx(61.3, abs=0.6)


def test_payload_dst_year_lengths():
    assert len(profile_payload(2024)["load"]) == 8784
    p = profile_payload(2025)
    assert np.sum(p["solar"]) == pytest.approx(1.0, rel=1e-3)
