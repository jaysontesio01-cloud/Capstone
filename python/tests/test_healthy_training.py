"""The anomaly model and pump part limits learn "normal" from the healthy pump's data."""

import json
from datetime import datetime
from unittest.mock import patch

import numpy as np
import pandas as pd

from pump_monitor import (
    anomaly_model,
    config,
    device_registry,
    predictive_maintenance,
    vibration_baseline,
    window_analysis,
)
from pump_monitor.signal_processing import extract_features, window_dynamic_rms
from tests.helpers import BackendTestCase

HIGH_LIMITS = {axis: {"rms": 99.0, "peak": 99.0} for axis in ("X-axis", "Y-axis", "Z-axis")}
MODEL_SAYS_NORMAL = (np.array([1]), np.array([0.2]))


def running_window(amplitude=0.5):
    wave = amplitude * np.sin(2 * np.pi * 24.6 * np.arange(config.WINDOW_SIZE) / 100.0)
    return np.column_stack([0.3 * wave, 0.1 * wave, 1.0 + wave])


def write_healthy_recording(path, seconds=40):
    """A recording like the healthy pump's: mostly z vibration, plus junk to ignore."""
    rng = np.random.default_rng(0)
    n = seconds * 100
    t = np.arange(n) / 100.0
    wave = 0.3 * np.sin(2 * np.pi * 24.6 * t)
    running = pd.DataFrame({
        "x": 0.4 * wave + rng.normal(0, 0.01, n),
        "y": 0.1 * wave + rng.normal(0, 0.01, n),
        "z": 1.0 + wave + rng.normal(0, 0.01, n),
        "temperature_c": 40.0,
        "pump_running": True,
    })
    running.loc[5, ["x", "y", "z"]] = [15.996, -15.996, 15.996]  # garbled reading
    # Pump-off rows with large swings: must not count as healthy running data.
    off = pd.DataFrame({"x": np.tile([3.0, -3.0], 150), "y": 0.0, "z": 1.0,
                        "temperature_c": 40.0, "pump_running": False})
    pd.concat([off, running]).to_csv(path, index=False)


class HealthyTrainingTests(BackendTestCase):
    def setUp(self):
        super().setUp()
        write_healthy_recording(self.data_dir / "healthy.csv")
        for name, filename in (
            ("TRAINING_DATA_FILE", "healthy.csv"),
            ("BEARING_MODEL_FILE", "model.pkl"),
            ("BEARING_SCALER_FILE", "scaler.pkl"),
            ("PUMP_PART_THRESHOLDS_FILE", "limits.json"),
        ):
            self.stack.enter_context(patch.object(config, name, self.data_dir / filename))

    def test_training_learns_model_and_part_limits_from_healthy_data(self):
        trained_model, trained_scaler = anomaly_model.train_bearing_anomaly_model()

        saved = json.loads(config.PUMP_PART_THRESHOLDS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(saved["source"], "healthy.csv")
        limits = saved["thresholds_g"]
        # z vibrates most (0.21 g RMS), x less (0.085 g), y least.
        self.assertTrue(0.2 < limits["Z-axis"]["rms"] < 0.3)
        self.assertTrue(limits["Z-axis"]["rms"] > limits["X-axis"]["rms"] > limits["Y-axis"]["rms"])
        # Neither the garbled reading nor the pump-off swings raised the limits.
        self.assertLess(limits["X-axis"]["peak"], 0.5)

        windows = anomaly_model.healthy_training_windows()
        healthy = trained_model.predict(trained_scaler.transform(extract_features(windows)))
        stronger = trained_model.predict(trained_scaler.transform(extract_features(windows * 3)))
        self.assertGreaterEqual(np.mean(healthy == 1), 0.9)
        self.assertGreaterEqual(np.mean(stronger == -1), 0.9)

    def test_training_uses_the_healthy_axes_set_by_hand(self):
        recorded = anomaly_model.healthy_training_windows()
        with patch.object(config, "HEALTHY_AXIS_RMS_G", {"x": 0.2, "y": 0.15}):
            trained_model, trained_scaler = anomaly_model.train_bearing_anomaly_model()
            windows = anomaly_model.healthy_training_windows()

        def median_rms(data, axis_idx):
            signal = data[:, :, axis_idx]
            centered = signal - signal.mean(axis=1, keepdims=True)
            return float(np.median(np.sqrt(np.mean(centered ** 2, axis=1))))

        # X and Y were scaled to the set values; Z was left as recorded.
        self.assertAlmostEqual(median_rms(windows, 0), 0.2, places=6)
        self.assertAlmostEqual(median_rms(windows, 1), 0.15, places=6)
        self.assertLess(median_rms(recorded, 0), 0.1)  # was ~0.085 g
        np.testing.assert_array_equal(windows[:, :, 2], recorded[:, :, 2])
        # The limits sit above the set values, and the model calls them normal.
        limits = json.loads(config.PUMP_PART_THRESHOLDS_FILE.read_text(encoding="utf-8"))["thresholds_g"]
        self.assertTrue(0.2 < limits["X-axis"]["rms"] < 0.3)
        self.assertTrue(0.15 < limits["Y-axis"]["rms"] < 0.25)
        healthy = trained_model.predict(trained_scaler.transform(extract_features(windows)))
        self.assertGreaterEqual(np.mean(healthy == 1), 0.9)

    def test_pump_parts_are_judged_against_the_healthy_limits(self):
        limits = {
            "X-axis": {"rms": 0.2, "peak": 1.0},
            "Y-axis": {"rms": 0.2, "peak": 1.0},
            "Z-axis": {"rms": 0.5, "peak": 2.0},
        }
        wave = np.sin(2 * np.pi * 24.6 * np.arange(128) / 100.0)
        window = np.column_stack([0.4 * wave, 0.05 * wave, 0.3 * wave])  # x RMS 0.28 > 0.2
        with patch.object(anomaly_model, "part_thresholds", limits):
            parts, metrics = anomaly_model.analyze_pump_parts(window)
        self.assertEqual(parts, ["Lateral Bearing/Shaft Alignment"])
        self.assertEqual([m["is_anomaly"] for m in metrics], [True, False, False])

    def test_missing_part_limits_are_learned_from_the_healthy_data(self):
        self.assertFalse(config.PUMP_PART_THRESHOLDS_FILE.exists())
        limits = anomaly_model.load_part_thresholds()
        self.assertTrue(config.PUMP_PART_THRESHOLDS_FILE.exists())
        self.assertNotEqual(limits, anomaly_model.DEFAULT_PART_THRESHOLDS)


class IsoInformationOnlyTests(BackendTestCase):
    """ISO 20816 is reported but does not decide; the healthy baseline does."""

    DEVICE_ID = "ESP32-DEVIATION"

    def setUp(self):
        super().setUp()
        self.stack.enter_context(patch.object(anomaly_model, "part_thresholds", HIGH_LIMITS))
        self.stack.enter_context(
            patch.object(window_analysis, "score_windows", return_value=MODEL_SAYS_NORMAL)
        )

    def test_iso_zone_d_alone_is_not_an_anomaly(self):
        results, anomaly_count, _ = window_analysis.analyze_windows(np.array([running_window()]))
        self.assertEqual(results[0]["standard_vibration_comparison"]["zone"], "D")
        self.assertFalse(results[0]["is_anomaly"])
        self.assertEqual(anomaly_count, 0)

    def analyze_against_baseline(self, baseline_rms_g):
        window = running_window(0.3)
        state = vibration_baseline.new_vibration_baseline_state(
            datetime.now(), 0, "healthy_reference_recording", known_healthy=True
        )
        state.update(status="established", baseline_rms_g=baseline_rms_g, baseline_std_g=0.01,
                     established_at=datetime.now().isoformat())
        vibration_baseline.save_vibration_baselines({self.DEVICE_ID: state})
        with device_registry.buffer_lock:
            device_registry.esp32_data_buffer[self.DEVICE_ID]["data"] = [
                {"x": x, "y": y, "z": z} for x, y, z in window
            ]
        try:
            return window, self.client.get(f"/api/esp32/analyze/{self.DEVICE_ID}").get_json()["data"]
        finally:
            with device_registry.buffer_lock:
                device_registry.esp32_data_buffer.pop(self.DEVICE_ID, None)

    def test_vibration_far_above_the_healthy_baseline_is_an_anomaly(self):
        window, data = self.analyze_against_baseline(baseline_rms_g=0.05)
        self.assertTrue(data["windows"][0]["vibration_deviation"]["warning"])
        self.assertTrue(data["windows"][0]["is_anomaly"])
        self.assertNotEqual(data["health_status"], "HEALTHY")

    def test_risk_follows_the_increase_over_the_healthy_baseline(self):
        window = running_window(0.3)
        baseline = window_dynamic_rms(window)
        state = vibration_baseline.new_vibration_baseline_state(
            datetime.now(), 0, "healthy_reference_recording", known_healthy=True
        )
        state.update(status="established", baseline_rms_g=baseline, baseline_std_g=0.01)
        vibration_baseline.save_vibration_baselines({self.DEVICE_ID: state})

        def risk(scale, device_id=self.DEVICE_ID):
            frame = pd.DataFrame(window * scale, columns=["x", "y", "z"])
            return predictive_maintenance.compute_predictive_risk(frame, device_id=device_id)

        self.assertAlmostEqual(risk(1.0)[0], 0.0, delta=1)
        self.assertAlmostEqual(risk(1.5)[0], 60.0, delta=1)   # 50% above = warning level
        self.assertAlmostEqual(risk(2.0)[0], 90.0, delta=1)   # 100% above = shut down
        self.assertEqual(risk(0.5)[0], 0.0)                    # below baseline (stopping)
        self.assertEqual(risk(2.0)[1]["failure_basis"], predictive_maintenance.FAILURE_BASIS_HEALTHY)
        # A pump without a healthy baseline keeps the ISO 20816 fallback.
        self.assertEqual(risk(2.0, device_id="ESP32-NO-BASELINE")[1]["failure_basis"],
                         predictive_maintenance.FAILURE_BASIS_ISO)

        summary = predictive_maintenance.build_predictive_maintenance_summary(
            self.DEVICE_ID, risk(2.0)[0], "D", record=False
        )
        self.assertEqual(summary["failure_basis"], predictive_maintenance.FAILURE_BASIS_HEALTHY)
        self.assertIn("above the healthy baseline", summary["maintenance_action"])

    def test_vibration_like_the_healthy_baseline_is_normal(self):
        window = running_window(0.3)
        _, data = self.analyze_against_baseline(baseline_rms_g=window_dynamic_rms(window))
        self.assertFalse(data["windows"][0]["is_anomaly"])
        self.assertEqual(data["health_status"], "HEALTHY")
