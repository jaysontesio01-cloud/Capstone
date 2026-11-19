"""Raw sensor recordings: start, stop and status."""

from flask import Blueprint, jsonify, request

from ..device_registry import is_esp32_device_id
from ..recorder import (
    active_recordings,
    recording_lock,
    recording_summary,
    start_recording,
    stop_recording,
)

bp = Blueprint("recording", __name__)


@bp.route("/api/esp32/recording/<device_id>/start", methods=["POST"])
def esp32_recording_start(device_id):
    """Save every raw sample from the device to recordings/<device>_<label>_<time>.csv.

    Optional JSON: {"label": "healthy"}
    """
    if not is_esp32_device_id(device_id):
        return jsonify({
            "status": "error",
            "message": "Only ESP32 device IDs are supported on this endpoint"
        }), 403

    data = request.get_json(silent=True) or {}
    try:
        _, summary = start_recording(device_id, data.get("label") or "session")
        return jsonify({"status": "success", **summary}), 200
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@bp.route("/api/esp32/recording/<device_id>/stop", methods=["POST"])
def esp32_recording_stop(device_id):
    """Stop the device's recording and return the saved file."""
    return jsonify({"status": "success", **stop_recording(device_id)}), 200


@bp.route("/api/esp32/recording/<device_id>", methods=["GET"])
def esp32_recording_status(device_id):
    with recording_lock:
        summary = recording_summary(device_id, active_recordings.get(device_id))
    return jsonify({"status": "success", **summary}), 200
