#include <Arduino.h>
#include <ArduinoJson.h>
#include <Adafruit_SHT4x.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <Wire.h>
#include <esp_system.h>
#include <esp_sntp.h>
#include <math.h>
#include <time.h>
#include "trusted_root.hpp"

#if defined(CONNECTED_BUILDING_COMPILE_CHECK) || defined(CONNECTED_BUILDING_FULL_CHECK)
#include "secrets.example.hpp"
#else
#if __has_include("secrets.hpp")
#include "secrets.hpp"
#else
#error "Copy include/secrets.example.hpp to ignored include/secrets.hpp and provision it before a hardware build."
#endif
#endif

static_assert(SDA == 22 && SCL == 20 && NEOPIXEL_I2C_POWER == 2,
              "Select the Adafruit Feather ESP32 V2 board, not HUZZAH32.");

namespace {
constexpr uint32_t SAMPLE_MS = 60000;
constexpr uint32_t MAX_QUEUED_AGE_MS = 14 * 60000;
constexpr uint8_t QUEUE_CAPACITY = 10;
constexpr time_t EARLIEST_PLAUSIBLE_UTC = 1767225600;  // 2026-01-01, not a time source.
constexpr uint32_t MAX_NTP_AGE_SECONDS = 24 * 60 * 60;
struct PendingReading {
  char json[384];
  uint32_t acquiredMs;
  uint32_t sequence;
  char observedAt[25];
  float temperatureC;
  float humidityPct;
};
PendingReading queue[QUEUE_CAPACITY] = {};
uint8_t head = 0;
uint8_t count = 0;
uint32_t nextSequence = 0;
char bootId[37] = {};
uint32_t nextSampleMs = 0;
uint32_t nextUploadMs = 0;
uint32_t nextWifiMs = 0;
uint32_t nextSensorInitMs = 0;
uint32_t retryMs = 5000;
uint32_t droppedCount = 0;
volatile time_t lastNtpSync = 0;
bool sensorReady = false;
bool authStopped = false;
bool configurationReady = false;
Adafruit_SHT4x *sht4 = nullptr;

bool due(uint32_t now, uint32_t target) {
  return static_cast<int32_t>(now - target) >= 0;
}

void onTimeSync(struct timeval *tv) { lastNtpSync = tv->tv_sec; }

bool clockReady() {
  const time_t now = time(nullptr);
  const time_t sync = lastNtpSync;
  return sync >= EARLIEST_PLAUSIBLE_UTC && now >= sync &&
         static_cast<uint64_t>(now - sync) <= MAX_NTP_AGE_SECONDS;
}

bool safeIdentifier(const char *value) {
  const size_t length = strlen(value);
  if (length == 0 || length > 64) return false;
  for (size_t i = 0; i < length; ++i) {
    const char c = value[i];
    if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
          (c >= '0' && c <= '9') || c == '.' || c == '_' || c == '-')) return false;
  }
  return true;
}

void makeBootId() {
  uint8_t bytes[16];
  esp_fill_random(bytes, sizeof(bytes));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  snprintf(bootId, sizeof(bootId),
           "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
           bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], bytes[5], bytes[6], bytes[7],
           bytes[8], bytes[9], bytes[10], bytes[11], bytes[12], bytes[13], bytes[14], bytes[15]);
}

void popReading(const char *reason) {
  Serial.printf("Reading sequence %lu removed: %s.\n",
                static_cast<unsigned long>(queue[head].sequence), reason);
  head = (head + 1) % QUEUE_CAPACITY;
  --count;
}

void initialiseSensor(uint32_t now) {
  if (sensorReady || !due(now, nextSensorInitMs)) return;
  nextSensorInitMs = now + 30000;
  delete sht4;
  sht4 = new Adafruit_SHT4x();
  sensorReady = sht4->begin(&Wire);
  if (!sensorReady) {
    Serial.println("SHT40 unavailable. Check the QT cable. No substitute readings generated.");
    return;
  }
  const uint32_t serial = sht4->readSerial();
  if (serial == 0) {
    sensorReady = false;
    Serial.println("SHT4x serial read failed or returned zero. Acquisition paused.");
    return;
  }
  // Serial stays on the private USB console. No unique sensor ID is published.
  Serial.printf("SHT4x detected, serial %08lx.\n", static_cast<unsigned long>(serial));
  sht4->setPrecision(SHT4X_HIGH_PRECISION);
  sht4->setHeater(SHT4X_NO_HEATER);
}

void acquireReading(uint32_t now) {
  if (!due(now, nextSampleMs)) return;
  nextSampleMs = now + SAMPLE_MS;  // Missed intervals are never backfilled.
  if (!sensorReady || !clockReady()) {
    Serial.println("Sample skipped: sensor or NTP-confirmed UTC unavailable.");
    return;
  }
  if (count == QUEUE_CAPACITY) {
    ++droppedCount;
    Serial.printf("Queue full. Sample skipped; total full-queue drops %lu.\n",
                  static_cast<unsigned long>(droppedCount));
    return;
  }
  if (nextSequence > 2147483647UL) {
    Serial.println("Sequence exhausted. Restart to begin a new boot ID.");
    return;
  }
  sensors_event_t humidity, temperature;
  // This joint API checks both SHT4x CRCs and reports I2C failures.
  if (!sht4->getEvent(&humidity, &temperature)) {
    sensorReady = false;
    Serial.println("SHT4x I2C/CRC read failed. No reading enqueued.");
    return;
  }
  if (!isfinite(temperature.temperature) || !isfinite(humidity.relative_humidity) ||
      temperature.temperature < -40 || temperature.temperature > 85 ||
      humidity.relative_humidity < 0 || humidity.relative_humidity > 100) {
    Serial.println("Sensor value outside ingest bounds. No reading enqueued.");
    return;
  }
  const time_t observed = time(nullptr);
  struct tm utc;
  gmtime_r(&observed, &utc);
  char observedAt[25];
  strftime(observedAt, sizeof(observedAt), "%Y-%m-%dT%H:%M:%S.000Z", &utc);
  PendingReading &reading = queue[(head + count) % QUEUE_CAPACITY];
  const int written = snprintf(
      reading.json, sizeof(reading.json),
      "{\"schema_version\":1,\"device_id\":\"%s\",\"boot_id\":\"%s\",\"sequence\":%lu,"
      "\"observed_at\":\"%s\",\"temperature_c\":%.3f,\"relative_humidity_pct\":%.3f,\"interval_seconds\":60}",
      SensorConfig::DEVICE_ID, bootId, static_cast<unsigned long>(nextSequence), observedAt,
      temperature.temperature, humidity.relative_humidity);
  if (written <= 0 || written >= static_cast<int>(sizeof(reading.json))) {
    Serial.println("Payload exceeded buffer. No reading enqueued.");
    return;
  }
  reading.acquiredMs = now;
  reading.sequence = nextSequence++;
  memcpy(reading.observedAt, observedAt, sizeof(observedAt));
  reading.temperatureC = roundf(temperature.temperature * 1000.0f) / 1000.0f;
  reading.humidityPct = roundf(humidity.relative_humidity * 1000.0f) / 1000.0f;
  ++count;
  Serial.printf("Measured %.3f degC, %.3f %%RH at %s; queued %u.\n",
                temperature.temperature, humidity.relative_humidity, observedAt, count);
}

void scheduleRetry(uint32_t now, uint32_t retryAfterSeconds) {
  uint32_t wait = retryMs + (esp_random() % 1000);
  if (retryAfterSeconds <= 3600 && retryAfterSeconds * 1000UL > wait) {
    wait = retryAfterSeconds * 1000UL;
  }
  nextUploadMs = now + wait;
  retryMs = min(retryMs * 2UL, 60000UL);
}

void uploadReading(uint32_t now) {
  while (count && now - queue[head].acquiredMs > MAX_QUEUED_AGE_MS) {
    popReading("expired before acknowledgement; not a successful measurement receipt");
  }
  if (!count || authStopped || !due(now, nextUploadMs) ||
      WiFi.status() != WL_CONNECTED || !clockReady()) return;

  WiFiClientSecure tls;
  tls.setCACert(CONNECTED_BUILDING_ROOT_CA);
  tls.setHandshakeTimeout(15);
  HTTPClient http;
  http.setConnectTimeout(15000);
  http.setTimeout(25000);  // A cold free host may need several exact retries.
  http.setFollowRedirects(HTTPC_DISABLE_FOLLOW_REDIRECTS);  // Never forward the token elsewhere.
  const String url = String(SensorConfig::API_ORIGIN) + "/api/live-sensor/readings";
  if (!http.begin(tls, url)) {
    Serial.println("HTTPS setup failed. Queue retained.");
    scheduleRetry(millis(), 0);
    return;
  }
  const char *headers[] = {"Retry-After"};
  http.collectHeaders(headers, 1);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("Authorization", String("Bearer ") + SensorConfig::INGEST_TOKEN);
  const int status = http.POST(reinterpret_cast<uint8_t *>(queue[head].json), strlen(queue[head].json));
  const long retryHeader = http.header("Retry-After").toInt();
  const uint32_t retryAfter = retryHeader > 0 ? static_cast<uint32_t>(retryHeader) : 0;
  bool acknowledged = false;
  if ((status == 201 || status == 200) && http.getSize() <= 2048) {
    StaticJsonDocument<1536> response;
    const DeserializationError error = deserializeJson(response, http.getString());
    const char *state = response["state"] | "";
    const char *observedAt = response["receipt"]["observed_at"] | "";
    const JsonVariantConst temperature = response["receipt"]["temperature_c"];
    const JsonVariantConst humidity = response["receipt"]["relative_humidity_pct"];
    acknowledged = !error && ((status == 201 && strcmp(state, "accepted") == 0) ||
        (status == 200 && strcmp(state, "duplicate") == 0)) &&
        strcmp(observedAt, queue[head].observedAt) == 0 && temperature.is<float>() &&
        humidity.is<float>() && fabsf(temperature.as<float>() - queue[head].temperatureC) < 0.001f &&
        fabsf(humidity.as<float>() - queue[head].humidityPct) < 0.001f;
  }
  http.end();
  tls.stop();
  if (acknowledged) {
    popReading(status == 201 ? "server accepted" : "server acknowledged exact duplicate");
    retryMs = 5000;
    nextUploadMs = millis() + 1000;
  } else if (status == 401 || status == 403) {
    authStopped = true;
    Serial.println("Authorisation refused. Uploads stopped until private configuration is corrected and device restarted.");
  } else if (status == 409 || status == 422) {
    Serial.printf("Server rejected payload (HTTP %d). Record not counted as accepted.\n", status);
    popReading("rejected, inspect API contract and UTC");
    nextUploadMs = millis() + 5000;
  } else {
    // Transport, TLS, host sleep and 429/503 failures retain byte-identical JSON.
    Serial.printf("Upload not acknowledged (HTTP %d). Queue retained.\n", status);
    scheduleRetry(millis(), retryAfter);
  }
}
}  // namespace

void setup() {
  Serial.begin(115200);
  delay(500);
#ifdef CONNECTED_BUILDING_COMPILE_CHECK
  Serial.println("Compilation-check build only. No acquisition or network requests.");
  return;
#else
  configurationReady = SensorConfig::PROVISIONED && safeIdentifier(SensorConfig::DEVICE_ID) &&
      strlen(SensorConfig::INGEST_TOKEN) >= 32 && strlen(SensorConfig::WIFI_SSID) > 0 &&
      String(SensorConfig::API_ORIGIN) == "https://connected-building-evidence.onrender.com";
  if (!configurationReady) {
    Serial.println("Private configuration incomplete. Device remains idle.");
    return;
  }
  pinMode(NEOPIXEL_I2C_POWER, OUTPUT);
  digitalWrite(NEOPIXEL_I2C_POWER, HIGH);
  delay(10);
  Wire.begin(SDA, SCL);
  Wire.setClock(100000);
  Wire.setTimeOut(100);
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(SensorConfig::WIFI_SSID, SensorConfig::WIFI_PASSWORD);
  makeBootId();  // Hardware RNG has Wi-Fi entropy; a fresh UUID for every boot.
  sntp_set_time_sync_notification_cb(onTimeSync);
  configTime(0, 0, "pool.ntp.org", "time.cloudflare.com", "time.google.com");
  nextSampleMs = millis() + 10000;
  nextWifiMs = millis() + 30000;
  Serial.println("Started. Waiting for SHT40, authorised Wi-Fi and NTP-confirmed UTC.");
#endif
}

void loop() {
  if (!configurationReady) { delay(1000); return; }
  const uint32_t now = millis();
  if (WiFi.status() != WL_CONNECTED && due(now, nextWifiMs)) {
    WiFi.reconnect();
    nextWifiMs = now + 30000;
    Serial.println("Attempting Wi-Fi reconnection; credentials stay private.");
  }
  initialiseSensor(now);
  acquireReading(now);
  uploadReading(millis());
  delay(10);
}
