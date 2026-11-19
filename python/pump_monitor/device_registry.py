"""Registered pump devices and their in-memory sample buffers.

esp32_data_buffer holds the latest samples of every device; hold buffer_lock
while reading or changing it. Registrations are also saved in Firebase when
it is available.
"""

from collections import defaultdict
from datetime import datetime
from threading import Lock

from . import config, firebase_store

esp32_data_buffer = defaultdict(lambda: {
    'data': [],
    'raw_data': [],
    'live_raw': [],  # raw samples (clipped readings replaced) for the app's live view
    'last_update': None,
    'device_name': None,
    'location': None
})
buffer_lock = Lock()
device_registration_state = {}


def is_esp32_device_id(device_id):
    if not isinstance(device_id, str):
        return False
    cleaned = device_id.strip()
    return bool(cleaned) and any(
        cleaned.upper().startswith(prefix)
        for prefix in ("ESP32", "PUMP", "WATER-PUMP", "DEVICE", "FLUTTER")
    )


def get_device_registration_state(device_id):
    with buffer_lock:
        if device_id in device_registration_state:
            return device_registration_state[device_id]

    db = firebase_store.init_firebase()
    if db is None:
        return True

    try:
        device = db.reference(
            f"esp32_devices/{firebase_store.firebase_safe_key(device_id)}"
        ).get()
        is_registered = (
            device.get("registered", True) is not False
            if isinstance(device, dict)
            else True
        )
    except Exception as exc:
        print(f"Firebase device registration read failed: {exc}")
        return True

    with buffer_lock:
        device_registration_state[device_id] = is_registered
    return is_registered


def register_device(device_id, device_name=None, location=None):
    """Register a new device and keep it available for multi-pump monitoring."""
    clean_device_id = str(device_id or '').strip()
    if not clean_device_id:
        return {
            "status": "error",
            "message": "device_id is required",
        }

    final_name = (device_name or f'Pump-{clean_device_id}').strip() or f'Pump-{clean_device_id}'
    final_location = (location or 'Unknown').strip() or 'Unknown'

    print(f"[DEBUG] Registering device: device_id={clean_device_id}, device_name={final_name}, location={final_location}")

    with buffer_lock:
        esp32_data_buffer[clean_device_id]['device_name'] = final_name
        esp32_data_buffer[clean_device_id]['location'] = final_location
        esp32_data_buffer[clean_device_id]['registered'] = True
        device_registration_state[clean_device_id] = True

    firebase_saved = firebase_store.store_device_in_firebase(clean_device_id, final_name, final_location)
    return {
        "status": "success",
        "message": f"Device {final_name} registered",
        "device_id": clean_device_id,
        "device_name": final_name,
        "location": final_location,
        "firebase_saved": firebase_saved,
    }


def seconds_since_update(last_update):
    """Seconds since the device's last data (ISO time), or None if it never sent any."""
    if not last_update:
        return None
    try:
        updated = datetime.fromisoformat(last_update)
    except ValueError:
        return None
    now = datetime.now(updated.tzinfo) if updated.tzinfo else datetime.now()
    return max(0.0, (now - updated).total_seconds())


def is_online(age_seconds):
    """A device is online while its ESP32 keeps sending data."""
    return age_seconds is not None and age_seconds <= config.LIVE_ONLINE_SECONDS


def device_is_online(device_id):
    """Whether the device's ESP32 is sending data right now."""
    with buffer_lock:
        info = esp32_data_buffer.get(device_id)
        last_update = info.get('last_update') if info else None
    return is_online(seconds_since_update(last_update))


def list_registered_devices():
    """Return all registered pump devices for the dashboard, each with whether
    its ESP32 is online (sending data) right now."""
    devices = []
    with buffer_lock:
        for device_id, info in sorted(esp32_data_buffer.items()):
            if info.get('registered') is False:
                continue
            age = seconds_since_update(info.get('last_update'))
            devices.append({
                "device_id": str(device_id),
                "device_name": info.get('device_name') or str(device_id),
                "location": info.get('location') or 'Unknown',
                "last_update": info.get('last_update'),
                "seconds_since_update": round(age, 1) if age is not None else None,
                "online": is_online(age),
                "sample_count": len(info.get('data', [])),
            })
    return devices
