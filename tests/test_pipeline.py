import numpy as np
import pandas as pd
import pytest

from pipeline.export import price_payload, utc_offset_changes
from pipeline.normalise import local_year_bounds, slice_local_year, to_hourly
from pipeline.validate import ValidationError, validate_year


def year_series(year: int, freq: str = "1h", value=None) -> pd.Series:
    start, end = local_year_bounds(year)
    idx = pd.date_range(start, end, freq=freq, inclusive="left")
    values = np.arange(len(idx), dtype=float) if value is None else np.full(len(idx), value)
    return pd.Series(values, index=idx, name="price_eur_mwh")


class TestLocalYear:
    def test_bounds_are_local_midnight(self):
        start, end = local_year_bounds(2025)
        assert start == pd.Timestamp("2024-12-31 23:00", tz="UTC")
        assert end == pd.Timestamp("2025-12-31 23:00", tz="UTC")

    @pytest.mark.parametrize("year,hours", [(2025, 8760), (2024, 8784)])
    def test_year_length(self, year, hours):
        assert len(year_series(year)) == hours

    def test_dst_days_reported(self):
        _, report = validate_year(year_series(2025), 2025)
        assert report.short_days == ["2025-03-30"]
        assert report.long_days == ["2025-10-26"]

    def test_slice_drops_neighbouring_years(self):
        s = pd.concat([year_series(2024), year_series(2025), year_series(2026)])
        assert len(slice_local_year(s, 2025)) == 8760

    def test_utc_offsets(self):
        s = year_series(2025)
        changes = utc_offset_changes(s.index)
        # summer time starts 2025-03-30 01:00 UTC, ends 2025-10-26 01:00 UTC
        assert changes[0] == [0, 1]
        assert s.index[changes[1][0]] == pd.Timestamp("2025-03-30 01:00", tz="UTC")
        assert changes[1][1] == 2
        assert s.index[changes[2][0]] == pd.Timestamp("2025-10-26 01:00", tz="UTC")
        assert changes[2][1] == 1
        assert len(changes) == 3


class TestToHourly:
    def test_hourly_passthrough(self):
        s = year_series(2025)
        assert to_hourly(s) is s

    def test_quarter_hours_averaged(self):
        idx = pd.date_range("2025-10-01 00:00", periods=8, freq="15min", tz="UTC")
        s = pd.Series([10, 20, 30, 40, -10, -10, 0, 20], index=idx, dtype=float)
        h = to_hourly(s)
        assert list(h.index) == list(pd.date_range("2025-10-01", periods=2, freq="1h", tz="UTC"))
        assert h.tolist() == [25.0, 0.0]

    def test_quarter_hours_across_dst_end(self):
        # 2025-10-26 local day has 25 hours = 100 quarter hours
        start = pd.Timestamp("2025-10-26", tz="Europe/Amsterdam").tz_convert("UTC")
        end = pd.Timestamp("2025-10-27", tz="Europe/Amsterdam").tz_convert("UTC")
        idx = pd.date_range(start, end, freq="15min", inclusive="left")
        assert len(idx) == 100
        h = to_hourly(pd.Series(1.0, index=idx))
        assert len(h) == 25

    def test_local_index_accepted(self):
        idx = pd.date_range("2025-06-01", periods=8, freq="15min", tz="Europe/Amsterdam")
        h = to_hourly(pd.Series(1.0, index=idx))
        assert str(h.index.tz) == "UTC" and len(h) == 2

    def test_naive_index_rejected(self):
        with pytest.raises(ValueError):
            to_hourly(pd.Series([1.0, 2.0], index=pd.date_range("2025-01-01", periods=2, freq="1h")))


class TestValidate:
    def test_negative_prices_are_valid(self):
        s = year_series(2025, value=-250.0)
        clean, report = validate_year(s, 2025)
        assert report.negative_hours == 8760
        assert report.min == -250.0

    def test_duplicates_rejected(self):
        s = year_series(2025)
        s = pd.concat([s, s.iloc[:1]])
        with pytest.raises(ValidationError, match="duplicate"):
            validate_year(s, 2025)

    def test_short_gap_interpolated(self):
        s = year_series(2025)
        s = s.drop(s.index[100:102])
        clean, report = validate_year(s, 2025)
        assert len(clean) == 8760
        assert clean.iloc[100] == pytest.approx(100.0) and clean.iloc[101] == pytest.approx(101.0)
        assert report.filled_gaps == [(clean.index[100].isoformat(), 2)]

    def test_long_gap_rejected(self):
        s = year_series(2025)
        s = s.drop(s.index[100:104])
        with pytest.raises(ValidationError, match="gaps longer"):
            validate_year(s, 2025)

    def test_gap_at_edge_rejected(self):
        s = year_series(2025).iloc[1:]
        with pytest.raises(ValidationError, match="start or end"):
            validate_year(s, 2025)

    def test_implausible_price_rejected(self):
        s = year_series(2025, value=50.0)
        s.iloc[5] = 50_000.0  # e.g. EUR/GWh by mistake
        with pytest.raises(ValidationError, match="implausible"):
            validate_year(s, 2025)

    def test_off_grid_timestamp_rejected(self):
        s = year_series(2025)
        s.index = s.index + pd.Timedelta(minutes=30)
        with pytest.raises(ValidationError):
            validate_year(s, 2025)


def test_payload_shape():
    s = year_series(2025, value=12.3456)
    p = price_payload(s, 2025, "test")
    assert p["start_utc"] == "2024-12-31T23:00:00Z"
    assert len(p["prices"]) == 8760
    assert p["prices"][0] == 12.35
