# ESP32 Water Pump Monitor - Quick Start Guide

## Hardware Setup

### Required Components:
1. **ESP32 Development Board** (e.g., DOIT DevKit v1)
2. **ADXL345 Accelerometer**
3. **Jumper Wires**
4. **USB Cable** for programming

### Wiring Diagram (ADXL345 to ESP32):
```
ADXL345 Pin    ESP32 Pin    Description
VCC            3.3V         Power
GND            GND          Ground
SCL            22 (GPIO22)  I2C Clock
SDA            21 (GPIO21)  I2C Data
INT            (optional)   Interrupt
```
The firmware reads vibration from the ADXL345. Hydraulic pressure sensing is
disabled by default; set `USE_HYDRAULIC_PRESSURE_SENSOR` to `true` in the
firmware only if a pressure sensor is connected to GPIO 32.

---

## Software Setup

### Step 1: Install Arduino IDE
- Download from: https://www.arduino.cc/en/software

### Step 2: Add ESP32 Board
1. Arduino IDE → File → Preferences
2. Add to "Additional Boards Manager URLs":
   ```
   https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json
   ```
3. Tools → Board → Boards Manager → Search "ESP32" → Install

### Step 3: Install Required Libraries
Arduino IDE → Sketch → Include Library → Manage Libraries

Search and install:
- `ArduinoJson` by Benoit Blanchon (v6.x or higher)
- `WiFiManager` by tzapu
- `Adafruit ADXL345`
- `Adafruit Unified Sensor`
- `MAX6675 library`

### Step 4: Configure ESP32 Code

The sketch uses WiFiManager, so you do not need to edit Wi-Fi credentials in
the code. The server URL is already configured as:
```cpp
const char* SERVER_BASE_URL = "https://pump-monitor-backend.onrender.com";
```

After uploading, the ESP32 first tries its saved network. If no network is
saved or the connection fails, connect your phone to `ESP32-Pump-Setup`, then
open `192.168.4.1` in a browser and choose the local 2.4 GHz Wi-Fi network.
The ESP32 stores the credentials and reconnects automatically on future boots.

To configure a different network later, hold the ESP32 BOOT button (GPIO 0)
while powering it on. It will clear the saved network and open the setup portal
again. The deployed backend must be running before the ESP32 sends data.

The ESP32 still needs an internet connection to communicate with Render. It no
longer needs to use the same network as the laptop, and the laptop does not
need to stay on. Use any 2.4 GHz Wi-Fi network, phone hotspot, or add a
cellular/Ethernet network module if Wi-Fi is not available.

Keep Wi-Fi credentials private and do not commit them to a public repository.

### Using the phone's hotspot

1. In the sketch: `USE_USB_LINK = false`, `SERVER_BASE_URL` = the Render URL,
   and `PUMP_BOARD` = the board you upload to (2 = healthy pump, 1 = faulty pump).
2. Turn the hotspot on and set it to **2.4 GHz** (the ESP32 cannot use 5 GHz).
   If the ESP32 has not saved the hotspot yet, use the `ESP32-Pump-Setup`
   portal above.
3. The phone app reads the same Render server, so it shows the pump as soon as
   data arrives (the phone itself needs mobile data for the hotspot's internet).

The Serial Monitor (115200 baud) shows how the link is doing:
```
[Net] server name pump-monitor-backend.onrender.com: resolves to 216.24.57.18 (45 ms) | internet: OK (111 ms)
[WiFi] connected to "<hotspot>", IP 10.168.100.202, signal -50 dBm | sent 7720, queued 270, dropped 0 | last HTTP 200 in 2601 ms
```
- `internet: NOT working`: the hotspot has no mobile data.
- `dropped` growing: the network is too slow to keep up; samples are taken at
  a steady 100 Hz in their own task and queued (up to 10 s), and each request
  carries up to 6 s of data, so short slowdowns are caught up.

---

## Workflow

### Initial Setup Flow:
```
1. ESP32 powers on
2. Connects to WiFi
3. Registers device: POST /api/esp32/register
4. Starts reading accelerometer at 100 Hz
5. Batches 10 samples and sends: POST /api/esp32/data
6. Backend auto-analyzes at 128 samples
7. Results available immediately
```

### Real-Time Monitoring:
```
ESP32 (Sensor)  →  Internet  →  Render Flask API  →  Flutter App
  readings        HTTPS        storage & ML         visualization
```

For a Render deployment:
- Confirm `https://<your-render-service>.onrender.com/api/status` opens successfully.
- Confirm `SERVER_BASE_URL` uses the exact Render service URL and starts with `https://`.
- Confirm the ESP32 has internet access through a 2.4 GHz network.

For local development only:
---
// Add this to debug the configured server URL
## Testing
Serial.println(SERVER_BASE_URL);
### Test 1: Check Device Registration
// Test the public API with Postman or a browser first
curl -X POST http://localhost:5000/api/esp32/register \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "ESP32-TEST",
    "device_name": "Test Device",
    "location": "Lab"
  }'
```

Expected response:
```json
{
  "status": "success",
  "message": "Device Test Device registered",
  "device_id": "ESP32-TEST"
}
```

### Test 2: Send Sample Data
```bash
curl -X POST http://localhost:5000/api/esp32/data \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "ESP32-TEST",
    "samples": [
      {"x": 0.5, "y": 0.2, "z": -0.1},
      {"x": 0.6, "y": 0.3, "z": -0.2}
    ]
  }'
```

### Test 3: Check Buffer Status
```bash
curl http://localhost:5000/api/esp32/status/ESP32-TEST
```

### Test 4: Trigger Analysis
```bash
curl http://localhost:5000/api/esp32/analyze/ESP32-TEST
```

---

## Common Issues

### Issue 1: WiFi Connection Fails
**Solution:**
- Check SSID and password spelling
- Ensure WiFi is 2.4GHz (most ESP32s don't support 5GHz)
- Check if network allows IoT devices

### Issue 2: MPU6050 Not Found
**Solution:**
- Verify I2C wiring (SCL to 22, SDA to 21)
- Check I2C address: Upload I2C Scanner code
- Ensure 3.3V power is supplied

### Issue 3: Backend Not Reachable
**Solution:**
```cpp
// Add this to debug IP connection
Serial.print("Server: ");
Serial.println(serverUrl);

// Or test with Postman first
```

### Issue 4: No Data Received
**Solution:**
- Check ESP32 Serial Monitor output
- Verify accelerometer is moving (should show non-zero values)
- Confirm WiFi connection is successful
- Check PC firewall allows port 5000

---

## Monitoring Commands

### Real-time ESP32 Serial Output:
1. Arduino IDE → Tools → Serial Monitor
2. Set baud rate to **115200**
3. Watch for: WiFi connection, sample readings, send confirmations

### Backend Logs:
```bash
# Terminal where Flask is running will show:
# [GET /api/esp32/status/ESP32-001]
# [POST /api/esp32/data]
```

### Flutter App Integration:
The Flutter app will:
1. Poll `/api/esp32/status/<device_id>` every 2 seconds
2. Display buffer fill percentage
3. Show "READY FOR ANALYSIS" when 128+ samples collected
4. Display real-time health status

---

## Performance Notes

- **Sample Rate:** 100 Hz (configurable)
- **Batch Size:** 10 samples per transmission
- **Analysis Trigger:** 128 samples accumulated
- **Buffer Size:** Unlimited (continuously updated)
- **Network Latency:** ~100-500ms per request

---

## Next Steps

1. ✅ Wire ESP32 + MPU6050
2. ✅ Upload Arduino code
3. ✅ Check Serial Monitor for successful startup
4. ✅ Verify data appears in Flutter app
5. ✅ Trigger pump to create motion
6. ✅ Watch for anomaly detection

---

## Support

For issues:
1. Check Serial Monitor output
2. Test API endpoints with curl
3. Verify network connectivity
4. Check firmware logs
