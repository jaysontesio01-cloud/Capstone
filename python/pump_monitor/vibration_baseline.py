"""Per-pump vibration baseline (ISO 17359:2018) and percentage deviation.

While a pump runs normally its baseline moves through wear-in, collecting and
established. After that, each window is compared with the baseline using
Formula 3 (percentage deviation). Baselines are saved in
data/vibration_baseline.json.
"""

import json
import os
from datetime import datetime
from threading import Lock

import numpy as np

from . import config
from .formulas import percentage_deviation
from .signal_processing import finite_or_none, window_dynamic_rms

vibration_baseline_lock = Lock()


def load_vibration_baselines():
    if not config.VIBRATION_BASELINE_FILE.exists():
        return {}
    with open(config.VIBRATION_BASELINE_FILE, "r", encoding="utf-8") as f_baseline:
        return json.load(f_baseline)


def save_vibration_baselines(baselines):
    temp_file = config.VIBRATION_BASELINE_FILE.with_suffix(".tmp")
    with open(temp_file, "w", encoding="utf-8") as f_baseline:
        json.dump(baselines, f_baseline, indent=2)
    os.replace(temp_file, config.VIBRATION_BASELINE_FILE)


def new_vibration_baseline_state(observed_at, wear_in_hours, reason, known_healthy=False):
    return {
        "status": "wear_in" if wear_in_hours > 0 else "collecting",
        "reason": reason,
        # Operator certifies the pump is in known good condition (ISO 17359:
        # "known to be acceptable"), so automatic anomaly checks don't gate it.
        "known_healthy": bool(known_healthy),
        "started_at": observed_at.isoformat(),
        "wear_in_hours": float(wear_in_hours),
        "running_hours": 0.0,
        "last_running_at": None,
        "readings": [],
        "baseline_rms_g": None,
        "baseline_std_g": None,
        "established_at": None,
    }


def reset_vibration_baseline(
    device_id, wear_in_hours=None, reason="maintenance", observed_at=None, known_healthy=False
):
    """Start a new baseline; ISO 17359 re-establishes baselines after an overhaul."""
    if wear_in_hours is None:
        wear_in_hours = config.VIBRATION_BASELINE_WEAR_IN_HOURS
    state = new_vibration_baseline_state(
        observed_at or datetime.now(), wear_in_hours, reason, known_healthy
    )
    with vibration_baseline_lock:
        baselines = load_vibration_baselines()
        baselines[device_id] = state
        save_vibration_baselines(baselines)
    return state


def advance_vibration_baseline(state, current_rms, acceptable, observed_at, temperature_c=None):
    """Move a running pump's baseline through wear-in -> collecting -> established."""
    if state["last_running_at"]:
        gap = (observed_at - datetime.fromisoformat(state["last_running_at"])).total_seconds()
        if 0 < gap <= config.VIBRATION_BASELINE_MAX_RUNNING_GAP_SECONDS:
            state["running_hours"] += gap / 3600.0
    state["last_running_at"] = observed_at.isoformat()

    if state["status"] == "wear_in" and state["running_hours"] >= state["wear_in_hours"]:
        state["status"] = "collecting"
    # Baseline data only come from operation known to be acceptable.
    if state["status"] != "collecting" or not (acceptable or state.get("known_healthy")):
        return

    readings = state["readings"]
    if readings:
        since_last = (observed_at - datetime.fromisoformat(readings[-1]["at"])).total_seconds()
        if since_last < config.VIBRATION_BASELINE_READING_INTERVAL_SECONDS:
            return
    readings.append({
        "at": observed_at.isoformat(),
        "rms_g": float(current_rms),
        "temperature_c": finite_or_none(temperature_c),
    })
    del readings[:-config.VIBRATION_BASELINE_READINGS]

    # Stable operation: the latest readings agree within the max CV.
    values = np.array([reading["rms_g"] for reading in readings])
    if len(values) == config.VIBRATION_BASELINE_READINGS and values.mean() > 0:
        if values.std() / values.mean() * 100.0 <= config.VIBRATION_BASELINE_MAX_CV_PERCENT:
            temperatures = [
                reading["temperature_c"] for reading in readings
                if reading.get("temperature_c") is not None
            ]
            state.update(
                status="established",
                baseline_rms_g=float(values.mean()),
                baseline_std_g=float(values.std()),
                baseline_temperature_c=float(np.mean(temperatures)) if temperatures else None,
                baseline_temperature_std_c=float(np.std(temperatures)) if temperatures else None,
                established_at=observed_at.isoformat(),
            )


def vibration_baseline_summary(state):
    summary = {
        "standard": "ISO 17359:2018",
        "baseline_status": "not_started",
        "baseline_established": False,
        "baseline_reason": None,
        "known_healthy": False,
        "baseline_started_at": None,
        "wear_in_hours": config.VIBRATION_BASELINE_WEAR_IN_HOURS,
        "running_hours": 0.0,
        "baseline_readings": 0,
        "baseline_readings_required": config.VIBRATION_BASELINE_READINGS,
        "readings_cv_percent": None,
        "max_cv_percent": config.VIBRATION_BASELINE_MAX_CV_PERCENT,
        "baseline_rms_g": None,
        "baseline_std_g": None,
        "baseline_temperature_c": None,
        "baseline_established_at": None,
    }
    if not state:
        return summary

    values = np.array([reading["rms_g"] for reading in state["readings"]])
    summary.update({
        "baseline_status": state["status"],
        "baseline_established": state["status"] == "established",
        "baseline_reason": state["reason"],
        "known_healthy": bool(state.get("known_healthy", False)),
        "baseline_started_at": state["started_at"],
        "wear_in_hours": round(state["wear_in_hours"], 3),
        "running_hours": round(state["running_hours"], 3),
        "baseline_readings": int(len(values)),
    })
    if len(values) >= 2 and values.mean() > 0:
        summary["readings_cv_percent"] = round(float(values.std() / values.mean() * 100.0), 2)
    if state["baseline_rms_g"] is not None:
        summary["baseline_rms_g"] = round(state["baseline_rms_g"], 4)
        summary["baseline_std_g"] = round(state["baseline_std_g"], 4)
        summary["baseline_established_at"] = state["established_at"]
    if state.get("baseline_temperature_c") is not None:
        summary["baseline_temperature_c"] = round(state["baseline_temperature_c"], 2)
    return summary


def vibration_deviation_for_window(
    device_id, window_data, is_idle=False, acceptable=False, record=False,
    observed_at=None, temperature_c=None,
):
    """Compare a window's three-axis RMS with the pump's ISO 17359 baseline (Formula 3).

    record=True (live data only) advances the baseline while it is being
    acquired; acceptable says the window may be used as baseline data.
    The temperature is stored with baseline readings and compared in degC.
    """
    observed_at = observed_at or datetime.now()
    current_rms = window_dynamic_rms(window_data)
    temperature_c = finite_or_none(temperature_c)

    if record and not is_idle:
        with vibration_baseline_lock:
            baselines = load_vibration_baselines()
            state = baselines.get(device_id) or new_vibration_baseline_state(
                observed_at, config.VIBRATION_BASELINE_WEAR_IN_HOURS, "monitoring_started"
            )
            if state["status"] != "established":
                advance_vibration_baseline(
                    state, current_rms, acceptable, observed_at, temperature_c
                )
                baselines[device_id] = state
                save_vibration_baselines(baselines)
    else:
        state = load_vibration_baselines().get(device_id)

    result = vibration_baseline_summary(state)
    result.update({
        "current_rms_g": round(current_rms, 4),
        "current_temperature_c": temperature_c,
        "temperature_difference_c": None,
        "deviation_percent": None,
        "warning_threshold_percent": config.VIBRATION_DEVIATION_WARNING_PERCENT,
        "warning": False,
    })
    if temperature_c is not None and result["baseline_temperature_c"] is not None:
        result["temperature_difference_c"] = round(
            temperature_c - result["baseline_temperature_c"], 2
        )

    if is_idle:
        result["message"] = "Pump is idle; vibration deviation is not calculated."
    elif result["baseline_status"] == "not_started":
        result["message"] = "Baseline acquisition starts when live running data arrives."
    elif result["baseline_status"] == "wear_in":
        result["message"] = (
            f"Wear-in period: {result['running_hours']:.2f} of "
            f"{result['wear_in_hours']:.2f} running hours before baseline readings start."
        )
    elif result["baseline_status"] == "collecting":
        result["message"] = (
            f"Collecting baseline readings during acceptable, stable operation "
            f"({result['baseline_readings']}/{config.VIBRATION_BASELINE_READINGS})."
        )
        cv = result["readings_cv_percent"]
        if result["baseline_readings"] == config.VIBRATION_BASELINE_READINGS and cv is not None:
            result["message"] += (
                f" Readings vary {cv:.1f}% (max {config.VIBRATION_BASELINE_MAX_CV_PERCENT:g}%);"
                " waiting for stable operation."
            )
    else:
        baseline_rms = state["baseline_rms_g"]
        deviation = percentage_deviation(current_rms, baseline_rms)
        direction = "above" if current_rms >= baseline_rms else "below"
        result["deviation_percent"] = round(deviation, 2)
        result["warning"] = deviation >= config.VIBRATION_DEVIATION_WARNING_PERCENT
        result["message"] = (
            f"Vibration is {deviation:.1f}% {direction} the ISO 17359 baseline"
            + ("; inspect the pump." if result["warning"] else ".")
        )
        if result["temperature_difference_c"] is not None:
            result["message"] += (
                f" Temperature is {result['temperature_difference_c']:+.1f} degC vs baseline."
            )
    return result
