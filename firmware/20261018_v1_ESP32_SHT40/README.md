# ESP32/SHT40 environmental station

Developed by Meysam Bayat for Connected Building.

This firmware reads a real Sensirion SHT40 over I²C and submits temperature and relative humidity to the application over certificate-validated HTTPS. The selected hardware is an **Adafruit Feather ESP32 V2, product 5400, with PCB antenna**, and **Adafruit SHT40 STEMMA QT breakout, product 4885**. A socket-to-socket JST-SH four-pin cable connects them without soldering. See the [equipment and commissioning guide](../../docs/20261018_v1_Real_Sensor_Setup.md).

## Build and provision

Install [PlatformIO Core](https://docs.platformio.org/en/latest/core/installation/index.html), version 6.1.18, or use the free PlatformIO extension for Visual Studio Code. Run these commands from this directory.

```text
pio run --environment compile_check
pio run --environment compile_full_check
```

These check compilation using public placeholder configuration. `compile_check` explicitly returns before acquisition setup. `compile_full_check` compiles the actual provisioning, Wi-Fi, NTP and sensor setup branch with the example's `PROVISIONED = false`. Both binaries remain idle if uploaded. Neither verifies hardware, a radio connection or sensor measurements.

Both environments passed local compilation on 18 October 2026. The [verification record](20261018_v1_Compile_Verification.json) records the exact source hashes, build tools, binary hashes and the separate desktop TLS check. It contains no sensor measurements or hardware-test claim.

For the board that will actually collect measurements:

1. Copy `include/secrets.example.hpp` to `include/secrets.hpp`. Git ignores the copy.
2. Set the authorised 2.4 GHz Wi-Fi credentials. A permitted home network or a compatible phone hotspot is suitable; this firmware does not implement enterprise Wi-Fi or captive-portal sign-in.
3. Privately configure a random ingest token of at least 32 characters, and the same device identifier in the server's `TWIN_SENSOR_INGEST_TOKEN` and `TWIN_SENSOR_DEVICE_ID` environment variables. Put matching values in `secrets.hpp`. The server setup must be completed by the application owner. Do not paste the token in a URL, browser page, issue, screenshot or repository.
4. Set `PROVISIONED = true` in the private copy. Leave the public example unchanged.
5. Build and upload the **hardware environment**, selecting the actual USB serial port:

```text
pio run --environment feather_esp32_v2
pio run --environment feather_esp32_v2 --target upload --upload-port COM7
pio device monitor --baud 115200 --port COM7
```

`COM7` is an example, not a detected port. Use `pio device list` to identify the connected board. If a serial driver is needed, use the manufacturer's [driver instructions](https://learn.adafruit.com/adafruit-esp32-feather-v2/arduino-ide-setup), not an unofficial download site. Keep serial logs private because they contain sensor serial numbers and measured conditions. Never publish a firmware binary built with real credentials.

Before the owner enables ingestion on Render, verify that Uvicorn's `FORWARDED_ALLOW_IPS` trusts only the actual hosting proxy addresses. Confirm an external HTTPS request is recognised as HTTPS through that trusted proxy, and that client-supplied forwarding headers alone cannot change the scheme. Do not set `FORWARDED_ALLOW_IPS=*` without an independently verified proxy-only network boundary. Leave the ingest credentials unset until this deployment check and the real hardware setup are ready.

## Acquisition and transport

- The Feather's QT supply is enabled through GPIO2. The board variant defines SDA as GPIO22 and SCL as GPIO20. I²C is set to 100 kHz. The manufacturer's pin names are used in the code and checked at compile time.
- The Adafruit SHT4x library is pinned to 1.0.5. Its joint `getEvent` API checks both CRCs and returns failures. The sensor is read at high precision with its heater disabled. The library applies its documented 0–100 %RH clipping after raw conversion. A failed serial read or zero serial blocks acquisition; the serial is printed only on the local USB console.
- There is one acquisition opportunity per 60 seconds. No measurement is substituted after an I²C/CRC failure, missing sensor or unsynchronised clock, and missed intervals are not backfilled.
- UTC must have been set by NTP during this boot and refreshed within 24 hours. The observation time has one-second resolution, serialised with a .000 millisecond suffix. The device sends the observation time; the server independently records reception time. NTP provides clock synchronisation, not a traceable calibration of the sensor or clock.
- At most ten actual readings are held in RAM. Each has a random UUID for the boot, a monotonically increasing sequence and immutable JSON. Retries preserve all those fields. A restart generates a new boot UUID and loses unsent RAM records. Full-queue and expiry losses are printed to the serial console.
- HTTPS validates the server hostname, chain and certificate validity against GTS Root R4. The public trust anchor came directly from [Google Trust Services](https://pki.goog/repo/certs/gtsr4.pem); its DER SHA-256 is `349dfa4058c5e263123b398ae795573c4e1313c83fe68f93556cd5e8031b3c7d`. It was checked against the live application's hostname on 18 October 2026 using a desktop TLS client. Certificate validation stays enabled; redirects are refused. If the host later changes certificate authority, check its chain and update the trusted root from the authority's official source.
- Timeouts and exponential retry delays are bounded. `Retry-After` in seconds is honoured up to one hour; current server values are at most 60 seconds. Records older than 14 minutes are dropped because new observations must reach the server within 15 minutes. HTTP 401/403 stops uploads until the owner corrects the private settings and restarts the device. HTTP 409/422 is recorded as rejection. Only a matching JSON receipt with state `accepted` or `duplicate` removes a record as acknowledged.

## API contract

`POST https://connected-building-evidence.onrender.com/api/live-sensor/readings` uses `Content-Type: application/json` and `Authorization: Bearer <private token>`. The payload contains only `schema_version` (1), `device_id`, `boot_id` (UUID), `sequence`, `observed_at` (UTC ISO 8601), `temperature_c`, `relative_humidity_pct`, and `interval_seconds` (60). The server accepts temperature from −40 to 85 °C and relative humidity from 0 to 100 %RH. It returns HTTP 201 for an accepted record, HTTP 200 for an identical normalised measurement with the same identity, and a receipt containing both observation and reception times. Backend tests use isolated temporary stores, never the public physical-sensor stream.

## Status and scope

The source and compilation checks prepare a physical experiment. Hardware commissioning, the first real sensor receipt, placement and comparison with a reference instrument still require a purchased or borrowed sensor and an authorised location. The environmental station is a separate site from the published 2017 IFC flat. Its reports must not be assigned to rooms in that source model. BMS integration and building controls are outside this firmware.

Dependencies keep their own licences: [Adafruit SHT4x](https://github.com/adafruit/Adafruit_SHT4X/tree/1.0.5), [BusIO](https://github.com/adafruit/Adafruit_BusIO), [Unified Sensor](https://github.com/adafruit/Adafruit_Sensor), [ArduinoJson](https://github.com/bblanchon/ArduinoJson/tree/v6.21.5), and [Espressif Arduino](https://github.com/espressif/arduino-esp32/tree/2.0.17). No dependency source code is copied into this directory.

