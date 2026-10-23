#!/usr/bin/env python3
"""Fetch LCRA hydromet data and write trail condition predictions.

Writes two files at the repository root:
  lcra.json         - raw LCRA station data, wrapped as {"updated", "results"}
  predictions.json  - Wet/Dry and temperature label per trail and per station

Run every 4 hours by .github/workflows/update-predictions.yml, which commits
both files to main.
"""

import datetime
import json
import logging
import math
import sys
from pathlib import Path

import requests
import urllib3

LCRA_URL = "https://hydromet.lcra.org/api/GetDataForAllSites"
ROOT = Path(__file__).resolve().parent

logger = logging.getLogger("austinmountainbiking")


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_lcra():
    # LCRA's certificate chain doesn't verify, as in the original script.
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    response = requests.get(LCRA_URL, verify=False, timeout=60)
    response.raise_for_status()
    return response.json()


def num(value):
    """LCRA reports numbers as strings ("0.00"); blanks become None."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def is_wet(station):
    rain_1day = num(station.get("rainfall1Day")) or 0
    rain_2days = num(station.get("rainfall2Days")) or 0
    return rain_1day > 0 or rain_2days > 0.5


def temperature_label(temp):
    if temp is None:
        return None
    if temp < 32:
        return "Very Cold"
    if temp < 50:
        return "Cold"
    if temp < 63:
        return "Cool"
    if temp < 73:
        return "Mild"
    if temp < 83:
        return "Warm"
    if temp < 93:
        return "Hot"
    return "Very Hot"


def prediction(station):
    wet = is_wet(station)
    return {
        "prediction": "Wet" if wet else "Dry",
        "isDry": not wet,
        "temperature": temperature_label(num(station.get("temperature"))),
    }


def miles_between(lat1, lon1, lat2, lon2):
    """Great-circle (haversine) distance in miles."""
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    a = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 3958.8 * 2 * math.asin(math.sqrt(a))


def closest_station(trail, stations):
    return min(
        stations,
        key=lambda s: miles_between(
            num(s["latitude"]), num(s["longitude"]), trail["latitude"], trail["longitude"]
        ),
    )


def build_predictions(trails, lcra_data):
    # Only stations that report temperature are useful for a prediction.
    stations = [
        s
        for s in lcra_data
        if num(s.get("temperature")) is not None
        and num(s.get("latitude")) is not None
        and num(s.get("longitude")) is not None
    ]
    by_name = {s["siteName"]: s for s in stations}
    results = []

    for trail in trails:
        station = closest_station(trail, stations)
        results.append(
            {"trail": trail["name"], "station": station["siteName"], **prediction(station)}
        )

    for name in sorted({t["station"] for t in trails if t.get("station")}):
        station = by_name.get(name)
        if station is None:
            logger.warning("no LCRA data for station %r, skipping", name)
            continue
        results.append({"station": name, **prediction(station)})

    return results


def write_json(filename, data):
    path = ROOT / filename
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    logger.info("wrote %s", path.name)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    with open(ROOT / "trails.json") as f:
        trails = json.load(f)

    lcra_data = fetch_lcra()
    updated = utc_now()
    logger.info("fetched %d LCRA stations", len(lcra_data))

    results = build_predictions(trails, lcra_data)
    if not results:
        logger.error("no predictions produced; leaving files unchanged")
        return 1

    write_json("lcra.json", {"updated": updated, "results": lcra_data})
    write_json("predictions.json", {"updated": updated, "results": results})
    return 0


if __name__ == "__main__":
    sys.exit(main())
