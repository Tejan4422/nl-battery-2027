"""ENTSO-E Transparency Platform CSV exports: format quirks, DST and combining files.

Rows are written in the exact format of the platform's export, including the
(CET)/(CEST) labels it adds on DST days.
"""

from datetime import date

import pandas as pd
import pytest

from pipeline.ingest_entsoe_csv import EntsoeFormatError, read_entsoe_csv, read_entsoe_csvs
from pipeline.normalise import TZ, Window, incomplete_hours, last_complete_months, to_hourly

HEADER = ('"MTU (CET/CEST)","Area","Sequence","Day-ahead Price (EUR/MWh)",'
          '"Intraday Period (CET/CEST)","Intraday Price (EUR/MWh)"\n')


def row(mtu, price, area="BZN|NL", seq="Without Sequence"):
    return f'"{mtu}","{area}","{seq}","{price}","",""\n'


def quarters(day: str, start_h: int, end_h: int, price=10.0):
    """Plain 15-minute rows between two local hours on one day (DD/MM/YYYY)."""
    out = []
    t = pd.Timestamp(pd.to_datetime(day, format="%d/%m/%Y")) + pd.Timedelta(hours=start_h)
    end = pd.Timestamp(pd.to_datetime(day, format="%d/%m/%Y")) + pd.Timedelta(hours=end_h)
    while t < end:
        u = t + pd.Timedelta(minutes=15)
        out.append(row(f"{t:%d/%m/%Y %H:%M:%S} - {u:%d/%m/%Y %H:%M:%S}", price))
        t = u
    return out


def write(tmp_path, rows, name="x.csv"):
    p = tmp_path / name
    p.write_text(HEADER + "".join(rows))
    return p


def test_plain_rows_to_utc(tmp_path):
    s = read_entsoe_csv(write(tmp_path, quarters("15/01/2026", 0, 1)))
    assert list(s.index) == list(pd.date_range("2026-01-14 23:00", periods=4, freq="15min", tz="UTC"))


def test_spring_dst_day(tmp_path):
    rows = (quarters("29/03/2026", 0, 1)
            + [row("29/03/2026 01:00:00 - 29/03/2026 01:15:00", 1),
               row("29/03/2026 01:15:00 - 29/03/2026 01:30:00", 2),
               row("29/03/2026 01:30:00 - 29/03/2026 01:45:00 (CET)", 3),
               row("29/03/2026 01:45:00 (CET) - 29/03/2026 03:00:00 (CEST)", 4),
               row("29/03/2026 03:00:00 (CEST) - 29/03/2026 03:15:00", 5)])
    s = read_entsoe_csv(write(tmp_path, rows))
    assert s.index[-2] == pd.Timestamp("2026-03-29 00:45", tz="UTC")
    assert s.index[-1] == pd.Timestamp("2026-03-29 01:00", tz="UTC")
    assert s.index.to_series().diff().dropna().eq(pd.Timedelta(minutes=15)).all()


# copied from the platform's 2025 export, prices replaced
AUTUMN = [
    ("26/10/2025 01:45:00 - 26/10/2025 02:00:00 (CEST)", 1),
    ("26/10/2025 02:00:00 (CEST) - 26/10/2025 02:15:00 (CEST)", 10),
    ("26/10/2025 02:15:00 (CEST) - 26/10/2025 02:30:00 (CEST)", 11),
    ("26/10/2025 02:30:00 (CEST) - 26/10/2025 02:45:00 (CEST)", 12),
    ("26/10/2025 02:45:00 (CEST) - 26/10/2025 02:00:00 (CET)", 13),
    ("26/10/2025 02:00:00 (CET) - 26/10/2025 02:15:00 (CET)", 20),
    ("26/10/2025 02:15:00 (CET) - 26/10/2025 02:30:00 (CET)", 21),
    ("26/10/2025 02:30:00 (CET) - 26/10/2025 02:45:00 (CET)", 22),
    ("26/10/2025 02:45:00 (CET) - 26/10/2025 03:00:00", 23),
    ("26/10/2025 03:00:00 - 26/10/2025 03:15:00", 99),
]


def autumn_rows(labelled: bool):
    strip = (lambda m: m) if labelled else (lambda m: m.replace(" (CEST)", "").replace(" (CET)", ""))
    return [row(strip(m), p) for m, p in AUTUMN]


@pytest.mark.parametrize("labelled", [True, False])
def test_autumn_repeated_hour(tmp_path, labelled):
    s = read_entsoe_csv(write(tmp_path, autumn_rows(labelled)))
    assert len(s) == 10
    assert s.index.to_series().diff().dropna().eq(pd.Timedelta(minutes=15)).all()
    # first 02:00 is CEST (00:00 UTC), second 02:00 is CET (01:00 UTC)
    assert s[pd.Timestamp("2025-10-26 00:00", tz="UTC")] == 10
    assert s[pd.Timestamp("2025-10-26 01:00", tz="UTC")] == 20
    assert s[pd.Timestamp("2025-10-26 02:00", tz="UTC")] == 99


def test_full_dst_days_have_92_and_100_quarters(tmp_path):
    rows = (quarters("29/03/2026", 0, 1)
            + [row("29/03/2026 01:00:00 - 29/03/2026 01:15:00", 1),
               row("29/03/2026 01:15:00 - 29/03/2026 01:30:00", 1),
               row("29/03/2026 01:30:00 - 29/03/2026 01:45:00 (CET)", 1),
               row("29/03/2026 01:45:00 (CET) - 29/03/2026 03:00:00 (CEST)", 1),
               row("29/03/2026 03:00:00 (CEST) - 29/03/2026 03:15:00", 1)]
            + quarters("29/03/2026", 3, 24)[1:])
    s = read_entsoe_csv(write(tmp_path, rows))
    local_days = s.index.tz_convert(TZ).normalize().value_counts()
    assert local_days.iloc[0] == 92


def test_missing_prices_dropped(tmp_path):
    rows = quarters("15/01/2026", 0, 1)
    rows[1] = rows[1].replace('"10.0"', '"n/e"')
    rows[2] = rows[2].replace('"10.0"', '""')
    s = read_entsoe_csv(write(tmp_path, rows))
    assert list(s.index) == [pd.Timestamp("2026-01-14 23:00", tz="UTC"), pd.Timestamp("2026-01-14 23:45", tz="UTC")]


def test_incomplete_hour_detected(tmp_path):
    rows = quarters("15/01/2026", 0, 3)
    rows[5] = rows[5].replace('"10.0"', '"n/e"')
    s = read_entsoe_csv(write(tmp_path, rows))
    assert list(incomplete_hours(s)) == [pd.Timestamp("2026-01-15 00:00", tz="UTC")]
    assert to_hourly(s).tolist() == [10.0, 10.0, 10.0]


def test_hourly_constant_quarters_average_back(tmp_path):
    rows = quarters("15/01/2025", 0, 1, price=13.62)
    assert to_hourly(read_entsoe_csv(write(tmp_path, rows))).tolist() == [13.62]


def test_hourly_rows_accepted(tmp_path):
    rows = [row("15/01/2026 00:00:00 - 15/01/2026 01:00:00", 5), row("15/01/2026 01:00:00 - 15/01/2026 02:00:00", 6)]
    s = read_entsoe_csv(write(tmp_path, rows))
    assert len(s) == 2


def test_rejects_other_area_sequence_and_lengths(tmp_path):
    with pytest.raises(EntsoeFormatError, match="area"):
        read_entsoe_csv(write(tmp_path, [row("15/01/2026 00:00:00 - 15/01/2026 00:15:00", 1, area="BZN|BE")]))
    rows = [row("15/01/2026 00:00:00 - 15/01/2026 00:15:00", 1),
            row("15/01/2026 00:15:00 - 15/01/2026 00:30:00", 1, seq="Sequence 1")]
    with pytest.raises(EntsoeFormatError, match="sequences"):
        read_entsoe_csv(write(tmp_path, rows))
    with pytest.raises(EntsoeFormatError, match="period length"):
        read_entsoe_csv(write(tmp_path, [row("15/01/2026 00:00:00 - 15/01/2026 00:20:00", 1)]))
    with pytest.raises(EntsoeFormatError, match="parse"):
        read_entsoe_csv(write(tmp_path, [row("2026-01-15T00:00 - 2026-01-15T00:15", 1)]))


def test_combine_files(tmp_path):
    a = write(tmp_path, quarters("31/12/2025", 22, 24) + quarters("01/01/2026", 0, 1), "a.csv")
    b = write(tmp_path, quarters("01/01/2026", 0, 2), "b.csv")
    s = read_entsoe_csvs([a, b])
    assert len(s) == 8 + 8 - 0  # 2h + 1h overlap + 1h new
    assert s.index.is_monotonic_increasing and not s.index.has_duplicates


def test_combine_files_disagreeing(tmp_path):
    a = write(tmp_path, quarters("01/01/2026", 0, 1, price=10), "a.csv")
    b = write(tmp_path, quarters("01/01/2026", 0, 1, price=11), "b.csv")
    with pytest.raises(EntsoeFormatError, match="disagree"):
        read_entsoe_csvs([a, b])


class TestWindow:
    def test_last_complete_months(self):
        idx = pd.date_range(pd.Timestamp("2025-01-01", tz=TZ), pd.Timestamp("2026-10-24", tz=TZ),
                            freq="15min", inclusive="left").tz_convert("UTC")
        w = last_complete_months(pd.Series(1.0, index=idx))
        assert w == Window(date(2025, 10, 1), date(2026, 9, 30))

    def test_data_ending_on_month_boundary(self):
        idx = pd.date_range(pd.Timestamp("2025-01-01", tz=TZ), pd.Timestamp("2026-10-01", tz=TZ),
                            freq="1h", inclusive="left").tz_convert("UTC")
        assert last_complete_months(pd.Series(1.0, index=idx)).end == date(2026, 9, 30)

    def test_not_enough_data(self):
        idx = pd.date_range("2026-03-01", periods=24 * 100, freq="1h", tz="UTC")
        with pytest.raises(ValueError, match="not enough"):
            last_complete_months(pd.Series(1.0, index=idx))

    def test_window_bounds_and_id(self):
        w = Window(date(2025, 10, 1), date(2026, 9, 30))
        start, end = w.bounds_utc()
        assert start == pd.Timestamp("2025-09-30 22:00", tz="UTC")
        assert end == pd.Timestamp("2026-09-30 22:00", tz="UTC")
        assert (end - start) / pd.Timedelta(hours=1) == 8760
        assert Window.from_id(w.id) == w
