"""The app dashboard: live per-axis levels, the overall verdict against the
healthy pump, possible problems, and timed tests."""

import json
from datetime import datetime, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd

from pump_monitor import (
    anomaly_model,
    config,
    device_registry,
    health_assessment,
    test_session,
    window_analysis,
)
from tests.helpers import BackendTestCase

LIMITS = {
    "X-axis": {"rms": 0.5, "peak": 99.0},
    "Y-axis": {"rms": 0.16, "peak": 99.0},
    "Z-axis": {"rms": 1.0, "peak": 99.0},
}
REFERENCE = {
    "vibration_rms_g": {"median": 0.75, "p10": 0.62, "p90": 0.94},
    "axis_rms_g": {"x": 0.33, "y": 0.08, "z": 0.66},
    "temperature_c": {"mean": 55.0, "max": 65.0},
}


def pump_samples(seconds, x=0.33, y=0.08, z=0.66, temperature=50.0, running=True):
    """Samples of a pump vibrating at 34 Hz with the given RMS per axis (g)."""
    t = np.arange(int(seconds * 100)) / 100.0
    wave = np.sqrt(2) * np.sin(2 * np.pi * 34.0 * t)
    return [
        {"x": x * w, "y": y * w, "z": 1.0 + z * w, "temperature_c": temperature,
         "pump_running": running}
        for w in wave
    ]


class DashboardTestCase(BackendTestCase):
    def setUp(self):
        super().setUp()
        reference_file = self.data_dir / "healthy_reference.json"
        reference_file.write_text(json.dumps(REFERENCE), encoding="utf-8")
        self.stack.enter_context(patch.object(config, "HEALTHY_REFERENCE_FILE", reference_file))
        self.stack.enter_context(
            patch.dict(health_assessment._reference_cache, {"mtime": None, "data": None})
        )
        self.stack.enter_context(patch.object(anomaly_model, "part_thresholds", LIMITS))
        # The anomaly model says "normal" unless a test says otherwise.
        self.model_unusual = False
        self.stack.enter_context(patch.object(
            window_analysis, "score_windows",
            side_effect=lambda windows: (
                np.full(len(windows), -1 if self.model_unusual else 1),
                np.full(len(windows), -0.1 if self.model_unusual else 0.2),
            ),
        ))

    def assess_samples(self, samples):
        windows, _ = health_assessment.running_windows(samples)
        return health_assessment.assess(health_assessment.summarize_windows(windows))


class AssessmentTests(DashboardTestCase):
    def test_healthy_axes_set_by_hand_replace_the_recorded_ones(self):
        with patch.object(config, "HEALTHY_AXIS_RMS_G", {"x": 0.50, "y": 0.15}):
            reference = health_assessment.healthy_reference_summary()
            # A pump near the set values (z as recorded) is judged against them.
            result = self.assess_samples(pump_samples(10, x=0.45, y=0.135, z=0.66))
        self.assertEqual(reference["axis_rms_g"], {"x": 0.50, "y": 0.15, "z": 0.66})
        # The three-axis total moved with the axes: 0.75 x |(.5,.15,.66)| / |(.33,.08,.66)|.
        self.assertAlmostEqual(reference["total_rms_g"], 0.75 * 0.8415 / 0.7422, delta=0.005)
        self.assertEqual([a["healthy_rms_g"] for a in result["axes"]], [0.50, 0.15, 0.66])
        self.assertAlmostEqual(result["axes"][0]["times_healthy"], 0.9, delta=0.05)
        self.assertAlmostEqual(result["axes"][1]["times_healthy"], 0.9, delta=0.05)
        self.assertEqual(result["verdict"], "HEALTHY")

    def test_vibration_like_the_healthy_pump_is_healthy(self):
        result = self.assess_samples(pump_samples(10))
        self.assertEqual(result["verdict"], "HEALTHY")
        self.assertGreater(result["health_percent"], 90)
        self.assertEqual([a["status"] for a in result["axes"]], ["healthy"] * 3)
        self.assertAlmostEqual(result["axes"][1]["rms_g"], 0.08, delta=0.01)
        self.assertEqual(result["problems"], [])

    def test_strong_y_axis_is_faulty_with_a_radial_bearing_problem(self):
        self.model_unusual = True
        result = self.assess_samples(pump_samples(10, y=1.2))
        self.assertEqual(result["verdict"], "FAULTY")
        self.assertLess(result["health_percent"], 10)
        self.assertEqual([a["status"] for a in result["axes"]], ["healthy", "faulty", "healthy"])
        self.assertAlmostEqual(result["axes"][1]["times_healthy"], 15, delta=1)
        self.assertEqual(result["problems"][0]["title"], "Radial bearing wear or rotor imbalance")
        self.assertIn("Y-axis vibration 1.20 g vs 0.08 g", result["problems"][0]["evidence"])

    def test_y_axis_slightly_over_its_limit_is_a_warning_level(self):
        result = self.assess_samples(pump_samples(10, y=0.25))
        self.assertEqual(result["axes"][1]["status"], "warning")

    def test_weak_vibration_while_on_means_not_running_at_normal_speed(self):
        result = self.assess_samples(pump_samples(10, x=0.01, y=0.01, z=0.02))
        self.assertLessEqual(result["deviation_percent"], -90)
        self.assertEqual(result["verdict"], "WARNING" if result["risk_score"] < 90 else "FAULTY")
        self.assertEqual(result["problems"][0]["title"], "Pump not running at normal speed")

    def test_running_hotter_than_the_healthy_pump_is_reported(self):
        result = self.assess_samples(pump_samples(10, temperature=82.0))
        self.assertEqual(result["temperature_max_c"], 82.0)
        self.assertNotEqual(result["verdict"], "HEALTHY")
        self.assertIn("Overheating", [p["title"] for p in result["problems"]])

    def test_the_healthy_pumps_own_variation_is_healthy(self):
        # Over 60 s the healthy pump reached +27.5% vibration and 45% flagged windows.
        windows = [
            {"axis_rms": {"x": 0.41, "y": 0.1, "z": 0.83}, "rms": 0.75 * 1.27,
             "model_unusual": i < 9, "parts": [], "is_anomaly": i < 9, "temperature_c": 55.0}
            for i in range(20)
        ]
        result = health_assessment.assess(windows)
        self.assertEqual(result["anomaly_window_percent"], 45.0)
        self.assertEqual(result["risk_score"], 0.0)
        self.assertEqual(result["verdict"], "HEALTHY")

    def test_warning_starts_at_the_formula_3_and_unusual_window_levels(self):
        def windows(rms, flagged, count=20):
            return [
                {"axis_rms": {"x": 0.3, "y": 0.1, "z": 0.6}, "rms": rms,
                 "model_unusual": i < flagged, "parts": [], "is_anomaly": i < flagged,
                 "temperature_c": None}
                for i in range(count)
            ]
        self.assertEqual(health_assessment.assess(windows(0.75 * 1.5, 0))["verdict"], "WARNING")
        self.assertEqual(health_assessment.assess(windows(0.75, 15))["verdict"], "WARNING")  # 75%
        self.assertEqual(health_assessment.assess(windows(0.75, 20))["verdict"], "FAULTY")  # 100%

    def test_too_little_running_data_gives_no_verdict_yet(self):
        result = self.assess_samples(pump_samples(4, y=1.2))  # 3 windows
        self.assertEqual(result["windows"], 3)
        self.assertEqual(result["verdict"], "NO_DATA")
        self.assertEqual(result["verdict_text"], "Collecting running data...")
        self.assertIsNone(result["health_percent"])

    def test_pump_off_data_is_not_judged(self):
        result = self.assess_samples(pump_samples(10, running=False))
        self.assertEqual(result["verdict"], "NO_DATA")
        self.assertIsNone(result["health_percent"])
        self.assertEqual([a["status"] for a in result["axes"]], ["off"] * 3)

    def test_windows_do_not_join_samples_across_a_pump_stop(self):
        samples = pump_samples(1.0) + pump_samples(0.5, running=False) + pump_samples(1.0)
        windows, leftover = health_assessment.running_windows(samples)
        self.assertEqual(len(windows), 0)
        self.assertEqual(len(leftover), 100)


class LiveEndpointTests(DashboardTestCase):
    DEVICE_ID = "ESP32-LIVE-TEST"

    def setUp(self):
        super().setUp()
        self.addCleanup(self.forget_device)

    def forget_device(self):
        with device_registry.buffer_lock:
            device_registry.esp32_data_buffer.pop(self.DEVICE_ID, None)
        health_assessment._live_cache.pop(self.DEVICE_ID, None)
        test_session.sessions.pop(self.DEVICE_ID, None)

    def send(self, samples):
        for start in range(0, len(samples), 100):
            response = self.client.post("/api/esp32/data", json={
                "device_id": self.DEVICE_ID, "samples": samples[start:start + 100],
            })
            self.assertEqual(response.status_code, 200)

    def test_live_view_shows_axes_verdict_and_healthy_reference(self):
        self.send(pump_samples(8, y=1.2))
        data = self.client.get(f"/api/esp32/live/{self.DEVICE_ID}").get_json()
        self.assertTrue(data["online"])
        self.assertTrue(data["pump_running"])
        self.assertEqual(data["latest_temperature_c"], 50.0)
        self.assertEqual([a["status"] for a in data["axes"]], ["healthy", "faulty", "healthy"])
        self.assertEqual(data["live_assessment"]["verdict"], "FAULTY")
        self.assertEqual(data["healthy_reference"]["axis_rms_g"]["y"], 0.08)
        self.assertEqual(data["healthy_reference"]["axis_limit_g"]["y"], 0.16)
        self.assertEqual(data["test"], {"state": "none"})

    def device_in_list(self):
        devices = self.client.get("/api/esp32/devices").get_json()["devices"]
        return next(d for d in devices if d["device_id"] == self.DEVICE_ID)

    def test_device_list_shows_each_pump_online_while_its_esp32_sends(self):
        self.send(pump_samples(1, running=False))
        device = self.device_in_list()
        self.assertTrue(device["online"])
        self.assertLessEqual(device["seconds_since_update"], config.LIVE_ONLINE_SECONDS)

        # The ESP32 stopped sending (e.g. the phone's hotspot was turned off).
        old = datetime.now().astimezone() - timedelta(seconds=config.LIVE_ONLINE_SECONDS + 5)
        with device_registry.buffer_lock:
            device_registry.esp32_data_buffer[self.DEVICE_ID]["last_update"] = old.isoformat()
        self.assertFalse(self.device_in_list()["online"])
        self.assertFalse(self.client.get(f"/api/esp32/live/{self.DEVICE_ID}").get_json()["online"])

    def test_bars_show_the_live_adxl_data_per_axis(self):
        self.send(pump_samples(6, x=0.4, y=0.1, z=0.8))
        axes = self.client.get(f"/api/esp32/live/{self.DEVICE_ID}").get_json()["axes"]
        self.assertEqual([a["axis"] for a in axes], ["X", "Y", "Z"])
        for axis, expected in zip(axes, (0.4, 0.1, 0.8)):
            self.assertAlmostEqual(axis["rms_g"], expected, delta=expected * 0.1)
            self.assertEqual(axis["status"], "healthy")

    def test_bars_keep_showing_the_adxl_data_when_the_pump_stops(self):
        self.send(pump_samples(9) + pump_samples(6, x=0.01, y=0.01, z=0.01, running=False))
        data = self.client.get(f"/api/esp32/live/{self.DEVICE_ID}").get_json()
        self.assertFalse(data["pump_running"])
        self.assertEqual([a["status"] for a in data["axes"]], ["off"] * 3)
        # Still the sensor's live values (small, the pump is off), not empty.
        for axis in data["axes"]:
            self.assertAlmostEqual(axis["rms_g"], 0.01, delta=0.005)
            self.assertIsNone(axis["times_healthy"])
        # The running seconds before the stop still count for the live verdict.
        self.assertEqual(data["live_assessment"]["verdict"], "HEALTHY")

    def test_a_test_starts_only_while_the_esp32_is_connected(self):
        start = f"/api/esp32/test/{self.DEVICE_ID}/start"
        response = self.client.post(start, json={"minutes": 5})
        self.assertEqual(response.status_code, 409)  # never sent data
        self.assertEqual(response.get_json()["error"], "esp32_offline")

        self.send(pump_samples(1, running=False))
        response = self.client.post(start, json={"minutes": 5})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["test"]["state"], "running")

        # The ESP32 stopped sending (e.g. the hotspot was turned off).
        old = datetime.now().astimezone() - timedelta(seconds=config.LIVE_ONLINE_SECONDS + 5)
        with device_registry.buffer_lock:
            device_registry.esp32_data_buffer[self.DEVICE_ID]["last_update"] = old.isoformat()
        self.assertEqual(self.client.post(start, json={"minutes": 5}).status_code, 409)

    def test_timed_test_collects_running_data_and_saves_a_summary(self):
        self.send(pump_samples(1, running=False))  # the ESP32 is connected
        response = self.client.post(f"/api/esp32/test/{self.DEVICE_ID}/start", json={"minutes": 5})
        test = response.get_json()["test"]
        self.assertEqual(test["state"], "running")
        self.assertAlmostEqual(test["remaining_seconds"], 300, delta=2)

        self.send(pump_samples(10))
        test = self.client.get(f"/api/esp32/test/{self.DEVICE_ID}").get_json()["test"]
        self.assertEqual(test["assessment"]["windows"], 7)  # 1000 running samples
        self.assertEqual(test["assessment"]["verdict"], "HEALTHY")
        recording = config.RECORDINGS_DIR / test["recording_file"].split("\\")[-1].split("/")[-1]
        self.assertTrue(recording.exists())

        later = datetime.now() + timedelta(minutes=6)
        test = test_session.test_summary(self.DEVICE_ID, now=later)
        self.assertEqual(test["state"], "finished")
        self.assertEqual(test["remaining_seconds"], 0.0)
        self.assertAlmostEqual(test["elapsed_seconds"], 300, delta=2)
        saved = list(config.APP_TESTS_DIR.glob(f"{self.DEVICE_ID}_test_*.json"))
        self.assertEqual(len(saved), 1)
        self.assertEqual(json.loads(saved[0].read_text())["assessment"]["verdict"], "HEALTHY")
        # The test's recording stopped; data after the end is not added.
        recording_status = self.client.get(f"/api/esp32/recording/{self.DEVICE_ID}").get_json()
        self.assertFalse(recording_status["recording"])

    def test_predict_reports_the_same_risk_as_the_app(self):
        self.model_unusual = True
        self.send(pump_samples(10, y=1.2))
        live = self.client.get(f"/api/esp32/live/{self.DEVICE_ID}").get_json()
        predict = self.client.get(f"/api/esp32/predict/{self.DEVICE_ID}").get_json()
        self.assertEqual(live["live_assessment"]["verdict"], "FAULTY")
        self.assertEqual(predict["risk_score"], live["live_assessment"]["risk_score"])
        self.assertEqual(predict["risk_score"], 100.0)

    def test_risk_trend_records_the_apps_risk_once_per_interval(self):
        self.model_unusual = True
        self.send(pump_samples(12, y=1.2))
        rows = pd.read_csv(config.PREDICTIVE_HISTORY_FILE)
        rows = rows[rows["device_id"] == self.DEVICE_ID]
        self.assertEqual(len(rows), 1)  # throttled: one point per interval
        self.assertEqual(rows["risk_score"].iloc[0], 100.0)

    def test_unreadable_json_is_rejected_as_a_bad_request(self):
        response = self.client.post(
            "/api/esp32/data", data='{"device_id": "ESP32-X", "samples": [{"x": NaN}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_stopping_a_test_early(self):
        self.send(pump_samples(1, running=False))  # the ESP32 is connected
        self.client.post(f"/api/esp32/test/{self.DEVICE_ID}/start", json={"minutes": 20})
        test = self.client.post(f"/api/esp32/test/{self.DEVICE_ID}/stop").get_json()["test"]
        self.assertEqual(test["state"], "stopped")
        self.assertEqual(test["assessment"]["verdict"], "NO_DATA")
