# austinmtbtrailconditions

Wet/dry and temperature predictions for Austin-area mountain bike trails, built from
[LCRA Hydromet](https://hydromet.lcra.org/) weather station data.

A GitHub Action refreshes the data every 4 hours and commits it straight to `main`,
so the app can read the files directly from GitHub.

## Data files

| File | What it is | URL |
|---|---|---|
| `predictions.json` | Prediction for each trail and each assigned weather station | https://raw.githubusercontent.com/mckenziehelper/austinmtbtrailconditions/main/predictions.json |
| `lcra.json` | Raw data for every LCRA station, as fetched | https://raw.githubusercontent.com/mckenziehelper/austinmtbtrailconditions/main/lcra.json |
| `trails.json` | Trail list (name, location, links, assigned station). Edit by hand. | — |

Both generated files have the same shape: `{"updated": "<UTC ISO 8601>", "results": [...]}`.

### `predictions.json` entries

Trail entries use the LCRA station closest to the trail's coordinates:

```json
{"trail": "Brushy Creek", "station": "Cedar Park 3 SSW", "prediction": "Dry", "isDry": true, "temperature": "Cool"}
```

Station entries cover each `station` named in `trails.json`:

```json
{"station": "Cedar Park 3 SSW", "prediction": "Dry", "isDry": true, "temperature": "Cool"}
```

A station named in `trails.json` that has no temperature reading in the LCRA data is skipped.

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

## How it runs

`.github/workflows/update-predictions.yml` runs `runupdates.py` on GitHub's servers
every 4 hours (`0 */4 * * *`, UTC; GitHub sometimes starts scheduled runs a few minutes
late). It commits `predictions.json` and `lcra.json` to `main` as github-actions[bot],
and skips the commit if nothing changed.

To run it now: **Actions → Update trail predictions → Run workflow**.

## Adding or changing a trail

Edit `trails.json` and push to `main`. The next run picks it up. Each trail needs
`name`, `latitude` and `longitude`; `station` is optional and adds a station entry
to `predictions.json`.

## Running locally

```sh
pip install -r requirement.txt
python runupdates.py
```

This writes both files to the repo root. The workflow does the committing, so you
don't need to commit them yourself.
