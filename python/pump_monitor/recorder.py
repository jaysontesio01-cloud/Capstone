"""Raw sensor recordings: save every sample a device sends to a CSV file.

Used to capture test sessions, e.g. a healthy and a defective pump. The files
use the same x/y/z columns as accelerometer.csv, so a recording can be
re-analyzed with /api/analyze/csv.
"""

import csv
import re
from datetime import datetime
from threading import Lock

from . import config

# device_id -> {"label", "file", "started_at", "samples"}
active_recordings = {}
recording_lock = Lock()


def display_path(path):
    """Path relative to the python folder when inside it (as the API reports files)."""
    try:
        return str(path.relative_to(config.BASE_DIR))
    except ValueError:
        return str(path)


def recording_summary(device_id, recording):
    if recording is None:
        return {"device_id": device_id, "recording": False}
    return {
        "device_id": device_id,
        "recording": device_id in active_recordings,
        "label": recording["label"],
        "file": display_path(recording["file"]),
        "started_at": recording["started_at"],
        "samples": recording["samples"],
    }


def append_recording_samples(device_id, samples, received_at):
    """Append a device's raw samples to its active recording, if any."""
    with recording_lock:
        recording = active_recordings.get(device_id)
        if recording is None:
            return
        with open(recording["file"], "a", newline="", encoding="utf-8") as f_recording:
            writer = csv.writer(f_recording)
            for batch_index, sample in enumerate(samples):
                if isinstance(sample, dict):
                    writer.writerow(
                        [received_at.isoformat(), batch_index]
                        + [sample.get(column) for column in config.RECORDING_COLUMNS[2:]]
                    )
                    recording["samples"] += 1


def safe_name(text, default="session"):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(text or "")).strip("_") or default


def start_recording(device_id, label):
    """Start (or restart) saving every raw sample of the device to
    recordings/<device>_<label>_<time>.csv. Returns (file path, summary)."""
    started_at = datetime.now()
    label = safe_name(label)
    config.RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    recording_file = config.RECORDINGS_DIR / (
        f"{safe_name(device_id, 'device')}_{label}_{started_at:%Y%m%d_%H%M%S}.csv"
    )
    with recording_lock:
        with open(recording_file, "w", newline="", encoding="utf-8") as f_recording:
            csv.writer(f_recording).writerow(config.RECORDING_COLUMNS)
        active_recordings[device_id] = {
            "label": label,
            "file": recording_file,
            "started_at": started_at.isoformat(),
            "samples": 0,
        }
        return recording_file, recording_summary(device_id, active_recordings[device_id])


def stop_recording(device_id):
    """Stop the device's recording and return its summary."""
    with recording_lock:
        return recording_summary(device_id, active_recordings.pop(device_id, None))
