#!venv/bin/python3

import logging
import logging.handlers
import logging.config
import time
import requests
import json
import datetime
from google.cloud import storage
import toml
import csv
from geopy import distance

import os
import sys
import copy

from dotenv import load_dotenv, find_dotenv


ROUTINE_FIRE_URL = "https://api.anthropic.com/v1/claude_code/routines/trig_01SdJmmvQ7RBfokcKmwnE7BH/fire"

# Fields from each LCRA station that the routine needs to make a prediction
STATION_FIELDS = [
    "siteName",
    "dateTime",
    "rainfall1Hour",
    "rainfall2Hours",
    "rainfall1Day",
    "rainfall2Days",
    "rainfall3Days",
    "rainfall1Week",
    "temperature",
    "maximumTemperature",
    "minimumTemperature",
]


def build_prediction_request(hydromet_data):
    with open("trails.json", "r") as f:
        trails = json.load(f)
    trimmed_hydromet = [i for i in hydromet_data if "temperature" in i]
    trail_stations = []
    for trail in trails:
        hydro_copy = copy.deepcopy(trimmed_hydromet)
        for hydro in hydro_copy:
            hydro["distance"] = distance.distance(
                (hydro["latitude"], hydro["longitude"]),
                (trail["latitude"], trail["longitude"]),
            ).mi
        closest_hydro = min(hydro_copy, key=lambda k: k["distance"])
        trail_stations.append(
            {"trail": trail["name"], "station": closest_hydro["siteName"]}
        )
    weatherstations = sorted(set(x["station"] for x in trails))
    needed = set(weatherstations) | set(x["station"] for x in trail_stations)
    stations = [
        {k: h.get(k) for k in STATION_FIELDS}
        for h in trimmed_hydromet
        if h["siteName"] in needed
    ]
    return {
        "trails": trail_stations,
        "weatherstations": weatherstations,
        "stations": stations,
    }


def fire_prediction_routine(logger, hydromet_data):
    _ = load_dotenv(find_dotenv())  # read local .env file
    payload = build_prediction_request(hydromet_data)
    logger.debug(json.dumps(payload, indent=2))
    response = requests.post(
        ROUTINE_FIRE_URL,
        headers={
            "Authorization": f"Bearer {os.getenv('CLAUDE_API_KEY', '')}",
            "anthropic-beta": "experimental-cc-routine-2026-04-01",
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={"text": json.dumps(payload)},
        timeout=60,
    )
    if not response.ok:
        logger.error("routine fire failed %s: %s", response.status_code, response.text)
        response.raise_for_status()
    result = response.json()
    logger.info("fired prediction routine: %s", result["claude_code_session_url"])
    return result


def get_austin_trail_conditions_csv(logger):
    logger.info("getting austintrailconditions csv")
    spreadsheet = "https://docs.google.com/spreadsheets/u/0/d/e/2PACX-1vT8vd0cOdPGg5l0HmhfxSZ7o0qwzrn2r93y4ZQ3Ajo8ZuXyJSAgZ8WtRDwyZnYzVMtApZXS5gHjHk03/pub?output=csv"
    response = requests.request("GET", spreadsheet, timeout=60000)
    responselines = response.text.splitlines()
    reader = csv.reader(responselines, delimiter=",")
    csvobject = []
    for row in reader:
        csvobject.append({"trail": row[0], "status": row[1], "updateTime": row[2]})
    csvobject.pop(0)
    full_response = {}
    full_response["updated"] = datetime.datetime.now().isoformat()
    full_response["results"] = csvobject
    dump_to_file(logger, full_response, "austintrailconditions.json")
    upload_to_bucket(logger, full_response, "austintrailconditions.json")


def get_lcra_hydromet(logger):
    logger.info("getting LCRA hydromet")
    response = requests.request(
        "GET",
        "https://hydromet.lcra.org/api/GetDataForAllSites",
        verify=False,
        timeout=60000,
    )
    json_response = response.json()
    full_response = {}
    full_response["updated"] = datetime.datetime.now().isoformat()
    full_response["results"] = json_response
    # logger.debug(json.dumps(full_response,indent=2))
    dump_to_file(logger, full_response, "lcra.json")
    upload_to_bucket(logger, full_response, "lcra.json")
    return json_response


def dump_to_file(logger, data, filename):
    logger.info("writing %s ", filename)
    with open(filename, "w") as f:
        json.dump(data, f, indent=5)


def upload_to_bucket(logger, data, filename):
    upload_string = json.dumps(data)
    logger.info("uploading %s ", filename)
    logger.debug(upload_string)
    storage_client = storage.Client(project="austinmountainbiking")
    bucket = storage_client.bucket("austinmountainbiking.appspot.com")
    blob = bucket.blob(filename)
    blob.upload_from_string(upload_string)

    bucket.cors = [
        {
            "origin": [
                "https://austinmountainbiking.web.app",
                "http://localhost:8100",
                "http://172.19.47.225:8100",
            ],
            "method": ["GET"],
            "maxAgeSeconds": 600,
        }
    ]
    bucket.patch()
    blob.cache_control = "public, max-age=60"
    blob.patch()
    blob.make_public()
    bucket_info = storage_client.get_bucket("austinmountainbiking.appspot.com")
    logger.debug(f"ID: {bucket_info.id}")
    logger.debug(f"Name: {bucket_info.name}")
    logger.debug(f"Storage Class: {bucket_info.storage_class}")
    logger.debug(f"Location: {bucket_info.location}")
    logger.debug(f"Location Type: {bucket_info.location_type}")
    logger.debug(f"Cors: {bucket_info.cors}")
    logger.debug(f"Default Event Based Hold: {bucket_info.default_event_based_hold}")
    logger.debug(f"Default KMS Key Name: {bucket_info.default_kms_key_name}")
    logger.debug(f"Metageneration: {bucket_info.metageneration}")
    logger.debug(
        f"Public Access Prevention: {bucket_info.iam_configuration.public_access_prevention}"
    )
    logger.debug(
        f"Retention Effective Time: {bucket_info.retention_policy_effective_time}"
    )
    logger.debug(f"Retention Period: {bucket_info.retention_period}")
    logger.debug(f"Retention Policy Locked: {bucket_info.retention_policy_locked}")
    logger.debug(f"Requester Pays: {bucket_info.requester_pays}")
    logger.debug(f"Self Link: {bucket_info.self_link}")
    logger.debug(f"Time Created: {bucket_info.time_created}")
    logger.debug(f"Versioning Enabled: {bucket_info.versioning_enabled}")
    logger.debug(f"Labels: {bucket_info.labels}")
    logger.debug(f"cache_control: {blob.cache_control}")


def main():
    with open("config/config.toml") as f:
        config = toml.load(f)

    logging.config.dictConfig(config["logging"])
    logging.captureWarnings(True)
    logger = logging.getLogger("austinmountainbiking")
    logger.debug(json.dumps(config, indent=2))
    logger.info("----loaded config-----")
    # get_austin_trail_conditions_csv(logger)
    hydromet_data = get_lcra_hydromet(logger)
    fire_prediction_routine(logger, hydromet_data)
    logger.info("----done--------------")
    return 0


if __name__ == "__main__":
    sys.exit(main())
