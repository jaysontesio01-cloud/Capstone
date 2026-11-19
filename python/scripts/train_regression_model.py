"""Train the learned regression baseline and report how well it fits.

Uses data/accelerometer.csv (plus data/online_regression_training_data.csv if
it exists) and saves models/linear_regression_model.pkl and its metadata.
Unlike the backend's automatic retraining, it holds back 20% of the rows to
report MAE and R2.

Run from the python folder:

    python scripts/train_regression_model.py
"""

import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn import metrics
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

# Lets this script import pump_monitor when run as `python scripts/<name>.py`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pump_monitor import config  # noqa: E402
from pump_monitor.regression_baseline import clean_regression_frame  # noqa: E402

FEATURE_COLUMNS = config.REGRESSION_FEATURE_COLUMNS
TARGET_COLUMN = config.REGRESSION_TARGET_COLUMN


def load_training_data():
    frames = [pd.read_csv(config.TRAINING_DATA_FILE)]
    if config.ONLINE_TRAINING_FILE.exists():
        frames.append(pd.read_csv(config.ONLINE_TRAINING_FILE))
    # Lower-case columns, numeric x/y/z, target = resultant acceleration (Formula 1).
    return clean_regression_frame(pd.concat(frames, ignore_index=True))


def train():
    df = load_training_data()
    X = df[FEATURE_COLUMNS]
    y = df[TARGET_COLUMN]

    X_train, X_test, y_train, y_test = train_test_split(
        X,
        y,
        test_size=0.2,
        random_state=101,
    )

    model = Pipeline([
        ("scaler", StandardScaler()),
        ("regression", LinearRegression()),
    ])
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    residuals = y - model.predict(X)
    abs_residuals = np.abs(residuals)

    metadata = {
        "feature_columns": FEATURE_COLUMNS,
        "target_column": TARGET_COLUMN,
        "training_rows": int(len(df)),
        "learned_vibration_mean": float(y.mean()),
        "learned_vibration_std": float(y.std(ddof=0)),
        "residual_abs_mean": float(abs_residuals.mean()),
        "residual_abs_std": float(abs_residuals.std(ddof=0)),
        "residual_abs_threshold": float(np.quantile(abs_residuals, 0.995)),
        "mae": float(metrics.mean_absolute_error(y_test, y_pred)),
        "r2": float(metrics.r2_score(y_test, y_pred)),
    }

    with open(config.REGRESSION_MODEL_FILE, "wb") as f_model:
        pickle.dump(model, f_model)

    with open(config.REGRESSION_METADATA_FILE, "w", encoding="utf-8") as f_metadata:
        json.dump(metadata, f_metadata, indent=2)

    print(f"Rows used: {metadata['training_rows']}")
    print(f"MAE: {metadata['mae']:.6f}")
    print(f"R2: {metadata['r2']:.6f}")
    print(f"Learned vibration mean: {metadata['learned_vibration_mean']:.6f}")
    print(f"Residual threshold: {metadata['residual_abs_threshold']:.6f}")
    print(f"Saved model: {config.REGRESSION_MODEL_FILE}")
    print(f"Saved metadata: {config.REGRESSION_METADATA_FILE}")


if __name__ == "__main__":
    train()
