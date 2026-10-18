#pragma once

// Copy to secrets.hpp locally. That file is ignored by Git.
// These are placeholders. The compile_check binary refuses to run acquisition.
namespace SensorConfig {
constexpr bool PROVISIONED = false;  // Set true only in the private secrets.hpp.
constexpr char WIFI_SSID[] = "YOUR_AUTHORISED_2_4_GHZ_WIFI";
constexpr char WIFI_PASSWORD[] = "YOUR_WIFI_PASSWORD";
constexpr char API_ORIGIN[] = "https://connected-building-evidence.onrender.com";
constexpr char DEVICE_ID[] = "desk-sht40-01";
constexpr char INGEST_TOKEN[] = "REPLACE_WITH_PRIVATE_RANDOM_TOKEN_OF_AT_LEAST_32_CHARACTERS";
}  // namespace SensorConfig

