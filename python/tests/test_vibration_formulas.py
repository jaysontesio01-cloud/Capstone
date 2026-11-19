import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import numpy as np

from pump_monitor import config, device_registry, recorder, signal_processing, vibration_baseline
from pump_monitor.formulas import (
    axis_rms,
    percentage_deviation,
    resultant_acceleration,
    three_axis_rms,
)
from tests.helpers import BackendTestCase


class VibrationFormulaTests(unittest.TestCase):
    def test_resultant_acceleration_matches_worked_example(self):
        self.assertAlmostEqual(resultant_acceleration(2, 3, 4), np.sqrt(29))
        self.assertAlmostEqual(round(float(resultant_acceleration(2, 3, 4)), 2), 5.39)

    def test_resultant_acceleration_is_per_sample_for_arrays(self):
        result = resultant_acceleration(
            np.array([2.0, 3.0]), np.array([3.0, 0.0]), np.array([4.0, 4.0])
        )
        np.testing.assert_allclose(result, [np.sqrt(29), 5.0])

    def test_three_axis_rms_averages_squared_magnitudes(self):
        # Samples (2, 3, 4) and (0, 0, 0): sqrt((29 + 0) / 2)
        self.assertAlmostEqual(three_axis_rms([2, 0], [3, 0], [4, 0]), np.sqrt(14.5))

    def test_axis_rms_of_square_wave_is_its_amplitude(self):
        self.assertAlmostEqual(axis_rms([3, -3, 3, -3]), 3.0)

    def test_rms_rejects_empty_input(self):
        with self.assertRaises(ValueError):
            three_axis_rms([], [], [])
        with self.assertRaises(ValueError):
            axis_rms([])

    def test_percentage_deviation_matches_worked_example(self):
        self.assertAlmostEqual(percentage_deviation(3.0, 2.0), 50.0)
        self.assertAlmostEqual(percentage_deviation(1.0, 2.0), 50.0)

    def test_percentage_deviation_rejects_non_positive_baseline(self):
        with self.assertRaises(ValueError):
            percentage_deviation(1.0, 0.0)

    def test_window_dynamic_rms_removes_static_offset(self):
        window = np.array([[1.0, 0.0, 2.0], [-1.0, 0.0, 0.0]]) + [0.0, 0.0, 1.0]
        # Centered samples are (1, 0, 1) and (-1, 0, -1): sqrt((2 + 2) / 2)
        self.assertAlmostEqual(signal_processing.window_dynamic_rms(window), np.sqrt(2))

    def test_clipped_readings_are_replaced_by_nearest_good_reading(self):
        samples = [
            {"x": 15.996, "y": 0.0, "z": 1.0},   # clipped, no earlier good one -> next
            {"x": 0.1, "y": -0.2, "z": 1.0},
            {"x": 0.2, "y": -16.0, "z": 1.0},    # clipped -> previous (tie)
            {"x": 0.3, "y": -0.1, "z": 1.1},
            {"x": 0.4, "y": 0.0, "z": -15.7},    # clipped, last -> previous
        ]
        cleaned = signal_processing.replace_clipped_samples(samples)
        self.assertEqual([s["x"] for s in cleaned], [0.1, 0.1, 0.1, 0.3, 0.3])
        self.assertEqual([s["z"] for s in cleaned], [1.0, 1.0, 1.0, 1.1, 1.1])
        self.assertEqual(samples[2]["y"], -16.0)  # input left unchanged


class VibrationBaselineTests(BackendTestCase):
    """Baseline acquisition per ISO 17359:2018 and the Formula 3 deviation."""

    DEVICE_ID = "ESP32-BASELINE"
    T0 = datetime(2026, 1, 1, 8, 0, 0)

    def setUp(self):
        super().setUp()  # VIBRATION_BASELINE_FILE now points into a temporary folder
        self.baseline_file = config.VIBRATION_BASELINE_FILE
        self.stack.enter_context(patch.object(config, "VIBRATION_BASELINE_READINGS", 3))
        self.stack.enter_context(patch.object(config, "VIBRATION_BASELINE_MAX_CV_PERCENT", 20.0))

    def settings(self, wear_in_hours, interval_seconds):
        self.stack.enter_context(
            patch.object(config, "VIBRATION_BASELINE_WEAR_IN_HOURS", wear_in_hours)
        )
        self.stack.enter_context(
            patch.object(config, "VIBRATION_BASELINE_READING_INTERVAL_SECONDS", interval_seconds)
        )

    @staticmethod
    def running_window(amplitude):
        t = np.arange(config.WINDOW_SIZE) / config.SENSOR_SAMPLE_RATE_HZ
        wave = amplitude * np.sin(2 * np.pi * 25 * t)
        return np.column_stack([wave, 0.5 * wave, 1.0 + 0.25 * wave])

    def feed(self, seconds, amplitude=0.1, acceptable=True, is_idle=False, temperature_c=None):
        return vibration_baseline.vibration_deviation_for_window(
            self.DEVICE_ID,
            self.running_window(amplitude),
            is_idle=is_idle,
            acceptable=acceptable,
            record=True,
            observed_at=self.T0 + timedelta(seconds=seconds),
            temperature_c=temperature_c,
        )

    def test_wear_in_then_spaced_readings_then_deviation_warns(self):
        self.settings(wear_in_hours=0.01, interval_seconds=30)  # 36 s wear-in

        for seconds in (0, 10, 20, 30):
            result = self.feed(seconds)
            self.assertEqual(result["baseline_status"], "wear_in")
            self.assertIsNone(result["deviation_percent"])

        result = self.feed(40)  # 40 s of running time > 36 s wear-in
        self.assertEqual(result["baseline_status"], "collecting")
        self.assertEqual(result["baseline_readings"], 1)

        result = self.feed(50)  # too soon after the last reading
        self.assertEqual(result["baseline_readings"], 1)

        for seconds in (60, 70, 80, 90, 100):
            result = self.feed(seconds)
        self.assertEqual(result["baseline_status"], "established")
        self.assertEqual(result["baseline_readings"], 3)
        self.assertAlmostEqual(result["deviation_percent"], 0.0, places=2)
        self.assertFalse(result["warning"])

        result = self.feed(110, amplitude=0.15)  # 50% stronger vibration
        self.assertAlmostEqual(result["deviation_percent"], 50.0, places=1)
        self.assertTrue(result["warning"])
        self.assertEqual(result["baseline_rms_g"], self.feed(120)["baseline_rms_g"])

        result = self.feed(130, amplitude=0.15, is_idle=True)
        self.assertIsNone(result["deviation_percent"])
        self.assertFalse(result["warning"])

    def test_pump_off_gaps_do_not_count_as_wear_in(self):
        self.settings(wear_in_hours=1.0, interval_seconds=30)
        self.feed(0)
        result = self.feed(120)  # longer than the 60 s running gap limit
        self.assertEqual(result["running_hours"], 0.0)
        result = self.feed(150)
        self.assertAlmostEqual(result["running_hours"], 30 / 3600, places=3)

    def test_unacceptable_windows_are_not_baseline_data(self):
        self.settings(wear_in_hours=0.0, interval_seconds=30)
        for seconds in (0, 40, 80):
            result = self.feed(seconds, acceptable=False)
        self.assertEqual(result["baseline_status"], "collecting")
        self.assertEqual(result["baseline_readings"], 0)
        self.assertEqual(self.feed(120)["baseline_readings"], 1)

    def test_unstable_readings_do_not_establish_baseline(self):
        self.settings(wear_in_hours=0.0, interval_seconds=0)
        for seconds, amplitude in enumerate((0.1, 0.2, 0.1, 0.1)):
            result = self.feed(seconds, amplitude=amplitude)
        self.assertEqual(result["baseline_status"], "collecting")
        self.assertGreater(result["readings_cv_percent"], 20.0)

        result = self.feed(4, amplitude=0.1)  # last three readings now agree
        self.assertEqual(result["baseline_status"], "established")

    def test_read_only_call_does_not_start_baseline(self):
        result = vibration_baseline.vibration_deviation_for_window(
            self.DEVICE_ID, self.running_window(0.1)
        )
        self.assertEqual(result["baseline_status"], "not_started")
        self.assertFalse(self.baseline_file.exists())

    def test_known_healthy_baseline_stores_temperature(self):
        self.settings(wear_in_hours=1.0, interval_seconds=0)
        vibration_baseline.reset_vibration_baseline(
            self.DEVICE_ID, 0, "healthy_reference", observed_at=self.T0, known_healthy=True
        )
        # Operator-certified healthy: windows the models flag are still used.
        for seconds, temperature in enumerate((40.0, 42.0, 44.0)):
            result = self.feed(seconds, acceptable=False, temperature_c=temperature)
        self.assertEqual(result["baseline_status"], "established")
        self.assertTrue(result["known_healthy"])
        self.assertAlmostEqual(result["baseline_temperature_c"], 42.0)

        result = self.feed(10, amplitude=0.2, temperature_c=50.0)
        self.assertAlmostEqual(result["deviation_percent"], 100.0, places=1)
        self.assertAlmostEqual(result["temperature_difference_c"], 8.0)

        result = self.feed(11, temperature_c=float("nan"))
        self.assertIsNone(result["temperature_difference_c"])

    def test_recording_saves_raw_samples(self):
        device_id = "ESP32-RECORDING"
        directory = self.data_dir
        with patch.object(config, "RECORDINGS_DIR", directory), \
             patch.object(config, "BASE_DIR", directory):
            try:
                started = self.client.post(
                    f"/api/esp32/recording/{device_id}/start", json={"label": "healthy pump"}
                ).get_json()
                self.assertTrue(started["recording"])
                self.assertIn("healthy_pump", started["file"])

                self.client.post("/api/esp32/data", json={"device_id": device_id, "samples": [
                    {"x": 0.01, "y": -0.02, "z": 1.0, "temperature_c": 41.5, "pump_running": True},
                    {"x": 0.02, "y": -0.01, "z": 0.99, "temperature_c": 41.5, "pump_running": True},
                ]})
                stopped = self.client.post(f"/api/esp32/recording/{device_id}/stop").get_json()
                self.assertFalse(stopped["recording"])
                self.assertEqual(stopped["samples"], 2)

                rows = (Path(directory) / stopped["file"]).read_text().splitlines()
                self.assertEqual(rows[0].split(","), config.RECORDING_COLUMNS)
                self.assertEqual(rows[1].split(",")[2:6], ["0.01", "-0.02", "1.0", "41.5"])
            finally:
                with device_registry.buffer_lock:
                    device_registry.esp32_data_buffer.pop(device_id, None)
                    device_registry.device_registration_state.pop(device_id, None)
                recorder.active_recordings.pop(device_id, None)

    def test_reset_and_status_endpoints(self):
        self.settings(wear_in_hours=1.0, interval_seconds=30)

        response = self.client.post(
            f"/api/esp32/baseline/{self.DEVICE_ID}/reset",
            json={"wear_in_hours": 0, "reason": "overhaul"},
        )
        self.assertEqual(response.status_code, 200)
        baseline = response.get_json()["baseline"]
        self.assertEqual(baseline["baseline_status"], "collecting")
        self.assertEqual(baseline["baseline_reason"], "overhaul")

        response = self.client.get(f"/api/esp32/baseline/{self.DEVICE_ID}")
        self.assertEqual(response.get_json()["baseline"]["baseline_status"], "collecting")

        response = self.client.post(f"/api/esp32/baseline/{self.DEVICE_ID}/reset")
        self.assertEqual(response.get_json()["baseline"]["baseline_status"], "wear_in")

        response = self.client.post(
            f"/api/esp32/baseline/{self.DEVICE_ID}/reset", json={"wear_in_hours": -1}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get("/api/esp32/baseline/not-a-device").status_code, 403)


if __name__ == "__main__":
    unittest.main()
