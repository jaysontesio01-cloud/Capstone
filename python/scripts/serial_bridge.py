"""Forward ESP32 sensor data from the USB cable to the backend.

For an ESP32 flashed with USE_USB_LINK = true and plugged into this computer.
The ESP32 writes every request as one line, "@@<url> <json>". This script
posts registrations as they come and merges data batches, so no samples are
lost while the backend is busy.

Run from the python folder while the backend (python app.py) is running:

    python scripts/serial_bridge.py --port COM5

Needs pyserial:  pip install pyserial
"""

import argparse
import json
import queue
import sys
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

import serial

MIN_SAMPLES_PER_POST = 20  # wait for at least this many samples ...
MAX_WAIT_SECONDS = 0.3  # ... or this long, before posting
MAX_SAMPLES_PER_POST = 100
MAX_PENDING_SAMPLES = 5000  # if the backend is down, keep about 50 s of data


def read_lines(port, lines, stop):
    """Read lines from the serial port into the queue (runs in a thread)."""
    buffer = b""
    while not stop.is_set():
        try:
            chunk = port.read(4096)
        except serial.SerialException as exc:
            lines.put(("error", str(exc)))
            return
        if not chunk:
            continue
        buffer += chunk
        *complete, buffer = buffer.split(b"\n")
        for raw in complete:
            lines.put(("line", raw.decode("utf-8", errors="replace").strip()))


def post(backend, path, payload):
    request = urllib.request.Request(
        backend + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def backend_knows(backend, device_id):
    try:
        with urllib.request.urlopen(f"{backend}/api/esp32/status/{device_id}", timeout=10) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def open_port(args):
    port = serial.Serial()
    port.port = args.port
    port.baudrate = args.baud
    port.timeout = 0.2
    # Both lines released: the ESP32 runs normally instead of staying in reset
    # or starting in flashing mode.
    port.dtr = False
    port.rts = False
    port.open()
    if hasattr(port, "set_buffer_size"):  # Windows
        # ~1 MB is ~90 s of data, so a short pause of this program loses nothing.
        try:
            port.set_buffer_size(rx_size=1 << 20)
        except serial.SerialException:
            pass
    return port


def keep_computer_awake():
    """Stop Windows from sleeping or turning the screen off while the bridge runs.

    On battery a laptop can go into standby after a few minutes, which pauses
    this program and loses data. Normal sleep settings return when it exits.
    """
    if sys.platform != "win32":
        return
    import ctypes

    es_continuous, es_system_required, es_display_required = 0x80000000, 0x1, 0x2
    ctypes.windll.kernel32.SetThreadExecutionState(
        es_continuous | es_system_required | es_display_required
    )
    print("Keeping the computer awake while the bridge runs")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", default="COM5", help="ESP32 serial port (default COM5)")
    parser.add_argument("--baud", type=int, default=921600, help="must match SERIAL_BAUD in the sketch")
    parser.add_argument("--backend", default="http://127.0.0.1:5000", help="backend address")
    parser.add_argument("--no-reset", action="store_true",
                        help="don't restart the ESP32 when the bridge starts")
    args = parser.parse_args()
    backend = args.backend.rstrip("/")
    sys.stdout.reconfigure(errors="replace")  # never crash on odd characters
    keep_computer_awake()

    while True:  # wait for the ESP32 to be plugged in
        try:
            port = open_port(args)
            break
        except serial.SerialException:
            print(f"Waiting for {args.port} (is the ESP32 plugged in?)...")
            time.sleep(2)
    if not args.no_reset:
        # Restart the ESP32 so it registers itself again (RTS pulses EN).
        port.rts = True
        time.sleep(0.1)
        port.rts = False
        print("Restarted the ESP32; waiting for it to register...")

    lines = queue.Queue()
    stop = threading.Event()
    threading.Thread(target=read_lines, args=(port, lines, stop), daemon=True).start()

    pending = {}  # device_id -> list of samples not posted yet
    first_pending_at = {}
    registered = set()
    stats = {"received": 0, "posted": 0, "failed_posts": 0}
    last_status = "-"
    last_esp32_line = ""
    last_report = time.monotonic()
    print(f"Bridge running: {args.port} @ {args.baud} baud -> {backend}  (Ctrl+C to stop)")

    try:
        while True:
            try:
                kind, line = lines.get(timeout=0.05)
            except queue.Empty:
                kind, line = None, None

            if kind == "error":
                # The USB connection dropped (cable moved, power dip). Keep
                # trying to open the port again instead of stopping.
                print(f"Serial port error: {line} -> reconnecting...")
                stop.set()
                port.close()
                while True:
                    time.sleep(2)
                    try:
                        port = open_port(args)
                        break
                    except serial.SerialException:
                        continue
                stop = threading.Event()
                threading.Thread(target=read_lines, args=(port, lines, stop), daemon=True).start()
                print(f"[{time.strftime('%H:%M:%S')}] Reconnected to {args.port}")
                continue
            if kind == "line" and line.startswith("@@"):
                url, _, body = line[2:].partition(" ")
                path = urlsplit(url).path
                try:
                    payload = json.loads(body)
                except json.JSONDecodeError:
                    print(f"Skipped a damaged line ({len(line)} characters)")
                    continue
                device_id = payload.get("device_id")
                if path == "/api/esp32/register":
                    last_status = post(backend, path, payload)
                    registered.add(device_id)
                    print(f"Registered {device_id}: HTTP {last_status}")
                elif path == "/api/esp32/data" and device_id:
                    if device_id not in registered:
                        # Missed the ESP32's own registration (e.g. bridge started
                        # later): register it only if the backend doesn't know it,
                        # so its name and location aren't replaced.
                        if not backend_knows(backend, device_id):
                            last_status = post(backend, "/api/esp32/register", {"device_id": device_id})
                            print(f"Registered {device_id} (from its data): HTTP {last_status}")
                        registered.add(device_id)
                    samples = payload.get("samples") or []
                    stats["received"] += len(samples)
                    if not pending.get(device_id):
                        first_pending_at[device_id] = time.monotonic()
                    pending.setdefault(device_id, []).extend(samples)
                    if len(pending[device_id]) > MAX_PENDING_SAMPLES:
                        dropped = len(pending[device_id]) - MAX_PENDING_SAMPLES
                        del pending[device_id][:dropped]
                        print(f"Backend unreachable: dropped {dropped} old samples")
            elif kind == "line" and line:
                if line.startswith("Vibration RMS"):
                    last_esp32_line = line
                elif "�" not in line:  # skip the bootloader's 115200-baud text
                    print(f"ESP32: {line}")

            for device_id, samples in pending.items():
                age = time.monotonic() - first_pending_at.get(device_id, time.monotonic())
                if samples and (len(samples) >= MIN_SAMPLES_PER_POST or age >= MAX_WAIT_SECONDS):
                    batch = samples[:MAX_SAMPLES_PER_POST]
                    try:
                        last_status = post(backend, "/api/esp32/data",
                                           {"device_id": device_id, "samples": batch})
                    except (urllib.error.URLError, OSError) as exc:
                        last_status = f"backend unreachable ({exc})"
                    if last_status == 200:
                        del samples[:len(batch)]
                        stats["posted"] += len(batch)
                        first_pending_at[device_id] = time.monotonic()
                    else:
                        stats["failed_posts"] += 1
                        time.sleep(1)

            if time.monotonic() - last_report >= 5:
                waiting = sum(len(s) for s in pending.values())
                print(f"[{time.strftime('%H:%M:%S')}] received {stats['received']}, "
                      f"posted {stats['posted']}, waiting {waiting}, "
                      f"last HTTP {last_status} | {last_esp32_line or 'no pump status yet'}")
                last_report = time.monotonic()
    except KeyboardInterrupt:
        print("Stopping bridge")
    finally:
        stop.set()
        port.close()


if __name__ == "__main__":
    main()
