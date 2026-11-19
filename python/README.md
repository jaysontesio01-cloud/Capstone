# Pump monitoring backend (Python)

Flask API that receives vibration and temperature data from the ESP32,
detects anomalies, forecasts maintenance and serves the Flutter app.

## Run

```
cd python
pip install -r requirements.txt
python app.py
```

The API listens on http://localhost:5000 (set `PORT` to change it). On Render
it runs as `gunicorn app:app` from this folder (see `../render.yaml`).
Set `FIREBASE_ENABLED=0` to run without Firebase.

## Test

```
cd python
python -m unittest
```

The tests use temporary copies of the data files and never connect to
Firebase, so they don't change anything in `data/`.

## Folder layout

```
python/
├── app.py                     Entry point: creates and runs the Flask app
├── pump_monitor/              The backend code
│   ├── config.py              File locations and all settings (environment variables)
│   ├── formulas/              Formulas 1-3: resultant acceleration, RMS, % deviation
│   ├── signal_processing.py   Sample clean-up, SVD denoising, windows, features
│   ├── anomaly_model.py       Bearing anomaly model (Isolation Forest), pump part checks
│   ├── regression_baseline.py Learned normal-vibration baseline and online learning
│   ├── iso20816.py            ISO 20816 vibration severity zones A-D
│   ├── window_analysis.py     Runs all checks above on a batch of windows
│   ├── predictive_maintenance.py  Risk score, failure forecast, maintenance log
│   ├── vibration_baseline.py  ISO 17359 per-pump baseline and deviation (Formula 3)
│   ├── health_assessment.py   App verdict vs the healthy pump: axes, health %, problems
│   ├── test_session.py        Timed tests started from the app
│   ├── device_registry.py     Registered devices and their sample buffers
│   ├── recorder.py            Raw sensor recordings to CSV
│   ├── firebase_store.py      Firebase Realtime Database
│   └── routes/                API endpoints, one file per area (list in routes/__init__.py)
├── data/                      Training data and files the backend writes while running
├── models/                    Trained models (.pkl)
├── scripts/                   Retrain the models, Firebase setup help
├── notebooks/                 Jupyter notebook experiments
├── legacy/                    Old CSV-upload web page from before the Flutter app
├── docs/                      API documentation for the Flutter app and ESP32
└── tests/
```

### Data files (`data/`)

| File | What it is |
| --- | --- |
| `healthy_reference/healthy_pump_training_data.csv` | **What "normal" means**: the healthy pump's running data (runs 2 and 3, 17 Nov 2026); trains both models and the pump part limits |
| `healthy_reference/ESP32-002_healthy_reference.json` | Summary of the healthy pump (vibration, per axis, temperature); compare faulty runs with it |
| `healthy_reference/ESP32-002_healthy_raw_*.csv` | The raw healthy runs the training data was built from |
| `app_tests/` | Summaries of timed tests started from the app (verdict, health, problems) |
| `faulty_tests/` | Faulty pump test (ESP32-001, 18 Nov 2026): summary JSON and the raw 20-minute running segment |
| `vibration_baseline.json` | Healthy baseline per pump (ESP32-002 and ESP32-001: from the healthy pump data) |
| `pump_predictive_history.csv` | Risk score history per pump (failure forecast) |
| `device_runtime_hours.csv` | Running hours per pump |
| `maintenance_history.csv` | Maintenance recommendations shown in the app |
| `recordings/` | Raw recordings started with `/api/esp32/recording/<id>/start` |
| `accelerometer.csv` | Generic dataset the models used before the healthy pump was recorded (kept for reference) |
| `archive/` | Data set aside when the healthy pump became the reference |

### Models (`models/`)

| File | Used by |
| --- | --- |
| `bearing_iforest_model.pkl`, `bearing_scaler.pkl` | Bearing anomaly model, trained on the healthy pump |
| `pump_part_thresholds.json` | Per-axis pump part limits learned from the healthy pump |
| `linear_regression_model.pkl`, `linear_regression_metadata.json` | Learned regression baseline (information only) |
| `archive_accelerometer_dataset/` | The earlier models trained on `accelerometer.csv` |
| `scaler.pkl`, `xg_reg.pkl` | Not used by the backend (earlier experiments) |

If a saved model doesn't load with the installed scikit-learn version, the
backend retrains it from the healthy pump data at start-up.

### How the backend judges a pump

- **Anomaly**: the window is unusual for the healthy-pump model, a pump part
  exceeds its healthy limit, or vibration is 50% or more away from the pump's
  healthy baseline (Formula 3).
- **Risk score / shutdown forecast**: how far vibration has risen above the
  healthy baseline: 60 (WARNING) at 50% above, 90 (shut down) at 100% above.
  Pumps without a healthy baseline fall back to ISO 20816.
- **ISO 20816 zone**: reported for information only. At 100 samples/s the
  velocity estimate is not reliable for this pump (the healthy pump reads
  zone D).
- All data (live, uploads, training) is processed the same way: readings at
  the sensor's ±16 g limit replaced, 128-sample windows, SVD-denoised.

- **App dashboard** (`/api/esp32/live/<id>`): per-axis bars with the live
  ADXL345 data of the last ~5 s, pump running or not (Healthy up to the healthy
  limit, Warning up to 2x, Faulty above; "off" while the pump is off), the verdict over the last
  ~60 s (HEALTHY / MONITOR / WARNING / FAULTY), machine health = 100 - risk, and
  the possible problems with the data behind them. The "no risk" bands come
  from the healthy pump's own 60 s range. `/predict`, the risk trend and the
  failure forecast use the same risk. See `health_assessment.py`.

To add more healthy data: append running rows to
`healthy_pump_training_data.csv`, then run both training scripts and
`analyze_recording.py ... --save ... --install-baseline ESP32-002`.

## Scripts

Run them from the `python` folder:

| Command | What it does |
| --- | --- |
| `python scripts/train_bearing_model.py` | Retrain the bearing anomaly model |
| `python scripts/train_regression_model.py` | Retrain the regression baseline and print MAE / R2 |
| `python scripts/firebase_setup_help.py` | Show where to put the Firebase key |
| `python scripts/serial_bridge.py --port COM5` | Forward ESP32 data from the USB cable to the backend (sketch with `USE_USB_LINK = true`; needs `pip install pyserial`) |
| `python scripts/analyze_recording.py <recording.csv> --start HH:MM:SS --end HH:MM:SS` | Summarize a pump run; `--save`/`--install-baseline` make it the healthy reference, `--compare-to` compares a faulty run with it |
| `python legacy/accelerometer.py` | Old CSV-upload web page on port 5000 |

Restart the backend after retraining so it loads the new models.

## Settings

All settings are in `pump_monitor/config.py`. The ones marked `(env)` can be
changed with an environment variable of the same name, for example:

```
set SENSOR_SAMPLE_RATE_HZ=200        (Windows cmd)
$env:SENSOR_SAMPLE_RATE_HZ = "200"   (PowerShell)
```
