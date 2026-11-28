# Copyright (c) 2026 Taras Bulubas. All rights reserved.
# Use requires prior written permission; see LICENSE.

"""Deterministic analytics on invented readings."""

import pytest

from energie import analysis as A
from conftest import add_readings


def prices(con):
    return A.Prices.from_db(con)


def test_empty_database_gives_empty_results(con):
    p = prices(con)
    assert A.summary(con, p)["electricity"] == {} and A.insights(con, p) == []
    assert A.coverage(con).empty and A.device_stats(con, p).empty and A.counter_hourly(con).empty


def test_intervals_are_scaled_to_24_hours(con):
    add_readings(con, "electricity", [("2026-05-01 07:00", 1000.0), ("2026-05-02 19:00", 1015.0)])
    iv = A.meter_intervals(con, "electricity")
    assert iv["hours"].tolist() == [36.0] and iv["per_24h"].tolist() == [10.0]


def test_dense_readings_become_one_interval_per_day(con):
    add_readings(con, "gas", [("2026-05-01 07:00", 100.0), ("2026-05-01 12:00", 100.3),
                              ("2026-05-01 23:00", 100.8), ("2026-05-02 23:00", 101.8)])
    iv = A.meter_intervals(con, "gas")
    assert len(iv) == 1 and iv["per_24h"].iloc[0] == pytest.approx(1.0)


def test_the_price_of_a_day_is_the_last_one_started(con):
    from datetime import date

    from energie import db
    assert A.tariff_at(con, "electricity", date(2026, 5, 1))["placeholder"] is True       # nothing entered yet
    db.put_tariff(con, "electricity", "2025-03-01", 0.352, 11.0)
    db.put_tariff(con, "electricity", "2026-10-01", 0.418, 15.0)
    at = lambda d: A.tariff_at(con, "electricity", d)["energy_eur_kwh"]
    assert at(date(2025, 2, 28)) == db.DEFAULT_PRICE_EUR_KWH["electricity"]
    assert at(date(2025, 3, 1)) == 0.352 and at(date(2026, 9, 30)) == 0.352 and at(date(2026, 10, 1)) == 0.418
    assert A.next_tariff(con, "electricity", date(2026, 9, 15))["valid_from"] == "2026-10-01"
    assert A.next_tariff(con, "electricity", date(2026, 10, 1)) is None
    assert A.Prices.from_db(con, date(2026, 10, 2)).electricity_eur_kwh == 0.418


def test_gas_is_not_turned_into_a_year_from_a_few_weeks(con):
    add_readings(con, "gas", [("2026-10-01 07:00", 100.0), ("2026-11-01 07:00", 150.0)])
    add_readings(con, "electricity", [("2026-10-01 07:00", 1000.0), ("2026-10-08 07:00", 1070.0)])
    assert A.cost_outlook(con, prices(con)) == {}                   # 31 days of gas, 7 of electricity
    add_readings(con, "electricity", [("2026-10-15 07:00", 1140.0)])
    assert list(A.cost_outlook(con, prices(con))) == ["electricity"]


def test_gas_kwh_uses_the_bill_factors(con):
    from energie import db
    db.set_setting(con, "gas_correction_factor", "0.95")
    db.set_setting(con, "gas_calorific_value", "11.0")
    assert prices(con).gas_kwh_per_m3 == pytest.approx(10.45)


def _period(con, window, floors: dict[str, dict[str, float]]):
    rows = []
    for fl, devs in floors.items():
        rows.append((window, "floor", fl, fl, sum(devs.values())))
        rows += [(window, "device", fl, n, wh) for n, wh in devs.items()]
    rows.append((window, "total", "", "total", sum(sum(d.values()) for d in floors.values())))
    con.executemany("INSERT INTO shelly_periods(window_end,scope,floor,name,energy_wh) VALUES(?,?,?,?,?)", rows)
    con.commit()


def test_coverage_only_counts_windows_with_every_floor(con):
    add_readings(con, "electricity", [("2026-05-01 07:00", 1000.0), ("2026-05-02 07:00", 1010.0),
                                      ("2026-05-03 07:00", 1020.0)])
    _period(con, "2026-05-02 07:10", {"Basement": {"Fridge": 1000}})
    _period(con, "2026-05-03 07:10", {"Basement": {"Fridge": 1000}, "Attic": {"Office": 4000}})
    cov = A.coverage(con)
    assert cov["full"].tolist() == [False, True]
    assert A.summary(con, prices(con))["coverage_mean"] == pytest.approx(0.5)


def test_missing_device_days_count_as_zero(con):
    for d, wh in (("01", 900), ("02", None), ("03", 1100)):
        devs = {"Fridge": 700}
        if wh:
            devs["Washer"] = wh
        _period(con, f"2026-05-{d} 07:00", {"Basement": devs})
    st = A.device_stats(con, prices(con)).set_index("name")
    assert st.loc["Washer", "count"] == 3 and st.loc["Washer", "mean"] == pytest.approx(2 / 3)


def test_steady_devices_are_reported(con):
    for d in range(1, 8):
        _period(con, f"2026-05-0{d} 07:00", {"Basement": {"Freezer": 840 + d, "Washer": 0 if d % 2 else 900}})
    found = [i for i in A.insights(con, prices(con)) if i["code"] == "steady"]
    assert [i["name"] for i in found] == ["Freezer"] and found[0]["w"] == 35


def test_counter_reset_is_an_increase_from_zero(con):
    con.executemany("INSERT INTO shelly_counters(ts,name,total_wh) VALUES(?,?,?)", [
        ("2026-05-01 10:00:00", "Fridge", 5000), ("2026-05-01 10:30:00", "Fridge", 5040),
        ("2026-05-01 10:45:00", "Fridge", 12), ("2026-05-01 11:15:00", "Fridge", 50)])
    con.commit()
    h = A.counter_hourly(con).set_index("hour")["kwh"]
    assert h.iloc[0] == pytest.approx(0.052) and h.iloc[1] == pytest.approx(0.038)
