"""Everything the app's dashboard shows, in one call, judged against the healthy pump.

GET  /api/esp32/live/<id>         live per-axis levels, temperature, overall
                                  verdict, machine health, possible problems
                                  and the current/last timed test
POST /api/esp32/test/<id>/start   {"minutes": 20}: start a timed test
                                  (409 while the ESP32 is not sending data)
POST /api/esp32/test/<id>/stop    stop it early
GET  /api/esp32/test/<id>         the test, judged over all its running data
GET  /api/esp32/tests/<id>        the previous (finished or stopped) tests, newest first
DELETE /api/esp32/tests/<id>      delete all previous tests of the device
"""

from flask import Blueprint, jsonify, request

from .. import config
from ..device_registry import (
    device_is_online,
    is_esp32_device_id,
    is_online,
    seconds_since_update,
)
from ..health_assessment import healthy_reference_summary, live_view
from ..recorder import active_recordings, recording_lock, recording_summary
from ..test_session import delete_tests, saved_tests, start_test, stop_test, test_summary

bp = Blueprint("live", __name__)


def _not_esp32():
    return jsonify({
        "status": "error",
        "message": "Only ESP32 device IDs are supported on this endpoint"
    }), 403


@bp.route("/api/esp32/live/<device_id>", methods=["GET"])
def esp32_live(device_id):
    if not is_esp32_device_id(device_id):
        return _not_esp32()
    try:
        view = live_view(device_id)
        if view is None:
            return jsonify({"status": "error", "message": f"Device {device_id} not found"}), 404
        age = seconds_since_update(view["last_update"])
        with recording_lock:
            recording = recording_summary(device_id, active_recordings.get(device_id))
        return jsonify({
            "status": "success",
            **view,
            "seconds_since_update": round(age, 1) if age is not None else None,
            "online": is_online(age),
            "healthy_reference": healthy_reference_summary(),
            "test": test_summary(device_id),
            "recording": recording,
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/esp32/test/<device_id>/start", methods=["POST"])
def esp32_test_start(device_id):
    if not is_esp32_device_id(device_id):
        return _not_esp32()
    data = request.get_json(silent=True) or {}
    try:
        minutes = float(data.get("minutes", 20))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "minutes must be a number"}), 400
    # A test needs the pump's data: only start it while the ESP32 is sending.
    if not device_is_online(device_id):
        return jsonify({
            "status": "error",
            "error": "esp32_offline",
            "message": f"{device_id} is not connected: no data in the last "
                       f"{config.LIVE_ONLINE_SECONDS} s. Turn on the ESP32 and "
                       "connect it to the internet (e.g. the phone's hotspot).",
        }), 409
    try:
        return jsonify({"status": "success", "test": start_test(device_id, minutes)}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/esp32/test/<device_id>/stop", methods=["POST"])
def esp32_test_stop(device_id):
    if not is_esp32_device_id(device_id):
        return _not_esp32()
    return jsonify({"status": "success", "test": stop_test(device_id)}), 200


@bp.route("/api/esp32/test/<device_id>", methods=["GET"])
def esp32_test(device_id):
    if not is_esp32_device_id(device_id):
        return _not_esp32()
    return jsonify({"status": "success", "test": test_summary(device_id)}), 200


@bp.route("/api/esp32/tests/<device_id>", methods=["GET"])
def esp32_tests(device_id):
    if not is_esp32_device_id(device_id):
        return _not_esp32()
    try:
        return jsonify({"status": "success", "tests": saved_tests(device_id)}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/esp32/tests/<device_id>", methods=["DELETE"])
def esp32_tests_delete(device_id):
    if not is_esp32_device_id(device_id):
        return _not_esp32()
    try:
        return jsonify({"status": "success", "deleted": delete_tests(device_id)}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
