"""Settings for the pump monitoring backend: file locations and tuning values.

Settings marked (env) can be changed with an environment variable of the same
name, e.g. SENSOR_SAMPLE_RATE_HZ=200. The other modules read these values as
``config.NAME`` while they run, so a test can point one somewhere else with
``patch.object(config, "NAME", value)``.
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Folders and files
# ---------------------------------------------------------------------------
# The python/ folder. The Firebase service-account key is also looked for here.
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"  # sensor data the backend reads and writes
MODELS_DIR = BASE_DIR / "models"  # trained machine-learning models

# What the backend learns as "normal": the healthy pump's recorded running data
# (x/y/z in g), used for the anomaly model, the pump part limits and the
# regression baseline. Add more healthy runs to this file, then retrain with
# scripts/train_bearing_model.py and scripts/train_regression_model.py.
# (Before the healthy pump was recorded, the generic dataset
# data/accelerometer.csv was used; it is kept for reference.)
HEALTHY_REFERENCE_DIR = DATA_DIR / "healthy_reference"
TRAINING_DATA_FILE = HEALTHY_REFERENCE_DIR / "healthy_pump_training_data.csv"

# Bearing anomaly model (Isolation Forest), see anomaly_model.py.
BEARING_MODEL_FILE = MODELS_DIR / "bearing_iforest_model.pkl"
BEARING_SCALER_FILE = MODELS_DIR / "bearing_scaler.pkl"
# Per-axis pump part limits learned from the healthy pump: healthy median +
# HEALTHY_LIMIT_SIGMAS robust standard deviations.
PUMP_PART_THRESHOLDS_FILE = MODELS_DIR / "pump_part_thresholds.json"
HEALTHY_LIMIT_SIGMAS = 3.0

# Learned regression baseline, see regression_baseline.py. The backend
# retrains and saves it again while it runs (online learning).
REGRESSION_MODEL_FILE = MODELS_DIR / "linear_regression_model.pkl"
REGRESSION_METADATA_FILE = MODELS_DIR / "linear_regression_metadata.json"
ONLINE_TRAINING_FILE = DATA_DIR / "online_regression_training_data.csv"

# Written by the backend while it runs.
PREDICTIVE_HISTORY_FILE = DATA_DIR / "pump_predictive_history.csv"
DEVICE_RUNTIME_FILE = DATA_DIR / "device_runtime_hours.csv"
MAINTENANCE_HISTORY_FILE = DATA_DIR / "maintenance_history.csv"
VIBRATION_BASELINE_FILE = DATA_DIR / "vibration_baseline.json"

# Raw sensor recordings (ADXL345 x/y/z in g + MAX6675 temperature) of test
# sessions, e.g. a healthy pump and a defective pump. Same x/y/z columns as
# accelerometer.csv, so a recording can be re-analyzed via /api/analyze/csv.
RECORDINGS_DIR = DATA_DIR / "recordings"
RECORDING_COLUMNS = [
    "received_at", "batch_index", "x", "y", "z",
    "temperature_c", "pump_running", "vibration_rms_g",
]

# ---------------------------------------------------------------------------
# Firebase
# ---------------------------------------------------------------------------
# (env) FIREBASE_SERVICE_ACCOUNT_JSON: the service-account key as JSON text or
# as a file path. Without it, this file name is looked for in BASE_DIR and in
# your user folders (Desktop, Downloads, Documents, ...).
FIREBASE_CREDENTIALS_FILENAME = (
    "water-pump-maintenancesystem-firebase-adminsdk-fbsvc-9e43492550.json"
)
FIREBASE_DATABASE_URL = (
    "https://water-pump-maintenancesystem-default-rtdb.asia-southeast1.firebasedatabase.app/"
)
FIREBASE_ENABLED = os.getenv("FIREBASE_ENABLED", "1").lower() not in {"0", "false", "no"}  # (env)

# ---------------------------------------------------------------------------
# Sensor data and analysis windows
# ---------------------------------------------------------------------------
WINDOW_SIZE = 128  # samples per analysis window
SENSOR_SAMPLE_RATE_HZ = float(os.getenv("SENSOR_SAMPLE_RATE_HZ", "100"))  # (env)
# Keep memory bounded on the server (analysis only needs the last window). (env)
MAX_DEVICE_BUFFER_SAMPLES = max(128, int(os.getenv("MAX_DEVICE_BUFFER_SAMPLES", "2048")))
# A pump with a hydraulic pressure sensor counts as idle at or below this. (env)
PUMP_RUNNING_PRESSURE_THRESHOLD_BAR = float(
    os.getenv("PUMP_RUNNING_PRESSURE_THRESHOLD_BAR", "0.1")
)
# Hankel-SVD denoising: components kept, and the minimum window to apply it. (env)
PUMP_SVD_RANK = max(1, int(os.getenv("PUMP_SVD_RANK", "2")))
PUMP_SVD_MIN_SAMPLES = max(4, int(os.getenv("PUMP_SVD_MIN_SAMPLES", "16")))
# The ADXL345 is set to +/-16 g. Readings at that limit are garbled I2C
# transfers (seen while the pump motor runs), not vibration: analysis replaces
# them with the nearest good reading. Recordings keep the raw values.
ACCEL_CLIP_LIMIT_G = 15.5
# Fallback idle check for devices that don't send pump_running. Must sit just
# above sensor noise: 0.06 g hid real ISO zone C vibration as "idle".
# Compared with the three-axis RMS (Formula 2); 0.0052 g equals the former
# 0.003 g per-axis-average limit (x sqrt(3)). (env)
IDLE_VIBRATION_RMS_THRESHOLD = float(os.getenv("IDLE_VIBRATION_RMS_THRESHOLD", "0.0052"))

# ---------------------------------------------------------------------------
# ISO 20816 vibration severity
# ---------------------------------------------------------------------------
ISO20816_MACHINE_CLASS = os.getenv("ISO20816_MACHINE_CLASS", "class_ii").lower()  # (env)
ISO20816_LOW_CUT_HZ = float(os.getenv("ISO20816_LOW_CUT_HZ", "2"))  # (env)
G_TO_MM_PER_S2 = 9806.65
# Zone boundaries in mm/s RMS: A/B, B/C and C/D.
ISO20816_VELOCITY_LIMITS_MM_S = {
    "class_i": {"ab": 0.71, "bc": 1.8, "cd": 4.5},
    "class_ii": {"ab": 1.12, "bc": 2.8, "cd": 7.1},
    "class_iii": {"ab": 1.8, "bc": 4.5, "cd": 11.2},
    "class_iv": {"ab": 2.8, "bc": 7.1, "cd": 18.0},
}

# ---------------------------------------------------------------------------
# Learned regression baseline
# ---------------------------------------------------------------------------
REGRESSION_FEATURE_COLUMNS = ["x", "y", "z"]
REGRESSION_TARGET_COLUMN = "learned_vibration"
MAX_ONLINE_TRAINING_ROWS = 10000
MIN_REGRESSION_TRAINING_ROWS = 20

# ---------------------------------------------------------------------------
# Predictive maintenance
# ---------------------------------------------------------------------------
PREDICTIVE_FAILURE_RISK_THRESHOLD = 90.0
PREDICTIVE_ALERT_THRESHOLD = PREDICTIVE_FAILURE_RISK_THRESHOLD
# No failure forecast (and no shutdown alert from a forecast) while the risk is
# below this: the pump is still within the healthy pump's range.
PREDICTIVE_FORECAST_MIN_RISK = 30.0
PREDICTIVE_SHUTDOWN_LEAD_DAYS = 7
# Save at most one forecast/maintenance point per device per interval. (env)
PREDICTIVE_HISTORY_INTERVAL_MINUTES = float(os.getenv("PREDICTIVE_HISTORY_INTERVAL_MINUTES", "10"))
# Only the most recent days are used to fit the wear trend. (env)
PREDICTIVE_TREND_WINDOW_DAYS = float(os.getenv("PREDICTIVE_TREND_WINDOW_DAYS", "7"))

# ---------------------------------------------------------------------------
# ISO 17359 vibration baseline and percentage deviation (Formula 3)
# ---------------------------------------------------------------------------
# Normal-vibration baseline per pump for the percentage deviation (Formula 3),
# acquired as ISO 17359:2018 describes baseline data: measured when operation is
# known to be acceptable and stable, in the normal operating state, after time
# for wear-in. After an overhaul the baseline is re-established (reset endpoint).
#
# Pump running time allowed for wear-in before baseline readings start. (env)
VIBRATION_BASELINE_WEAR_IN_HOURS = max(
    0.0, float(os.getenv("VIBRATION_BASELINE_WEAR_IN_HOURS", "1"))
)
# Baseline = mean of this many readings, one per interval, taken while the
# pump is acceptable (no anomaly, ISO 20816 zone A/B). They must be stable:
# coefficient of variation (std / mean) at most the max CV. (env)
VIBRATION_BASELINE_READINGS = max(2, int(os.getenv("VIBRATION_BASELINE_READINGS", "20")))
VIBRATION_BASELINE_READING_INTERVAL_SECONDS = max(
    0.0, float(os.getenv("VIBRATION_BASELINE_READING_INTERVAL_SECONDS", "30"))
)
VIBRATION_BASELINE_MAX_CV_PERCENT = float(os.getenv("VIBRATION_BASELINE_MAX_CV_PERCENT", "20"))
# Longer gaps between running windows (pump off, device offline) are not
# counted as running time.
VIBRATION_BASELINE_MAX_RUNNING_GAP_SECONDS = 60.0
# ISO 17359 sets no fixed alert level: alert criteria should give the earliest
# indication of a fault and be refined over time, so this is configurable. (env)
VIBRATION_DEVIATION_WARNING_PERCENT = float(
    os.getenv("VIBRATION_DEVIATION_WARNING_PERCENT", "50")
)

# ---------------------------------------------------------------------------
# App dashboard: live view and timed tests, judged against the healthy pump
# ---------------------------------------------------------------------------
# Summary of the healthy pump (per-axis RMS, temperature) that every pump is
# compared with; written by scripts/analyze_recording.py --save.
HEALTHY_REFERENCE_FILE = HEALTHY_REFERENCE_DIR / "ESP32-002_healthy_reference.json"
# Healthy per-axis vibration (RMS around the mean, g) that the whole system
# uses, set by hand from the healthy pump's run on 19 Nov 2026 (X 0.47 g,
# Y 0.16 g; its 17 Nov recording gave X 0.334 g, Y 0.077 g). They replace the
# recorded values in the healthy reference (bars, statuses, possible problems;
# the three-axis total moves with them), and the healthy training data's X
# and Y are scaled to them, so the anomaly model and the pump part limits
# learn the same healthy pump. Z keeps the recorded value.
HEALTHY_AXIS_RMS_G = {"x": 0.50, "y": 0.15}
# Raw samples kept per device for the live view: the live result is judged
# over the last ~60 s of running data (6144 samples at 100 Hz). Over 20 s the
# healthy pump's own stretches of stronger vibration read as warnings.
LIVE_RAW_SAMPLES = 6144
# The live per-axis bars use the latest windows (4 x 128 samples = about 5 s).
LIVE_BAR_WINDOWS = 4
# Device counts as online when its last data is at most this old.
LIVE_ONLINE_SECONDS = 15
# Per-axis level: up to the healthy limit = Healthy, up to this many times the
# limit = Warning, above = Faulty (limits: models/pump_part_thresholds.json).
AXIS_FAULTY_LIMIT_FACTOR = 2.0
# Calibrated on the healthy pump over 60 s stretches (205 of them): its
# vibration stayed within -14%..+27.5% of its median, and up to 45% of its
# windows were flagged (median 11%). The faulty pump: 54%+ away, 100% flagged.
# No risk within these, warning (risk 60) at VIBRATION_DEVIATION_WARNING_PERCENT
# and at ANOMALY_SHARE_WARNING_PERCENT.
DEVIATION_ALLOWED_PERCENT = 30.0
ANOMALY_SHARE_ALLOWED_PERCENT = 45.0
ANOMALY_SHARE_WARNING_PERCENT = 75.0
# A verdict needs at least this many running windows (~8 s).
MIN_WINDOWS_FOR_VERDICT = 6
# Overall verdict from the risk (0-100); machine health = 100 - risk.
VERDICT_MONITOR_RISK = 30.0
VERDICT_WARNING_RISK = 60.0
VERDICT_FAULTY_RISK = 90.0
# Timed tests started from the app: their summaries are saved here.
APP_TESTS_DIR = DATA_DIR / "app_tests"
APP_TEST_MAX_MINUTES = 180
