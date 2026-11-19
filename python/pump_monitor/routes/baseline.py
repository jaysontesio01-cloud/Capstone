"""ISO 17359 vibration baseline: status and reset."""

import numpy as np
from flask import Blueprint, jsonify, request

from ..device_registry import is_esp32_device_id
from ..signal_processing import numeric_value
from ..vibration_baseline import (
    load_vibration_baselines,
    reset_vibration_baseline,
    vibration_baseline_summary,
)

bp = Blueprint("baseline", __name__)


@bp.route("/api/esp32/baseline/<device_id>", methods=["GET"])
def esp32_baseline_status(device_id):
    """Return the ISO 17359 vibration baseline status for a device."""
    if not is_esp32_device_id(device_id):
        return jsonify({
            "status": "error",
            "message": "Only ESP32 device IDs are supported on this endpoint"
        }), 403

    try:
        state = load_vibration_baselines().get(device_id)
        return jsonify({
            "status": "success",
            "device_id": device_id,
            "baseline": vibration_baseline_summary(state)
        }), 200
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@bp.route("/api/esp32/baseline/<device_id>/reset", methods=["POST"])
def esp32_baseline_reset(device_id):
    """Re-establish the vibration baseline, e.g. after an overhaul (ISO 17359).

    Optional JSON: {"wear_in_hours": 1.0, "reason": "overhaul", "known_healthy": false}
    known_healthy=true: the operator certifies the pump is in good condition, so
    every stable running reading is used, not only windows the models accept.
    """
    if not is_esp32_device_id(device_id):
        return jsonify({
            "status": "error",
            "message": "Only ESP32 device IDs are supported on this endpoint"
        }), 403

    data = request.get_json(silent=True) or {}
    wear_in_hours = None
    if data.get("wear_in_hours") is not None:
        wear_in_hours = numeric_value(data.get("wear_in_hours"))
        if wear_in_hours is None or not np.isfinite(wear_in_hours) or wear_in_hours < 0:
            return jsonify({
                "status": "error",
                "message": "wear_in_hours must be a number of hours >= 0"
            }), 400

    try:
        state = reset_vibration_baseline(
            device_id,
            wear_in_hours,
            str(data.get("reason") or "maintenance"),
            known_healthy=str(data.get("known_healthy", "")).lower() in {"true", "1", "yes"},
        )
        return jsonify({
            "status": "success",
            "device_id": device_id,
            "baseline": vibration_baseline_summary(state)
        }), 200
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500
