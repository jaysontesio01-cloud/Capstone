"""Analyze a batch of vibration windows with every check the backend has.

Each window is checked by the bearing anomaly model and the per-axis pump part
limits, both learned from the healthy pump. The ISO 20816 severity zone is
reported for information only. Used by /api/esp32/analyze, /api/analyze and
/api/analyze/csv.
"""

import numpy as np
import pandas as pd

from .anomaly_model import analyze_pump_parts, score_windows
from .iso20816 import compare_to_standard_vibration
from .signal_processing import prepare_window


def axis_metrics_for_response(axis_metrics):
    return [
        {
            "axis": m['axis'],
            "pump_part": m['pump_part'],
            "rms": round(m['rms'], 4),
            "std": round(m['std'], 4),
            "max": round(m['max'], 4),
            "mean": round(m['mean'], 4),
            "is_idle": bool(m.get('is_idle', False)),
            "is_anomaly": bool(m['is_anomaly'])
        }
        for m in axis_metrics
    ]


def analyze_windows(windows):
    """Return (window results, anomaly count, affected pump parts) for the windows."""
    # Same processing as live data and the healthy training data: garbled
    # readings replaced, then SVD-denoised.
    windows = np.array([prepare_window(window) for window in windows])
    preds, scores = score_windows(windows)

    windows_results = []
    anomaly_count = 0
    all_affected_parts = set()

    for i in range(len(preds)):
        anomalous_parts, axis_metrics = analyze_pump_parts(windows[i])
        all_affected_parts.update(anomalous_parts)
        window_df = pd.DataFrame(windows[i], columns=['x', 'y', 'z'])
        standard_comparison = compare_to_standard_vibration(window_df)

        is_idle = any(m.get('is_idle') for m in axis_metrics)
        # The healthy-pump model and part limits decide; ISO 20816 is information only.
        is_anomaly = False if is_idle else (
            preds[i] == -1 or
            any(m['is_anomaly'] for m in axis_metrics)
        )
        if is_anomaly:
            anomaly_count += 1

        windows_results.append({
            "window_id": int(i),
            "is_anomaly": bool(is_anomaly),
            "is_idle": bool(is_idle),
            "anomaly_score": float(scores[i]),
            "standard_vibration_comparison": standard_comparison,
            "affected_parts": list(anomalous_parts),
            "axis_metrics": axis_metrics_for_response(axis_metrics),
        })

    return windows_results, anomaly_count, all_affected_parts


def health_status(anomaly_count, window_count):
    """HEALTHY, WARNING, or CRITICAL when more than half the windows are anomalous."""
    if anomaly_count == 0:
        return "HEALTHY"
    return "CRITICAL" if anomaly_count > window_count * 0.5 else "WARNING"
