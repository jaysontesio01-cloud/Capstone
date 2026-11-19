# Water Pump Anomaly Detection - Flutter & ESP32 API Documentation

## Overview

This backend supports:
- **Flutter Mobile App** - Mobile interface for analysis
- **ESP32 Microcontroller** - Real-time sensor data from water pump
- **Web Browser** - Legacy HTML interface

---

## Backend API Endpoints

### 1. Health Check
**Endpoint:** `GET /api/status`

**Response:**
```json
{
    "status": "ok",
    "message": "Water Pump Anomaly Detection API is running",
    "version": "1.0"
}
```

---

## ESP32 Integration Endpoints

### 1. Register ESP32 Device
**Endpoint:** `POST /api/esp32/register`

**Purpose:** Register a new ESP32 device in the system

**Request Body:**
```json
{
    "device_id": "ESP32-001",
    "device_name": "Pump Monitor - Kitchen",
    "location": "Basement Pump Room"
}
```

**Response:**
```json
{
    "status": "success",
    "message": "Device Pump Monitor - Kitchen registered",
    "device_id": "ESP32-001"
}
```

### List Registered Devices
**Endpoint:** `GET /api/esp32/devices`

**Purpose:** Every registered pump and whether its ESP32 is online (sent data
in the last `LIVE_ONLINE_SECONDS` = 15 s, e.g. while connected to the phone's
hotspot). The app shows this as Online/Offline in "Pump sensors".

**Response:**
```json
{
    "status": "success",
    "devices": [
        {
            "device_id": "ESP32-002",
            "device_name": "ESP32 Pump Sensor2",
            "location": "Pump Area",
            "last_update": "2026-11-19T01:45:02.411126+00:00",
            "seconds_since_update": 1.2,
            "online": true,
            "sample_count": 2048
        }
    ]
}
```

### 2. Remove a Registered ESP32 Device
**Endpoint:** `DELETE /api/esp32/devices/<device_id>`

**Purpose:** Remove a device from the registered device list. Sensor history is retained.

**Response:**
```json
{
    "status": "success",
    "device_id": "ESP32-001",
    "sensor_history_retained": true
}
```

---

### 3. Send Sensor Data from ESP32
**Endpoint:** `POST /api/esp32/data`

**Purpose:** Send accelerometer readings from ESP32 to backend (continuous stream)

**Request Body:**
```json
{
    "device_id": "ESP32-001",
    "samples": [
        {"x": 0.5, "y": 0.2, "z": -0.1},
        {"x": 0.6, "y": 0.3, "z": -0.2},
        {"x": 0.55, "y": 0.25, "z": -0.15}
    ]
}
```

**Response:**
```json
{
    "status": "success",
    "message": "Received 3 samples",
    "device_id": "ESP32-001",
    "buffer_status": {
        "total_samples": 45,
        "samples_remaining": 83,
        "ready_for_analysis": false
    },
    "analysis": null
}
```

**Auto-Analysis Response** (when 128+ samples collected):
```json
{
    "status": "success",
    "message": "Received 3 samples",
    "device_id": "ESP32-001",
    "buffer_status": {
        "total_samples": 128,
        "samples_remaining": 0,
        "ready_for_analysis": true
    },
    "analysis": {
        "is_anomaly": false,
        "anomaly_score": 0.123,
        "affected_parts": [],
        "axis_metrics": [
            {
                "axis": "X-axis",
                "pump_part": "Lateral Bearing/Shaft Alignment",
                "rms": 0.995,
                "std": 0.026,
                "max": 1.432,
                "is_anomaly": false
            }
        ]
    }
}
```

---

### 3. Get Device Buffer Status
**Endpoint:** `GET /api/esp32/status/<device_id>`

**Purpose:** Check how much data is buffered for analysis

**Response:**
```json
{
    "status": "success",
    "device_id": "ESP32-001",
    "device_name": "Pump Monitor - Kitchen",
    "location": "Basement Pump Room",
    "last_update": "2024-04-16T10:30:45.123456",
    "buffer_status": {
        "total_samples": 95,
        "samples_needed": 128,
        "samples_remaining": 33,
        "ready_for_analysis": false,
        "buffer_fill_percentage": 74.22
    }
}
```

---

### 4. Analyze Buffered Data
**Endpoint:** `GET /api/esp32/analyze/<device_id>`

**Purpose:** Perform full analysis on collected samples

**Response:**
```json
{
    "status": "success",
    "device_id": "ESP32-001",
    "device_name": "Pump Monitor - Kitchen",
    "data": {
        "total_samples_analyzed": 128,
        "anomaly_count": 0,
        "health_status": "HEALTHY",
        "overall_status": "Pump operating normally",
        "affected_parts": [],
        "windows": [
            {
                "window_id": 0,
                "is_anomaly": false,
                "anomaly_score": 0.123,
                "affected_parts": [],
                "axis_metrics": [...]
            }
        ]
    }
}
```

---

### 5. Clear Buffer
**Endpoint:** `POST /api/esp32/clear/<device_id>`

**Purpose:** Reset buffer and start fresh

**Response:**
```json
{
    "status": "success",
    "message": "Cleared 128 samples from buffer",
    "device_id": "ESP32-001"
}
```

---

### 6. Live Dashboard (used by the app)
**Endpoint:** `GET /api/esp32/live/<device_id>`

**Purpose:** Everything the app's dashboard shows, in one call, judged against
the **healthy pump** (its recorded data in
`data/healthy_reference/ESP32-002_healthy_reference.json`, the part limits
learned from it and the anomaly model trained on it). Poll every 2 seconds.

**Response (shortened):**
```json
{
    "status": "success",
    "device_id": "ESP32-001",
    "device_name": "ESP32 Pump Sensor1",
    "online": true,
    "seconds_since_update": 0.4,
    "pump_running": true,
    "latest_temperature_c": 58.0,
    "axes": [
        {"axis": "X", "rms_g": 0.40, "healthy_rms_g": 0.33, "healthy_limit_g": 0.50,
         "times_healthy": 1.2, "status": "healthy", "pump_part": "Lateral Bearing/Shaft Alignment"},
        {"axis": "Y", "rms_g": 1.19, "healthy_rms_g": 0.08, "healthy_limit_g": 0.16,
         "times_healthy": 15.5, "status": "faulty", "pump_part": "Radial Bearing/Motor Housing"},
        {"axis": "Z", "rms_g": 0.74, "healthy_rms_g": 0.67, "healthy_limit_g": 1.02,
         "times_healthy": 1.1, "status": "healthy", "pump_part": "Impeller/Pump Head/Axial Load"}
    ],
    "live_assessment": {
        "verdict": "FAULTY",
        "verdict_text": "Faulty: shut down and inspect the pump",
        "health_percent": 0.0,
        "risk_score": 100.0,
        "total_rms_g": 1.51,
        "healthy_total_rms_g": 0.75,
        "deviation_percent": 98.7,
        "anomaly_window_percent": 100.0,
        "temperature_max_c": 62.25,
        "problems": [
            {"title": "Radial bearing wear or rotor imbalance", "severity": "high",
             "evidence": "Y-axis vibration 1.19 g vs 0.08 g on the healthy pump (15.5x); ...",
             "check": "Listen for grinding or rumbling at the motor, ..."}
        ]
    },
    "healthy_reference": {"total_rms_g": 0.8524, "axis_rms_g": {"x": 0.5, "y": 0.15, "z": 0.6654},
                          "axis_limit_g": {"x": 0.7444, "y": 0.3043, "z": 1.0246},
                          "temperature_mean_c": 55.37, "temperature_max_c": 65.25},
    "test": {"state": "running", "minutes": 20, "remaining_seconds": 754.0, "assessment": {"...": "as above"}},
    "recording": {"recording": true, "file": "data\\recordings\\ESP32-001_app_test_20min_....csv"}
}
```

- `axes`: the last ~5 s while the pump runs (`status`: `healthy` up to the
  healthy limit, `warning` up to 2x the limit, `faulty` above, `off`).
- `live_assessment`: the last ~60 s of running data (at least ~8 s before a
  verdict). `verdict`: `HEALTHY`, `MONITOR`, `WARNING`, `FAULTY` or `NO_DATA`;
  machine health = 100 - risk; `risk_parts` gives each check's risk.
  Risk is the largest of: deviation from the healthy pump (within 30% = 0,
  50% = 60, 100% = 90), share of unusual windows (up to 45% = 0, 75% = 60,
  100% = 100) and temperature above the healthy pump's maximum (+10 C = 60,
  +20 C = 90). The "no risk" bands are the healthy pump's own range over 60 s
  stretches (vibration -14%..+27.5%, up to 45% unusual windows).
- `GET /api/esp32/predict/<id>` uses the same risk while the pump runs, and the
  failure forecast is fitted to that risk (recorded every 10 minutes); there is
  no forecast or shutdown alert while the risk is below 30.

### 7. Timed Tests
**Endpoints:**
- `POST /api/esp32/test/<device_id>/start` with `{"minutes": 20}`
- `POST /api/esp32/test/<device_id>/stop`
- `GET /api/esp32/test/<device_id>`

**Purpose:** A test of a set length started from the app. Every running window
during the test is judged against the healthy pump and kept (`assessment` covers
the whole test), the raw samples are recorded to `data/recordings/`, and when the
time is up the summary is saved to `data/app_tests/`. `state`: `none`,
`running`, `finished` or `stopped`.

A test only starts while the pump's ESP32 is connected (data in the last 15 s);
otherwise `start` answers **409** with `"error": "esp32_offline"`.

---

### 2. Analyze Accelerometer Data (JSON)
**Endpoint:** `POST /api/analyze`

**Request Body:**
```json
{
    "data": [
        {"x": 0.5, "y": 0.2, "z": -0.1},
        {"x": 0.6, "y": 0.3, "z": -0.2},
        {"x": 0.4, "y": 0.1, "z": -0.15}
    ]
}
```

**Response (Success):**
```json
{
    "status": "success",
    "data": {
        "total_windows": 42,
        "anomaly_count": 5,
        "anomaly_percentage": 11.9,
        "health_status": "WARNING",
        "overall_status": "WARNING: Anomalies detected in 5 windows",
        "affected_parts": [
            "Lateral Bearing/Shaft Alignment",
            "Radial Bearing/Motor Housing"
        ],
        "windows": [
            {
                "window_id": 0,
                "is_anomaly": false,
                "anomaly_score": 0.123,
                "affected_parts": [],
                "axis_metrics": [
                    {
                        "axis": "X-axis",
                        "pump_part": "Lateral Bearing/Shaft Alignment",
                        "rms": 0.995,
                        "std": 0.026,
                        "max": 1.432,
                        "mean": 0.991,
                        "is_anomaly": false
                    },
                    {
                        "axis": "Y-axis",
                        "pump_part": "Radial Bearing/Motor Housing",
                        "rms": 0.0775,
                        "std": 0.077,
                        "max": 0.215,
                        "mean": 0.005,
                        "is_anomaly": false
                    },
                    {
                        "axis": "Z-axis",
                        "pump_part": "Impeller/Pump Head/Axial Load",
                        "rms": 0.131,
                        "std": 0.014,
                        "max": 0.354,
                        "mean": -0.118,
                        "is_anomaly": false
                    }
                ]
            }
        ]
    }
}
```

**Response (Error):**
```json
{
    "status": "error",
    "message": "Missing columns. Required: ['x', 'y', 'z']"
}
```

---

### 3. Upload CSV File
**Endpoint:** `POST /api/analyze/csv`

**Request:** Form-data with file upload
```
Key: "file"
Value: <accelerometer.csv>
```

**Response:** Same as `/api/analyze` endpoint

---

## Health Status Levels

| Status | Mean | Description |
|--------|------|-------------|
| `HEALTHY` | Green | No anomalies detected |
| `WARNING` | Yellow | Anomalies in < 50% of windows |
| `CRITICAL` | Red | Anomalies in >= 50% of windows |

---

## Flutter Integration Example

### 1. Install HTTP Package
Add to `pubspec.yaml`:
```yaml
dependencies:
  http: ^1.1.0
```

### 2. Dart Service Class
```dart
import 'package:http/http.dart' as http;
import 'dart:convert';

class PumpAnalysisService {
  final String baseUrl = 'http://192.168.1.100:5000'; // Your backend IP

  Future<Map<String, dynamic>> checkHealth() async {
    try {
      final response = await http.get(
        Uri.parse('$baseUrl/api/status'),
      );
      
      if (response.statusCode == 200) {
        return json.decode(response.body);
      } else {
        throw Exception('Failed to connect');
      }
    } catch (e) {
      throw Exception('Connection error: $e');
    }
  }

  Future<Map<String, dynamic>> analyzeData(List<Map<String, double>> data) async {
    try {
      final response = await http.post(
        Uri.parse('$baseUrl/api/analyze'),
        headers: {'Content-Type': 'application/json'},
        body: json.encode({'data': data}),
      );
      
      if (response.statusCode == 200) {
        return json.decode(response.body);
      } else {
        final error = json.decode(response.body);
        throw Exception(error['message']);
      }
    } catch (e) {
      throw Exception('Analysis error: $e');
    }
  }

  Future<Map<String, dynamic>> uploadCsvFile(String filePath) async {
    try {
      var request = http.MultipartRequest(
        'POST',
        Uri.parse('$baseUrl/api/analyze/csv'),
      );
      
      request.files.add(
        await http.MultipartFile.fromPath('file', filePath),
      );
      
      var response = await request.send();
      
      if (response.statusCode == 200) {
        return json.decode(await response.stream.bytesToString());
      } else {
        throw Exception('Upload failed');
      }
    } catch (e) {
      throw Exception('File upload error: $e');
    }
  }
}
```

### 3. Usage in Flutter Widget
```dart
class PumpMonitorScreen extends StatefulWidget {
  @override
  _PumpMonitorScreenState createState() => _PumpMonitorScreenState();
}

class _PumpMonitorScreenState extends State<PumpMonitorScreen> {
  final PumpAnalysisService _service = PumpAnalysisService();
  Map<String, dynamic>? _analysisResult;
  bool _isLoading = false;
  String? _error;

  void _analyzeAccelerometerData() async {
    setState(() {
      _isLoading = true;
      _error = null;
    });

    try {
      // Example accelerometer data
      List<Map<String, double>> data = [
        {'x': 0.5, 'y': 0.2, 'z': -0.1},
        {'x': 0.6, 'y': 0.3, 'z': -0.2},
        // ... more samples
      ];
      
      final result = await _service.analyzeData(data);
      
      setState(() {
        _analysisResult = result;
      });
    } catch (e) {
      setState(() {
        _error = e.toString();
      });
    } finally {
      setState(() {
        _isLoading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text('Water Pump Monitor')),
      body: _isLoading
          ? Center(child: CircularProgressIndicator())
          : SingleChildScrollView(
              child: Column(
                children: [
                  if (_error != null)
                    Container(
                      color: Colors.red,
                      padding: EdgeInsets.all(16),
                      child: Text(_error!, style: TextStyle(color: Colors.white)),
                    ),
                  ElevatedButton(
                    onPressed: _analyzeAccelerometerData,
                    child: Text('Analyze Pump'),
                  ),
                  if (_analysisResult != null)
                    PumpAnalysisWidget(data: _analysisResult!),
                ],
              ),
            ),
    );
  }
}
```

---

## Starting the Backend

Run the Flask app from the `python` folder:
```bash
cd python
python app.py
```

The API will be available at:
```
http://localhost:5000
```

For Flutter on Android emulator (if backend is on your PC):
```
http://10.0.2.2:5000
```

For Flutter on physical device on same network:
```
http://<YOUR_PC_IP>:5000
```

---

## ESP32 Arduino Code Example

### Required Libraries
```cpp
// In Arduino IDE: Sketch > Include Library > Manage Libraries
// Install:
// - ArduinoJson by Benoit Blanchon
// - HTTPClient (built-in)
// - Wire (built-in)
// - MPU6050 library (for accelerometer)
```

### Complete ESP32 Code
```cpp
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <MPU6050.h>

// WiFi Configuration
const char* ssid = "YOUR_WIFI_SSID";
const char* password = "YOUR_WIFI_PASSWORD";

// Backend Server Configuration  
const char* serverUrl = "http://192.168.1.100:5000";  // Your PC IP
const char* deviceId = "ESP32-001";
const char* deviceName = "Pump Monitor Kitchen";
const char* location = "Basement";

// Sensor Configuration
MPU6050 mpu;
const int SAMPLE_RATE = 100;  // Hz
const int SAMPLES_PER_BATCH = 10;  // Send 10 samples per request

unsigned long lastSampleTime = 0;
int sampleCount = 0;
JsonDocument batchData;
JsonArray samplesArray;

void setup() {
    Serial.begin(115200);
    delay(2000);
    
    // Initialize I2C and MPU6050
    Wire.begin();
    mpu.initialize();
    if (!mpu.testConnection()) {
        Serial.println("MPU6050 connection failed!");
        while(1);
    }
    
    // Connect to WiFi
    connectToWiFi();
    
    // Register device
    registerDevice();
    
    // Initialize batch data
    samplesArray = batchData["samples"].to<JsonArray>();
    batchData["device_id"] = deviceId;
    
    Serial.println("ESP32 Pump Monitor initialized!");
}

void loop() {
    unsigned long currentTime = millis();
    
    // Sample at specified rate
    if (currentTime - lastSampleTime >= (1000 / SAMPLE_RATE)) {
        lastSampleTime = currentTime;
        
        // Read accelerometer
        int16_t ax, ay, az;
        mpu.getAcceleration(&ax, &ay, &az);
        
        // Convert to g (9.81 m/s²)
        float accelX = ax / 16384.0;
        float accelY = ay / 16384.0;
        float accelZ = az / 16384.0;
        
        // Add to batch
        JsonObject sample = samplesArray.createNestedObject();
        sample["x"] = accelX;
        sample["y"] = accelY;
        sample["z"] = accelZ;
        
        sampleCount++;
        
        // Send batch when ready
        if (sampleCount >= SAMPLES_PER_BATCH) {
            sendDataToBE();
            sampleCount = 0;
            samplesArray.clear();
        }
        
        // Print for debugging
        Serial.printf("Sample %d - X:%.3f Y:%.3f Z:%.3f\n", 
                     sampleCount, accelX, accelY, accelZ);
    }
    
    delay(10);
}

void connectToWiFi() {
    Serial.print("Connecting to WiFi: ");
    Serial.println(ssid);
    
    WiFi.mode(WIFI_STA);
    WiFi.begin(ssid, password);
    
    int attempts = 0;
    while (WiFi.status() != WL_CONNECTED && attempts < 20) {
        delay(500);
        Serial.print(".");
        attempts++;
    }
    
    if (WiFi.status() == WL_CONNECTED) {
        Serial.println("\nWiFi connected!");
        Serial.print("IP address: ");
        Serial.println(WiFi.localIP());
    } else {
        Serial.println("\nWiFi connection failed!");
    }
}

void registerDevice() {
    if (WiFi.status() != WL_CONNECTED) return;
    
    HTTPClient http;
    String url = String(serverUrl) + "/api/esp32/register";
    
    http.begin(url);
    http.addHeader("Content-Type", "application/json");
    
    JsonDocument regData;
    regData["device_id"] = deviceId;
    regData["device_name"] = deviceName;
    regData["location"] = location;
    
    String payload;
    serializeJson(regData, payload);
    
    int httpCode = http.POST(payload);
    
    if (httpCode == 200) {
        Serial.println("Device registered successfully!");
    } else {
        Serial.printf("Registration failed with code: %d\n", httpCode);
    }
    
    http.end();
}

void sendDataToBE() {
    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("WiFi disconnected, reconnecting...");
        connectToWiFi();
        return;
    }
    
    HTTPClient http;
    String url = String(serverUrl) + "/api/esp32/data";
    
    http.begin(url);
    http.addHeader("Content-Type", "application/json");
    
    String payload;
    serializeJson(batchData, payload);
    
    Serial.printf("Sending %d samples to backend...\n", samplesArray.size());
    
    int httpCode = http.POST(payload);
    
    if (httpCode == 200) {
        String response = http.getString();
        Serial.println("Data sent successfully!");
        
        // Check if analysis was performed
        JsonDocument responseDoc;
        deserializeJson(responseDoc, response);
        
        if (responseDoc["analysis"] != nullptr) {
            bool isAnomaly = responseDoc["analysis"]["is_anomaly"];
            float score = responseDoc["analysis"]["anomaly_score"];
            
            Serial.printf("Analysis Result - Anomaly: %s, Score: %.3f\n", 
                         isAnomaly ? "YES" : "NO", score);
            
            if (isAnomaly) {
                alertAnomalyDetected();
            }
        }
    } else {
        Serial.printf("Send failed with code: %d\n", httpCode);
        Serial.println("Response: " + http.getString());
    }
    
    http.end();
}

void alertAnomalyDetected() {
    Serial.println("\n*** ANOMALY DETECTED! ***");
    Serial.println("Check Flutter app or web interface for details!");
    
    // Optional: Trigger buzzer, LED, or other alert
    // digitalWrite(ALERT_PIN, HIGH);
    // delay(1000);
    // digitalWrite(ALERT_PIN, LOW);
}
```

### Configuration Steps

1. **Update WiFi credentials:**
   ```cpp
   const char* ssid = "YOUR_WIFI_NETWORK";
   const char* password = "YOUR_WIFI_PASSWORD";
   ```

2. **Update server IP:**
   ```cpp
   const char* serverUrl = "http://192.168.1.X:5000";  // Your PC's IP address
   ```

3. **Install Board:**
   - Tools > Board > esp32 > "ESP32 Dev Module"

4. **Upload to ESP32:**
   - Select COM port
   - Click Upload

5. **View Serial Monitor:**
   - Tools > Serial Monitor > 115200 baud

---

## Workflow

### ESP32 → Backend Flow:
1. ESP32 reads accelerometer data from MPU6050 sensor
2. Collects samples and batches (default: 10 samples)
3. Sends batch to `POST /api/esp32/data` endpoint
4. Backend automatically analyzes when 128 samples collected
5. Returns anomaly detection results immediately
6. ESP32 displays alert if anomaly detected

### Real-Time Monitoring:
- Flutter app polls `/api/esp32/status/<device_id>` to check buffer
- Flutter app gets results from `/api/esp32/analyze/<device_id>`
- Live dashboard updates with health status and affected parts

---
