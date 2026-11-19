"""Bearing anomaly model (Isolation Forest) and per-axis pump part checks.

Both learn what "normal" is from the healthy pump's recorded data
(config.TRAINING_DATA_FILE), processed exactly like live data: running samples
only, garbled readings replaced, 128-sample windows, SVD-denoised. Training
saves the model and scaler in models/ and the per-axis pump part limits in
models/pump_part_thresholds.json. load_model() loads them (retraining when the
saved model does not work with the installed scikit-learn); create_app()
calls it at start-up.
"""

import json
import pickle
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from . import config
from .formulas import axis_rms
from .signal_processing import create_windows, extract_features, prepare_window, window_dynamic_rms

AXES = ['X-axis', 'Y-axis', 'Z-axis']
PUMP_PARTS = {
    'X-axis': 'Lateral Bearing/Shaft Alignment',
    'Y-axis': 'Radial Bearing/Motor Housing',
    'Z-axis': 'Impeller/Pump Head/Axial Load',
}
# Only used if no limits learned from the healthy pump are available: the
# original fixed limits (RMS and standard deviation are equal for a signal
# with its mean removed, so the smaller of the two applied).
DEFAULT_PART_THRESHOLDS = {
    'X-axis': {'rms': 0.08, 'peak': 2.0},
    'Y-axis': {'rms': 0.15, 'peak': 2.0},
    'Z-axis': {'rms': 0.05, 'peak': 2.0},
}

# Set by load_model().
model = None
scaler = None
part_thresholds = DEFAULT_PART_THRESHOLDS


def healthy_training_windows():
    """The healthy pump's running data as 128-sample windows, processed like live data."""
    training_file = config.TRAINING_DATA_FILE
    if not training_file.exists():
        raise FileNotFoundError(f"Training file not found: {training_file}")

    df = pd.read_csv(training_file, low_memory=False)
    df.columns = df.columns.str.strip().str.lower()
    if "pump_running" in df.columns:
        df = df[df["pump_running"].astype(str).str.lower().eq("true")]
    data = df[['x', 'y', 'z']].apply(pd.to_numeric, errors="coerce").dropna()
    windows = create_windows(data, window_size=config.WINDOW_SIZE, step=64)
    if len(windows) == 0:
        raise ValueError("Not enough healthy pump rows to train the anomaly model")
    return np.array([prepare_window(window) for window in windows])


def part_thresholds_from_windows(windows):
    """Per-axis limits: healthy median + HEALTHY_LIMIT_SIGMAS robust standard deviations."""
    thresholds = {}
    for axis_idx, axis_key in enumerate(AXES):
        signal = windows[:, :, axis_idx]
        centered = signal - signal.mean(axis=1, keepdims=True)
        metrics = {
            'rms': np.sqrt(np.mean(centered ** 2, axis=1)),
            'peak': np.max(np.abs(centered), axis=1),
        }
        thresholds[axis_key] = {}
        for name, values in metrics.items():
            median = np.median(values)
            robust_std = 1.4826 * np.median(np.abs(values - median))
            limit = median + config.HEALTHY_LIMIT_SIGMAS * robust_std
            thresholds[axis_key][name] = round(float(limit), 4)
    return thresholds


def save_part_thresholds(thresholds, window_count):
    payload = {
        "source": config.TRAINING_DATA_FILE.name,
        "windows": int(window_count),
        "rule": (f"healthy median + {config.HEALTHY_LIMIT_SIGMAS:g} robust standard deviations "
                 "(1.4826 x median absolute deviation) over SVD-denoised 128-sample windows"),
        "created_at": datetime.now().isoformat(),
        "thresholds_g": thresholds,
    }
    config.PUMP_PART_THRESHOLDS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def train_bearing_anomaly_model():
    """Train on the healthy pump's data and save the model, scaler and pump part limits."""
    windows = healthy_training_windows()
    features = extract_features(windows)
    trained_scaler = StandardScaler()
    features_scaled = trained_scaler.fit_transform(features)

    trained_model = IsolationForest(n_estimators=200, contamination=0.05, random_state=42)
    trained_model.fit(features_scaled)

    with open(config.BEARING_MODEL_FILE, "wb") as f_model:
        pickle.dump(trained_model, f_model)

    with open(config.BEARING_SCALER_FILE, "wb") as f_scaler:
        pickle.dump(trained_scaler, f_scaler)

    save_part_thresholds(part_thresholds_from_windows(windows), len(windows))
    print(f"Bearing anomaly model retrained from {config.TRAINING_DATA_FILE.name} ({len(windows)} windows)")
    return trained_model, trained_scaler


def load_or_train_bearing_model():
    model_file = config.BEARING_MODEL_FILE
    scaler_file = config.BEARING_SCALER_FILE

    try:
        with open(model_file, "rb") as f_model:
            loaded_model = pickle.load(f_model)

        with open(scaler_file, "rb") as f_scaler:
            loaded_scaler = pickle.load(f_scaler)

        # Make sure the loaded pair actually works with this library version.
        probe = loaded_scaler.transform(np.zeros((1, loaded_scaler.n_features_in_)))
        loaded_model.predict(probe)
        loaded_model.decision_function(probe)

        return loaded_model, loaded_scaler
    except Exception as exc:
        print(f"Bearing model load failed; retraining locally compatible model: {exc}")
        return train_bearing_anomaly_model()


def load_part_thresholds():
    """Pump part limits learned from the healthy pump (learned again if the file is missing)."""
    try:
        payload = json.loads(config.PUMP_PART_THRESHOLDS_FILE.read_text(encoding="utf-8"))
        return payload["thresholds_g"]
    except FileNotFoundError:
        pass
    except Exception as exc:
        print(f"Pump part limits could not be read ({exc}); learning them again")
    try:
        windows = healthy_training_windows()
        thresholds = part_thresholds_from_windows(windows)
        save_part_thresholds(thresholds, len(windows))
        return thresholds
    except Exception as exc:
        print(f"Pump part limits unavailable ({exc}); using the default limits")
        return DEFAULT_PART_THRESHOLDS


def load_model():
    """Load the bearing model, scaler and pump part limits used for analysis."""
    global model, scaler, part_thresholds
    model, scaler = load_or_train_bearing_model()
    part_thresholds = load_part_thresholds()
    return model, scaler


def score_windows(windows):
    """Run the bearing model on each window.

    Returns (predictions, scores): a prediction of -1 means anomaly, and a
    lower score is more anomalous.
    """
    if model is None or scaler is None:
        load_model()
    features_scaled = scaler.transform(extract_features(windows))
    return model.predict(features_scaled), model.decision_function(features_scaled)


def analyze_pump_parts(window_data):
    """Find which pump parts vibrate beyond the healthy pump's limits, per axis."""
    anomalous_parts = []
    axis_metrics = []
    is_idle = window_dynamic_rms(window_data) < config.IDLE_VIBRATION_RMS_THRESHOLD

    for axis_idx, axis_key in enumerate(AXES):
        signal = window_data[:, axis_idx]
        mean_val = np.mean(signal)
        vibration_signal = signal - mean_val

        rms = axis_rms(vibration_signal)
        std = np.std(vibration_signal)
        max_val = np.max(np.abs(vibration_signal))

        fft_vals = np.abs(np.fft.fft(vibration_signal))
        spectrum_energy = np.sum(fft_vals**2)

        limits = part_thresholds.get(axis_key, DEFAULT_PART_THRESHOLDS[axis_key])
        is_anomaly = False if is_idle else (
            (rms > limits['rms']) or
            (max_val > limits['peak'])
        )

        metrics = {
            'axis': axis_key,
            'pump_part': PUMP_PARTS[axis_key],
            'rms': float(rms),
            'std': float(std),
            'max': float(max_val),
            'mean': float(mean_val),
            'spectrum_energy': float(spectrum_energy),
            'is_idle': bool(is_idle),
            'is_anomaly': bool(is_anomaly)
        }

        axis_metrics.append(metrics)

        if is_anomaly:
            anomalous_parts.append(PUMP_PARTS[axis_key])

    return anomalous_parts, axis_metrics
