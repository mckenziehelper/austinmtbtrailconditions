#!/usr/bin/env python3
"""Fetch LCRA hydromet and Open-Meteo data and write trail condition predictions.

Writes these files at the repository root:
  lcra.json         - raw LCRA station data, wrapped as {"updated", "results"}
  weather.json      - Open-Meteo soil moisture, rain and forecast at each trail
  predictions.json  - Wet/Dry and temperature label per trail and per station

It also folds in two files written by Claude scheduled tasks, when present:
  assessments.json  - Claude's Wet/Dry call and reason per trail (twice a day)
  status.json       - open/closed/caution per trail from status pages (daily)

Run every 4 hours by .github/workflows/update-predictions.yml, which commits
them to main.
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
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_HOURLY = [
    "soil_moisture_0_to_1cm",
    "soil_moisture_1_to_3cm",
    "precipitation",
    "precipitation_probability",
    "et0_fao_evapotranspiration",
]
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


def fetch_open_meteo(trails):
    """One Open-Meteo request for every trail's own coordinates."""
    params = {
        "latitude": ",".join(str(t["latitude"]) for t in trails),
        "longitude": ",".join(str(t["longitude"]) for t in trails),
        "hourly": ",".join(OPEN_METEO_HOURLY),
        "past_days": 3,
        "forecast_days": 2,
        "precipitation_unit": "inch",
        "timezone": "UTC",
    }
    response = requests.get(OPEN_METEO_URL, params=params, timeout=60)
    response.raise_for_status()
    data = response.json()
    # A single location comes back as an object, several as a list.
    return data if isinstance(data, list) else [data]


def summarize_open_meteo(location, now):
    """Reduce one location's hourly series to a few numbers about now."""
    hourly = location["hourly"]
    current = now.strftime("%Y-%m-%dT%H:00")
    if current not in hourly["time"]:
        return None
    i = hourly["time"].index(current)

    def total(name, start, end):
        values = [v for v in hourly[name][max(start, 0):end] if v is not None]
        return round(sum(values), 2) if values else None

    rain_chances = [v for v in hourly["precipitation_probability"][i + 1 : i + 25] if v is not None]
    return {
        "soilMoisture": hourly["soil_moisture_0_to_1cm"][i],
        "soilMoisture1to3cm": hourly["soil_moisture_1_to_3cm"][i],
        "rainLast72h": total("precipitation", i - 71, i + 1),
        "evaporationLast72h": total("et0_fao_evapotranspiration", i - 71, i + 1),
        "rainNext24h": total("precipitation", i + 1, i + 25),
        "rainChance24h": max(rain_chances) if rain_chances else None,
    }


def build_weather(trails):
    """Per-trail Open-Meteo summary, or None if Open-Meteo is unavailable."""
    try:
        locations = fetch_open_meteo(trails)
    except (requests.RequestException, ValueError) as e:
        logger.warning("Open-Meteo unavailable, skipping: %s", e)
        return None
    now = datetime.datetime.now(datetime.timezone.utc)
    weather = {}
    for trail, location in zip(trails, locations):
        try:
            summary = summarize_open_meteo(location, now)
        except (KeyError, TypeError, ValueError) as e:
            logger.warning("Open-Meteo data for %s unusable: %s", trail["name"], e)
            summary = None
        if summary:
            weather[trail["name"]] = summary
    return weather


def crossing(trail, lcra_by_name):
    """Current reading of the river gauge at a trail's creek crossing."""
    gauge = lcra_by_name.get(trail.get("crossingGauge"))
    if gauge is None:
        return {}
    return {
        "crossingStage": num(gauge.get("stage")),
        "crossingFlow": num(gauge.get("flow")),
    }


def build_predictions(trails, lcra_data, weather=None):
    # Only stations that report temperature are useful for a prediction.
    stations = [
        s
        for s in lcra_data
        if num(s.get("temperature")) is not None
        and num(s.get("latitude")) is not None
        and num(s.get("longitude")) is not None
    ]
    by_name = {s["siteName"]: s for s in stations}
    lcra_by_name = {s["siteName"]: s for s in lcra_data}
    weather = weather or {}
    results = []

    for trail in trails:
        station = closest_station(trail, stations)
        extra = {}
        if trail["name"] in weather:
            w = weather[trail["name"]]
            extra = {"soilMoisture": w["soilMoisture"], "rainChance24h": w["rainChance24h"]}
        results.append(
            {
                "trail": trail["name"],
                "station": station["siteName"],
                **prediction(station),
                **extra,
                **crossing(trail, lcra_by_name),
            }
        )

    for name in sorted({t["station"] for t in trails if t.get("station")}):
        station = by_name.get(name)
        if station is None:
            logger.warning("no LCRA data for station %r, skipping", name)
            continue
        results.append({"station": name, **prediction(station)})

    return results


# LCRA rainfall windows, shortest first, as (hours covered, field name).
RAIN_WINDOWS = [
    (1, "rainfall1Hour"),
    (2, "rainfall2Hours"),
    (3, "rainfall3Hours"),
    (6, "rainfall6Hours"),
    (12, "rainfall12Hours"),
    (24, "rainfall1Day"),
]
ASSESSMENT_MAX_AGE_HOURS = 24
STATUS_MAX_AGE_HOURS = 72


def parse_time(value):
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def read_claude_file(filename):
    """Load a file written by one of the Claude scheduled tasks, if present."""
    path = ROOT / filename
    if not path.exists():
        return None, {}
    try:
        with open(path) as f:
            data = json.load(f)
        return parse_time(data.get("updated")), {r["trail"]: r for r in data["results"]}
    except (ValueError, KeyError, TypeError) as e:
        logger.warning("ignoring unreadable %s: %s", filename, e)
        return None, {}


def rained_since(station, hours):
    """True if the station recorded rain in the window covering the last `hours`."""
    for window, field in RAIN_WINDOWS:
        if hours <= window:
            return (num(station.get(field)) or 0) > 0
    return True  # older than a day: treat as stale


def apply_claude(results, lcra_data, now):
    """Fold Claude's trail calls and closure status into the rule-based results.

    Claude's Wet/Dry call is used only while it is fresh and no rain has fallen
    at the trail's station since Claude made it; otherwise the rule stands.
    """
    lcra_by_name = {s["siteName"]: s for s in lcra_data}
    assessed_at, assessments = read_claude_file("assessments.json")
    checked_at, statuses = read_claude_file("status.json")

    assessment_age = (now - assessed_at).total_seconds() / 3600 if assessed_at else None
    status_fresh = checked_at and (now - checked_at).total_seconds() / 3600 <= STATUS_MAX_AGE_HOURS

    for entry in results:
        if "trail" not in entry:
            continue
        entry["source"] = "rule"
        call = assessments.get(entry["trail"])
        station = lcra_by_name.get(entry["station"], {})
        if (
            call
            and call.get("prediction") in ("Wet", "Dry")
            and assessment_age is not None
            and assessment_age <= ASSESSMENT_MAX_AGE_HOURS
            and not rained_since(station, max(assessment_age, 1))
        ):
            entry["prediction"] = call["prediction"]
            entry["isDry"] = call["prediction"] == "Dry"
            entry["source"] = "claude"
            if call.get("reason"):
                entry["reason"] = call["reason"]

        status = statuses.get(entry["trail"])
        if status_fresh and status and status.get("status") in ("open", "closed", "caution"):
            entry["status"] = status["status"]
            if status.get("statusNote"):
                entry["statusNote"] = status["statusNote"]
            entry["statusChecked"] = checked_at.strftime("%Y-%m-%dT%H:%M:%SZ")
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

    weather = build_weather(trails)
    if weather is not None:
        logger.info("fetched Open-Meteo data for %d trails", len(weather))

    results = build_predictions(trails, lcra_data, weather)
    if not results:
        logger.error("no predictions produced; leaving files unchanged")
        return 1
    apply_claude(results, lcra_data, datetime.datetime.now(datetime.timezone.utc))

    write_json("lcra.json", {"updated": updated, "results": lcra_data})
    if weather:
        write_json(
            "weather.json",
            {
                "updated": updated,
                "source": "Open-Meteo",
                "results": [{"trail": name, **w} for name, w in weather.items()],
            },
        )
    write_json("predictions.json", {"updated": updated, "results": results})
    return 0


if __name__ == "__main__":
    sys.exit(main())
