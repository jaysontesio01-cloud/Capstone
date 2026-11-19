"""Analyze uploaded accelerometer data: JSON (/api/analyze) or a CSV file (/api/analyze/csv)."""

import pandas as pd
from flask import Blueprint, jsonify, request

from ..device_registry import is_esp32_device_id
from ..signal_processing import create_windows
from ..window_analysis import analyze_windows, health_status

bp = Blueprint("analysis", __name__)


def analysis_response(windows):
    """Analyze every window and build the response both endpoints return."""
    windows_results, anomaly_count, all_affected_parts = analyze_windows(windows)

    if anomaly_count > 0:
        overall_status = f"WARNING: Anomalies detected in {anomaly_count} windows"
        affected_parts_list = list(all_affected_parts)
    else:
        overall_status = "Water pump operating normally"
        affected_parts_list = []

    return jsonify({
        "status": "success",
        "data": {
            "total_windows": len(windows),
            "anomaly_count": anomaly_count,
            "anomaly_percentage": round((anomaly_count / len(windows)) * 100, 2),
            "health_status": health_status(anomaly_count, len(windows)),
            "overall_status": overall_status,
            "affected_parts": affected_parts_list,
            "windows": windows_results
        }
    }), 200


@bp.route("/api/analyze", methods=["POST"])
def api_analyze():
    """Main API endpoint for analyzing accelerometer data from Flutter"""
    try:
        data = request.get_json()

        if not data:
            return jsonify({
                "status": "error",
                "message": "No JSON data provided"
            }), 400

        device_id = data.get('device_id')
        if not device_id:
            return jsonify({
                "status": "error",
                "message": "device_id is required for analysis"
            }), 400
        if not is_esp32_device_id(device_id):
            return jsonify({
                "status": "error",
                "message": "Only ESP32 device IDs are accepted for analysis"
            }), 403

        if 'data' not in data:
            return jsonify({
                "status": "error",
                "message": "Missing 'data' field in JSON"
            }), 400

        accel_data = data['data']

        if isinstance(accel_data, list):
            df = pd.DataFrame(accel_data)
        else:
            return jsonify({
                "status": "error",
                "message": "Data must be a list of {x, y, z} objects"
            }), 400

        required_cols = ['x', 'y', 'z']
        if not all(col in df.columns for col in required_cols):
            return jsonify({
                "status": "error",
                "message": f"Missing columns. Required: {required_cols}"
            }), 400

        df = df[['x', 'y', 'z']]

        windows = create_windows(df)
        if len(windows) == 0:
            return jsonify({
                "status": "error",
                "message": "Not enough data to create windows (minimum 128 samples required)"
            }), 400

        return analysis_response(windows)

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Analysis failed: {str(e)}"
        }), 500


@bp.route("/api/analyze/csv", methods=["POST"])
def api_analyze_csv():
    """API endpoint for uploading CSV files from Flutter"""
    try:
        device_id = request.form.get('device_id')
        if not device_id:
            return jsonify({
                "status": "error",
                "message": "device_id form field is required for analysis"
            }), 400
        if not is_esp32_device_id(device_id):
            return jsonify({
                "status": "error",
                "message": "Only ESP32 device IDs are accepted for analysis"
            }), 403

        if "file" not in request.files:
            return jsonify({
                "status": "error",
                "message": "No file provided"
            }), 400

        file = request.files["file"]
        if file.filename == "":
            return jsonify({
                "status": "error",
                "message": "No file selected"
            }), 400

        df = pd.read_csv(file)
        df = df[['x', 'y', 'z']]

        windows = create_windows(df)
        if len(windows) == 0:
            return jsonify({
                "status": "error",
                "message": "Not enough data to create windows"
            }), 400

        return analysis_response(windows)

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"CSV analysis failed: {str(e)}"
        }), 500
