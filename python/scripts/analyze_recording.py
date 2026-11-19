"""Summarize a pump recording, save it as the healthy reference, or compare to it.

Uses the backend's own processing, so the healthy and the faulty pumps are
measured exactly the same way:
  1. only samples the ESP32 marked as running, within --start/--end
  2. readings at the sensor's +/-16 g limit replaced (garbled transfers)
  3. 128-sample windows, each SVD-denoised, then the three-axis RMS (Formula 2)
  4. ISO 20816 velocity and zone per window, and the temperature

Run from the python folder. Examples:

  # Save the healthy pump's run as the reference and make it ESP32-002's baseline
  python scripts/analyze_recording.py data/recordings/<healthy>.csv --start 19:17:05 --end 19:19:30 \
      --save data/healthy_reference/healthy_reference.json --export-raw data/healthy_reference/healthy_raw.csv \
      --install-baseline ESP32-002

  # Compare a faulty pump's run with the healthy reference (Formula 3)
  python scripts/analyze_recording.py data/recordings/<faulty>.csv --start ... --end ... \
      --compare-to data/healthy_reference/healthy_reference.json
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# Lets this script import pump_monitor when run as `python scripts/<name>.py`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pump_monitor import config  # noqa: E402
from pump_monitor.formulas import percentage_deviation  # noqa: E402
from pump_monitor.iso20816 import classify_iso20816_velocity, velocity_rms_from_acceleration_g  # noqa: E402
from pump_monitor.signal_processing import (  # noqa: E402
    replace_clipped_samples,
    svd_denoise_window,
    window_dynamic_rms,
)
from pump_monitor.vibration_baseline import load_vibration_baselines, save_vibration_baselines  # noqa: E402

AXES = ["x", "y", "z"]


def load_segment(path, start=None, end=None):
    df = pd.read_csv(path, parse_dates=["received_at"], low_memory=False)
    day = df["received_at"].iloc[0].strftime("%Y-%m-%d")
    if start:
        df = df[df["received_at"] >= pd.Timestamp(f"{day} {start}")]
    if end:
        df = df[df["received_at"] <= pd.Timestamp(f"{day} {end}")]
    running = df["pump_running"].astype(str).str.lower().eq("true")
    for axis in AXES + ["temperature_c"]:
        df[axis] = pd.to_numeric(df[axis], errors="coerce")
    return df, df[running].dropna(subset=AXES)


def summarize(segment):
    raw = segment[AXES].to_dict("records")
    clipped = sum(any(abs(s[a]) >= config.ACCEL_CLIP_LIMIT_G for a in AXES) for s in raw)
    values = pd.DataFrame(replace_clipped_samples(raw))[AXES].to_numpy(dtype=float)
    times = segment["received_at"].to_numpy()

    size = config.WINDOW_SIZE
    windows = []
    for i in range(0, len(values) - size + 1, size):
        denoised = svd_denoise_window(values[i:i + size])
        centered = denoised - denoised.mean(axis=0)
        velocity = velocity_rms_from_acceleration_g(denoised)["velocity_rms_mm_s"]
        windows.append({
            "at": pd.Timestamp(times[i + size - 1]).isoformat(),
            "rms_g": window_dynamic_rms(denoised),
            "axis_rms_g": np.sqrt((centered ** 2).mean(axis=0)),
            "velocity_mm_s": velocity,
        })
    if not windows:
        raise SystemExit("Not enough running samples for one 128-sample window")

    rms = np.array([w["rms_g"] for w in windows])
    median = float(np.median(rms))
    spread = float(1.4826 * np.median(np.abs(rms - median)))  # robust standard deviation
    axis_rms = np.median([w["axis_rms_g"] for w in windows], axis=0)
    velocity = float(np.median([w["velocity_mm_s"] for w in windows]))
    zone, condition, _ = classify_iso20816_velocity(velocity)
    temperature = segment["temperature_c"]
    bad_temperature = int((temperature <= 0).sum())
    temperature = temperature[temperature > 0]

    return {
        "from": segment["received_at"].min().isoformat(),
        "to": segment["received_at"].max().isoformat(),
        "running_samples": int(len(segment)),
        "clipped_samples_replaced": int(clipped),
        "windows": len(windows),
        "vibration_rms_g": {
            "median": round(median, 4),
            "mean": round(float(rms.mean()), 4),
            "robust_std": round(spread, 4),
            "variation_percent": round(spread / median * 100, 1),
            "p10": round(float(np.percentile(rms, 10)), 4),
            "p90": round(float(np.percentile(rms, 90)), 4),
        },
        "axis_rms_g": dict(zip(AXES, (round(float(v), 4) for v in axis_rms))),
        "iso20816": {
            "velocity_rms_mm_s": round(velocity, 2),
            "zone": zone,
            "condition": condition,
            "note": f"estimated from {config.SENSOR_SAMPLE_RATE_HZ:g} samples/s; vibration above "
                    f"{config.SENSOR_SAMPLE_RATE_HZ / 2:g} Hz is not measured correctly",
        },
        "temperature_c": {
            "mean": round(float(temperature.mean()), 2) if len(temperature) else None,
            "min": float(temperature.min()) if len(temperature) else None,
            "max": float(temperature.max()) if len(temperature) else None,
            "invalid_readings_skipped": bad_temperature,
        },
        "processing": f"running samples only; readings at +/-{config.ACCEL_CLIP_LIMIT_G} g replaced; "
                      f"{config.WINDOW_SIZE}-sample windows, SVD-denoised (rank {config.PUMP_SVD_RANK}); "
                      "three-axis RMS (Formula 2); median over windows",
        "_window_readings": windows,
    }


def install_baseline(device_id, summary, source):
    """Make this summary the device's established ISO 17359 baseline."""
    readings = [
        {"at": w["at"], "rms_g": round(float(w["rms_g"]), 5), "temperature_c": None}
        for w in summary["_window_readings"]
    ]
    state = {
        "status": "established",
        "reason": "healthy_reference_recording",
        "known_healthy": True,
        "source": source,
        "started_at": summary["from"],
        "wear_in_hours": 0.0,
        "running_hours": round(len(readings) * config.WINDOW_SIZE / config.SENSOR_SAMPLE_RATE_HZ / 3600, 4),
        "last_running_at": summary["to"],
        "readings": readings,
        "baseline_rms_g": summary["vibration_rms_g"]["median"],
        "baseline_std_g": summary["vibration_rms_g"]["robust_std"],
        "baseline_temperature_c": summary["temperature_c"]["mean"],
        "baseline_temperature_std_c": None,
        "established_at": datetime.now().isoformat(),
    }
    baselines = load_vibration_baselines()
    baselines[device_id] = state
    save_vibration_baselines(baselines)


def compare(summary, reference):
    current = summary["vibration_rms_g"]["median"]
    healthy = reference["vibration_rms_g"]["median"]
    deviation = percentage_deviation(current, healthy)
    print("\nCompared with the healthy reference (Formula 3):")
    print(f"  vibration RMS: {current:.4f} g vs healthy {healthy:.4f} g -> "
          f"{deviation:.1f}% {'above' if current >= healthy else 'below'}"
          f"{'  (inspect the pump)' if deviation >= config.VIBRATION_DEVIATION_WARNING_PERCENT else ''}")
    for axis in AXES:
        print(f"  {axis} axis: {summary['axis_rms_g'][axis]:.4f} g vs {reference['axis_rms_g'][axis]:.4f} g")
    print(f"  ISO 20816 velocity: {summary['iso20816']['velocity_rms_mm_s']} mm/s "
          f"(zone {summary['iso20816']['zone']}) vs healthy {reference['iso20816']['velocity_rms_mm_s']} mm/s "
          f"(zone {reference['iso20816']['zone']})")
    t_now, t_ref = summary["temperature_c"]["mean"], reference["temperature_c"]["mean"]
    if t_now is not None and t_ref is not None:
        print(f"  temperature: {t_now:.2f} C vs healthy {t_ref:.2f} C -> {t_now - t_ref:+.2f} C")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("recording", help="CSV from data/recordings/")
    parser.add_argument("--start", help="HH:MM:SS, e.g. after the pump has started")
    parser.add_argument("--end", help="HH:MM:SS, e.g. before the pump stops")
    parser.add_argument("--save", help="write the summary to this JSON file")
    parser.add_argument("--export-raw", help="also save the selected raw samples to this CSV")
    parser.add_argument("--install-baseline", metavar="DEVICE_ID",
                        help="make this the device's healthy baseline in the backend")
    parser.add_argument("--compare-to", help="healthy reference JSON to compare with")
    args = parser.parse_args()

    selected, segment = load_segment(args.recording, args.start, args.end)
    summary = summarize(segment)
    summary["recording"] = str(Path(args.recording))
    printable = {k: v for k, v in summary.items() if not k.startswith("_")}
    print(json.dumps(printable, indent=2))

    if args.export_raw:
        Path(args.export_raw).parent.mkdir(parents=True, exist_ok=True)
        selected.to_csv(args.export_raw, index=False)
        print(f"\nRaw samples saved: {args.export_raw} ({len(selected)} rows)")
    if args.save:
        Path(args.save).parent.mkdir(parents=True, exist_ok=True)
        Path(args.save).write_text(json.dumps(printable, indent=2), encoding="utf-8")
        print(f"Summary saved: {args.save}")
    if args.install_baseline:
        install_baseline(args.install_baseline, summary, str(Path(args.recording)))
        print(f"Installed as {args.install_baseline}'s healthy baseline in {config.VIBRATION_BASELINE_FILE}")
    if args.compare_to:
        compare(summary, json.loads(Path(args.compare_to).read_text(encoding="utf-8")))


if __name__ == "__main__":
    main()
