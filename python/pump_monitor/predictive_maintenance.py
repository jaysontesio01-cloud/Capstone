"""Predictive maintenance: risk score, failure forecast and maintenance log.

For a pump with an established healthy baseline, the risk score follows how
far its vibration has risen above that baseline: 60 (WARNING) at the Formula 3
warning level (50%), 90 (failure threshold: shut down) at twice that. Pumps
without a baseline fall back to ISO 20816 vibration velocity (90 = zone D
limit). The forecast fits a trend to each pump's recent risk history to
estimate when it reaches 90 and when it should be shut down for maintenance.
"""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

from . import config
from .iso20816 import compare_to_standard_vibration
from .signal_processing import window_dynamic_rms
from .vibration_baseline import load_vibration_baselines

FAILURE_BASIS_HEALTHY = "Deviation from the healthy pump baseline (ISO 17359)"
FAILURE_BASIS_ISO = "ISO 20816 vibration standard"

# Last time a maintenance event was logged, per device.
maintenance_log_last_at = {}


def healthy_baseline_rms(device_id):
    """The device's established healthy baseline RMS in g, or None if it has none."""
    if not isinstance(device_id, str):
        return None
    try:
        state = load_vibration_baselines().get(device_id) or {}
    except Exception:
        return None
    if state.get("status") != "established" or not state.get("baseline_rms_g"):
        return None
    return float(state["baseline_rms_g"])


def failure_basis_for(device_id):
    return FAILURE_BASIS_HEALTHY if healthy_baseline_rms(device_id) else FAILURE_BASIS_ISO


def compute_predictive_risk(df, analysis_result=None, device_id=None):
    standard = compare_to_standard_vibration(df)
    if standard is None:
        return 0.0, {
            "status": "insufficient_data",
            "message": "Not enough vibration data to calculate predictive risk."
        }

    anomaly_score = float((analysis_result or {}).get("anomaly_score", 0.0) or 0.0)
    baseline_rms = healthy_baseline_rms(device_id)
    if baseline_rms:
        # Wear raises vibration, so risk follows the increase over the healthy
        # baseline (vibration below it, e.g. pump stopping, is risk 0).
        values = df[["x", "y", "z"]].apply(pd.to_numeric, errors="coerce").dropna().to_numpy(dtype=float)
        current_rms = window_dynamic_rms(values)
        increase = max(0.0, (current_rms - baseline_rms) / baseline_rms * 100.0)
        warning = config.VIBRATION_DEVIATION_WARNING_PERCENT
        risk_score = float(np.interp(
            increase,
            [0.0, warning, 2.0 * warning, 4.0 * warning],
            [0.0, 60.0, config.PREDICTIVE_FAILURE_RISK_THRESHOLD, 100.0],
        ))
        return round(risk_score, 2), {
            "status": "ok",
            "zone": standard.get("zone"),  # ISO 20816, information only
            "velocity_rms_mm_s": round(float(standard.get("velocity_rms_mm_s", 0.0) or 0.0), 4),
            "baseline_rms_g": round(baseline_rms, 4),
            "current_rms_g": round(current_rms, 4),
            "increase_over_baseline_percent": round(increase, 2),
            "anomaly_score": round(anomaly_score, 4),
            "failure_basis": FAILURE_BASIS_HEALTHY,
            "message": (f"Predictive risk follows the vibration increase over the healthy baseline; "
                        f"60 = {warning:g}% above, 90 (shut down) = {2 * warning:g}% above."),
        }

    # No healthy baseline: risk rises continuously with vibration velocity so the trend (and the
    # forecast) responds to every bit of wear, not only to zone changes.
    # Risk 90 (= PREDICTIVE_FAILURE_RISK_THRESHOLD) is the ISO 20816 C/D limit,
    # i.e. the point where vibration is severe enough to damage the machine.
    velocity = float(standard.get("velocity_rms_mm_s", 0.0) or 0.0)
    limits = standard.get("limits_mm_s") or config.ISO20816_VELOCITY_LIMITS_MM_S["class_ii"]
    risk_score = float(np.interp(
        velocity,
        [0.0, limits["ab"], limits["bc"], limits["cd"], 2.0 * limits["cd"]],
        [0.0, 30.0, 60.0, config.PREDICTIVE_FAILURE_RISK_THRESHOLD, 100.0],
    ))
    risk_score = min(100.0, max(0.0, risk_score))

    # Informational only: these models were trained on raw (gravity-included)
    # data and do not track wear on the denoised live signal.
    baseline = standard.get("learned_baseline_comparison") or {}
    diff = float(baseline.get("average_difference_g", 0.0) or 0.0)
    threshold = float(baseline.get("difference_threshold_g", 0.05) or 0.05)
    baseline_score = min(100.0, max(0.0, (diff / max(threshold, 0.0001)) * 100.0))

    return round(risk_score, 2), {
        "status": "ok",
        "zone": standard.get("zone"),
        "velocity_rms_mm_s": round(velocity, 4),
        "baseline_score": round(baseline_score, 2),
        "anomaly_score": round(anomaly_score, 4),
        "failure_basis": FAILURE_BASIS_ISO,
        "message": "Predictive risk follows ISO 20816 vibration velocity; 90 = zone D (damage) limit."
    }


def append_predictive_history(device_id, risk_score, timestamp=None):
    if not isinstance(device_id, str):
        return None

    timestamp = timestamp or datetime.now()
    row = {
        "device_id": device_id,
        "timestamp": timestamp.isoformat(),
        "risk_score": float(risk_score),
    }

    if config.PREDICTIVE_HISTORY_FILE.exists():
        history_df = pd.read_csv(config.PREDICTIVE_HISTORY_FILE)
    else:
        history_df = pd.DataFrame(columns=["device_id", "timestamp", "risk_score"])

    combined = pd.concat([history_df, pd.DataFrame([row])], ignore_index=True)
    combined = combined.dropna(subset=["device_id", "timestamp", "risk_score"])
    combined.to_csv(config.PREDICTIVE_HISTORY_FILE, index=False)
    return combined


def update_device_runtime_hours(device_id, observed_at=None, runtime_hours=None):
    observed_at = observed_at or datetime.now()
    runtime_hours = float(runtime_hours or 0.0)

    if config.DEVICE_RUNTIME_FILE.exists():
        runtime_df = pd.read_csv(config.DEVICE_RUNTIME_FILE)
    else:
        runtime_df = pd.DataFrame(columns=["device_id", "last_seen", "runtime_hours"])

    device_df = runtime_df[runtime_df["device_id"] == device_id].copy() if not runtime_df.empty else pd.DataFrame(columns=["device_id", "last_seen", "runtime_hours"])

    if not device_df.empty:
        last_seen = pd.to_datetime(device_df["last_seen"].iloc[-1], errors="coerce")
        last_hours = float(device_df["runtime_hours"].iloc[-1] or 0.0)
        if pd.notna(last_seen) and observed_at > last_seen:
            runtime_hours = max(runtime_hours, last_hours + (observed_at - last_seen).total_seconds() / 3600.0)
        else:
            runtime_hours = max(runtime_hours, last_hours)

    new_row = {
        "device_id": device_id,
        "last_seen": observed_at.isoformat(),
        "runtime_hours": round(runtime_hours, 3),
    }

    runtime_df = runtime_df[runtime_df["device_id"] != device_id].copy() if not runtime_df.empty else pd.DataFrame(columns=["device_id", "last_seen", "runtime_hours"])
    runtime_df = pd.concat([runtime_df, pd.DataFrame([new_row])], ignore_index=True)
    runtime_df = runtime_df.dropna(subset=["device_id", "last_seen", "runtime_hours"])
    runtime_df.to_csv(config.DEVICE_RUNTIME_FILE, index=False)

    state = runtime_df[runtime_df["device_id"] == device_id].sort_values("last_seen").iloc[-1].to_dict()
    return {
        "device_id": str(state.get("device_id", device_id)),
        "last_seen": state.get("last_seen"),
        "runtime_hours": float(state.get("runtime_hours", 0.0) or 0.0),
    }


def log_maintenance_event(
    device_id,
    risk_score,
    zone,
    predicted_failure_date,
    suggested_shutdown_date,
    maintenance_action,
    observed_at=None,
    summary=None,
):
    observed_at = observed_at or datetime.now()
    summary = summary or {}
    row = {
        "device_id": device_id,
        "timestamp": observed_at.isoformat(),
        "risk_score": float(risk_score or 0.0),
        "zone": zone,
        "predicted_failure_date": predicted_failure_date,
        "suggested_shutdown_date": suggested_shutdown_date,
        "maintenance_action": maintenance_action,
        "failure_basis": summary.get("failure_basis", FAILURE_BASIS_ISO),
        "remaining_life_days": summary.get("remaining_life_days"),
        "alert_active": summary.get("alert_active"),
        "runtime_hours": summary.get("runtime_hours"),
    }

    if config.MAINTENANCE_HISTORY_FILE.exists():
        history_df = pd.read_csv(config.MAINTENANCE_HISTORY_FILE)
    else:
        history_df = pd.DataFrame(columns=[
            "device_id", "timestamp", "risk_score", "zone", "predicted_failure_date",
            "suggested_shutdown_date", "maintenance_action", "failure_basis"
        ])

    history_df = pd.concat([history_df, pd.DataFrame([row])], ignore_index=True)
    history_df = history_df.dropna(subset=["device_id", "timestamp"])
    history_df.to_csv(config.MAINTENANCE_HISTORY_FILE, index=False)
    return row


def get_maintenance_history(device_id, limit=25):
    if not config.MAINTENANCE_HISTORY_FILE.exists():
        return []

    try:
        history_df = pd.read_csv(config.MAINTENANCE_HISTORY_FILE)
        if history_df.empty:
            return []
        filtered = history_df[history_df["device_id"] == device_id].copy() if "device_id" in history_df.columns else pd.DataFrame()
        if filtered.empty:
            return []
        filtered = filtered.sort_values(by="timestamp", ascending=False, kind="mergesort")
        filtered = filtered.head(limit)
        filtered = filtered.astype(object).where(pd.notna(filtered), None)
        return filtered.to_dict(orient="records")
    except Exception:
        return []


def estimate_remaining_life_days(risk_score, predicted_failure_date=None, observed_at=None):
    risk_score = float(risk_score or 0.0)
    if predicted_failure_date:
        try:
            failure_dt = pd.to_datetime(predicted_failure_date)
            if observed_at is None:
                observed_at = datetime.now()
            observed_dt = pd.to_datetime(observed_at)
            remaining_days = (failure_dt - observed_dt).total_seconds() / 86400.0
            if remaining_days >= 0:
                return round(remaining_days, 2)
        except Exception:
            pass

    if risk_score >= config.PREDICTIVE_FAILURE_RISK_THRESHOLD:
        return 0.0
    if risk_score <= 0:
        return None
    estimated_days = (config.PREDICTIVE_FAILURE_RISK_THRESHOLD - risk_score) / max(1.0, risk_score / 10.0)
    return round(max(0.0, estimated_days), 2)


def build_predictive_maintenance_summary(
    device_id, risk_score, zone, observed_at=None, runtime_hours=0.0,
    record=True, prediction=None,
):
    observed_at = observed_at or datetime.now()
    risk_score = float(risk_score or 0.0)
    runtime_state = update_device_runtime_hours(device_id, observed_at, runtime_hours)
    if prediction is None:
        prediction = predict_failure_for_device(device_id, risk_score, observed_at, record=record)

    # Severity always reflects the current vibration, even before a trend exists.
    severity = "CRITICAL" if risk_score >= 85 else "WARNING" if risk_score >= 60 else "MONITOR"
    failure_basis = failure_basis_for(device_id)
    if risk_score >= config.PREDICTIVE_FAILURE_RISK_THRESHOLD:
        maintenance_action = (
            "Shut down and inspect now: vibration is "
            f"{2 * config.VIBRATION_DEVIATION_WARNING_PERCENT:g}%+ above the healthy baseline"
            if failure_basis == FAILURE_BASIS_HEALTHY
            else "Shut down and inspect now: vibration is in ISO 20816 zone D"
        )
    elif prediction.get("status") == "forecast_available":
        maintenance_action = f"Recommended shutdown: {prediction['suggested_shutdown_date']}"
    elif prediction.get("status") in {"insufficient_data", "stable_or_improving"}:
        maintenance_action = (
            "Plan an inspection; continue collecting vibration history"
            if severity != "MONITOR"
            else "Continue monitoring and collect more vibration history"
        )
    else:
        maintenance_action = "Check device configuration and sensor health"

    remaining_life_days = prediction.get("remaining_life_days")
    if remaining_life_days is None:
        remaining_life_days = estimate_remaining_life_days(
            risk_score,
            prediction.get("predicted_failure_date"),
            observed_at,
        )

    alert_active = bool(prediction.get("alert_active", risk_score >= config.PREDICTIVE_ALERT_THRESHOLD))
    if remaining_life_days is not None and remaining_life_days <= config.PREDICTIVE_SHUTDOWN_LEAD_DAYS:
        alert_active = True

    summary = {
        "device_id": device_id,
        "risk_score": round(risk_score, 2),
        "zone": zone,
        "runtime_hours": float(runtime_state.get("runtime_hours", runtime_hours) or 0.0),
        "failure_basis": failure_basis,
        "severity": severity,
        "predicted_failure_date": prediction.get("predicted_failure_date"),
        "suggested_shutdown_date": prediction.get("suggested_shutdown_date"),
        "remaining_life_days": remaining_life_days,
        "days_to_failure": remaining_life_days,
        "failure_probability": prediction.get("failure_probability", 0.0),
        "alert_active": alert_active,
        "alert_threshold": config.PREDICTIVE_ALERT_THRESHOLD,
        "maintenance_action": maintenance_action,
        "message": prediction.get("message", "No maintenance forecast available yet."),
    }
    last_logged = maintenance_log_last_at.get(device_id)
    if record and (
        last_logged is None
        or observed_at < last_logged
        or (observed_at - last_logged) >= timedelta(minutes=config.PREDICTIVE_HISTORY_INTERVAL_MINUTES)
    ):
        log_maintenance_event(
            device_id=device_id,
            risk_score=risk_score,
            zone=zone,
            predicted_failure_date=prediction.get("predicted_failure_date"),
            suggested_shutdown_date=prediction.get("suggested_shutdown_date"),
            maintenance_action=maintenance_action,
            observed_at=observed_at,
            summary=summary,
        )
        maintenance_log_last_at[device_id] = observed_at
    return summary


def suggested_shutdown_for(predicted_failure_datetime, observed_at):
    """Shutdown lead time before failure, but never a date in the past."""
    return max(
        observed_at,
        predicted_failure_datetime - timedelta(days=config.PREDICTIVE_SHUTDOWN_LEAD_DAYS),
    )


def history_point_due(device_id, observed_at=None):
    """True when the device's risk trend is due a new point (at most one per
    PREDICTIVE_HISTORY_INTERVAL_MINUTES)."""
    observed_at = observed_at or datetime.now()
    path = config.PREDICTIVE_HISTORY_FILE
    if not path.exists():
        return True
    history = pd.read_csv(path)
    if history.empty or "device_id" not in history:
        return True
    times = pd.to_datetime(
        history.loc[history["device_id"] == device_id, "timestamp"], errors="coerce"
    ).dropna()
    if times.empty:
        return True
    return pd.Timestamp(observed_at) - times.max() >= pd.Timedelta(
        minutes=config.PREDICTIVE_HISTORY_INTERVAL_MINUTES
    )


def predict_failure_for_device(device_id, risk_score, observed_at=None, record=True):
    """Forecast when risk reaches the failure threshold.

    ``record=False`` forecasts without saving this point (e.g. pump idle).
    Points are saved at most once per PREDICTIVE_HISTORY_INTERVAL_MINUTES so a
    device posting 10x per second does not flood the history file.
    """
    observed_at = observed_at or datetime.now()
    risk_score = float(risk_score or 0.0)

    if not isinstance(device_id, str):
        return {
            "status": "invalid_device_id",
            "risk_score": round(risk_score, 2),
            "failure_basis": FAILURE_BASIS_ISO,
            "message": "A valid device ID is required for predictive maintenance."
        }

    failure_basis = failure_basis_for(device_id)
    history_path = config.PREDICTIVE_HISTORY_FILE
    if history_path.exists():
        history_df = pd.read_csv(history_path)
    else:
        history_df = pd.DataFrame(columns=["device_id", "timestamp", "risk_score"])

    if not history_df.empty:
        history_df = history_df[history_df["device_id"] == device_id].copy()

    history_df = history_df.dropna(subset=["timestamp", "risk_score"])
    if not history_df.empty:
        history_df["timestamp"] = pd.to_datetime(history_df["timestamp"], errors="coerce")
        history_df = history_df.dropna(subset=["timestamp"]).sort_values("timestamp")

    runtime_state = update_device_runtime_hours(device_id, observed_at, 0.0)
    runtime_hours = float(runtime_state.get("runtime_hours", 0.0) or 0.0)
    row = pd.DataFrame([{
        "device_id": device_id,
        "timestamp": pd.Timestamp(observed_at),
        "risk_score": risk_score,
        "runtime_hours": runtime_hours,
    }])

    last_saved_at = history_df["timestamp"].max() if not history_df.empty else None
    should_persist = record and (
        last_saved_at is None
        or pd.isna(last_saved_at)
        or (pd.Timestamp(observed_at) - last_saved_at)
        >= pd.Timedelta(minutes=config.PREDICTIVE_HISTORY_INTERVAL_MINUTES)
    )

    history_df = pd.concat([history_df, row], ignore_index=True) if not history_df.empty else row
    history_df["timestamp"] = pd.to_datetime(history_df["timestamp"], errors="coerce")
    history_df = history_df.dropna(subset=["timestamp"]).drop_duplicates(subset=["device_id", "timestamp"], keep="last")

    if should_persist:
        if history_path.exists():
            all_history = pd.read_csv(history_path)
            all_history = pd.concat([all_history, row], ignore_index=True)
        else:
            all_history = row
        all_history.to_csv(history_path, index=False)

    device_history = history_df[history_df["device_id"] == device_id].sort_values("timestamp").copy()
    # Fit the trend on recent history only: wear accelerates, so old healthy
    # points would make a straight-line fit badly underestimate the slope.
    trend_start = pd.Timestamp(observed_at) - pd.Timedelta(days=config.PREDICTIVE_TREND_WINDOW_DAYS)
    device_history = device_history[
        (device_history["timestamp"] >= trend_start)
        & (device_history["timestamp"] <= pd.Timestamp(observed_at))
    ]
    if len(device_history) < 3:
        remaining_life_days = estimate_remaining_life_days(risk_score, None, observed_at)
        alert_active = risk_score >= config.PREDICTIVE_ALERT_THRESHOLD
        return {
            "status": "insufficient_data",
            "risk_score": round(risk_score, 2),
            "predicted_failure_date": None,
            "suggested_shutdown_date": None,
            "remaining_life_days": remaining_life_days,
            "days_to_failure": remaining_life_days,
            "failure_basis": failure_basis,
            "failure_probability": min(100.0, max(0.0, (risk_score / config.PREDICTIVE_FAILURE_RISK_THRESHOLD) * 100.0)),
            "alert_active": alert_active,
            "alert_threshold": config.PREDICTIVE_ALERT_THRESHOLD,
            "message": "Need at least 3 vibration-risk data points before estimating a failure date."
        }

    runtime_units = device_history.get("runtime_hours", pd.Series([0.0] * len(device_history)))
    runtime_units = pd.to_numeric(runtime_units, errors="coerce").fillna(0.0)
    device_history["runtime_hours"] = runtime_units

    if device_history["runtime_hours"].nunique() > 1:
        X = device_history["runtime_hours"].to_numpy().reshape(-1, 1)
        y = device_history["risk_score"].astype(float).to_numpy()
        model = LinearRegression()
        model.fit(X, y)
        slope_per_hour = float(model.coef_[0])
        latest_risk = float(device_history["risk_score"].iloc[-1])
        latest_runtime = float(device_history["runtime_hours"].iloc[-1])

        # No forecast while the pump is within the healthy pump's range: small
        # ups and downs of a healthy pump must not read as a coming failure.
        if slope_per_hour <= 0 or latest_risk < config.PREDICTIVE_FORECAST_MIN_RISK:
            remaining_life_days = estimate_remaining_life_days(latest_risk, None, observed_at)
            return {
                "status": "stable_or_improving",
                "risk_score": round(latest_risk, 2),
                "predicted_failure_date": None,
                "suggested_shutdown_date": None,
                "remaining_life_days": remaining_life_days,
                "days_to_failure": remaining_life_days,
                "failure_basis": failure_basis,
                "failure_probability": min(100.0, max(0.0, (latest_risk / config.PREDICTIVE_FAILURE_RISK_THRESHOLD) * 100.0)),
                "alert_active": latest_risk >= config.PREDICTIVE_ALERT_THRESHOLD,
                "alert_threshold": config.PREDICTIVE_ALERT_THRESHOLD,
                "message": "The vibration trend is not worsening yet, so no failure forecast is available."
            }

        hours_to_failure = (config.PREDICTIVE_FAILURE_RISK_THRESHOLD - latest_risk) / slope_per_hour
        predicted_failure_datetime = observed_at + timedelta(hours=max(0.0, hours_to_failure))
        suggested_shutdown_datetime = suggested_shutdown_for(predicted_failure_datetime, observed_at)
        failure_probability = min(100.0, max(0.0, (latest_risk / config.PREDICTIVE_FAILURE_RISK_THRESHOLD) * 100.0))
        remaining_life_days = round(max(0.0, hours_to_failure / 24.0), 2)

        return {
            "status": "forecast_available",
            "risk_score": round(latest_risk, 2),
            "predicted_failure_date": predicted_failure_datetime.isoformat(),
            "suggested_shutdown_date": suggested_shutdown_datetime.isoformat(),
            "remaining_life_days": remaining_life_days,
            "days_to_failure": remaining_life_days,
            "runtime_hours": round(latest_runtime, 2),
            "failure_probability": round(failure_probability, 2),
            "failure_basis": failure_basis,
            "lead_days": config.PREDICTIVE_SHUTDOWN_LEAD_DAYS,
            "alert_active": latest_risk >= config.PREDICTIVE_ALERT_THRESHOLD or remaining_life_days <= config.PREDICTIVE_SHUTDOWN_LEAD_DAYS,
            "alert_threshold": config.PREDICTIVE_ALERT_THRESHOLD,
            "message": f"Suggested shut down: {suggested_shutdown_datetime.strftime('%Y-%m-%d')} before vibration reaches the failure level ({failure_basis})."
        }

    device_history["days_since_start"] = (
        device_history["timestamp"] - device_history["timestamp"].iloc[0]
    ).dt.total_seconds() / 86400.0
    X = device_history["days_since_start"].to_numpy().reshape(-1, 1)
    y = device_history["risk_score"].astype(float).to_numpy()

    model = LinearRegression()
    model.fit(X, y)
    slope_per_day = float(model.coef_[0])
    latest_risk = float(device_history["risk_score"].iloc[-1])

    if slope_per_day <= 0 or latest_risk < config.PREDICTIVE_FORECAST_MIN_RISK:
        remaining_life_days = estimate_remaining_life_days(latest_risk, None, observed_at)
        return {
            "status": "stable_or_improving",
            "risk_score": round(latest_risk, 2),
            "predicted_failure_date": None,
            "suggested_shutdown_date": None,
            "remaining_life_days": remaining_life_days,
            "days_to_failure": remaining_life_days,
            "failure_basis": failure_basis,
            "failure_probability": min(100.0, max(0.0, (latest_risk / config.PREDICTIVE_FAILURE_RISK_THRESHOLD) * 100.0)),
            "alert_active": latest_risk >= config.PREDICTIVE_ALERT_THRESHOLD,
            "alert_threshold": config.PREDICTIVE_ALERT_THRESHOLD,
            "message": "The vibration trend is not worsening yet, so no failure forecast is available."
        }

    days_to_failure = (config.PREDICTIVE_FAILURE_RISK_THRESHOLD - latest_risk) / slope_per_day
    predicted_failure_date = observed_at + timedelta(days=max(0.0, days_to_failure))
    suggested_shutdown_date = suggested_shutdown_for(predicted_failure_date, observed_at)
    failure_probability = min(100.0, max(0.0, (latest_risk / config.PREDICTIVE_FAILURE_RISK_THRESHOLD) * 100.0))
    remaining_life_days = round(max(0.0, days_to_failure), 2)

    return {
        "status": "forecast_available",
        "risk_score": round(latest_risk, 2),
        "predicted_failure_date": predicted_failure_date.isoformat(),
        "suggested_shutdown_date": suggested_shutdown_date.isoformat(),
        "remaining_life_days": remaining_life_days,
        "days_to_failure": remaining_life_days,
        "runtime_hours": round(float(runtime_state.get("runtime_hours", 0.0) or 0.0), 2),
        "failure_probability": round(failure_probability, 2),
        "failure_basis": failure_basis,
        "lead_days": config.PREDICTIVE_SHUTDOWN_LEAD_DAYS,
        "alert_active": latest_risk >= config.PREDICTIVE_ALERT_THRESHOLD or remaining_life_days <= config.PREDICTIVE_SHUTDOWN_LEAD_DAYS,
        "alert_threshold": config.PREDICTIVE_ALERT_THRESHOLD,
        "message": f"Suggested shut down: {suggested_shutdown_date.strftime('%Y-%m-%d')} before vibration reaches the failure level ({failure_basis})."
    }
