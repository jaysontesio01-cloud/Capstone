"""Pump devices: register, list and remove."""

from flask import Blueprint, jsonify, request

from .. import firebase_store
from ..device_registry import (
    buffer_lock,
    device_registration_state,
    esp32_data_buffer,
    is_esp32_device_id,
    list_registered_devices,
    register_device,
)

bp = Blueprint("devices", __name__)


@bp.route("/api/pumps", methods=["GET"])
def list_pumps():
    """Compatibility route for a multi-pump dashboard."""
    return jsonify({
        "status": "success",
        "devices": list_registered_devices(),
    }), 200


@bp.route("/api/esp32/devices", methods=["GET"])
def esp32_list_devices():
    """List all registered devices in the app."""
    try:
        return jsonify({
            "status": "success",
            "devices": list_registered_devices(),
        }), 200
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e),
        }), 500


@bp.route("/api/esp32/devices/<device_id>", methods=["DELETE"])
def esp32_delete_device(device_id):
    """Unregister a device while retaining its sensor history."""
    clean_device_id = str(device_id or "").strip()
    if not is_esp32_device_id(clean_device_id):
        return jsonify({
            "status": "error",
            "message": "Invalid ESP32 device ID",
        }), 400

    with buffer_lock:
        device_in_memory = clean_device_id in esp32_data_buffer

    db = firebase_store.init_firebase()
    firebase_saved = False
    if db is not None:
        try:
            device_ref = db.reference(
                f"esp32_devices/{firebase_store.firebase_safe_key(clean_device_id)}"
            )
            device = device_ref.get()
            if not device_in_memory and not device:
                return jsonify({
                    "status": "error",
                    "message": f"Device {clean_device_id} not found",
                }), 404
            device_ref.update({
                "registered": False,
                "unregistered_at": firebase_store.firebase_timestamp(),
            })
            firebase_saved = True
        except Exception as exc:
            print(f"Firebase device removal failed: {exc}")
            return jsonify({
                "status": "error",
                "message": "Could not persist device removal",
            }), 500
    elif not device_in_memory:
        return jsonify({
            "status": "error",
            "message": f"Device {clean_device_id} not found",
        }), 404

    with buffer_lock:
        device_registration_state[clean_device_id] = False
        if device_in_memory:
            esp32_data_buffer[clean_device_id]["registered"] = False

    return jsonify({
        "status": "success",
        "message": f"Device {clean_device_id} removed",
        "device_id": clean_device_id,
        "sensor_history_retained": True,
        "firebase_saved": firebase_saved,
    }), 200


@bp.route("/api/esp32/register", methods=["POST"])
def esp32_register():
    """Register a new ESP32 device"""
    try:
        data = request.get_json() or {}
        device_id = data.get('device_id')
        device_name = data.get('device_name')
        location = data.get('location')

        if not device_id:
            return jsonify({
                "status": "error",
                "message": "device_id is required"
            }), 400

        result = register_device(device_id, device_name, location)
        if result["status"] == "error":
            return jsonify(result), 400
        return jsonify(result), 200

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500
