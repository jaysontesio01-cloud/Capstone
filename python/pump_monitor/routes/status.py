"""GET /api/status: health check used by the Flutter app."""

from flask import Blueprint, jsonify

from .. import firebase_store

bp = Blueprint("status", __name__)


@bp.route("/api/status", methods=["GET"])
def api_status():
    """Health check endpoint for Flutter app"""
    return jsonify({
        "status": "ok",
        "message": "Water Pump Anomaly Detection API is running",
        "version": "1.0",
        "firebase_connected": firebase_store.firebase_available()
    }), 200
