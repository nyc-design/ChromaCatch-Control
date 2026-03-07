# ESP32 Display Quick Test

Minimal firmware to verify the e-ink display is alive.

## Wiring used
- 3V3 -> VCC
- GND -> GND
- GPIO18 -> CLK
- GPIO17 -> DIN (MOSI)
- GPIO5 -> CS
- GPIO4 -> DC
- GPIO16 -> RST
- GPIO15 -> BUSY

## Flash
```bash
pio run -d esp32-display-test -t upload
```

## Serial monitor
```bash
pio device monitor -b 115200
```

The display refreshes every ~4 seconds with a counter and uptime.

## Panel driver note
- This test is set to `GxEPD2_213_B74` (Waveshare 2.13 V4, partial refresh panel).
- If your board revision differs, switch the driver in `src/main.cpp` to `GxEPD2_213_BN`.
