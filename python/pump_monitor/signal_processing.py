"""Sensor sample helpers and signal processing for vibration windows.

Cleans the raw samples the ESP32 sends, removes noise with a Hankel-SVD
filter, cuts the data into fixed-size windows and extracts the features the
bearing anomaly model uses.
"""

from bisect import bisect_left

import numpy as np
import pywt

from . import config
from .formulas import axis_rms, three_axis_rms


def numeric_value(value):
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def finite_or_none(value):
    value = numeric_value(value)
    return value if value is not None and np.isfinite(value) else None


def latest_sample_values(samples):
    if not samples:
        return {
            "latest_sample": {},
            "latest_temperature_c": None,
            "latest_pressure_bar": None,
            "latest_pressure_psi": None,
        }

    latest_sample = samples[-1] if isinstance(samples[-1], dict) else {}
    temperature_c = numeric_value(latest_sample.get("temperature_c"))
    pressure_bar = numeric_value(latest_sample.get("pressure_bar"))
    pressure_psi = numeric_value(latest_sample.get("pressure_psi"))

    return {
        "latest_sample": latest_sample,
        "latest_temperature_c": temperature_c,
        "latest_pressure_bar": pressure_bar,
        "latest_pressure_psi": pressure_psi,
    }


def sample_pump_is_idle(sample):
    """True when the device reports the pump is off.

    Uses the ESP32's own vibration-based ``pump_running`` flag when present,
    and the hydraulic pressure sensor when that is installed.
    """
    if not isinstance(sample, dict):
        return False

    pump_running = sample.get("pump_running")
    if isinstance(pump_running, bool) and not pump_running:
        return True

    pressure_bar = numeric_value(sample.get("pressure_bar"))
    return (
        pressure_bar is not None
        and pressure_bar <= config.PUMP_RUNNING_PRESSURE_THRESHOLD_BAR
    )


def replace_clipped_samples(samples, limit_g=None):
    """Replace readings at the sensor's +/-16 g limit with the nearest good reading.

    A sample counts as clipped when any axis is at or beyond limit_g
    (config.ACCEL_CLIP_LIMIT_G). Its x/y/z become those of the nearest good
    sample in the list (the earlier one on a tie), so the timing is kept.
    Returns new sample dicts; the input list is not changed.
    """
    limit_g = config.ACCEL_CLIP_LIMIT_G if limit_g is None else limit_g
    axes = ("x", "y", "z")

    def is_clipped(sample):
        if not isinstance(sample, dict):
            return False
        values = (numeric_value(sample.get(axis)) for axis in axes)
        return any(value is not None and abs(value) >= limit_g for value in values)

    cleaned = [sample.copy() if isinstance(sample, dict) else sample for sample in samples]
    good = [i for i, sample in enumerate(cleaned) if isinstance(sample, dict) and not is_clipped(sample)]
    if not good:
        return cleaned
    for i, sample in enumerate(cleaned):
        if is_clipped(sample):
            pos = bisect_left(good, i)
            candidates = [good[j] for j in (pos - 1, pos) if 0 <= j < len(good)]
            nearest = min(candidates, key=lambda g: abs(g - i))
            for axis in axes:
                sample[axis] = cleaned[nearest][axis]
    return cleaned


def filter_pump_samples(samples):
    """Zero axes when the pump is reported idle (pump_running flag or low pressure)."""
    filtered_samples = []
    for sample in samples:
        filtered_sample = sample.copy() if isinstance(sample, dict) else sample
        if sample_pump_is_idle(filtered_sample):
            filtered_sample.update({"x": 0.0, "y": 0.0, "z": 0.0})
        filtered_samples.append(filtered_sample)
    return filtered_samples


def svd_denoise_window(window_data, rank=config.PUMP_SVD_RANK):
    """Keep dominant Hankel-SVD components and remove each axis DC offset."""
    signal = np.asarray(window_data, dtype=float)
    if signal.ndim != 2 or signal.shape[1] != 3:
        raise ValueError("Expected a sample window with x, y, and z columns")
    if len(signal) < config.PUMP_SVD_MIN_SAMPLES:
        return signal.copy()

    embedding_length = min(len(signal) // 2, 32)
    denoised = np.empty_like(signal)
    keep_rank = max(1, int(rank))

    for axis_index in range(signal.shape[1]):
        centered_signal = signal[:, axis_index] - np.mean(signal[:, axis_index])
        trajectory = np.lib.stride_tricks.sliding_window_view(
            centered_signal, embedding_length
        )
        left_vectors, singular_values, right_vectors = np.linalg.svd(
            trajectory, full_matrices=False
        )
        retained_rank = min(keep_rank, len(singular_values))
        reconstructed = (
            left_vectors[:, :retained_rank] * singular_values[:retained_rank]
        ) @ right_vectors[:retained_rank, :]

        reconstructed_signal = np.zeros(len(centered_signal), dtype=float)
        overlap_counts = np.zeros(len(centered_signal), dtype=float)
        for row_index in range(reconstructed.shape[0]):
            reconstructed_signal[row_index:row_index + embedding_length] += reconstructed[row_index]
            overlap_counts[row_index:row_index + embedding_length] += 1
        denoised[:, axis_index] = reconstructed_signal / overlap_counts

    return denoised


def prepare_window(window_data):
    """Process an x/y/z window the way live data is: clipped readings replaced, SVD-denoised."""
    values = np.asarray(window_data, dtype=float)
    records = [{"x": x, "y": y, "z": z} for x, y, z in values]
    cleaned = replace_clipped_samples(records)
    return svd_denoise_window(np.array([[s["x"], s["y"], s["z"]] for s in cleaned], dtype=float))


def create_windows(data, window_size=128, step=64):
    windows = []
    for i in range(0, len(data) - window_size + 1, step):
        windows.append(data.iloc[i:i+window_size].values)
    return np.array(windows)


def extract_features(windows):
    features = []
    for w in windows:
        feat = []
        for i in range (w.shape[1]):
            signal = w[:, i]

            mean = np.mean(signal)
            std = np.std(signal)
            max_val = np.max(signal)
            min_val = np.min(signal)
            rms = axis_rms(signal)

            fft_vals = np.abs(np.fft.fft(signal))
            fft_mean = np.mean(fft_vals)
            fft_max = np.max(fft_vals)

            coeffs = pywt.wavedec(signal, 'db4', level=3)
            wavelet_energy = sum(np.sum(c**2) for c in coeffs)

            feat.extend([mean, std, max_val, min_val, rms, fft_mean, fft_max, wavelet_energy])
        features.append(feat)
    return np.array(features)


def window_dynamic_rms(window_data):
    """Three-axis RMS (Formula 2) of a window after removing each axis' static offset."""
    window = np.asarray(window_data, dtype=float)
    centered = window - np.mean(window, axis=0)
    return three_axis_rms(centered[:, 0], centered[:, 1], centered[:, 2])
