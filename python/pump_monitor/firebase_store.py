"""Firebase Realtime Database: connection, device records and sensor history.

The functions return None/False instead of raising when Firebase is not
available, so the backend also runs without it (set FIREBASE_ENABLED=0).
"""

import json
import os
import queue
import threading
from datetime import datetime
from pathlib import Path

from . import config
from .signal_processing import latest_sample_values

try:
    import firebase_admin
    from firebase_admin import credentials, db as realtime_db
except ImportError:
    firebase_admin = None
    credentials = None
    realtime_db = None


def resolve_firebase_credentials_file():
    """Find the Firebase service-account JSON in the project or common Windows folders."""
    env_value = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    if env_value and env_value.startswith("{"):
        return None
    if env_value and os.path.exists(env_value):
        return Path(env_value).expanduser().resolve()

    expected_name = config.FIREBASE_CREDENTIALS_FILENAME
    candidates = [
        config.BASE_DIR / expected_name,
        Path.home() / expected_name,
        Path.home() / "Desktop" / expected_name,
        Path.home() / "Downloads" / expected_name,
        Path.home() / "Documents" / expected_name,
        Path.home() / "AppData" / "Roaming" / expected_name,
        Path.home() / "AppData" / "Local" / expected_name,
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()

    return config.BASE_DIR / expected_name


FIREBASE_CREDENTIALS_FILE = resolve_firebase_credentials_file()
firebase_db = None  # set by init_firebase()


def init_firebase():
    """Initialize Firebase Admin SDK from a cloud secret or local dev file."""
    global firebase_db

    if firebase_db is not None:
        return firebase_db

    if not config.FIREBASE_ENABLED:
        return None

    if firebase_admin is None:
        print("Firebase Admin SDK is not installed. Run: pip install -r requirements.txt")
        return None

    try:
        if not firebase_admin._apps:
            config_value = (os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON") or "").strip()
            if config_value:
                if config_value.startswith("{"):
                    cred = credentials.Certificate(json.loads(config_value))
                elif Path(config_value).exists():
                    cred = credentials.Certificate(str(Path(config_value).expanduser().resolve()))
                else:
                    print(f"Firebase JSON env var path not found: {config_value}")
                    return None
            elif FIREBASE_CREDENTIALS_FILE.exists():
                cred = credentials.Certificate(str(FIREBASE_CREDENTIALS_FILE))
            else:
                print(
                    "Firebase credentials not found. Set FIREBASE_SERVICE_ACCOUNT_JSON "
                    f"or add the local dev file: {FIREBASE_CREDENTIALS_FILE}"
                )
                return None

            firebase_admin.initialize_app(cred, {
                "databaseURL": config.FIREBASE_DATABASE_URL
            })
        firebase_db = realtime_db
        print("Firebase Realtime Database connected")
        return firebase_db
    except Exception as exc:
        print(f"Firebase initialization failed: {exc}")
        firebase_db = None
        return None


def firebase_available():
    return init_firebase() is not None


def firebase_safe_key(value):
    return str(value).translate(str.maketrans({
        ".": "_",
        "#": "_",
        "$": "_",
        "[": "_",
        "]": "_",
        "/": "_",
    }))


def firebase_timestamp():
    return {
        ".sv": "timestamp"
    }


def machine_condition_from_analysis(analysis_result):
    if not analysis_result:
        return "Collecting data"
    return "Anomaly Detected" if analysis_result.get("is_anomaly") else "Normal"


def store_device_in_firebase(device_id, device_name, location):
    db = init_firebase()
    if db is None:
        return False

    try:
        device_ref = db.reference(f"esp32_devices/{firebase_safe_key(device_id)}")
        existing = device_ref.get()
        payload = {
            "device_id": device_id,
            "device_name": device_name,
            "location": location,
            "registered": True,
            "last_seen": firebase_timestamp(),
            "updated_at": firebase_timestamp(),
        }
        if not existing:
            payload["created_at"] = firebase_timestamp()

        device_ref.update(payload)
        return True
    except Exception as exc:
        print(f"Firebase device write failed: {exc}")
        return False


# Sensor batches waiting to be written to Firebase by the background writer.
_batch_queue = queue.Queue(maxsize=config.FIREBASE_BATCH_QUEUE_SIZE)
_batch_writer = None
_batch_writer_lock = threading.Lock()


def _write_batches():
    while True:
        device_id, samples, analysis_result = _batch_queue.get()
        try:
            store_sensor_batch_in_firebase(device_id, samples, analysis_result)
        finally:
            _batch_queue.task_done()


def queue_sensor_batch_for_firebase(device_id, samples, analysis_result=None):
    """Write the batch to Firebase in the background and return at once:
    the ESP32's upload must not wait for Firebase's network round trips (three
    per batch), or its data arrives late and the pump looks offline. When
    Firebase falls far behind, the oldest waiting batch is skipped. Returns
    whether the batch was queued."""
    global _batch_writer
    if init_firebase() is None:
        return False
    with _batch_writer_lock:
        if _batch_writer is None or not _batch_writer.is_alive():
            _batch_writer = threading.Thread(
                target=_write_batches, name="firebase-batches", daemon=True
            )
            _batch_writer.start()
    item = (device_id, list(samples), analysis_result)
    try:
        _batch_queue.put_nowait(item)
    except queue.Full:
        try:
            _batch_queue.get_nowait()  # skip the oldest
            _batch_queue.task_done()
            print("Firebase is behind: skipped the oldest sensor batch")
        except queue.Empty:
            pass
        try:
            _batch_queue.put_nowait(item)
        except queue.Full:
            return False
    return True


def store_sensor_batch_in_firebase(device_id, samples, analysis_result=None):
    db = init_firebase()
    if db is None:
        return None

    try:
        device_ref = db.reference(f"esp32_devices/{firebase_safe_key(device_id)}")
        batch_ref = device_ref.child("sensor_batches").push()

        latest_values = latest_sample_values(samples)

        batch_payload = {
            "device_id": device_id,
            "sample_count": len(samples),
            "samples": samples,
            "analysis": analysis_result,
            "machine_condition": machine_condition_from_analysis(analysis_result),
            "temperature_c": latest_values["latest_temperature_c"],
            "pressure_bar": latest_values["latest_pressure_bar"],
            "pressure_psi": latest_values["latest_pressure_psi"],
            "checked_at_local": datetime.now().isoformat(),
            "received_at": firebase_timestamp(),
        }
        batch_ref.set(batch_payload)

        latest_payload = {
            "last_seen": firebase_timestamp(),
            "last_sample_count": len(samples),
            "last_batch_id": batch_ref.key,
            "last_samples": samples[-10:],
            "last_temperature_c": latest_values["latest_temperature_c"],
            "last_pressure_bar": latest_values["latest_pressure_bar"],
            "last_pressure_psi": latest_values["latest_pressure_psi"],
            "last_analysis": analysis_result,
            "last_machine_condition": machine_condition_from_analysis(analysis_result),
        }
        device_ref.update(latest_payload)

        return batch_ref.key
    except Exception as exc:
        print(f"Firebase sensor batch write failed: {exc}")
        return None


def get_history_from_firebase(device_id, limit=25):
    db = init_firebase()
    if db is None:
        return None

    try:
        # Push keys are chronological, so ordering by key needs no
        # ".indexOn" rule (ordering by "received_at" fails without one).
        batches = (
            db.reference(f"esp32_devices/{firebase_safe_key(device_id)}/sensor_batches")
            .order_by_key()
            .limit_to_last(limit)
            .get()
        )
        if not batches:
            return []

        history = []
        for batch_id, batch in batches.items():
            analysis = batch.get("analysis") or {}
            history.append({
                "batch_id": batch_id,
                "device_id": batch.get("device_id", device_id),
                "sample_count": batch.get("sample_count", 0),
                "machine_condition": batch.get("machine_condition") or machine_condition_from_analysis(analysis),
                "checked_at": batch.get("received_at") or batch.get("checked_at_local"),
                "checked_at_local": batch.get("checked_at_local"),
                "is_anomaly": bool(analysis.get("is_anomaly", False)) if analysis else False,
                "anomaly_score": analysis.get("anomaly_score"),
                "affected_parts": analysis.get("affected_parts", []) if analysis else [],
                "temperature_c": batch.get("temperature_c"),
                "pressure_bar": batch.get("pressure_bar"),
                "pressure_psi": batch.get("pressure_psi"),
            })

        return sorted(history, key=lambda item: str(item.get("checked_at") or ""), reverse=True)
    except Exception as exc:
        print(f"Firebase history read failed: {exc}")
        return None
