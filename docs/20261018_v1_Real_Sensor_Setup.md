# Real environmental sensor: equipment and commissioning

Prepared 18 October 2026. Developed by Meysam Bayat for Connected Building.

The first device experiment uses one USB-powered temperature and humidity sensor at a desk. The software and firmware builds are verified; the next stage is to obtain the equipment and commission the station on an authorised network. The published IFC dataset and new station records have separate identities.

## Recommended solderless kit

Prices include VAT and exclude delivery, checked against the retailer's individual product pages and current variant listings on 18 October 2026. Stock and prices can change. No purchase has been made.

| Quantity | Exact item | Price | Checked availability |
|---|---|---:|---|
| 1 | [Adafruit Feather ESP32 V2, PCB antenna, ADA5400](https://thepihut.com/products/adafruit-esp32-feather-v2-8mb-flash-2-mb-psram-stemma-qt) | £19.20 | Available |
| 1 | [Adafruit Sensirion SHT40 breakout, STEMMA QT/Qwiic, ADA4885](https://thepihut.com/products/adafruit-sensirion-sht40-temperature-humidity-sensor-stemma-qt-qwiic) | £5.70 | Available |
| 1 | [STEMMA QT/Qwiic JST-SH four-pin socket-to-socket cable, 200 mm, SKU 104965](https://thepihut.com/products/stemma-qt-qwiic-jst-sh-4-pin-cable-200mm-long) | £1.00 | In stock |
| 1 if needed | [USB-A to USB-C **data** cable, 50 cm, SKU 103482](https://thepihut.com/products/usb-a-to-usb-c-cable-1m?variant=37890096234691) | £2.50 | Available variant |
| | **Total with the 50 cm USB cable** | **£28.40** | Before delivery |

The board, sensor and QT cable total £25.90 if a suitable USB data cable is already available. The cheaper 15 cm USB cable and the 1 m variant were unavailable in the retailer's current variant listing. The 100 mm QT product page was also sold out, so the 200 mm cable is recommended. USB-C to USB-C data cables can be used when appropriate for the computer's ports. A charge-only USB cable cannot programme the board. The retailer confirms USB data support for the specified cable.

The manufacturer documents an onboard QT connector and USB-C programming, and the SHT40 breakout has matching QT sockets. These connections avoid soldering even when the separate header strips supplied with the products are not fitted. No breadboard, jumper wires, Raspberry Pi, battery or paid sensor subscription is required for the USB-powered experiment. Sources: [Feather product](https://www.adafruit.com/product/5400), [Feather pinouts](https://learn.adafruit.com/adafruit-esp32-feather-v2/pinouts), [SHT40 breakout guide](https://learn.adafruit.com/adafruit-sht40-temperature-humidity-sensor/overview).

## Connect it when the equipment is available

1. With USB disconnected, plug the four-pin QT cable into the Feather's **QT I2C** socket and either QT socket on the SHT40 breakout. Both ends are the same; align the keyed connectors and do not force them. Use the **small JST-SH four-pin sockets**, not the Feather's larger battery connector.
2. Put both boards on a dry, non-conductive surface. Keep the sensor separate from the warm microcontroller board, direct sunlight, radiators and computer exhaust. Leave its sensing opening exposed to room air. The 200 mm cable allows some separation. This is an initial placement proposal to be checked during commissioning, not a certified installation specification.
3. Connect the Feather to the computer with the USB data cable. The board supplies 3.3 V through QT. The firmware explicitly enables `NEOPIXEL_I2C_POWER` (GPIO2) and uses the Feather V2's SDA22/SCL20 pin assignment. See [manufacturer pinout](https://raw.githubusercontent.com/adafruit/Adafruit-ESP32-Feather-V2-PCB/main/Adafruit%20ESP32%20Feather%20V2%20Pinout.pdf).
4. Follow the [firmware provisioning and upload instructions](../firmware/20261018_v1_ESP32_SHT40/README.md). Use an authorised 2.4 GHz Wi-Fi network, or a phone hotspot configured for a compatible band and security mode. Phone data charges depend on the existing tariff. The initial firmware supports ordinary password-based Wi-Fi; it does not support university enterprise authentication or a web sign-in portal.
5. The owner configures the server's ingest token and device identifier only when the real device is ready. Keep the token, Wi-Fi credentials and exact location private. No address, floor plan or room occupancy is required for the public demonstration.

Before enabling the server token, verify the deployment's proxy configuration. Uvicorn's `FORWARDED_ALLOW_IPS` must trust the verified Render proxy addresses so that HTTPS is recognised correctly; a browser's arbitrary forwarding header must not establish that trust. Do not use a blanket `*` without verifying a proxy-only network boundary. A failed proxy/scheme check must be resolved before collecting public sensor reports. The credentials remain unset during preparation.

## First experiment and acceptance record

Record actual outcomes, including failures. The following are acceptance checks to perform, not results already obtained.

| Check | Evidence to retain |
|---|---|
| Board and sensor identity | Product references, local SHT4x serial and firmware source revision. Keep identifiers and credentials out of public screenshots. |
| First real reading | Private serial log showing successful I²C/CRC acquisition; matching app receipt containing observation time, reception time and the measured values. |
| Stable collection | At least 60 consecutive one-minute acquisition opportunities, recording actual accepted counts, gaps and rejected records. Do not fill missing values with invented data. |
| Network interruption | Disconnect only this device's Wi-Fi for two minutes, restore it and inspect acknowledgement of the retained records. Record exact duplicates and any losses. |
| Sensor disconnection | Remove USB power before unplugging the QT sensor, then restart without it. Confirm no substitute measurements are published. Reconnect with power removed and repeat the check. |
| Restart | Confirm a new boot identifier and sequence after reboot, and report that unsent RAM queue entries are lost at a power interruption. |
| Clock unavailable | On an isolated test network with NTP unavailable, confirm acquisition remains blocked until UTC synchronisation succeeds. |
| Reference comparison | Place a separate thermometer/hygrometer alongside the SHT40 and compare equilibrated readings at several times. Record both instruments, their specifications, location and time, sample count, differences and any correction applied. |

An inexpensive household reference instrument is useful for a reasonableness comparison, but this does not establish accredited calibration or an uncertainty traceable to national standards. Do not apply an unexplained offset merely to make the two displays match. Sensor accuracy figures, if quoted, must use the conditions in [Sensirion's SHT40 documentation](https://sensirion.com/products/catalog/SHT40), not treat a typical specification as an achieved field accuracy.

The live server records receipt time independently, rejects invalid values and stale new observations, and acknowledges exact duplicates. Authentication establishes possession of the device token; the physical provenance of measurements is evidenced by the documented commissioning procedure. Download `/api/live-sensor/export.csv` regularly during the experiment and retain raw observations and commissioning notes locally. The station retains at most seven days or 10,080 rows. The Render free service uses ephemeral storage, so a redeploy or host restart may erase the hosted station history earlier. See the app's runtime status before relying on continuity.

## Evidence this can add for the role

A completed commissioning record adds evidence of physical I²C acquisition, UTC handling, authenticated sensor-to-cloud transport, receipt traceability, fault handling and a reference-instrument comparison. A subsequent stage can connect the station to an IFC model of its actual measurement space and extend the building-performance study.

Until then, the accurate status is: **physical-sensor integration prepared; equipment and commissioning pending**.
