"""Predictive maintenance: failure forecast and maintenance history."""

from datetime import datetime

import pandas as pd
from flask import Blueprint, jsonify, request

from .. import config
from ..device_registry import is_esp32_device_id
from ..health_assessment import live_risk
from ..predictive_maintenance import (
    build_predictive_maintenance_summary,
    get_maintenance_history,
    predict_failure_for_device,
)

bp = Blueprint("maintenance", __name__)


@bp.route("/api/esp32/predict/<device_id>", methods=["GET"])
def esp32_predict(device_id):
    """Return a predictive-maintenance forecast for an ESP32 device."""
    try:
        if not is_esp32_device_id(device_id):
            return jsonify({
                "status": "error",
                "message": "Only ESP32 device IDs are supported on this endpoint"
            }), 403

        # While the pump runs: the same risk as the app's machine health (last
        # ~60 s of running data). Otherwise the last risk recorded while running.
        risk_score = live_risk(device_id)
        if risk_score is None:
            risk_score = 0.0
            history_df = pd.read_csv(config.PREDICTIVE_HISTORY_FILE) if config.PREDICTIVE_HISTORY_FILE.exists() else pd.DataFrame()
            device_history = history_df[history_df["device_id"] == device_id] if not history_df.empty else pd.DataFrame()
            if not device_history.empty:
                risk_score = float(device_history["risk_score"].iloc[-1])

        # Read-only endpoint: the data POST records history, polling must not.
        now = datetime.now()
        prediction = predict_failure_for_device(device_id, risk_score, now, record=False)
        summary = build_predictive_maintenance_summary(
            device_id,
            risk_score,
            "A" if risk_score < 30 else "B" if risk_score < 60 else "C" if risk_score < 90 else "D",
            now,
            runtime_hours=0.0,
            record=False,
            prediction=prediction,
        )
        return jsonify({
            "status": "success",
            "device_id": device_id,
            "predictive_maintenance": prediction,
            "maintenance_summary": summary,
            "risk_score": round(risk_score, 2)
        }), 200
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@bp.route("/api/esp32/maintenance/<device_id>", methods=["GET"])
def esp32_maintenance_history(device_id):
    """Return saved maintenance recommendations for a device."""
    try:
        if not is_esp32_device_id(device_id):
            return jsonify({
                "status": "error",
                "message": "Only ESP32 device IDs are supported on this endpoint"
            }), 403

        limit = request.args.get("limit", default=25, type=int)
        limit = max(1, min(limit, 100))
        history = get_maintenance_history(device_id, limit=limit)
        return jsonify({
            "status": "success",
            "device_id": device_id,
            "history": history
        }), 200
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500
