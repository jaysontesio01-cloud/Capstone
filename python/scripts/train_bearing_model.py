"""Retrain the bearing anomaly model (Isolation Forest).

Reads data/accelerometer.csv and saves models/bearing_iforest_model.pkl and
models/bearing_scaler.pkl, the files the backend loads at start-up. It uses
the backend's own windowing and features, so the saved model always matches
the data the API gives it.

Run from the python folder, then restart the backend to load the new model:

    python scripts/train_bearing_model.py
"""

import sys
from pathlib import Path

# Lets this script import pump_monitor when run as `python scripts/<name>.py`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pump_monitor import config  # noqa: E402
from pump_monitor.anomaly_model import train_bearing_anomaly_model  # noqa: E402


if __name__ == "__main__":
    train_bearing_anomaly_model()
    print(f"Saved model:  {config.BEARING_MODEL_FILE}")
    print(f"Saved scaler: {config.BEARING_SCALER_FILE}")
