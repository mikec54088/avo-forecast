"""Record National Weather Service forecasts AS ISSUED, hourly (2026-10-04).

Run:  uv run python scripts/weather_capture.py      (launchd, hourly at :41)
Out:  data/external/nws/date=YYYY-MM-DD/HHMMSS.parquet, log data/weather-capture.log

Why: ~41,000 resolved Kalshi markets are daily high / low / hourly temperature
in ~25 US cities, and the obvious outside number to price them from is the
NWS forecast for the settlement station. Observations are backfillable (ASOS
archives) and so are global model runs (Open-Meteo's previous-runs API), but
the NWS forecast as it stood at a given hour is NOT: api.weather.gov only
serves the current one. Same asymmetry as top-of-book snapshots, so it is
captured before anything is decided about using it. Measurement only; nothing
in the pipeline reads it yet.

What: the raw gridpoint series for each station's forecast cell --
maxTemperature, minTemperature, temperature, probabilityOfPrecipitation,
quantitativePrecipitation -- one row per (station, element, valid interval),
with the gridpoint's updateTime and our fetch time. ~30 stations x ~400 rows.

Stations: the airport/climate station each Kalshi city is believed to settle
on. Where that is uncertain (Dallas, Houston, Chicago), both candidates are
recorded; cheap, and settling which one Kalshi uses can wait for the analysis.

Exit 0 and a file on disk are NOT proof (2026-10-01 lesson). If fewer than 90%
of stations return data, print `!!! ALARM`, raise a desktop notification and
exit nonzero.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/external/nws"
GRID_CACHE = OUT / "stations.json"
UA = {"User-Agent": "avo-forecast weather capture (research; hourly)",
      "Accept": "application/geo+json"}

# Kalshi city code -> candidate settlement stations (ICAO).
STATIONS = {
    "NY": ["KNYC"], "CHI": ["KMDW", "KORD"], "AUS": ["KAUS"], "MIA": ["KMIA"],
    "LAX": ["KLAX"], "DEN": ["KDEN"], "PHIL": ["KPHL"], "ATL": ["KATL"],
    "BOS": ["KBOS"], "DAL": ["KDFW", "KDAL"], "DC": ["KDCA"], "EWR": ["KEWR"],
    "HOU": ["KHOU", "KIAH"], "LV": ["KLAS"], "MIN": ["KMSP"], "NOLA": ["KMSY"],
    "OKC": ["KOKC"], "PHX": ["KPHX"], "SAN": ["KSAN"], "SATX": ["KSAT"],
    "SDF": ["KSDF"], "SEA": ["KSEA"], "SFO": ["KSFO"], "TTN": ["KTTN"],
}
ELEMENTS = ("maxTemperature", "minTemperature", "temperature",
            "probabilityOfPrecipitation", "quantitativePrecipitation")
ALARM_FRACTION = 0.90


def get(client: httpx.Client, url: str) -> dict:
    for attempt in range(4):
        try:
            r = client.get(url)
            if r.status_code == 200:
                return r.json()
        except httpx.HTTPError:
            pass
        time.sleep(2 + 3 * attempt)
    raise RuntimeError(f"GET {url} failed")


def gridpoints(client: httpx.Client) -> dict[str, dict]:
    """station -> {wfo, x, y, lat, lon}; resolved once and cached."""
    cache = json.loads(GRID_CACHE.read_text()) if GRID_CACHE.exists() else {}
    for station in sorted({s for v in STATIONS.values() for s in v}):
        if station in cache:
            continue
        try:
            lon, lat = get(client, f"https://api.weather.gov/stations/{station}")[
                "geometry"]["coordinates"]
            p = get(client, f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}")["properties"]
            cache[station] = {"wfo": p["gridId"], "x": p["gridX"], "y": p["gridY"],
                              "lat": lat, "lon": lon}
        except Exception as exc:  # resolved again next run
            print(f"  could not resolve {station}: {exc!r}", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    GRID_CACHE.write_text(json.dumps(cache, indent=1, sort_keys=True))
    return cache


def notify(message: str) -> None:
    try:
        subprocess.run(["osascript", "-e",
                        f'display notification "{message[:120]}" with title "avo weather ALARM"'],
                       timeout=10, check=False)
    except Exception:
        pass


def main() -> int:
    started = datetime.now(timezone.utc)
    rows: list[dict] = []
    failed: list[str] = []
    with httpx.Client(headers=UA, timeout=30) as client:
        grid = gridpoints(client)
        city_of = {s: c for c, v in STATIONS.items() for s in v}
        for station, g in sorted(grid.items()):
            url = f"https://api.weather.gov/gridpoints/{g['wfo']}/{g['x']},{g['y']}"
            try:
                p = get(client, url)["properties"]
            except Exception:
                failed.append(station)
                continue
            fetched = datetime.now(timezone.utc)
            n0 = len(rows)
            for el in ELEMENTS:
                block = p.get(el) or {}
                for v in block.get("values", []):
                    start, _, dur = v["validTime"].partition("/")
                    rows.append({
                        "station": station, "city": city_of.get(station, ""),
                        "wfo": g["wfo"], "grid_x": g["x"], "grid_y": g["y"],
                        "element": el, "uom": block.get("uom", ""),
                        "valid_start": pd.Timestamp(start), "valid_duration": dur,
                        "value": v["value"],
                        "update_time": pd.Timestamp(p.get("updateTime")),
                        "fetched_at": fetched,
                    })
            if len(rows) == n0:
                failed.append(station)
            time.sleep(0.3)

    expected = len({s for v in STATIONS.values() for s in v})
    ok = expected - len(failed)
    path = None
    if rows:
        df = pd.DataFrame(rows)
        df["value"] = pd.to_numeric(df.value, errors="coerce")
        part = OUT / f"date={started:%Y-%m-%d}"
        part.mkdir(parents=True, exist_ok=True)
        path = part / f"{started:%H%M%S}.parquet"
        df.to_parquet(path, index=False)
    secs = (datetime.now(timezone.utc) - started).total_seconds()
    print(f"[weather] {started:%Y-%m-%d %H:%MZ} {ok}/{expected} stations, {len(rows):,} rows "
          f"in {secs:.0f}s -> {path}" + (f"; FAILED {','.join(failed)}" if failed else ""),
          flush=True)
    if ok < expected * ALARM_FRACTION:
        msg = f"!!! ALARM weather capture: only {ok}/{expected} stations returned forecasts"
        print(msg, flush=True)
        notify(msg)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
