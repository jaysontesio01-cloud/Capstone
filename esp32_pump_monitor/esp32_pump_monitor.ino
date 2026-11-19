#include <Arduino.h>
#include <ArduinoJson.h>
#include <Adafruit_ADXL345_U.h>
#include <Adafruit_Sensor.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <WiFiManager.h>
#include <WiFiClientSecure.h>
#include <Wire.h>
#include <max6675.h>



// Where the ESP32 sends its data over Wi-Fi (USE_USB_LINK = false):
//  - Render (cloud, default): works on any Wi-Fi with internet, e.g. the phone's
//    hotspot, and the phone app reads the same server.
//  - A backend on the laptop: "http://<laptop IP>:5000" (laptop on the same Wi-Fi).
const char *SERVER_BASE_URL = "https://pump-monitor-backend.onrender.com";

// USB link: send every request over the USB cable instead of Wi-Fi. Use it when
// the ESP32 is plugged into the laptop; python/scripts/serial_bridge.py reads
// the lines and posts them to the backend. No Wi-Fi is needed.
// false = Wi-Fi (the saved network, e.g. the phone's hotspot; it must be 2.4 GHz).
const bool USE_USB_LINK = false;
const unsigned long SERIAL_BAUD = USE_USB_LINK ? 921600 : 115200;

// Each pump has its own ESP32 and sensors, and each board needs its own ID.
// Set which board you are uploading to:
//   2 = healthy pump (ESP32-002, the healthy reference)
//   1 = faulty pump  (ESP32-001)
#define PUMP_BOARD 2

#if PUMP_BOARD == 1
const char *DEVICE_ID = "ESP32-001";
const char *DEVICE_NAME = "ESP32 Pump Sensor1";
const char *DEVICE_LOCATION = "Faulty Pump";
#else
const char *DEVICE_ID = "ESP32-002";
const char *DEVICE_NAME = "ESP32 Pump Sensor2";
const char *DEVICE_LOCATION = "Pump Area";
#endif

const uint8_t I2C_SDA_PIN = 21;
const uint8_t I2C_SCL_PIN = 22;
const uint8_t ADXL345_I2C_ADDRESS = 0x53;
const bool USE_HYDRAULIC_PRESSURE_SENSOR = false;
const uint8_t HYDRAULIC_PRESSURE_PIN = 32;
const uint8_t THERMOCOUPLE_SCK_PIN = 18;
const uint8_t THERMOCOUPLE_CS_PIN = 5;
const uint8_t THERMOCOUPLE_SO_PIN = 19;
const uint8_t WIFI_CONFIG_BUTTON_PIN = 0;
const unsigned long SAMPLE_INTERVAL_MS = 10;
const unsigned long TEMPERATURE_INTERVAL_MS = 1000;
const unsigned long WIFI_RECONNECT_TIMEOUT_MS = 15000;
const unsigned long BATCH_RETRY_INTERVAL_MS = 2000;  // wait after a failed request
// Samples are checked (frozen sensor, pump ON/OFF) in blocks of BATCH_SIZE.
const size_t BATCH_SIZE = 10;
// Sampling runs in its own task at a steady 100 Hz and fills a queue; loop()
// sends from the queue, so a slow network never pauses or gaps the sampling.
// Wi-Fi: about 1 s of data per request (one request per second, not ten).
const size_t SEND_SAMPLES_WIFI = 100;
const unsigned long SEND_INTERVAL_WIFI_MS = 1000;
const size_t MAX_SEND_SAMPLES = 600;          // most samples in one request (catch up after delays)
const size_t USB_SEND_SAMPLES = 20;           // per line over the USB link
const size_t SAMPLE_QUEUE_CAPACITY = 1000;    // 10 s of data if the network stalls
const unsigned long WIFI_STATUS_INTERVAL_MS = 5000;
const unsigned long HTTP_TIMEOUT_MS = 15000;  // Render free tier can take a while to wake up
const unsigned long TLS_HANDSHAKE_TIMEOUT_S = 15;  // library default 120 s blocks too long
const unsigned long REGISTER_RETRY_MS = 30000;
const unsigned long NETWORK_CHECK_INTERVAL_MS = 60000;

// ---- Pump ON/OFF detection from vibration -------------------------------
// Gravity is removed with a slow moving average, and the remaining (dynamic)
// acceleration is turned into an RMS value for each batch. Values below the
// OFF threshold are sensor noise, so the pump is reported as OFF and the
// server zeroes the vibration axes.
// TUNING: watch "Vibration RMS" in the Serial Monitor with the pump OFF and
// then ON. Set ON a bit above the OFF-noise level and well below the running level.
// ADXL345 noise with the pump OFF is ~0.008 g; a healthy running pump is ~0.015 g+.
const float PUMP_ON_RMS_THRESHOLD_G = 0.012f;
const float PUMP_OFF_RMS_THRESHOLD_G = 0.010f;  // lower than ON (hysteresis)
const uint8_t PUMP_ON_CONFIRM_BATCHES = 2;      // consecutive batches above ON
const uint8_t PUMP_OFF_CONFIRM_BATCHES = 3;     // consecutive batches below OFF
const float GRAVITY_BASELINE_ALPHA = 0.02f;     // smaller = slower baseline

// ---- Accelerometer health check ------------------------------------------
// A loose SDA/SCL/VCC/GND wire or a power glitch can leave the ADXL345 returning
// the same value forever (seen in testing: RMS decayed to 0.0000 g for 42 min and
// the pump was falsely reported OFF). A live ADXL345 always toggles a few LSBs,
// so a long run of bit-identical readings means the data is frozen.
const uint16_t ACCEL_FROZEN_SAMPLE_LIMIT = 50;           // 0.5 s of identical samples
const unsigned long ACCEL_RECOVERY_INTERVAL_MS = 2000;   // retry re-init every 2 s
const uint8_t ADXL345_EXPECTED_DEVICE_ID = 0xE5;
const uint8_t ADXL345_POWER_CTL_MEASURE_BIT = 0x08;


const float ADC_REFERENCE_VOLTAGE = 3.3f;
const int ADC_MAX_READING = 4095;
const float GRAVITY_MS2 = 9.80665f;
const float BAR_TO_PSI = 14.5038f;
const float PRESSURE_SENSOR_MIN_VOLTAGE = 0.5f;
const float PRESSURE_SENSOR_MAX_VOLTAGE = 4.5f;
const float PRESSURE_SENSOR_MIN_BAR = 0.0f;
const float PRESSURE_SENSOR_MAX_BAR = 10.0f;
const float PRESSURE_VOLTAGE_DIVIDER_SCALE = 1.0f;

struct Sample {
  float x;
  float y;
  float z;
  int pressureRaw;
  float pressurePinVoltage;
  float pressureSensorVoltage;
  float pressureBar;
  float pressurePsi;
  float temperatureC;
  bool pumpRunning;      // pump state when this sample's block was checked
  float vibrationRmsG;   // that block's vibration RMS
};

Adafruit_ADXL345_Unified adxl = Adafruit_ADXL345_Unified(12345);
MAX6675 thermocouple(THERMOCOUPLE_SCK_PIN, THERMOCOUPLE_CS_PIN, THERMOCOUPLE_SO_PIN);
Sample samples[BATCH_SIZE];  // block being sampled (sampler task only)
size_t sampleCount = 0;
unsigned long lastTemperatureReadAt = 0;
unsigned long lastBatchAttemptAt = 0;

// Checked samples waiting to be sent: a ring buffer shared by the sampler task
// and loop(), guarded by queueLock. When full, the oldest samples are dropped.
Sample sampleQueue[SAMPLE_QUEUE_CAPACITY];
size_t queueHead = 0;  // index of the oldest sample
size_t queueCount = 0;
volatile uint32_t droppedSamples = 0;
portMUX_TYPE queueLock = portMUX_INITIALIZER_UNLOCKED;
Sample sendBuffer[MAX_SEND_SAMPLES];
unsigned long lastSendAt = 0;
unsigned long lastWifiStatusAt = 0;
uint32_t samplesSent = 0;
int lastHttpStatus = 0;
unsigned long lastHttpMs = 0;
bool lastSendFailed = false;
uint8_t consecutiveSendFailures = 0;
unsigned long lastRegisterAttemptAt = 0;
unsigned long lastNetworkCheckAt = 0;
float latestTemperatureC = 0.0f;
bool deviceRegistered = false;
bool forceWifiSetup = false;
bool wifiHasConnected = false;

// Vibration / pump-state tracking
bool gravityBaselineReady = false;
float baselineX = 0.0f;
float baselineY = 0.0f;
float baselineZ = 0.0f;
float batchDynamicSumSq = 0.0f;
size_t batchDynamicCount = 0;
float latestVibrationRmsG = 0.0f;
bool pumpRunning = false;
uint8_t pumpOnStreak = 0;
uint8_t pumpOffStreak = 0;

// Accelerometer health tracking
uint8_t adxlAddress = ADXL345_I2C_ADDRESS;
bool accelHealthy = true;
unsigned long lastAccelRecoveryAt = 0;
uint16_t identicalSampleStreak = 0;
bool haveLastRawSample = false;
float lastRawX = 0.0f;
float lastRawY = 0.0f;
float lastRawZ = 0.0f;

// Reused so HTTPS keeps the connection alive instead of a new TLS handshake per batch.
WiFiClientSecure secureClient;
WiFiClient plainClient;

// Writes a whole line at once: the sampler task and loop() both print, and
// separate print() calls could interleave mid-line (and break "@@" lines).
void logLine(const String &line) {
  Serial.print(line + "\r\n");
}

String endpoint(const char *path) {
  return String(SERVER_BASE_URL) + path;
}

bool ensureWifiConnected() {
  if (WiFi.status() == WL_CONNECTED) {
    wifiHasConnected = true;
    return true;
  }

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);

  WiFiManager wifiManager;
  wifiManager.setConfigPortalTimeout(180);

  if (forceWifiSetup || digitalRead(WIFI_CONFIG_BUTTON_PIN) == LOW) {
    Serial.println("BOOT button held: clearing saved WiFi settings");
    wifiManager.resetSettings();
    WiFi.disconnect(true, true);
    delay(500);
    Serial.println("Starting WiFi setup portal: ESP32-Pump-Setup");
    const bool connected = wifiManager.startConfigPortal("ESP32-Pump-Setup");
    forceWifiSetup = false;
    wifiHasConnected = connected;
    return connected;
  }

  if (wifiHasConnected) {
    Serial.println("WiFi disconnected. Attempting to reconnect...");
    WiFi.reconnect();
    const unsigned long reconnectStartedAt = millis();
    while (WiFi.status() != WL_CONNECTED &&
           millis() - reconnectStartedAt < WIFI_RECONNECT_TIMEOUT_MS) {
      delay(250);
    }
    if (WiFi.status() == WL_CONNECTED) {
      Serial.print("WiFi reconnected. IP: ");
      Serial.println(WiFi.localIP());
      return true;
    }

    Serial.println("Reconnect timed out; trying saved WiFi settings...");
  } else {
    Serial.println("Initial WiFi connection; using WiFiManager...");
  }

  Serial.println("Connecting with saved WiFi settings...");
  Serial.println("If setup is needed, connect to ESP32-Pump-Setup");
  if (!wifiManager.autoConnect("ESP32-Pump-Setup")) {
    Serial.println("WiFi setup timed out or failed");
    return false;
  }

  Serial.print("WiFi connected. IP: ");
  Serial.println(WiFi.localIP());
  wifiHasConnected = true;
  return true;
}

bool bootButtonPressedDuringStartup() {
  Serial.println("Press BOOT now for WiFi setup (5 seconds)...");
  const unsigned long startedAt = millis();
  while (millis() - startedAt < 5000) {
    if (digitalRead(WIFI_CONFIG_BUTTON_PIN) == LOW) {
      return true;
    }
    delay(50);
  }
  return false;
}

// POST over Wi-Fi. The body is the payload string, or a stream of streamLength
// bytes (large requests are streamed: see SamplePayloadStream).
bool postOverWifi(const String &url, const String *payload, Stream *stream,
                  size_t streamLength, String *responseBody) {
  if (!ensureWifiConnected()) {
    return false;
  }

  HTTPClient http;
  if (url.startsWith("https://")) {
    // Render uses HTTPS. Certificate validation can be added for production deployment.
    secureClient.setInsecure();
    secureClient.setHandshakeTimeout(TLS_HANDSHAKE_TIMEOUT_S);
    http.begin(secureClient, url);
  } else {
    http.begin(plainClient, url);
  }
  http.setReuse(true);
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.setConnectTimeout(HTTP_TIMEOUT_MS);
  http.addHeader("Content-Type", "application/json");

  const int statusCode = stream != nullptr
                             ? http.sendRequest("POST", stream, streamLength)
                             : http.POST(*payload);
  const String body = http.getString();
  http.end();
  lastHttpStatus = statusCode;

  if (responseBody != nullptr) {
    *responseBody = body;
  }

  if (statusCode < 200 || statusCode >= 300) {
    logLine("POST " + url + " failed (HTTP " + String(statusCode) +
            "); keeping the samples for retry. " + body.substring(0, 160));
    return false;
  }
  return true;
}

bool postJson(const String &url, const String &payload, String *responseBody = nullptr) {
  if (USE_USB_LINK) {
    // One line per request, "@@<url> <json>"; serial_bridge.py posts it.
    String line;
    line.reserve(url.length() + payload.length() + 8);
    line += "@@";
    line += url;
    line += ' ';
    line += payload;
    logLine(line);
    return true;
  }
  return postOverWifi(url, &payload, nullptr, 0, responseBody);
}

bool registerDevice() {
  StaticJsonDocument<256> doc;
  doc["device_id"] = DEVICE_ID;
  doc["device_name"] = DEVICE_NAME;
  doc["location"] = DEVICE_LOCATION;

  String payload;
  serializeJson(doc, payload);

  String response;
  const bool ok = postJson(endpoint("/api/esp32/register"), payload, &response);
  if (ok) {
    Serial.println("Device registered");
  }
  return ok;
}

// Removes gravity (static tilt) and accumulates the dynamic part for this batch.
void trackVibration(const Sample &sample) {
  if (!gravityBaselineReady) {
    baselineX = sample.x;
    baselineY = sample.y;
    baselineZ = sample.z;
    gravityBaselineReady = true;
  }

  baselineX += GRAVITY_BASELINE_ALPHA * (sample.x - baselineX);
  baselineY += GRAVITY_BASELINE_ALPHA * (sample.y - baselineY);
  baselineZ += GRAVITY_BASELINE_ALPHA * (sample.z - baselineZ);

  const float dx = sample.x - baselineX;
  const float dy = sample.y - baselineY;
  const float dz = sample.z - baselineZ;
  batchDynamicSumSq += (dx * dx) + (dy * dy) + (dz * dz);
  batchDynamicCount++;
}

// Called once per full batch: decides ON/OFF with hysteresis so noise can't flip it.
void updatePumpState() {
  if (batchDynamicCount == 0) {
    return;
  }

  latestVibrationRmsG = sqrtf(batchDynamicSumSq / batchDynamicCount);
  batchDynamicSumSq = 0.0f;
  batchDynamicCount = 0;

  if (latestVibrationRmsG >= PUMP_ON_RMS_THRESHOLD_G) {
    pumpOffStreak = 0;
    if (pumpOnStreak < 255) pumpOnStreak++;
  } else if (latestVibrationRmsG <= PUMP_OFF_RMS_THRESHOLD_G) {
    pumpOnStreak = 0;
    if (pumpOffStreak < 255) pumpOffStreak++;
  } else {
    // Between thresholds: keep the current state.
    pumpOnStreak = 0;
    pumpOffStreak = 0;
  }

  if (!pumpRunning && pumpOnStreak >= PUMP_ON_CONFIRM_BATCHES) {
    pumpRunning = true;
    logLine("Pump state: RUNNING");
  } else if (pumpRunning && pumpOffStreak >= PUMP_OFF_CONFIRM_BATCHES) {
    pumpRunning = false;
    logLine("Pump state: OFF");
  }

  logLine("Vibration RMS: " + String(latestVibrationRmsG, 4) + " g | pump " +
          (pumpRunning ? "RUNNING" : "OFF"));
}

void readAcceleration(Sample &sample) {
  sensors_event_t event;
  adxl.getEvent(&event);


  sample.x = event.acceleration.x / GRAVITY_MS2;
  sample.y = event.acceleration.y / GRAVITY_MS2;
  sample.z = event.acceleration.z / GRAVITY_MS2;

  // Count bit-identical consecutive readings (frozen-sensor detection).
  if (haveLastRawSample && sample.x == lastRawX && sample.y == lastRawY && sample.z == lastRawZ) {
    if (identicalSampleStreak < 65535) identicalSampleStreak++;
  } else {
    identicalSampleStreak = 0;
  }
  lastRawX = sample.x;
  lastRawY = sample.y;
  lastRawZ = sample.z;
  haveLastRawSample = true;

  trackVibration(sample);
}

// Clears the gravity baseline and per-batch state so a stale baseline from
// before a sensor fault can't show up as fake vibration afterwards.
void resetVibrationTracking() {
  gravityBaselineReady = false;
  batchDynamicSumSq = 0.0f;
  batchDynamicCount = 0;
  identicalSampleStreak = 0;
  haveLastRawSample = false;
  pumpOnStreak = 0;
  pumpOffStreak = 0;
}

// Returns a short reason if the accelerometer looks disconnected or frozen, else nullptr.
const char *accelerometerFaultReason() {
  if (adxl.getDeviceID() != ADXL345_EXPECTED_DEVICE_ID) {
    return "not responding on I2C (device ID mismatch)";
  }
  if ((adxl.readRegister(ADXL345_REG_POWER_CTL) & ADXL345_POWER_CTL_MEASURE_BIT) == 0) {
    return "fell back to standby (power glitch?)";
  }
  if (identicalSampleStreak >= ACCEL_FROZEN_SAMPLE_LIMIT) {
    return "readings frozen (identical samples)";
  }
  return nullptr;
}

// Re-initializes the I2C bus and the ADXL345 without blocking. Returns true on success.
bool recoverAdxl345() {
  logLine("ADXL345: re-initializing I2C bus and sensor...");
  Wire.end();
  Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);
  Wire.setClock(100000);

  const uint8_t otherAddress = (adxlAddress == 0x53) ? 0x1D : 0x53;
  const uint8_t candidateAddresses[] = {adxlAddress, otherAddress};
  for (uint8_t address : candidateAddresses) {
    if (adxl.begin(address)) {
      adxl.setRange(ADXL345_RANGE_16_G);
      adxl.setDataRate(ADXL345_DATARATE_100_HZ);
      adxlAddress = address;
      resetVibrationTracking();
      logLine("ADXL345: recovered, sampling resumed");
      return true;
    }
  }

  logLine("ADXL345: still not responding -> check SDA/SCL/VCC/GND wires (retrying)");
  return false;
}

float readHydraulicPressureBar(float sensorVoltage) {
  if (sensorVoltage <= PRESSURE_SENSOR_MIN_VOLTAGE) {
    return PRESSURE_SENSOR_MIN_BAR;
  }

  const float sensorRange = PRESSURE_SENSOR_MAX_VOLTAGE - PRESSURE_SENSOR_MIN_VOLTAGE;
  const float pressureRatio = (sensorVoltage - PRESSURE_SENSOR_MIN_VOLTAGE) / sensorRange;
  const float pressureBar = PRESSURE_SENSOR_MIN_BAR + (pressureRatio * (PRESSURE_SENSOR_MAX_BAR - PRESSURE_SENSOR_MIN_BAR));

  return constrain(pressureBar, PRESSURE_SENSOR_MIN_BAR, PRESSURE_SENSOR_MAX_BAR);
}

void readHydraulicPressure(Sample &sample) {
  sample.pressureRaw = analogRead(HYDRAULIC_PRESSURE_PIN);
  sample.pressurePinVoltage = (sample.pressureRaw * ADC_REFERENCE_VOLTAGE) / ADC_MAX_READING;
  sample.pressureSensorVoltage = sample.pressurePinVoltage * PRESSURE_VOLTAGE_DIVIDER_SCALE;
  sample.pressureBar = readHydraulicPressureBar(sample.pressureSensorVoltage);
  sample.pressurePsi = sample.pressureBar * BAR_TO_PSI;
}

void readSensors(Sample &sample) {
  readAcceleration(sample);
  if (USE_HYDRAULIC_PRESSURE_SENSOR) {
    readHydraulicPressure(sample);
  }

  const unsigned long now = millis();
  if (now - lastTemperatureReadAt >= TEMPERATURE_INTERVAL_MS) {
    const float reading = thermocouple.readCelsius();
    if (!isnan(reading)) {
      latestTemperatureC = reading;
    }
    lastTemperatureReadAt = now;
  }
  sample.temperatureC = latestTemperatureC;
}

void enqueueSamples(const Sample *block, size_t count) {
  portENTER_CRITICAL(&queueLock);
  for (size_t i = 0; i < count; i++) {
    if (queueCount == SAMPLE_QUEUE_CAPACITY) {
      // Network down for a while: drop the oldest sample.
      queueHead = (queueHead + 1) % SAMPLE_QUEUE_CAPACITY;
      queueCount--;
      droppedSamples++;
    }
    sampleQueue[(queueHead + queueCount) % SAMPLE_QUEUE_CAPACITY] = block[i];
    queueCount++;
  }
  portEXIT_CRITICAL(&queueLock);
}

size_t queuedSamples() {
  portENTER_CRITICAL(&queueLock);
  const size_t count = queueCount;
  portEXIT_CRITICAL(&queueLock);
  return count;
}

// Copies up to maxCount of the oldest queued samples; they stay queued until sent.
size_t peekSamples(Sample *out, size_t maxCount) {
  portENTER_CRITICAL(&queueLock);
  const size_t count = queueCount < maxCount ? queueCount : maxCount;
  for (size_t i = 0; i < count; i++) {
    out[i] = sampleQueue[(queueHead + i) % SAMPLE_QUEUE_CAPACITY];
  }
  portEXIT_CRITICAL(&queueLock);
  return count;
}

// Removes the sent samples (some may already be gone if the queue overflowed).
void removeSentSamples(size_t count, uint32_t droppedBefore) {
  portENTER_CRITICAL(&queueLock);
  const size_t droppedMeanwhile = droppedSamples - droppedBefore;
  const size_t alreadyGone = droppedMeanwhile < count ? droppedMeanwhile : count;
  size_t remove = count - alreadyGone;
  if (remove > queueCount) remove = queueCount;
  queueHead = (queueHead + remove) % SAMPLE_QUEUE_CAPACITY;
  queueCount -= remove;
  portEXIT_CRITICAL(&queueLock);
}

// The /api/esp32/data JSON for a batch of samples, produced piece by piece as
// it is sent, so even a request of hundreds of samples (30-40 KB) needs no big
// block of memory. Pieces: the header, one per sample, and the closing "]}".
class SamplePayloadStream : public Stream {
 public:
  SamplePayloadStream(const Sample *batch, size_t count) : batch_(batch), count_(count) {
    for (size_t i = 0; i < count_ + 2; i++) {
      total_ += formatPiece(i, piece_);
    }
  }

  size_t totalLength() const { return total_; }
  int available() override { return (int)(total_ - position_); }
  int read() override {
    if (!loadPiece()) return -1;
    position_++;
    return (uint8_t)piece_[pieceOffset_++];
  }
  int peek() override {
    if (!loadPiece()) return -1;
    return (uint8_t)piece_[pieceOffset_];
  }
  size_t write(uint8_t) override { return 0; }

 private:
  static const size_t PIECE_SIZE = 320;
  const Sample *batch_;
  size_t count_;
  size_t total_ = 0;
  size_t position_ = 0;
  size_t nextPiece_ = 0;
  size_t pieceLength_ = 0;
  size_t pieceOffset_ = 0;
  char piece_[PIECE_SIZE];

  bool loadPiece() {
    while (pieceOffset_ >= pieceLength_) {
      if (nextPiece_ >= count_ + 2) return false;
      pieceLength_ = formatPiece(nextPiece_++, piece_);
      pieceOffset_ = 0;
    }
    return true;
  }

  static void number(char *out, size_t size, const char *format, float value) {
    if (isnan(value) || isinf(value)) {
      snprintf(out, size, "null");
    } else {
      snprintf(out, size, format, value);
    }
  }

  size_t formatPiece(size_t index, char *out) const {
    if (index == 0) {
      const Sample &latest = batch_[count_ - 1];
      char rms[20];
      number(rms, sizeof(rms), "%.4f", latest.vibrationRmsG);
      return snprintf(out, PIECE_SIZE,
                      "{\"device_id\":\"%s\",\"pump_running\":%s,\"vibration_rms_g\":%s,\"samples\":[",
                      DEVICE_ID, latest.pumpRunning ? "true" : "false", rms);
    }
    if (index == count_ + 1) {
      return snprintf(out, PIECE_SIZE, "]}");
    }
    const Sample &sample = batch_[index - 1];
    char x[20], y[20], z[20], temperature[20], rms[20];
    number(x, sizeof(x), "%.5f", sample.x);
    number(y, sizeof(y), "%.5f", sample.y);
    number(z, sizeof(z), "%.5f", sample.z);
    number(temperature, sizeof(temperature), "%.2f", sample.temperatureC);
    number(rms, sizeof(rms), "%.4f", sample.vibrationRmsG);
    int length = snprintf(out, PIECE_SIZE,
                          "%s{\"x\":%s,\"y\":%s,\"z\":%s,\"temperature_c\":%s,\"pump_running\":%s,\"vibration_rms_g\":%s",
                          index > 1 ? "," : "", x, y, z, temperature,
                          sample.pumpRunning ? "true" : "false", rms);
    if (USE_HYDRAULIC_PRESSURE_SENSOR) {
      char voltage[20], sensorVoltage[20], bar[20], psi[20];
      number(voltage, sizeof(voltage), "%.4f", sample.pressurePinVoltage);
      number(sensorVoltage, sizeof(sensorVoltage), "%.4f", sample.pressureSensorVoltage);
      number(bar, sizeof(bar), "%.4f", sample.pressureBar);
      number(psi, sizeof(psi), "%.4f", sample.pressurePsi);
      length += snprintf(out + length, PIECE_SIZE - length,
                         ",\"pressure_raw\":%d,\"pressure_pin_voltage\":%s,\"pressure_sensor_voltage\":%s,"
                         "\"pressure_bar\":%s,\"pressure_psi\":%s",
                         sample.pressureRaw, voltage, sensorVoltage, bar, psi);
    }
    length += snprintf(out + length, PIECE_SIZE - length, "}");
    return length;
  }
};

// Sends up to maxCount of the oldest queued samples; they are removed only
// after the server (or the USB link) accepted them.
bool sendQueuedSamples(size_t maxCount) {
  const uint32_t droppedBefore = droppedSamples;
  const size_t count = peekSamples(sendBuffer, maxCount);
  if (count == 0) {
    return true;
  }
  const String url = endpoint("/api/esp32/data");
  SamplePayloadStream body(sendBuffer, count);
  const unsigned long startedAt = millis();
  bool ok;
  if (USE_USB_LINK) {
    if (Serial.availableForWrite() < (int)(url.length() + body.totalLength() + 8)) {
      return false;  // serial buffer busy: try again in a moment, never block
    }
    String payload;
    payload.reserve(body.totalLength());
    for (int c = body.read(); c >= 0; c = body.read()) {
      payload += (char)c;
    }
    ok = postJson(url, payload);
  } else {
    ok = postOverWifi(url, nullptr, &body, body.totalLength(), nullptr);
  }
  lastHttpMs = millis() - startedAt;
  lastSendAt = millis();
  if (ok) {
    removeSentSamples(count, droppedBefore);
    samplesSent += count;
    consecutiveSendFailures = 0;
  } else if (consecutiveSendFailures < 255) {
    consecutiveSendFailures++;
  }
  return ok;
}

// Whether the Wi-Fi (e.g. the phone's hotspot) has working internet and the
// server's name resolves; printed at start-up and when sending keeps failing.
void checkNetwork() {
  lastNetworkCheckAt = millis();
  String host = String(SERVER_BASE_URL);
  host.replace("https://", "");
  host.replace("http://", "");
  const int slash = host.indexOf('/');
  if (slash >= 0) host = host.substring(0, slash);
  const int colon = host.indexOf(':');
  if (colon >= 0) host = host.substring(0, colon);

  unsigned long startedAt = millis();
  IPAddress serverIp;
  const bool resolved = WiFi.hostByName(host.c_str(), serverIp) == 1;
  String line = "[Net] server name " + host + ": " +
                (resolved ? "resolves to " + serverIp.toString() : String("NOT resolved")) +
                " (" + String(millis() - startedAt) + " ms)";

  startedAt = millis();
  HTTPClient http;
  http.setConnectTimeout(8000);
  http.setTimeout(8000);
  int code = -1;
  if (http.begin(plainClient, "http://connectivitycheck.gstatic.com/generate_204")) {
    code = http.GET();
    http.end();
  }
  line += " | internet: " + (code == 204 ? String("OK") : "NOT working (" + String(code) + ")") +
          " (" + String(millis() - startedAt) + " ms)";
  logLine(line);
}

// Every few seconds in Wi-Fi mode: which network, signal, and how sending goes.
void printWifiStatus() {
  String line = "[WiFi] ";
  if (WiFi.status() == WL_CONNECTED) {
    line += "connected to \"" + WiFi.SSID() + "\", IP " + WiFi.localIP().toString() +
            ", signal " + String(WiFi.RSSI()) + " dBm";
  } else {
    line += "NOT connected";
  }
  line += " | sent " + String(samplesSent) + ", queued " + String(queuedSamples()) +
          ", dropped " + String((uint32_t)droppedSamples) + " | last HTTP " +
          String(lastHttpStatus) + " in " + String(lastHttpMs) + " ms";
  logLine(line);
}

// Takes a sample every SAMPLE_INTERVAL_MS, checks each block of BATCH_SIZE
// (accelerometer fault, pump ON/OFF) and queues it. Its own task, at a higher
// priority than loop(), so sending (Wi-Fi or USB) never delays a sample.
void samplerTask(void *) {
  TickType_t lastWake = xTaskGetTickCount();
  const TickType_t period = pdMS_TO_TICKS(SAMPLE_INTERVAL_MS);
  for (;;) {
    vTaskDelayUntil(&lastWake, period);
    const unsigned long now = millis();

    // While the accelerometer is faulted, don't sample or send fake data; keep
    // trying to re-initialize it.
    if (!accelHealthy) {
      if (now - lastAccelRecoveryAt >= ACCEL_RECOVERY_INTERVAL_MS) {
        lastAccelRecoveryAt = now;
        accelHealthy = recoverAdxl345();
      }
      continue;
    }

    readSensors(samples[sampleCount]);
    sampleCount++;
    if (sampleCount < BATCH_SIZE) {
      continue;
    }

    const char *faultReason = accelerometerFaultReason();
    if (faultReason != nullptr) {
      logLine(String("ADXL345 fault: ") + faultReason + " -> discarding batch");
      sampleCount = 0;
      resetVibrationTracking();
      accelHealthy = false;
      lastAccelRecoveryAt = now - ACCEL_RECOVERY_INTERVAL_MS;  // try right away
      continue;
    }

    updatePumpState();
    for (size_t i = 0; i < sampleCount; i++) {
      samples[i].pumpRunning = pumpRunning;
      samples[i].vibrationRmsG = latestVibrationRmsG;
    }
    enqueueSamples(samples, sampleCount);
    sampleCount = 0;
  }
}

// Lists every device that answers on the I2C bus (helps diagnose wiring).
void scanI2cBus() {
  Serial.println("Scanning I2C bus...");
  uint8_t found = 0;
  for (uint8_t address = 1; address < 127; address++) {
    Wire.beginTransmission(address);
    if (Wire.endTransmission() == 0) {
      Serial.print("  Found I2C device at 0x");
      if (address < 16) Serial.print("0");
      Serial.println(address, HEX);
      found++;
    }
  }
  if (found == 0) {
    Serial.println("  No I2C devices found -> check wiring/power (see below)");
  }
}

void setupAdxl345() {
  Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);
  Wire.setClock(100000);  // 100 kHz is more tolerant of long wires / weak pull-ups

  Serial.println("Initializing ADXL345...");

  // ADXL345 address: SDO pin to GND = 0x53, SDO pin to 3.3V = 0x1D.
  const uint8_t candidateAddresses[] = {ADXL345_I2C_ADDRESS, 0x1D};
  uint8_t attempt = 0;
  while (true) {
    for (uint8_t address : candidateAddresses) {
      if (adxl.begin(address)) {
        adxl.setRange(ADXL345_RANGE_16_G);
        adxl.setDataRate(ADXL345_DATARATE_100_HZ);
        adxlAddress = address;
        Serial.print("ADXL345 connected at 0x");
        Serial.println(address, HEX);
        return;
      }
    }

    attempt++;
    Serial.print("ADXL345 not found (attempt ");
    Serial.print(attempt);
    Serial.println(").");
    scanI2cBus();
    Serial.println("Check: VCC->3.3V, GND->GND, SDA->GPIO21, SCL->GPIO22,");
    Serial.println("       CS->3.3V (required for I2C mode), SDO->GND (0x53).");
    Serial.println("Retrying in 3 seconds...");
    delay(3000);
  }
}

void setup() {
  // Buffered output, so printing never delays the 100 Hz sampling.
  Serial.setTxBufferSize(8192);
  Serial.begin(SERIAL_BAUD);
  delay(1000);

  pinMode(WIFI_CONFIG_BUTTON_PIN, INPUT_PULLUP);

  if (USE_HYDRAULIC_PRESSURE_SENSOR) {
    analogReadResolution(12);
    analogSetPinAttenuation(HYDRAULIC_PRESSURE_PIN, ADC_11db);
  }
  delay(500);
  latestTemperatureC = thermocouple.readCelsius();
  lastTemperatureReadAt = millis();

  setupAdxl345();
  if (USE_USB_LINK) {
    Serial.println("USB link mode: data goes over the USB cable, Wi-Fi is not used");
  } else {
    Serial.print("Wi-Fi mode: sending to ");
    Serial.println(SERVER_BASE_URL);
    forceWifiSetup = bootButtonPressedDuringStartup();
    if (forceWifiSetup) {
      WiFi.disconnect(true, true);
    }
    ensureWifiConnected();
    // Wi-Fi power saving delays incoming packets by 100-300 ms, which made each
    // POST take seconds.
    WiFi.setSleep(false);
    printWifiStatus();
    checkNetwork();
  }

  // Sampling task on core 1 (with loop()), at a higher priority than loop().
  // Started before registering, so a slow network never delays the sampling.
  xTaskCreatePinnedToCore(samplerTask, "sampler", 6144, nullptr, 3, nullptr, 1);

  lastRegisterAttemptAt = millis();
  deviceRegistered = registerDevice();
}

void loop() {
  const unsigned long now = millis();
  // Registration is retried now and then; data is sent meanwhile (the server
  // also accepts data from a device it already knows).
  if (!deviceRegistered && now - lastRegisterAttemptAt >= REGISTER_RETRY_MS) {
    lastRegisterAttemptAt = now;
    deviceRegistered = registerDevice();
    return;
  }

  if (!USE_USB_LINK && now - lastWifiStatusAt >= WIFI_STATUS_INTERVAL_MS) {
    lastWifiStatusAt = now;
    printWifiStatus();
  }
  if (!USE_USB_LINK && consecutiveSendFailures >= 3 &&
      now - lastNetworkCheckAt >= NETWORK_CHECK_INTERVAL_MS) {
    checkNetwork();
  }

  const size_t queued = queuedSamples();
  if (USE_USB_LINK) {
    if (queued < BATCH_SIZE) {
      delay(2);
      return;
    }
    sendQueuedSamples(USB_SEND_SAMPLES);
    return;
  }

  // Wi-Fi: about once per second, sooner if data piled up; wait after a failure.
  const bool due = queued >= SEND_SAMPLES_WIFI ||
                   (queued > 0 && now - lastSendAt >= SEND_INTERVAL_WIFI_MS);
  const bool backingOff = lastSendFailed && now - lastBatchAttemptAt < BATCH_RETRY_INTERVAL_MS;
  if (!due || backingOff) {
    delay(5);
    return;
  }
  lastBatchAttemptAt = now;
  lastSendFailed = !sendQueuedSamples(MAX_SEND_SAMPLES);
}
