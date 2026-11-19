"""ISO 20816 vibration severity: velocity RMS estimate and zones A to D.

compare_to_standard_vibration() is the entry point. It also reports how far
the window is from the learned regression baseline.
"""

import numpy as np

from . import config
from .regression_baseline import clean_regression_frame, load_or_train_regression_baseline


def velocity_rms_from_acceleration_g(values, sample_rate_hz=config.SENSOR_SAMPLE_RATE_HZ):
    samples = np.asarray(values, dtype=float)
    if samples.ndim != 2 or samples.shape[0] < 2:
        return {
            "axis_velocity_rms_mm_s": {"x": 0.0, "y": 0.0, "z": 0.0},
            "velocity_rms_mm_s": 0.0,
        }

    velocity_axis_rms = []
    for axis_idx in range(samples.shape[1]):
        acceleration = samples[:, axis_idx] - np.mean(samples[:, axis_idx])
        acceleration_mm_s2 = acceleration * config.G_TO_MM_PER_S2
        frequencies = np.fft.rfftfreq(len(acceleration_mm_s2), d=1.0 / sample_rate_hz)
        acceleration_fft = np.fft.rfft(acceleration_mm_s2)
        velocity_fft = np.zeros_like(acceleration_fft, dtype=np.complex128)

        valid = frequencies >= config.ISO20816_LOW_CUT_HZ
        velocity_fft[valid] = acceleration_fft[valid] / (1j * 2 * np.pi * frequencies[valid])
        velocity = np.fft.irfft(velocity_fft, n=len(acceleration_mm_s2))
        velocity_axis_rms.append(float(np.sqrt(np.mean(velocity ** 2))))

    axis_names = ["x", "y", "z"]
    axis_velocity = {
        axis_names[idx]: round(velocity_axis_rms[idx], 4)
        for idx in range(min(len(axis_names), len(velocity_axis_rms)))
    }
    return {
        "axis_velocity_rms_mm_s": axis_velocity,
        "velocity_rms_mm_s": float(max(velocity_axis_rms)) if velocity_axis_rms else 0.0,
    }


def classify_iso20816_velocity(velocity_rms_mm_s):
    limits = config.ISO20816_VELOCITY_LIMITS_MM_S.get(
        config.ISO20816_MACHINE_CLASS,
        config.ISO20816_VELOCITY_LIMITS_MM_S["class_ii"],
    )

    if velocity_rms_mm_s < limits["ab"]:
        zone = "A"
        condition = "Good"
    elif velocity_rms_mm_s < limits["bc"]:
        zone = "B"
        condition = "Satisfactory"
    elif velocity_rms_mm_s < limits["cd"]:
        zone = "C"
        condition = "Unsatisfactory"
    else:
        zone = "D"
        condition = "Unacceptable"

    return zone, condition, limits


def compare_to_standard_vibration(df):
    regression_result = None
    trained_model, trained_metadata = load_or_train_regression_baseline()
    regression_df = clean_regression_frame(df)
    if regression_df.empty:
        return None

    if trained_model is not None and trained_metadata is not None:
        expected = trained_model.predict(regression_df[config.REGRESSION_FEATURE_COLUMNS])
        actual = regression_df[config.REGRESSION_TARGET_COLUMN].to_numpy()
        abs_residuals = np.abs(actual - expected)
        threshold = float(trained_metadata.get("residual_abs_threshold", 0.0))
        if threshold <= 0:
            threshold = float(abs_residuals.mean() + (3 * abs_residuals.std()))

        anomaly_flags = abs_residuals > threshold
        regression_result = {
            "actual_acceleration_g": float(actual[-1]),
            "learned_standard_acceleration_g": float(expected[-1]),
            "average_actual_acceleration_g": float(actual.mean()),
            "average_learned_standard_acceleration_g": float(expected.mean()),
            "average_difference_g": float(abs_residuals.mean()),
            "max_difference_g": float(abs_residuals.max()),
            "difference_threshold_g": threshold,
            "outside_learned_baseline_count": int(np.sum(anomaly_flags)),
            "outside_learned_baseline": bool(np.any(anomaly_flags)),
            "training_rows": int(trained_metadata.get("training_rows", 0)),
        }

    velocity_result = velocity_rms_from_acceleration_g(
        regression_df[config.REGRESSION_FEATURE_COLUMNS].to_numpy()
    )
    velocity_rms = velocity_result["velocity_rms_mm_s"]
    zone, condition, limits = classify_iso20816_velocity(velocity_rms)

    return {
        "standard_name": "ISO 20816",
        "measurement": "estimated housing vibration velocity RMS",
        "unit": "mm/s RMS",
        "sample_rate_hz": config.SENSOR_SAMPLE_RATE_HZ,
        "low_cut_hz": config.ISO20816_LOW_CUT_HZ,
        "machine_class": config.ISO20816_MACHINE_CLASS,
        "velocity_rms_mm_s": round(velocity_rms, 4),
        "axis_velocity_rms_mm_s": velocity_result["axis_velocity_rms_mm_s"],
        "zone": zone,
        "condition": condition,
        "limits_mm_s": limits,
        "outside_standard": zone in {"C", "D"},
        "outside_standard_count": 1 if zone in {"C", "D"} else 0,
        "learned_baseline_comparison": regression_result,
    }
