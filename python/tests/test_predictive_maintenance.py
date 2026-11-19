import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from pump_monitor import (
    config,
    device_registry,
    firebase_store,
    predictive_maintenance,
    regression_baseline,
    signal_processing,
)
from tests.helpers import BackendTestCase


class PredictiveMaintenanceTests(BackendTestCase):
    def test_svd_denoiser_reduces_random_noise_from_periodic_signal(self):
        sample_count = 128
        phase = np.linspace(0, 8 * np.pi, sample_count, endpoint=False)
        clean_signal = np.sin(phase)
        noise = np.random.default_rng(42).normal(0, 0.35, sample_count)
        noisy_window = np.column_stack([
            clean_signal + noise,
            0.5 * clean_signal + noise,
            0.25 * clean_signal + noise,
        ])

        denoised_window = signal_processing.svd_denoise_window(noisy_window)

        self.assertEqual(denoised_window.shape, noisy_window.shape)
        self.assertLess(
            np.mean((denoised_window[:, 0] - clean_signal) ** 2),
            np.mean((noisy_window[:, 0] - clean_signal) ** 2),
        )

    def test_sensor_data_zeroes_axes_without_running_pressure(self):
        device_id = "ESP32-PRESSURE-GATE"
        samples = [
            {"x": 0.4, "y": -0.2, "z": 0.8, "pressure_bar": 0.0},
            {"x": 0.4, "y": -0.2, "z": 0.8, "pressure_bar": 1.0},
        ]
        try:
            response = self.client.post(
                "/api/esp32/data",
                json={"device_id": device_id, "samples": samples},
            )

            self.assertEqual(response.status_code, 200)
            received_samples = device_registry.esp32_data_buffer[device_id]["data"]
            self.assertEqual(
                [received_samples[0][axis] for axis in ("x", "y", "z")],
                [0.0, 0.0, 0.0],
            )
            self.assertEqual(
                [received_samples[1][axis] for axis in ("x", "y", "z")],
                [0.4, -0.2, 0.8],
            )
            self.assertEqual(samples[0]["x"], 0.4)
        finally:
            with device_registry.buffer_lock:
                device_registry.esp32_data_buffer.pop(device_id, None)
                device_registry.device_registration_state.pop(device_id, None)

    def test_sensor_data_zeroes_axes_when_device_reports_pump_off(self):
        device_id = "ESP32-PUMP-FLAG"
        sample_count = config.PUMP_SVD_MIN_SAMPLES + 4
        rng = np.random.default_rng(7)
        samples = [
            {
                "x": float(0.01 + rng.normal(0, 0.004)),
                "y": float(-0.02 + rng.normal(0, 0.004)),
                "z": float(1.0 + rng.normal(0, 0.006)),
                "pump_running": False,
            }
            for _ in range(sample_count)
        ]
        try:
            response = self.client.post(
                "/api/esp32/data",
                json={"device_id": device_id, "samples": samples},
            )

            self.assertEqual(response.status_code, 200)
            received_samples = device_registry.esp32_data_buffer[device_id]["data"]
            self.assertTrue(all(
                received_sample[axis] == 0.0
                for received_sample in received_samples
                for axis in ("x", "y", "z")
            ))
            self.assertEqual(
                response.get_json()["latest_sample"]["x"], 0.0
            )
        finally:
            with device_registry.buffer_lock:
                device_registry.esp32_data_buffer.pop(device_id, None)
                device_registry.device_registration_state.pop(device_id, None)

    def test_sensor_data_replaces_clipped_readings_before_analysis(self):
        device_id = "ESP32-CLIPPED"
        samples = [
            {"x": 0.01, "y": -0.02, "z": 1.0, "pump_running": True},
            {"x": 15.996, "y": -15.996, "z": 15.996, "pump_running": True},
            {"x": 0.02, "y": -0.01, "z": 0.99, "pump_running": True},
        ]
        try:
            response = self.client.post(
                "/api/esp32/data", json={"device_id": device_id, "samples": samples}
            )
            self.assertEqual(response.status_code, 200)
            buffered = device_registry.esp32_data_buffer[device_id]["data"]
            self.assertTrue(all(
                abs(sample[axis]) < config.ACCEL_CLIP_LIMIT_G
                for sample in buffered for axis in ("x", "y", "z")
            ))
            self.assertEqual(samples[1]["x"], 15.996)  # caller's data unchanged
        finally:
            with device_registry.buffer_lock:
                device_registry.esp32_data_buffer.pop(device_id, None)
                device_registry.device_registration_state.pop(device_id, None)

    def test_sensor_data_without_pressure_preserves_vibration_axes(self):
        device_id = "ESP32-NO-PRESSURE"
        sample_count = config.PUMP_SVD_MIN_SAMPLES + 4
        samples = [
            {
                "x": np.sin(index * 0.5),
                "y": np.sin(index * 0.5 + 0.2),
                "z": np.sin(index * 0.5 + 0.4),
            }
            for index in range(sample_count)
        ]
        try:
            response = self.client.post(
                "/api/esp32/data",
                json={"device_id": device_id, "samples": samples},
            )

            self.assertEqual(response.status_code, 200)
            received_samples = device_registry.esp32_data_buffer[device_id]["data"]
            self.assertEqual(len(received_samples), sample_count)
            self.assertTrue(
                any(
                    abs(received_sample[axis]) > 1e-6
                    for received_sample in received_samples
                    for axis in ("x", "y", "z")
                )
            )
        finally:
            with device_registry.buffer_lock:
                device_registry.esp32_data_buffer.pop(device_id, None)
                device_registry.device_registration_state.pop(device_id, None)

    def _vibration_window(self, amplitude_g):
        t = np.arange(128) / config.SENSOR_SAMPLE_RATE_HZ
        y = amplitude_g * np.sin(2 * np.pi * 24.6 * t)
        return pd.DataFrame({"x": 0.0 * y, "y": y, "z": 0.3 * y})

    def test_risk_rises_continuously_with_vibration(self):
        risks = [
            predictive_maintenance.compute_predictive_risk(self._vibration_window(amp))[0]
            for amp in (0.02, 0.04, 0.08, 0.16, 0.32)
        ]
        self.assertEqual(risks, sorted(risks))
        self.assertLess(risks[0], 30)
        self.assertGreaterEqual(risks[-1], config.PREDICTIVE_FAILURE_RISK_THRESHOLD)

    def test_forecast_predicts_breakdown_for_worsening_trend(self):
        device_id = "ESP32-TEST-TREND"
        start = datetime(2026, 11, 1, 8, 0)
        for day, risk in enumerate([40, 50, 60, 70]):
            prediction = predictive_maintenance.predict_failure_for_device(
                device_id, risk, start + timedelta(days=day)
            )
        self.assertEqual(prediction["status"], "forecast_available")
        # 10 risk points/day from 70 -> 90 => ~2 days
        self.assertAlmostEqual(prediction["remaining_life_days"], 2.0, delta=0.1)
        shutdown = datetime.fromisoformat(prediction["suggested_shutdown_date"])
        self.assertGreaterEqual(shutdown, start + timedelta(days=3))

    def test_no_forecast_or_alert_while_the_pump_is_healthy(self):
        device_id = "ESP32-TEST-HEALTHY-TREND"
        start = datetime(2026, 11, 1, 8, 0)
        for day, risk in enumerate([2, 8, 14, 20]):  # rising, but healthy
            prediction = predictive_maintenance.predict_failure_for_device(
                device_id, risk, start + timedelta(days=day)
            )
        self.assertEqual(prediction["status"], "stable_or_improving")
        self.assertIsNone(prediction["suggested_shutdown_date"])
        self.assertFalse(prediction["alert_active"])

    def test_forecast_history_is_throttled_and_skips_idle(self):
        device_id = "ESP32-TEST-THROTTLE"
        start = datetime(2026, 11, 1, 8, 0)
        for second in range(50):  # 50 posts within one minute
            predictive_maintenance.predict_failure_for_device(
                device_id, 40, start + timedelta(seconds=second)
            )
        predictive_maintenance.predict_failure_for_device(
            device_id, 0, start + timedelta(hours=1), record=False
        )
        rows = pd.read_csv(config.PREDICTIVE_HISTORY_FILE)
        self.assertEqual(len(rows), 1)

    def test_runtime_hours_are_tracked(self):
        device_id = "ESP32-TEST-RUNTIME"
        now = datetime.now()
        state = predictive_maintenance.update_device_runtime_hours(device_id, now, 0.0)
        self.assertGreaterEqual(state["runtime_hours"], 0.0)
        self.assertEqual(state["device_id"], device_id)

    def test_maintenance_summary_includes_shutdown_recommendation(self):
        summary = predictive_maintenance.build_predictive_maintenance_summary(
            "ESP32-TEST-SUMMARY",
            risk_score=90.0,
            zone="D",
            observed_at=datetime.now(),
            runtime_hours=120.0,
        )
        self.assertIn("predicted_failure_date", summary)
        self.assertIn("suggested_shutdown_date", summary)
        self.assertIn("maintenance_action", summary)
        self.assertIn("ISO 20816", summary["failure_basis"])
        self.assertIn("remaining_life_days", summary)
        self.assertIn("alert_active", summary)

    def test_maintenance_history_persists_predictive_snapshot(self):
        observed_at = datetime(2026, 11, 4, 12, 30)
        summary = {
            "failure_basis": "ISO 20816 vibration standard",
            "remaining_life_days": 12.5,
            "alert_active": True,
            "runtime_hours": 240.0,
        }

        predictive_maintenance.log_maintenance_event(
            device_id="ESP32-HISTORY-SNAPSHOT",
            risk_score=87.5,
            zone="D",
            predicted_failure_date="2026-11-20",
            suggested_shutdown_date="2026-11-13",
            maintenance_action="Recommended shutdown",
            observed_at=observed_at,
            summary=summary,
        )
        history = predictive_maintenance.get_maintenance_history("ESP32-HISTORY-SNAPSHOT")

        self.assertEqual(len(history), 1)
        snapshot = history[0]
        self.assertEqual(snapshot["timestamp"], observed_at.isoformat())
        self.assertEqual(snapshot["risk_score"], 87.5)
        self.assertEqual(snapshot["failure_basis"], summary["failure_basis"])
        self.assertEqual(snapshot["remaining_life_days"], 12.5)
        self.assertTrue(snapshot["alert_active"])
        self.assertEqual(snapshot["predicted_failure_date"], "2026-11-20")
        self.assertEqual(snapshot["suggested_shutdown_date"], "2026-11-13")
        self.assertEqual(snapshot["runtime_hours"], 240.0)

    def test_prediction_exposes_alert_and_remaining_life_metrics(self):
        prediction = predictive_maintenance.predict_failure_for_device(
            "ESP32-TEST-ALERT",
            82.0,
            observed_at=datetime.now(),
        )
        self.assertTrue("remaining_life_days" in prediction)
        self.assertTrue("alert_active" in prediction)
        self.assertTrue("alert_threshold" in prediction)

    def test_multiple_devices_can_be_registered_and_listed(self):
        device_registry.register_device("ESP32-DEVICE-A", "Pump A", "Plant 1")
        device_registry.register_device("ESP32-DEVICE-B", "Pump B", "Plant 2")
        devices = device_registry.list_registered_devices()
        self.assertTrue(any(item.get("device_id") == "ESP32-DEVICE-A" for item in devices))
        self.assertTrue(any(item.get("device_id") == "ESP32-DEVICE-B" for item in devices))

    def test_removing_device_keeps_history_and_hides_future_sensor_data(self):
        device_id = "ESP32-DEVICE-REMOVE"
        sample = {"x": 0.1, "y": 0.2, "z": 0.3}
        try:
            device_registry.register_device(device_id, "Pump to remove")
            device_registry.esp32_data_buffer[device_id]["data"].append(sample)

            response = self.client.delete(f"/api/esp32/devices/{device_id}")

            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.get_json()["sensor_history_retained"])
            self.assertEqual(
                device_registry.esp32_data_buffer[device_id]["data"],
                [sample],
            )
            self.assertFalse(
                any(
                    item["device_id"] == device_id
                    for item in device_registry.list_registered_devices()
                )
            )

            data_response = self.client.post(
                "/api/esp32/data",
                json={"device_id": device_id, "samples": [sample]},
            )
            self.assertEqual(data_response.status_code, 200)
            self.assertFalse(
                any(
                    item["device_id"] == device_id
                    for item in device_registry.list_registered_devices()
                )
            )
        finally:
            with device_registry.buffer_lock:
                device_registry.esp32_data_buffer.pop(device_id, None)
                device_registry.device_registration_state.pop(device_id, None)

    def test_regression_baseline_retrains_when_pickle_cannot_load(self):
        # Simulates a pickle saved by a newer numpy/scikit-learn than the server has.
        saved = (regression_baseline.regression_model, regression_baseline.regression_metadata)
        with TemporaryDirectory() as directory:
            model_file = Path(directory) / "model.pkl"
            metadata_file = Path(directory) / "metadata.json"
            model_file.write_bytes(b"not a loadable pickle")
            metadata_file.write_text("{}", encoding="utf-8")
            try:
                regression_baseline.regression_model = None
                regression_baseline.regression_metadata = None
                with patch.object(config, "REGRESSION_MODEL_FILE", model_file), \
                     patch.object(config, "REGRESSION_METADATA_FILE", metadata_file):
                    trained_model, trained_metadata = (
                        regression_baseline.load_or_train_regression_baseline()
                    )

                self.assertIsNotNone(trained_model)
                self.assertGreater(trained_metadata["training_rows"], 0)
                prediction = trained_model.predict(pd.DataFrame(
                    [[0.1, 0.2, 0.3]], columns=config.REGRESSION_FEATURE_COLUMNS,
                ))
                self.assertEqual(len(prediction), 1)
            finally:
                regression_baseline.regression_model, regression_baseline.regression_metadata = saved

    def test_firebase_history_orders_by_key_without_index(self):
        fake_db = MagicMock()
        batches_ref = fake_db.reference.return_value
        batches_ref.order_by_child.side_effect = AssertionError(
            "order_by_child needs an .indexOn rule in Firebase"
        )
        batches_ref.order_by_key.return_value.limit_to_last.return_value.get.return_value = {
            "-key1": {"device_id": "ESP32-HIST", "sample_count": 10,
                      "checked_at_local": "2026-11-13T06:45:00", "temperature_c": 40.0},
        }
        with patch.object(firebase_store, "init_firebase", return_value=fake_db):
            history = firebase_store.get_history_from_firebase("ESP32-HIST", limit=5)

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["batch_id"], "-key1")
        batches_ref.order_by_key.return_value.limit_to_last.assert_called_once_with(5)


if __name__ == "__main__":
    unittest.main()
