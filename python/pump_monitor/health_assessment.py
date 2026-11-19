"""Judge a pump against the healthy pump: per-axis levels, temperature,
overall verdict, machine health and possible problems.

"Healthy" always means the healthy pump's recorded data: its summary
(config.HEALTHY_REFERENCE_FILE: per-axis vibration and temperature), the pump
part limits learned from it (models/pump_part_thresholds.json) and the anomaly
model trained on it. Used by the app's live view and its timed tests
(routes/live.py, test_session.py).

Risk (0-100) is the largest of:
  - vibration deviation from the healthy pump (Formula 3), in either direction:
    within 30% (the healthy pump's own range) = 0, 50% = 60 (warning),
    100% = 90, 200% = 100
  - share of windows the healthy-pump checks flag (model or part limits):
    up to 45% (the healthy pump's own maximum) = 0, 75% = 60, 100% = 100
  - running temperature above the healthy pump's maximum: +10 C = 60, +20 C = 90
Machine health = 100 - risk. Verdict: HEALTHY < 30 <= MONITOR < 60 <= WARNING < 90 <= FAULTY.
The live result covers the last ~60 s of running data (config.LIVE_RAW_SAMPLES).
"""

import json
import math

import numpy as np

from . import anomaly_model, config
from .device_registry import buffer_lock, esp32_data_buffer
from .signal_processing import numeric_value, prepare_window, sample_pump_is_idle
from .window_analysis import analyze_windows

AXES = ("x", "y", "z")
AXIS_KEYS = {"x": "X-axis", "y": "Y-axis", "z": "Z-axis"}

# What a raised axis points to, and what to check (pump parts as in anomaly_model.PUMP_PARTS).
AXIS_PROBLEMS = {
    "x": ("Shaft misalignment or lateral bearing wear",
          "Check the shaft/coupling alignment, the bearing play and the base bolts."),
    "y": ("Radial bearing wear or rotor imbalance",
          "Listen for grinding or rumbling at the motor, check the bearings for play "
          "and the motor housing bolts for looseness."),
    "z": ("Impeller damage, blockage or cavitation",
          "Check the impeller for debris or damage, and the suction side for air leaks "
          "or a low water level."),
}

VERDICTS = {
    "HEALTHY": "Healthy: vibration matches the healthy pump",
    "MONITOR": "Slightly different from the healthy pump: keep monitoring",
    "WARNING": "Clearly different from the healthy pump: inspect soon",
    "FAULTY": "Faulty: shut down and inspect the pump",
    "NO_DATA": "No running data yet: switch the pump on",
    "COLLECTING": "Collecting running data...",
}

_reference_cache = {"mtime": None, "data": None}


def healthy_reference():
    """The healthy pump's summary JSON (re-read when the file changes), or None."""
    try:
        mtime = config.HEALTHY_REFERENCE_FILE.stat().st_mtime
    except OSError:
        return None
    if _reference_cache["mtime"] != mtime:
        _reference_cache["data"] = json.loads(
            config.HEALTHY_REFERENCE_FILE.read_text(encoding="utf-8")
        )
        _reference_cache["mtime"] = mtime
    return _reference_cache["data"]


def axis_limit(axis):
    limits = anomaly_model.part_thresholds.get(AXIS_KEYS[axis]) or \
        anomaly_model.DEFAULT_PART_THRESHOLDS[AXIS_KEYS[axis]]
    return float(limits["rms"])


def _three_axis(values):
    return math.sqrt(sum(value * value for value in values.values()))


def healthy_reference_summary():
    """The healthy pump every pump is compared with: the recorded summary,
    with the per-axis values set by hand (config.HEALTHY_AXIS_RMS_G) in place
    of the recorded ones and the three-axis total moved with them."""
    reference = healthy_reference() or {}
    vibration = reference.get("vibration_rms_g") or {}
    temperature = reference.get("temperature_c") or {}
    recorded_axes = reference.get("axis_rms_g") or {}
    axes = {**recorded_axes, **config.HEALTHY_AXIS_RMS_G} if recorded_axes else {}
    recorded_total = _three_axis(recorded_axes) if recorded_axes else 0.0
    total_factor = _three_axis(axes) / recorded_total if recorded_total > 0 else 1.0

    def total(key):
        value = vibration.get(key)
        return round(value * total_factor, 4) if value is not None else None

    return {
        "available": bool(reference),
        "source": config.HEALTHY_REFERENCE_FILE.name,
        "recorded_from": reference.get("from"),
        "recorded_to": reference.get("to"),
        "total_rms_g": total("median"),
        "total_rms_p10_g": total("p10"),
        "total_rms_p90_g": total("p90"),
        "axis_rms_g": axes,
        "axis_limit_g": {axis: round(axis_limit(axis), 4) for axis in AXES},
        "temperature_mean_c": temperature.get("mean"),
        "temperature_max_c": temperature.get("max"),
    }


def sample_is_running(sample):
    """The ESP32's own pump_running flag; older data without it: not idle."""
    if not isinstance(sample, dict):
        return False
    flag = sample.get("pump_running")
    if isinstance(flag, bool):
        return flag
    return not sample_pump_is_idle(sample)


def sample_row(sample):
    """[x, y, z, temperature] of a sample, or None when an axis is missing."""
    values = [numeric_value(sample.get(axis)) for axis in AXES]
    if any(value is None for value in values):
        return None
    temperature = numeric_value(sample.get("temperature_c"))
    return values + [temperature if temperature is not None and temperature > 0 else math.nan]


def running_windows(samples, pending=None):
    """Cut consecutive running samples into windows of WINDOW_SIZE rows
    (x, y, z, temperature). Returns (windows, leftover rows)."""
    rows = list(pending or [])
    windows = []
    for sample in samples:
        row = sample_row(sample) if sample_is_running(sample) else None
        if row is None:
            rows = []  # the pump stopped: don't join samples across the gap
            continue
        rows.append(row)
        if len(rows) == config.WINDOW_SIZE:
            windows.append(rows)
            rows = []
    return windows, rows


def summarize_windows(windows):
    """Run the healthy-pump checks on (x, y, z, temperature) windows; return
    one compact result per running (not idle) window."""
    if not windows:
        return []
    values = np.asarray(windows, dtype=float)
    results, _, _ = analyze_windows(values[:, :, :3])
    summaries = []
    for window, result in zip(values, results):
        if result["is_idle"]:
            continue
        axis_rms = {m["axis"][0].lower(): float(m["rms"]) for m in result["axis_metrics"]}
        temperatures = window[:, 3][np.isfinite(window[:, 3])]
        summaries.append({
            "axis_rms": axis_rms,
            # Formula 2: three-axis RMS of the denoised window.
            "rms": float(np.sqrt(sum(value * value for value in axis_rms.values()))),
            "model_unusual": result["anomaly_score"] < 0,
            "parts": list(result["affected_parts"]),
            "is_anomaly": bool(result["is_anomaly"]),
            "temperature_c": float(np.median(temperatures)) if temperatures.size else None,
        })
    return summaries


def axis_status(rms, limit):
    if rms is None:
        return "off"
    if rms <= limit:
        return "healthy"
    if rms <= limit * config.AXIS_FAULTY_LIMIT_FACTOR:
        return "warning"
    return "faulty"


def axis_levels(window_results, running=True):
    """Median RMS per axis with the healthy pump's value, limit and status.
    With the pump off the RMS is still given, with the status "off"."""
    reference = healthy_reference_summary()
    levels = []
    for axis in AXES:
        values = [w["axis_rms"][axis] for w in window_results if axis in w["axis_rms"]]
        rms = float(np.median(values)) if values else None
        healthy = reference["axis_rms_g"].get(axis)
        limit = axis_limit(axis)
        levels.append({
            "axis": axis.upper(),
            "pump_part": anomaly_model.PUMP_PARTS[AXIS_KEYS[axis]],
            "rms_g": round(rms, 4) if rms is not None else None,
            "healthy_rms_g": healthy,
            "healthy_limit_g": round(limit, 4),
            "times_healthy": round(rms / healthy, 2)
            if running and rms is not None and healthy else None,
            "status": axis_status(rms, limit) if running else "off",
        })
    return levels


def live_bar_windows(raw):
    """Per-axis RMS of the latest ADXL345 samples (the last LIVE_BAR_WINDOWS
    windows, about 5 s), whether the pump runs or not. Each window is prepared
    like the healthy pump's data (clipped readings replaced, SVD-denoised) and
    its RMS taken around the mean, as for the healthy pump's per-axis values."""
    rows = [row[:3] for row in map(sample_row, raw[-config.LIVE_BAR_WINDOWS * config.WINDOW_SIZE:])
            if row is not None]
    count = len(rows) // config.WINDOW_SIZE
    if count == 0:
        return []
    windows = np.asarray(rows[len(rows) - count * config.WINDOW_SIZE:], dtype=float)
    results = []
    for window in windows.reshape(count, config.WINDOW_SIZE, 3):
        prepared = prepare_window(window)
        rms = np.sqrt(np.mean((prepared - prepared.mean(axis=0)) ** 2, axis=0))
        results.append({"axis_rms": {axis: float(value) for axis, value in zip(AXES, rms)}})
    return results


def verdict_for(risk):
    if risk >= config.VERDICT_FAULTY_RISK:
        return "FAULTY"
    if risk >= config.VERDICT_WARNING_RISK:
        return "WARNING"
    if risk >= config.VERDICT_MONITOR_RISK:
        return "MONITOR"
    return "HEALTHY"


def possible_problems(axes, deviation, anomaly_share, model_share, temperature_max, reference):
    """Likely causes, each with the data that points to it and what to check."""
    problems = []
    for level in axes:
        if level["status"] not in ("warning", "faulty"):
            continue
        axis = level["axis"].lower()
        title, check = AXIS_PROBLEMS[axis]
        healthy = level["healthy_rms_g"]
        evidence = f"{level['axis']}-axis vibration {level['rms_g']:.2f} g"
        if healthy:
            evidence += f" vs {healthy:.2f} g on the healthy pump ({level['times_healthy']:.1f}x)"
        evidence += f"; healthy limit {level['healthy_limit_g']:.2f} g ({level['pump_part']})."
        problems.append({
            "title": title,
            "severity": "high" if level["status"] == "faulty" else "medium",
            "evidence": evidence,
            "check": check,
        })

    warning = config.VIBRATION_DEVIATION_WARNING_PERCENT
    healthy_total = reference["total_rms_g"]
    if deviation is not None and deviation <= -warning:
        problems.append({
            "title": "Pump not running at normal speed",
            "severity": "high",
            "evidence": f"Vibration only {100 + deviation:.0f}% of the healthy pump's "
                        f"{healthy_total:.2f} g while the pump is on ({deviation:.0f}%).",
            "check": "Check that the impeller turns freely (not stuck or blocked), the motor's "
                     "start capacitor, and that the sensor is firmly attached.",
        })
    elif deviation is not None and deviation >= warning and not problems:
        problems.append({
            "title": "General imbalance or looseness",
            "severity": "high" if deviation >= 2 * warning else "medium",
            "evidence": f"Overall vibration {deviation:+.0f}% compared with the healthy pump.",
            "check": "Check the mounting bolts, the impeller balance and the motor fan.",
        })

    if model_share >= 50 and not problems:
        problems.append({
            "title": "Unusual vibration pattern",
            "severity": "medium",
            "evidence": f"The anomaly model (trained on the healthy pump) found {model_share:.0f}% "
                        "of the vibration windows unusual.",
            "check": "Inspect the bearings and the impeller, and compare the sound with the healthy pump.",
        })

    healthy_max = reference["temperature_max_c"]
    if temperature_max is not None and healthy_max is not None and temperature_max > healthy_max + 5:
        problems.append({
            "title": "Overheating",
            "severity": "high" if temperature_max > healthy_max + 15 else "medium",
            "evidence": f"Up to {temperature_max:.1f} C while running vs at most "
                        f"{healthy_max:.1f} C on the healthy pump.",
            "check": "Check for bearing friction, motor overload, and low water flow or running dry.",
        })
    return problems


def assess(window_results):
    """Overall verdict over running windows (from summarize_windows)."""
    reference = healthy_reference_summary()
    count = len(window_results)
    axes = axis_levels(window_results)
    temperatures = [w["temperature_c"] for w in window_results if w.get("temperature_c") is not None]
    result = {
        "windows": count,
        "running_seconds": round(count * config.WINDOW_SIZE / config.SENSOR_SAMPLE_RATE_HZ, 1),
        "axes": axes,
        "total_rms_g": None,
        "healthy_total_rms_g": reference["total_rms_g"],
        "deviation_percent": None,
        "anomaly_window_percent": None,
        "model_unusual_percent": None,
        "part_flags_percent": {},
        "temperature_max_c": round(max(temperatures), 2) if temperatures else None,
        "temperature_mean_c": round(float(np.mean(temperatures)), 2) if temperatures else None,
        "healthy_temperature_mean_c": reference["temperature_mean_c"],
        "healthy_temperature_max_c": reference["temperature_max_c"],
        "risk_score": None,
        "risk_parts": {},
        "health_percent": None,
        "verdict": "NO_DATA",
        "verdict_text": VERDICTS["NO_DATA"],
        "problems": [],
    }
    if count < config.MIN_WINDOWS_FOR_VERDICT:
        # Too little running data for a fair judgement (e.g. the pump just started).
        if count:
            result["verdict_text"] = VERDICTS["COLLECTING"]
        return result

    total = float(np.median([w["rms"] for w in window_results]))
    healthy_total = reference["total_rms_g"]
    deviation = (total - healthy_total) / healthy_total * 100 if healthy_total else None
    anomaly_share = 100.0 * sum(w["is_anomaly"] for w in window_results) / count
    model_share = 100.0 * sum(w["model_unusual"] for w in window_results) / count
    part_counts = {}
    for w in window_results:
        for part in w["parts"]:
            part_counts[part] = part_counts.get(part, 0) + 1

    warning = config.VIBRATION_DEVIATION_WARNING_PERCENT
    risk_parts = {
        "vibration_deviation": float(np.interp(
            abs(deviation),
            [0, config.DEVIATION_ALLOWED_PERCENT, warning, 2 * warning, 4 * warning],
            [0, 0, 60, 90, 100],
        )) if deviation is not None else 0.0,
        "unusual_windows": float(np.interp(
            anomaly_share,
            [config.ANOMALY_SHARE_ALLOWED_PERCENT, config.ANOMALY_SHARE_WARNING_PERCENT, 100],
            [0, 60, 100],
        )),
        "temperature": 0.0,
    }
    healthy_max = reference["temperature_max_c"]
    if temperatures and healthy_max is not None:
        risk_parts["temperature"] = float(np.interp(
            max(temperatures) - healthy_max, [0, 10, 20], [0, 60, 90]
        ))
    risk = max(risk_parts.values())
    verdict = verdict_for(risk)

    result.update({
        "total_rms_g": round(total, 4),
        "deviation_percent": round(deviation, 1) if deviation is not None else None,
        "anomaly_window_percent": round(anomaly_share, 1),
        "model_unusual_percent": round(model_share, 1),
        "part_flags_percent": {
            part: round(100.0 * n / count, 1) for part, n in sorted(part_counts.items())
        },
        "risk_score": round(risk, 1),
        "risk_parts": {name: round(value, 1) for name, value in risk_parts.items()},
        "health_percent": round(100.0 - risk, 1),
        "verdict": verdict,
        "verdict_text": VERDICTS[verdict],
        "problems": [] if verdict == "HEALTHY" else possible_problems(
            axes, deviation, anomaly_share, model_share, result["temperature_max_c"], reference
        ),
    })
    return result


# ---------------------------------------------------------------------------
# Live view of a device (shared by /api/esp32/live, /predict and the trend)
# ---------------------------------------------------------------------------

# device_id -> (last_update, view): the analysis only reruns when new data arrived.
_live_cache = {}


def _compute_live_view(raw):
    windows, _ = running_windows(raw)
    results = summarize_windows(windows)
    latest = raw[-1] if raw else {}
    running_now = bool(raw) and sample_is_running(latest)
    # Bars: the live ADXL345 data of the last few seconds, pump running or not
    # (status "off" while it doesn't run).
    return {
        "pump_running": running_now,
        "latest_temperature_c": numeric_value(latest.get("temperature_c")),
        "sensor_vibration_rms_g": numeric_value(latest.get("vibration_rms_g")),
        "axes": axis_levels(live_bar_windows(raw), running=running_now),
        "live_assessment": assess(results),
    }


def live_view(device_id):
    """The device's live per-axis levels and its assessment over the last ~60 s
    of running data, plus name/location/last update; None for an unknown device."""
    with buffer_lock:
        if device_id not in esp32_data_buffer:
            return None
        info = esp32_data_buffer[device_id]
        raw = list(info.get("live_raw", []))
        device = {
            "device_id": device_id,
            "device_name": info.get("device_name") or device_id,
            "location": info.get("location") or "Unknown",
            "last_update": info.get("last_update"),
        }
    cached = _live_cache.get(device_id)
    if cached and cached[0] == device["last_update"]:
        view = cached[1]
    else:
        view = _compute_live_view(raw)
        _live_cache[device_id] = (device["last_update"], view)
    return {**device, **view}


def live_risk(device_id):
    """Risk of the running pump over the last ~60 s, or None (pump off, too little data)."""
    view = live_view(device_id)
    if not view or not view["pump_running"]:
        return None
    return view["live_assessment"]["risk_score"]
