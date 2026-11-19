"""Learned "normal vibration" baseline (linear regression) and online learning.

The model predicts the resultant acceleration (Formula 1) from x, y and z. It
is trained on data/accelerometer.csv plus live windows recorded while the pump
ran normally (online learning), and saved in models/.
"""

import json
import pickle
from datetime import datetime
from threading import Lock

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from . import config
from .formulas import resultant_acceleration

# Cached model, filled by load_or_train_regression_baseline().
regression_model = None
regression_metadata = None
regression_lock = Lock()


def clean_regression_frame(df):
    cleaned = df.copy()
    cleaned.columns = cleaned.columns.str.strip().str.lower()
    missing_cols = [col for col in config.REGRESSION_FEATURE_COLUMNS if col not in cleaned.columns]
    if missing_cols:
        raise ValueError(f"Missing regression columns: {missing_cols}")

    cleaned = cleaned[config.REGRESSION_FEATURE_COLUMNS].apply(pd.to_numeric, errors="coerce").dropna()
    # Readings at the sensor's +/-16 g limit are garbled transfers, not vibration.
    cleaned = cleaned[(cleaned.abs() < config.ACCEL_CLIP_LIMIT_G).all(axis=1)].copy()
    cleaned[config.REGRESSION_TARGET_COLUMN] = resultant_acceleration(
        cleaned["x"], cleaned["y"], cleaned["z"]
    )
    return cleaned


def train_regression_model_from_frame(df):
    training_df = clean_regression_frame(df)
    if len(training_df) < config.MIN_REGRESSION_TRAINING_ROWS:
        raise ValueError(
            f"Need at least {config.MIN_REGRESSION_TRAINING_ROWS} rows to train regression baseline"
        )

    X = training_df[config.REGRESSION_FEATURE_COLUMNS]
    y = training_df[config.REGRESSION_TARGET_COLUMN]

    trained_model = Pipeline([
        ("scaler", StandardScaler()),
        ("regression", LinearRegression()),
    ])
    trained_model.fit(X, y)

    predictions = trained_model.predict(X)
    abs_residuals = np.abs(y - predictions)
    trained_metadata = {
        "feature_columns": config.REGRESSION_FEATURE_COLUMNS,
        "target_column": config.REGRESSION_TARGET_COLUMN,
        "training_rows": int(len(training_df)),
        "learned_vibration_mean": float(y.mean()),
        "learned_vibration_std": float(y.std(ddof=0)),
        "residual_abs_mean": float(abs_residuals.mean()),
        "residual_abs_std": float(abs_residuals.std(ddof=0)),
        "residual_abs_threshold": float(np.quantile(abs_residuals, 0.995)),
        "last_trained_at": datetime.now().isoformat(),
    }
    return trained_model, trained_metadata


def save_regression_baseline(trained_model, trained_metadata):
    with open(config.REGRESSION_MODEL_FILE, "wb") as f_model:
        pickle.dump(trained_model, f_model)

    with open(config.REGRESSION_METADATA_FILE, "w", encoding="utf-8") as f_metadata:
        json.dump(trained_metadata, f_metadata, indent=2)


def load_or_train_regression_baseline():
    global regression_model, regression_metadata

    with regression_lock:
        if regression_model is not None and regression_metadata is not None:
            return regression_model, regression_metadata

        if config.REGRESSION_MODEL_FILE.exists() and config.REGRESSION_METADATA_FILE.exists():
            # A pickle saved by a different numpy/scikit-learn version (e.g. a
            # local dev machine vs. the server) can fail to load or to predict.
            # Retrain from the CSV data instead of failing every analysis.
            try:
                with open(config.REGRESSION_MODEL_FILE, "rb") as f_model:
                    loaded_model = pickle.load(f_model)
                with open(config.REGRESSION_METADATA_FILE, "r", encoding="utf-8") as f_metadata:
                    loaded_metadata = json.load(f_metadata)
                loaded_model.predict(
                    pd.DataFrame([[0.0] * len(config.REGRESSION_FEATURE_COLUMNS)],
                                 columns=config.REGRESSION_FEATURE_COLUMNS)
                )
                regression_model, regression_metadata = loaded_model, loaded_metadata
                return regression_model, regression_metadata
            except Exception as exc:
                print(f"Regression baseline load failed; retraining locally compatible model: {exc}")

        source_files = [config.TRAINING_DATA_FILE]
        if config.ONLINE_TRAINING_FILE.exists():
            source_files.append(config.ONLINE_TRAINING_FILE)

        frames = [pd.read_csv(path) for path in source_files if path.exists()]
        if not frames:
            return None, None

        regression_model, regression_metadata = train_regression_model_from_frame(
            pd.concat(frames, ignore_index=True)
        )
        save_regression_baseline(regression_model, regression_metadata)
        return regression_model, regression_metadata


def append_online_training_rows(df):
    online_df = clean_regression_frame(df)
    if online_df.empty:
        return 0

    online_df["learned_at"] = datetime.now().isoformat()
    columns = config.REGRESSION_FEATURE_COLUMNS + [config.REGRESSION_TARGET_COLUMN, "learned_at"]

    if config.ONLINE_TRAINING_FILE.exists():
        existing_df = pd.read_csv(config.ONLINE_TRAINING_FILE)
        combined = pd.concat([existing_df, online_df[columns]], ignore_index=True)
    else:
        combined = online_df[columns]

    combined = combined.tail(config.MAX_ONLINE_TRAINING_ROWS)
    combined.to_csv(config.ONLINE_TRAINING_FILE, index=False)
    return len(online_df)


def learn_from_standard_sensor_window(df):
    global regression_model, regression_metadata

    learned_rows = append_online_training_rows(df)
    source_files = [config.TRAINING_DATA_FILE, config.ONLINE_TRAINING_FILE]
    frames = [pd.read_csv(path) for path in source_files if path.exists()]
    if not frames:
        return {"learned": False, "rows_added": 0, "training_rows": 0}

    trained_model, trained_metadata = train_regression_model_from_frame(
        pd.concat(frames, ignore_index=True)
    )
    save_regression_baseline(trained_model, trained_metadata)

    with regression_lock:
        regression_model = trained_model
        regression_metadata = trained_metadata

    return {
        "learned": True,
        "rows_added": int(learned_rows),
        "training_rows": int(trained_metadata["training_rows"]),
        "last_trained_at": trained_metadata["last_trained_at"],
    }
