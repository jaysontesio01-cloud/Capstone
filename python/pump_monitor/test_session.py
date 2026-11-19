"""Timed pump tests started from the app (for example 20 minutes).

While a test runs, every window of running data the device sends is checked
against the healthy pump (health_assessment) and kept, and the raw samples are
recorded to data/recordings/. The result is judged over the whole test. When
the time is up (or the test is stopped) the recording stops and the summary is
saved to data/app_tests/.
"""

import json
from datetime import datetime, timedelta
from threading import Lock

from . import config
from .health_assessment import assess, running_windows, summarize_windows
from .recorder import (
    active_recordings,
    display_path,
    recording_lock,
    start_recording,
    stop_recording,
)

# device_id -> test session
sessions = {}
session_lock = Lock()


def start_test(device_id, minutes, now=None):
    now = now or datetime.now()
    minutes = min(max(float(minutes), 0.5), config.APP_TEST_MAX_MINUTES)
    with session_lock:
        previous = sessions.get(device_id)
        if previous and previous["state"] == "running":
            _finish(previous, "stopped", now)
        recording_path, _ = start_recording(device_id, f"app_test_{minutes:g}min")
        sessions[device_id] = {
            "device_id": device_id,
            "minutes": minutes,
            "started_at": now,
            "ends_at": now + timedelta(minutes=minutes),
            "finished_at": None,
            "state": "running",
            "pending": [],
            "windows": [],
            "recording_path": recording_path,
            "summary_file": None,
        }
    return test_summary(device_id, now)


def stop_test(device_id, now=None):
    now = now or datetime.now()
    with session_lock:
        session = sessions.get(device_id)
        if session and session["state"] == "running":
            _finish(session, "stopped", now)
    return test_summary(device_id, now)


def add_samples(device_id, samples, now=None):
    """Feed a device's new samples (clipped readings already replaced) to its running test."""
    now = now or datetime.now()
    with session_lock:
        session = sessions.get(device_id)
        if session is None or session["state"] != "running":
            return
        if now >= session["ends_at"]:
            _finish(session, "finished", now)
            return
        windows, session["pending"] = running_windows(samples, session["pending"])
    if not windows:
        return
    results = summarize_windows(windows)  # outside the lock: the model takes a few ms
    with session_lock:
        if sessions.get(device_id) is session and session["state"] == "running":
            session["windows"].extend(results)


def test_summary(device_id, now=None):
    """The device's current or last test, judged over all its running data."""
    now = now or datetime.now()
    with session_lock:
        session = sessions.get(device_id)
        if session is None:
            return {"state": "none"}
        if session["state"] == "running" and now >= session["ends_at"]:
            _finish(session, "finished", now)
        return _summary(session, now)


def _summary(session, now):
    end = session["finished_at"] or now
    elapsed = max(0.0, (min(end, session["ends_at"]) - session["started_at"]).total_seconds())
    return {
        "state": session["state"],
        "minutes": session["minutes"],
        "started_at": session["started_at"].isoformat(),
        "ends_at": session["ends_at"].isoformat(),
        "finished_at": session["finished_at"].isoformat() if session["finished_at"] else None,
        "elapsed_seconds": round(elapsed, 1),
        "remaining_seconds": round(max(0.0, (session["ends_at"] - now).total_seconds()), 1)
        if session["state"] == "running" else 0.0,
        "recording_file": display_path(session["recording_path"]),
        "summary_file": session["summary_file"],
        "assessment": assess(session["windows"]),
    }


def _finish(session, state, now):
    """End a test (session_lock held): stop its recording and save the summary."""
    session["state"] = state
    session["finished_at"] = min(now, session["ends_at"])
    session["pending"] = []
    device_id = session["device_id"]
    with recording_lock:
        ours = (active_recordings.get(device_id) or {}).get("file") == session["recording_path"]
    if ours:
        stop_recording(device_id)
    try:
        config.APP_TESTS_DIR.mkdir(parents=True, exist_ok=True)
        path = config.APP_TESTS_DIR / (
            f"{device_id}_test_{session['started_at']:%Y%m%d_%H%M%S}.json"
        )
        session["summary_file"] = display_path(path)
        path.write_text(json.dumps(
            {"device_id": device_id, **_summary(session, now)}, indent=2
        ), encoding="utf-8")
    except Exception as exc:
        session["summary_file"] = None
        print(f"Saving the test summary failed: {exc}")
