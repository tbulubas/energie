# Copyright (c) 2026 Taras Bulubas. All rights reserved.
# Use requires prior written permission; see LICENSE.

"""Deterministic analytics. Every number shown in the app is computed here, never by the LLM;
Gemma only gets the results and writes a commentary on them."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import pandas as pd

from energie import db


@dataclass
class Prices:
    electricity_eur_kwh: float
    gas_eur_kwh: float
    gas_correction_factor: float     # "Zustandszahl" on a German gas bill
    gas_calorific_value: float       # "Brennwert", kWh per m3

    @property
    def gas_kwh_per_m3(self) -> float:
        return self.gas_correction_factor * self.gas_calorific_value

    @classmethod
    def from_db(cls, con) -> "Prices":
        return cls(db.get_float(con, "electricity_eur_kwh"), db.get_float(con, "gas_eur_kwh"),
                   db.get_float(con, "gas_correction_factor"), db.get_float(con, "gas_calorific_value"))


# ---------- meter readings ----------

def readings(con: sqlite3.Connection, meter: str) -> pd.DataFrame:
    df = pd.read_sql_query("SELECT ts, value FROM meter_readings WHERE meter=? ORDER BY ts", con, params=(meter,))
    df["ts"] = pd.to_datetime(df["ts"])
    return df


def meter_intervals(con: sqlite3.Connection, meter: str) -> pd.DataFrame:
    """Consumption between consecutive readings, scaled to 24 h.

    With dense data (gas pulses from the collector) only the last reading of each day is used,
    so the result stays one interval per day."""
    df = readings(con, meter)
    df = df.groupby(df["ts"].dt.date).tail(1).reset_index(drop=True)
    out = pd.DataFrame({"t0": df["ts"].shift(), "t1": df["ts"], "v0": df["value"].shift(), "v1": df["value"]})
    out = out.dropna().reset_index(drop=True)
    out["hours"] = (out["t1"] - out["t0"]).dt.total_seconds() / 3600
    out["delta"] = out["v1"] - out["v0"]
    out["per_24h"] = out["delta"] / out["hours"] * 24
    return out


def overall_rate(con: sqlite3.Connection, meter: str) -> dict:
    df = readings(con, meter)
    if len(df) < 2:
        return {}
    hours = (df["ts"].iloc[-1] - df["ts"].iloc[0]).total_seconds() / 3600
    delta = df["value"].iloc[-1] - df["value"].iloc[0]
    return {"from": df["ts"].iloc[0], "to": df["ts"].iloc[-1], "days": hours / 24,
            "delta": delta, "per_day": delta / hours * 24, "per_year": delta / hours * 24 * 365}


# ---------- Shelly 24 h totals ----------

def shelly(con: sqlite3.Connection, scope: str) -> pd.DataFrame:
    df = pd.read_sql_query("SELECT window_end, floor, name, energy_wh/1000.0 AS kwh FROM shelly_periods "
                           "WHERE scope=? ORDER BY window_end", con, params=(scope,))
    df["window_end"] = pd.to_datetime(df["window_end"])
    df["day"] = df["window_end"].dt.normalize()
    return df


def full_windows(con: sqlite3.Connection) -> set:
    """Windows that show every floor ever seen: before all floors had plugs, the Shelly total
    covers less of the house and would distort the coverage."""
    fl = shelly(con, "floor")
    if fl.empty:
        return set(shelly(con, "total")["window_end"])
    n = fl.groupby("window_end")["name"].nunique()
    return set(n[n == n.max()].index)


def coverage(con: sqlite3.Connection, tol_h: float = 3.0) -> pd.DataFrame:
    """Each Shelly 24 h window next to the main meter's interval with about the same bounds:
    how much of the house the plugs see, and the unmetered rest."""
    iv = meter_intervals(con, "electricity")
    tot = shelly(con, "total")
    full = full_windows(con)
    rows = []
    for _, s in tot.iterrows():
        start = s["window_end"] - pd.Timedelta(hours=24)
        m = iv[((iv["t1"] - s["window_end"]).abs() <= pd.Timedelta(hours=tol_h)) &
               ((iv["t0"] - start).abs() <= pd.Timedelta(hours=tol_h))]
        if m.empty:
            continue
        r = m.iloc[0]
        rows.append({"day": s["window_end"].normalize(), "meter_kwh": r["per_24h"], "shelly_kwh": s["kwh"],
                     "unmetered_kwh": r["per_24h"] - s["kwh"], "coverage": s["kwh"] / r["per_24h"],
                     "full": s["window_end"] in full})
    return pd.DataFrame(rows, columns=["day", "meter_kwh", "shelly_kwh", "unmetered_kwh", "coverage", "full"])


def device_daily(con: sqlite3.Connection) -> pd.DataFrame:
    """Day x device matrix in kWh. The Shelly app leaves out a device with ~0 Wh, so gaps between
    a device's first and last appearance are filled with 0."""
    d = shelly(con, "device")
    if d.empty:
        return pd.DataFrame()
    piv = d.pivot_table(index="day", columns="name", values="kwh", aggfunc="sum")
    for c in piv.columns:
        first, last = piv[c].first_valid_index(), piv[c].last_valid_index()
        piv.loc[first:last, c] = piv.loc[first:last, c].fillna(0.0)
    return piv


def device_stats(con: sqlite3.Connection, p: Prices) -> pd.DataFrame:
    cols = ["floor", "name", "mean", "min", "max", "count", "cv", "avg_w", "kwh_year", "eur_year"]
    piv = device_daily(con)
    if piv.empty:
        return pd.DataFrame(columns=cols)
    floors = shelly(con, "device").drop_duplicates("name", keep="last").set_index("name")["floor"]
    long = piv.reset_index().melt(id_vars="day", var_name="name", value_name="kwh").dropna()
    long["floor"] = long["name"].map(floors)
    g = long.groupby(["floor", "name"])["kwh"].agg(["mean", "min", "max", "count", "std"]).reset_index()
    g["cv"] = (g["std"] / g["mean"]).fillna(0)
    g["avg_w"] = g["mean"] * 1000 / 24
    g["kwh_year"] = g["mean"] * 365
    g["eur_year"] = g["kwh_year"] * p.electricity_eur_kwh
    return g.sort_values("kwh_year", ascending=False).reset_index(drop=True)[cols]


def gas_intervals(con: sqlite3.Connection, p: Prices) -> pd.DataFrame:
    iv = meter_intervals(con, "gas")
    iv["kwh_per_24h"] = iv["per_24h"] * p.gas_kwh_per_m3
    return iv


def summary(con: sqlite3.Connection, p: Prices) -> dict:
    el = overall_rate(con, "electricity")
    gas = overall_rate(con, "gas")
    cov = coverage(con)
    cov = cov[cov["full"]]
    # gas is deliberately not extrapolated to a year: a few weeks are not representative
    return {
        "electricity": el,
        "gas": (gas | {"kwh_per_day": gas["per_day"] * p.gas_kwh_per_m3}) if gas else {},
        "coverage_mean": cov["coverage"].mean() if len(cov) else None,
        "unmetered_mean_kwh": cov["unmetered_kwh"].mean() if len(cov) else None,
        "electricity_eur_year": el["per_year"] * p.electricity_eur_kwh if el else None,
    }


# ---------- collector data (cumulative counters) ----------

def counter_hourly(con: sqlite3.Connection, days: int = 7) -> pd.DataFrame:
    """kWh per hour and device from cumulative counters.

    A counter that drops (plug restarted) starts again from 0: the new value is the increase."""
    df = pd.read_sql_query(
        "SELECT ts, name, total_wh FROM shelly_counters WHERE ts >= "
        "(SELECT datetime(MAX(ts), ?) FROM shelly_counters) ORDER BY name, ts", con, params=(f"-{days} days",))
    if df.empty:
        return pd.DataFrame(columns=["hour", "name", "kwh"])
    df["ts"] = pd.to_datetime(df["ts"])
    df["d"] = df.groupby("name")["total_wh"].diff()
    df.loc[df["d"] < 0, "d"] = df["total_wh"]
    df = df.dropna(subset=["d"])
    df["hour"] = df["ts"].dt.floor("h")
    return (df.groupby(["hour", "name"])["d"].sum() / 1000).rename("kwh").reset_index()


def base_load(con: sqlite3.Connection, days: int = 7) -> pd.DataFrame:
    """Night base load (01:00-05:00) per device in W: what runs while nobody uses anything."""
    h = counter_hourly(con, days)
    if h.empty:
        return pd.DataFrame(columns=["name", "base_w"])
    night = h[h["hour"].dt.hour.between(1, 4)]
    return (night.groupby("name")["kwh"].median() * 1000).rename("base_w").reset_index()


# ---------- findings ----------

def insights(con: sqlite3.Connection, p: Prices) -> list[dict]:
    """Rule-based findings as codes with numbers; the frontend words them in the UI language."""
    out: list[dict] = []
    sm = summary(con, p)
    if sm["coverage_mean"] is not None:
        out.append({"code": "coverage", "pct": round(sm["coverage_mean"] * 100),
                    "unmetered_kwh": round(sm["unmetered_mean_kwh"], 1)})
    dev = device_stats(con, p)
    # steady consumers: little day-to-day variation, a noticeable load all the time (fridges, pumps, aquariums)
    steady = dev[(dev["count"] >= 5) & (dev["cv"] < 0.15) & (dev["avg_w"] >= 5)]
    for _, r in steady.head(4).iterrows():
        out.append({"code": "steady", "name": r["name"], "w": round(r["avg_w"]),
                    "kwh_year": round(r["kwh_year"]), "eur_year": round(r["eur_year"])})
    cov = coverage(con)
    cov = cov[cov["full"]]
    if len(cov) >= 3:
        hi = cov.loc[cov["unmetered_kwh"].idxmax()]
        out.append({"code": "max_unmetered", "kwh": round(hi["unmetered_kwh"], 1), "day": f"{hi['day']:%Y-%m-%d}"})
    g = gas_intervals(con, p)
    if len(g) >= 5:
        out.append({"code": "gas_trend", "recent": round(g.tail(3)["per_24h"].mean(), 2),
                    "start": round(g.head(2)["per_24h"].mean(), 2)})
    bl = base_load(con)
    if len(bl):
        top = bl.sort_values("base_w", ascending=False).head(4)
        out.append({"code": "base_load", "items": [[r["name"], round(r["base_w"])] for _, r in top.iterrows()]})
    return out
