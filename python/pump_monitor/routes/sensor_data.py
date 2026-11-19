"""Live sensor data from the ESP32: receive samples, buffer status, history,
on-demand analysis and clearing the buffer."""

import time
from datetime import datetime

import numpy as np
import pandas as pd
from flask import Blueprint, jsonify, request

from .. import config, firebase_store, test_session
from ..anomaly_model import analyze_pump_parts, score_windows
from ..device_registry import (
    buffer_lock,
    esp32_data_buffer,
    get_device_registration_state,
    is_esp32_device_id,
)
from ..iso20816 import compare_to_standard_vibration
from ..health_assessment import live_risk, sample_is_running
from ..predictive_maintenance import (
    build_predictive_maintenance_summary,
    compute_predictive_risk,
    history_point_due,
    predict_failure_for_device,
)
from ..recorder import append_recording_samples
from ..regression_baseline import learn_from_standard_sensor_window
from ..signal_processing import (
    create_windows,
    filter_pump_samples,
    latest_sample_values,
    numeric_value,
    replace_clipped_samples,
    sample_pump_is_idle,
    svd_denoise_window,
)
from ..vibration_baseline import vibration_deviation_for_window
from ..window_analysis import analyze_windows, health_status

bp = Blueprint("sensor_data", __name__)


@bp.route("/api/esp32/data", methods=["POST"])
def esp32_receive_data():
    """Receive accelerometer data from ESP32

    Expected JSON format:
    {
        "device_id": "ESP32-001",
        "samples": [
            {"x": 0.5, "y": 0.2, "z": -0.1},
            {"x": 0.6, "y": 0.3, "z": -0.2}
        ]
    }
    """
    try:
        # silent: unreadable JSON is the sender's error (400), not a server error.
        data = request.get_json(silent=True)

        if not data:
            return jsonify({
                "status": "error",
                "message": "No valid JSON data provided"
            }), 400

        device_id = data.get('device_id')
        samples = data.get('samples', [])

        if not device_id:
            return jsonify({
                "status": "error",
                "message": "device_id is required"
            }), 400

        if not is_esp32_device_id(device_id):
            return jsonify({
                "status": "error",
                "message": "Only ESP32 device data is accepted"
            }), 403

        if not samples:
            return jsonify({
                "status": "error",
                "message": "samples array is required"
            }), 400

        # Raw samples (unmodified) feed the SVD denoiser; zeroing idle samples
        # there would create a fake step/spike when the pump starts or stops.
        raw_samples = [
            sample.copy() if isinstance(sample, dict) else sample
            for sample in samples
        ]
        try:
            append_recording_samples(device_id, raw_samples, datetime.now())
        except Exception as recording_error:
            print(f"Recording write failed: {recording_error}")
        # Recordings keep the raw readings; analysis uses them with garbled
        # readings (at the sensor's +/-16 g limit) replaced.
        raw_samples = replace_clipped_samples(raw_samples)
        samples = filter_pump_samples(replace_clipped_samples(samples))

        is_registered = get_device_registration_state(device_id)
        print(f"[DEBUG] Received ESP32 data: device_id={device_id}, samples={len(samples)}")

        with buffer_lock:
            device_buffer = esp32_data_buffer[device_id]
            device_buffer['registered'] = is_registered
            device_buffer['data'].extend(samples)
            if len(device_buffer['data']) > config.MAX_DEVICE_BUFFER_SAMPLES:
                del device_buffer['data'][:-config.MAX_DEVICE_BUFFER_SAMPLES]
            device_buffer['raw_data'].extend(raw_samples)
            if len(device_buffer['raw_data']) > config.WINDOW_SIZE:
                del device_buffer['raw_data'][:-config.WINDOW_SIZE]
            device_buffer['live_raw'].extend(raw_samples)
            device_buffer['live_total'] = device_buffer.get('live_total', 0) + len(raw_samples)
            if len(device_buffer['live_raw']) > config.LIVE_RAW_SAMPLES:
                del device_buffer['live_raw'][:-config.LIVE_RAW_SAMPLES]

            raw_window = device_buffer['raw_data']
            valid_window = all(
                isinstance(sample, dict) and all(
                    numeric_value(sample.get(axis)) is not None
                    for axis in ('x', 'y', 'z')
                )
                for sample in raw_window
            )
            if len(raw_window) >= config.PUMP_SVD_MIN_SAMPLES and valid_window:
                raw_values = np.asarray([
                    [numeric_value(sample[axis]) for axis in ('x', 'y', 'z')]
                    for sample in raw_window
                ], dtype=float)
                denoised_values = svd_denoise_window(raw_values)
                processed_window = []
                for sample, axes in zip(raw_window, denoised_values):
                    processed_sample = sample.copy()
                    if sample_pump_is_idle(processed_sample):
                        processed_sample.update({'x': 0.0, 'y': 0.0, 'z': 0.0})
                    else:
                        processed_sample.update({
                            axis: float(axes[index])
                            for index, axis in enumerate(('x', 'y', 'z'))
                        })
                    processed_window.append(processed_sample)

                device_buffer['data'][-len(processed_window):] = processed_window
                samples = processed_window[-len(samples):]

            # With the time zone, so the app's "online" check also works when
            # the server's clock is not UTC (e.g. the backend on a laptop).
            device_buffer['last_update'] = datetime.now().astimezone().isoformat()

            buffer_size = len(device_buffer['data'])
            samples_remaining = config.WINDOW_SIZE - buffer_size
            latest_values = latest_sample_values(device_buffer['data'])

            # The analysis of the latest window (anomaly model, forecast, ISO
            # comparison) runs every UPLOAD_ANALYSIS_INTERVAL_SECONDS, not on
            # every upload, and outside the lock on a copy: it is slow, and
            # every other request (the other ESP32, the app) waits for the lock.
            # On a small server that made the pumps flicker offline.
            analysis_now = time.monotonic()
            analyze = (
                buffer_size >= config.WINDOW_SIZE and
                analysis_now - device_buffer.get('last_analysis_at', float('-inf'))
                >= config.UPLOAD_ANALYSIS_INTERVAL_SECONDS
            )
            window_samples = []
            if analyze:
                device_buffer['last_analysis_at'] = analysis_now
                window_samples = list(device_buffer['data'][-config.WINDOW_SIZE:])

        auto_analyze = False
        analysis_result = None
        latest_window_df = None

        if analyze:

            try:
                df = pd.DataFrame(window_samples)
                df = df[['x', 'y', 'z']]
                latest_window_df = df.copy()

                windows = create_windows(df, window_size=config.WINDOW_SIZE, step=config.WINDOW_SIZE)
                preds, scores = score_windows(windows)


                latest_window = windows[-1] if len(windows) > 0 else None
                if latest_window is not None:
                    anomalous_parts, axis_metrics = analyze_pump_parts(latest_window)
                    standard_comparison = compare_to_standard_vibration(df)
                    is_idle = (
                        any(m.get('is_idle') for m in axis_metrics) or
                        sample_pump_is_idle(window_samples[-1])
                    )
                    if is_idle:
                        anomalous_parts = []
                        for m in axis_metrics:
                            m['is_idle'] = True
                            m['is_anomaly'] = False
                    # The healthy-pump model and part limits decide (plus the
                    # baseline deviation below); ISO 20816 is information only.
                    is_anomaly = False if is_idle else (
                        preds[-1] == -1 or
                        any(m['is_anomaly'] for m in axis_metrics)
                    )

                    risk_score, _ = compute_predictive_risk(latest_window_df, {
                        "anomaly_score": float(scores[-1])
                    }, device_id=device_id)
                    observed_at = datetime.now()
                    # This window's forecast for the response; the trend is
                    # recorded below with the app's 60 s risk.
                    prediction = predict_failure_for_device(
                        device_id, risk_score, observed_at, record=False,
                    )

                    analysis_result = {
                        "is_anomaly": bool(is_anomaly),
                        "is_idle": bool(is_idle),
                        "anomaly_score": float(scores[-1]),
                        "risk_score": risk_score,
                        "standard_vibration_comparison": standard_comparison,
                        "affected_parts": list(anomalous_parts),
                        "axis_metrics": [
                            {
                                "axis": m['axis'],
                                "pump_part": m['pump_part'],
                                "rms": round(m['rms'], 4),
                                "std": round(m['std'], 4),
                                "max": round(m['max'], 4),
                                "is_idle": bool(m.get('is_idle', False)),
                                "is_anomaly": bool(m['is_anomaly'])
                            }
                            for m in axis_metrics
                        ]
                    }
                    analysis_result["predictive_maintenance"] = prediction
                    analysis_result["maintenance_summary"] = build_predictive_maintenance_summary(
                        device_id,
                        risk_score,
                        (analysis_result.get("standard_vibration_comparison") or {}).get("zone", "A"),
                        observed_at,
                        runtime_hours=0.0,
                        record=False,
                        prediction=prediction,
                    )
                    auto_analyze = True
            except Exception as analyze_error:
                print(f"ESP32 auto-analysis failed: {analyze_error}")

        online_learning_result = None
        if auto_analyze and analysis_result and latest_window_df is not None:
            should_learn = (
                not analysis_result["is_anomaly"] and
                not analysis_result["is_idle"] and
                not (
                    analysis_result.get("standard_vibration_comparison") and
                    analysis_result["standard_vibration_comparison"]["outside_standard"]
                )
            )
            if should_learn:
                try:
                    online_learning_result = learn_from_standard_sensor_window(latest_window_df)
                except Exception as learn_error:
                    online_learning_result = {
                        "learned": False,
                        "error": str(learn_error)
                    }
            else:
                online_learning_result = {
                    "learned": False,
                    "reason": "window was idle or outside the ISO 20816 standard vibration zone"
                }

            analysis_result["online_learning"] = online_learning_result

            # Formula 3: deviation of this window's RMS from the pump's ISO 17359
            # baseline. Acceptable windows (no anomaly, zone A/B) can become
            # baseline data while the baseline is being acquired.
            try:
                analysis_result["vibration_deviation"] = vibration_deviation_for_window(
                    device_id,
                    latest_window_df.to_numpy(),
                    is_idle=analysis_result["is_idle"],
                    acceptable=should_learn,
                    record=True,
                    temperature_c=latest_values["latest_temperature_c"],
                )
            except Exception as deviation_error:
                analysis_result["vibration_deviation"] = {"error": str(deviation_error)}
            # Vibration 50%+ away from the healthy baseline is an anomaly too.
            if analysis_result["vibration_deviation"].get("warning") and not analysis_result["is_idle"]:
                analysis_result["is_anomaly"] = True

        # The risk trend (failure forecast, maintenance history) records the risk
        # the app shows: the last ~60 s of running data, every few minutes. Not
        # the risk of one 1.3 s window, which jumps around. Checked on every
        # upload while the pump runs (not only when the window is analysed).
        if buffer_size >= config.WINDOW_SIZE and raw_samples and sample_is_running(raw_samples[-1]):
            try:
                observed_at = datetime.now()
                if history_point_due(device_id, observed_at):
                    trend_risk = live_risk(device_id)
                    if trend_risk is not None:
                        prediction = predict_failure_for_device(
                            device_id, trend_risk, observed_at, record=True,
                        )
                        build_predictive_maintenance_summary(
                            device_id,
                            trend_risk,
                            "A" if trend_risk < 30 else "B" if trend_risk < 60 else "C" if trend_risk < 90 else "D",
                            observed_at,
                            runtime_hours=0.0,
                            record=True,
                            prediction=prediction,
                        )
            except Exception as trend_error:
                print(f"Risk trend update failed: {trend_error}")

        try:
            test_session.add_samples(device_id, raw_samples)
        except Exception as test_error:
            print(f"Timed test update failed: {test_error}")

        # Saved to Firebase in the background: the ESP32 must not wait for it.
        firebase_queued = firebase_store.queue_sensor_batch_for_firebase(device_id, samples, analysis_result)

        return jsonify({
            "status": "success",
            "message": f"Received {len(samples)} samples",
            "device_id": device_id,
            "firebase_saved": firebase_queued,
            "firebase_batch_id": None,  # assigned when the background write runs
            "buffer_status": {
                "total_samples": buffer_size,
                "samples_remaining": max(0, samples_remaining),
                "ready_for_analysis": buffer_size >= config.WINDOW_SIZE
            },
            "latest_sample": latest_values["latest_sample"],
            "latest_temperature_c": latest_values["latest_temperature_c"],
            "latest_pressure_bar": latest_values["latest_pressure_bar"],
            "latest_pressure_psi": latest_values["latest_pressure_psi"],
            "online_learning": online_learning_result,
            "analysis": analysis_result if auto_analyze else None
        }), 200

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@bp.route("/api/esp32/status/<device_id>", methods=["GET"])
def esp32_status(device_id):
    """Get current buffer status for an ESP32 device"""
    try:
        if not is_esp32_device_id(device_id):
            return jsonify({
                "status": "error",
                "message": "Only ESP32 device IDs are supported on this endpoint"
            }), 403

        with buffer_lock:
            device_present = device_id in esp32_data_buffer
            print(f"[DEBUG] Status request for device_id={device_id}, device_present={device_present}")
            if not device_present:
                print(f"[DEBUG] Device {device_id} missing from buffer")
                return jsonify({
                    "status": "error",
                    "message": f"Device {device_id} not found"
                }), 404

            buffer_info = esp32_data_buffer[device_id]
            buffer_size = len(buffer_info['data'])
            latest_values = latest_sample_values(buffer_info['data'])
            print(f"[DEBUG] Device {device_id} status buffer_size={buffer_size}")

            return jsonify({
                "status": "success",
                "device_id": device_id,
                "device_name": buffer_info['device_name'],
                "location": buffer_info['location'],
                "last_update": buffer_info['last_update'],
                "latest_sample": latest_values["latest_sample"],
                "latest_temperature_c": latest_values["latest_temperature_c"],
                "latest_pressure_bar": latest_values["latest_pressure_bar"],
                "latest_pressure_psi": latest_values["latest_pressure_psi"],
                "buffer_status": {
                    "total_samples": buffer_size,
                    "samples_needed": config.WINDOW_SIZE,
                    "samples_remaining": max(0, config.WINDOW_SIZE - buffer_size),
                    "ready_for_analysis": buffer_size >= config.WINDOW_SIZE,
                    "buffer_fill_percentage": round((buffer_size / config.WINDOW_SIZE) * 100, 2)
                }
            }), 200

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@bp.route("/api/esp32/history/<device_id>", methods=["GET"])
def esp32_history(device_id):
    """Get saved machine-check history for an ESP32 or Flutter device."""
    try:
        if not is_esp32_device_id(device_id):
            return jsonify({
                "status": "error",
                "message": "Only ESP32 device IDs are supported on this endpoint"
            }), 403

        limit = request.args.get("limit", default=25, type=int)
        limit = max(1, min(limit, 100))
        use_firebase = request.args.get("firebase", default="1").lower() not in {"0", "false", "no"}

        firebase_history = firebase_store.get_history_from_firebase(device_id, limit=limit) if use_firebase else None
        if use_firebase and firebase_history is not None:
            return jsonify({
                "status": "success",
                "device_id": device_id,
                "source": "firebase",
                "history": firebase_history
            }), 200

        with buffer_lock:
            if device_id not in esp32_data_buffer:
                return jsonify({
                    "status": "success",
                    "device_id": device_id,
                    "source": "memory",
                    "history": []
                }), 200

            buffer_info = esp32_data_buffer[device_id]
            history = [{
                "batch_id": "current-buffer",
                "device_id": device_id,
                "sample_count": len(buffer_info["data"]),
                "machine_condition": "Collecting data",
                "checked_at": buffer_info["last_update"],
                "checked_at_local": buffer_info["last_update"],
                "is_anomaly": False,
                "anomaly_score": None,
                "affected_parts": [],
                "temperature_c": latest_sample_values(buffer_info["data"])["latest_temperature_c"],
                "pressure_bar": latest_sample_values(buffer_info["data"])["latest_pressure_bar"],
                "pressure_psi": latest_sample_values(buffer_info["data"])["latest_pressure_psi"],
            }] if buffer_info["last_update"] else []

        return jsonify({
            "status": "success",
            "device_id": device_id,
            "source": "memory",
            "history": history
        }), 200

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@bp.route("/api/esp32/analyze/<device_id>", methods=["GET"])
def esp32_analyze(device_id):
    """Analyze buffered data from ESP32 device"""
    try:
        if not is_esp32_device_id(device_id):
            return jsonify({
                "status": "error",
                "message": "Only ESP32 device IDs are supported on this endpoint"
            }), 403

        with buffer_lock:
            if device_id not in esp32_data_buffer:
                return jsonify({
                    "status": "error",
                    "message": f"Device {device_id} not found"
                }), 404

            buffer_data = esp32_data_buffer[device_id]['data']

            if len(buffer_data) < config.WINDOW_SIZE:
                return jsonify({
                    "status": "error",
                    "message": f"Not enough data. Need {config.WINDOW_SIZE} samples, have {len(buffer_data)}"
                }), 400

            df = pd.DataFrame(buffer_data[-config.WINDOW_SIZE:])
            df = df[['x', 'y', 'z']]

            windows = create_windows(df, window_size=config.WINDOW_SIZE, step=config.WINDOW_SIZE)
            windows_results, anomaly_count, all_affected_parts = analyze_windows(windows)
            latest_temperature_c = (buffer_data[-1] or {}).get("temperature_c")
            for window, window_result in zip(windows, windows_results):
                deviation = vibration_deviation_for_window(
                    device_id, window, is_idle=window_result["is_idle"],
                    temperature_c=latest_temperature_c,
                )
                window_result["vibration_deviation"] = deviation
                # Vibration 50%+ away from the healthy baseline is an anomaly too.
                if deviation.get("warning") and not window_result["is_anomaly"]:
                    window_result["is_anomaly"] = True
                    anomaly_count += 1

            if anomaly_count > 0:
                overall_status = f"Anomalies detected in {anomaly_count} samples"
                affected_parts_list = list(all_affected_parts)
            else:
                overall_status = "Pump operating normally"
                affected_parts_list = []

            return jsonify({
                "status": "success",
                "device_id": device_id,
                "device_name": esp32_data_buffer[device_id]['device_name'],
                "data": {
                    "total_samples_analyzed": len(buffer_data[-config.WINDOW_SIZE:]),
                    "anomaly_count": anomaly_count,
                    "health_status": health_status(anomaly_count, len(windows)),
                    "overall_status": overall_status,
                    "affected_parts": affected_parts_list,
                    "windows": windows_results
                }
            }), 200

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@bp.route("/api/esp32/clear/<device_id>", methods=["POST"])
def esp32_clear_buffer(device_id):
    """Clear the data buffer for an ESP32 device"""
    try:
        if not is_esp32_device_id(device_id):
            return jsonify({
                "status": "error",
                "message": "Only ESP32 device IDs are supported on this endpoint"
            }), 403

        with buffer_lock:
            if device_id not in esp32_data_buffer:
                return jsonify({
                    "status": "error",
                    "message": f"Device {device_id} not found"
                }), 404

            buffer_size = len(esp32_data_buffer[device_id]['data'])
            esp32_data_buffer[device_id]['data'] = []

            return jsonify({
                "status": "success",
                "message": f"Cleared {buffer_size} samples from buffer",
                "device_id": device_id
            }), 200

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500
