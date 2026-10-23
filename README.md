# austinmtbtrailconditions

Wet/dry and temperature predictions for Austin-area mountain bike trails, built from
[LCRA Hydromet](https://hydromet.lcra.org/) weather station data, plus
[Open-Meteo](https://open-meteo.com/) soil moisture and rain forecasts at each trail.

A GitHub Action refreshes the data every 4 hours and commits it straight to `main`,
so the app can read the files directly from GitHub.

## Data files

| File | What it is | URL |
|---|---|---|
| `predictions.json` | Prediction for each trail and each assigned weather station | https://raw.githubusercontent.com/mckenziehelper/austinmtbtrailconditions/main/predictions.json |
| `lcra.json` | Raw data for every LCRA station, as fetched | https://raw.githubusercontent.com/mckenziehelper/austinmtbtrailconditions/main/lcra.json |
| `weather.json` | Open-Meteo summary at each trail's own coordinates | https://raw.githubusercontent.com/mckenziehelper/austinmtbtrailconditions/main/weather.json |
| `trails.json` | Trail list and permanent trail facts. Edit by hand. | — |

All generated files have the same shape: `{"updated": "<UTC ISO 8601>", "results": [...]}`.

### `predictions.json` entries

Trail entries use the LCRA station closest to the trail's coordinates:

```json
{"trail": "Brushy Creek", "station": "Cedar Park 3 SSW", "prediction": "Dry", "isDry": true, "temperature": "Cool"}
```

Station entries cover each `station` named in `trails.json`:

```json
{"station": "Cedar Park 3 SSW", "prediction": "Dry", "isDry": true, "temperature": "Cool"}
```

Trail entries can also carry these optional fields:

| Field | Meaning |
|---|---|
| `soilMoisture` | Open-Meteo modeled topsoil moisture (0–1 cm), m³/m³; higher is wetter |
| `rainChance24h` | Highest hourly chance of rain over the next 24 hours, % |
| `crossingStage`, `crossingFlow` | Current stage (ft) and flow (cfs) at the gauge for the trail's creek crossing |
| `source` | `claude` if the Wet/Dry call came from Claude's assessment, `rule` if from the rain rule |
| `reason` | Claude's one-sentence explanation (only when `source` is `claude`) |
| `status` | `open`, `closed` or `caution`, from Claude's daily check of status pages |
| `statusNote` | Short note on the status, e.g. why it's closed |
| `statusChecked` | When that status was checked (UTC) |

If Open-Meteo is unavailable, its fields are left out and the rest of the run still completes.

A station named in `trails.json` that has no temperature reading in the LCRA data is skipped.

### `weather.json` entries

```json
{"trail": "Walnut Creek", "soilMoisture": 0.209, "soilMoisture1to3cm": 0.244, "rainLast72h": 0.0,
 "evaporationLast72h": 0.46, "rainNext24h": 0.0, "rainChance24h": 0}
```

Rain and `evaporationLast72h` are in inches. `evaporationLast72h` is Open-Meteo's reference
evapotranspiration: roughly how much water the weather could dry out of the ground.

## Prediction rules

**Wet** if the station's `rainfall1Day` is greater than 0 or `rainfall2Days` is greater than 0.5 inches.
Otherwise **Dry**.

Temperature label (°F):

| Temperature | Label |
|---|---|
| below 32 | Very Cold |
| 32–49 | Cold |
| 50–62 | Cool |
| 63–72 | Mild |
| 73–82 | Warm |
| 83–92 | Hot |
| 93 and above | Very Hot |

## Claude's calls and closure status

Two Claude scheduled tasks add judgment the rain rule can't:

- **Trail calls** (about 5am and 2pm Central) read `lcra.json`, `weather.json` and
  `trails.json` and write `assessments.json`: a Wet/Dry call and one-sentence reason per trail.
- **Closures** (about 4:30am Central) read each trail's status page and recent rider reports and
  write `status.json`: `open`, `closed` or `caution` per trail, with a note.

They push to `claude/trail-data-*` branches. The workflow takes only those two files (if valid and
newer than `main`'s copy), folds them into `predictions.json`, and deletes the branch.

Every 4-hour run applies them like this:

- Claude's call is used while it is under 24 hours old **and** the trail's station has recorded no
  rain since Claude made it. Otherwise the rain rule decides, so new rain always wins.
- Status is shown while it is under 72 hours old.
- If either file is missing or stale, predictions are rule-only and still complete.

## How it runs

`.github/workflows/update-predictions.yml` runs `runupdates.py` on GitHub's servers
every 4 hours (`0 */4 * * *`, UTC; GitHub sometimes starts scheduled runs a few minutes
late). It commits `predictions.json`, `lcra.json` and `weather.json` to `main` as github-actions[bot],
and skips the commit if nothing changed.

To run it now: **Actions → Update trail predictions → Run workflow**.

## Adding or changing a trail

Edit `trails.json` and push to `main`. The next run picks it up. Each trail needs
`name`, `latitude` and `longitude`; `station` is optional and adds a station entry
to `predictions.json`.

Optional permanent facts about a trail (leave out anything unknown):

| Field | Values |
|---|---|
| `surface` | `rocky`, `mixed` or `dirt` |
| `shade` | `heavy`, `partial` or `open` |
| `drainage` | `slow` if the trail is known to stay wet |
| `crossingGauge` | LCRA `siteName` of the river gauge for the trail's creek crossing |
| `closesWhenWet` | `true` if the land manager closes trails after rain |
| `seasonalClosure` | Recurring closure, e.g. `Nov-Dec` |
| `statusUrl` | Where the land manager posts trail status |
| `notes` | Anything else that stays true, in a sentence or two |

Keep temporary information (this weekend's race, a downed tree) out of `trails.json`.

## Running locally

```sh
pip install -r requirement.txt
python runupdates.py
```

This writes the generated files to the repo root. The workflow does the committing, so you
don't need to commit them yourself.
