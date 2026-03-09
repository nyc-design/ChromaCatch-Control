#pragma once

#include <Arduino.h>

#if defined(CONFIG_IDF_TARGET_ESP32S3)
#include "USB.h"
#include "USBHID.h"

#if CONFIG_TINYUSB_HID_ENABLED

/// Nintendo Switch Pro Controller over USB HID.
/// Uses the real Pro Controller HID descriptor with vendor-defined report IDs
/// (0x30/0x21/0x81 input, 0x01/0x10/0x80 output) and handles:
///  - 0x80 USB-specific handshake (MAC exchange, connect/disconnect)
///  - 0x01 subcommand protocol (device info, SPI reads, mode set, etc.)
///  - 0x30 standard full input reports (~8ms cadence)
class SwitchProUSB : public USBHIDDevice {
public:
    SwitchProUSB();

    void begin();
    void end();
    bool write();
    void loop();

    // Gamepad state setters (8-bit axis, 0x80 = center)
    inline void buttons(uint16_t b) { _buttons = b; }
    void press(uint8_t b);
    void release(uint8_t b);
    inline void releaseAll() { _buttons = 0; }
    inline void leftXAxis(uint8_t a) { _lx = a; }
    inline void leftYAxis(uint8_t a) { _ly = a; }
    inline void rightXAxis(uint8_t a) { _rx = a; }
    inline void rightYAxis(uint8_t a) { _ry = a; }
    void dPad(uint8_t d);

    bool isConnected() const { return _connected; }

    // USBHIDDevice overrides
    uint16_t _onGetDescriptor(uint8_t* buffer) override;
    void _onOutput(uint8_t report_id, const uint8_t* buffer, uint16_t len) override;

private:
    void sendInputReport(uint8_t reportId, const uint8_t* data, size_t len);
    void sendStandardInputReport();
    void sendSimpleInputReport();
    void sendSubcommandReply(uint8_t subcmd, uint8_t ackByte, const uint8_t* data, size_t dataLen);
    void handleSpiFlashRead(const uint8_t* cmdData, uint16_t cmdLen);
    void handleUsbCommand(const uint8_t* data, uint16_t len);
    void handleSubcommand(const uint8_t* data, uint16_t len);
    void fillInputHeader(uint8_t* buf);
    void convertButtonsTo3Byte();
    static void packStick12bit(uint16_t x, uint16_t y, uint8_t out[3]);

    bool trySendReport(uint8_t reportId, const uint8_t* data, size_t len);
    void flushPendingReplies();

    USBHID _hid;
    uint32_t _lastReportMs = 0;
    uint8_t _timer = 0;
    bool _connected = false;

    // Reply queue: callbacks can't block, so buffer replies for loop() to flush.
    static constexpr size_t kMaxPending = 8;
    static constexpr size_t kReportBufLen = 48;
    struct PendingReport {
        uint8_t reportId;
        uint8_t data[kReportBufLen];
        size_t len;
    };
    PendingReport _pendingQueue[kMaxPending];
    volatile uint8_t _pendingCount = 0;

    // Minimum hold enforcement: PA uses 48ms hold + 24ms cooldown.
    // When a button is pressed, we prevent any release from taking effect
    // until at least kMinHoldMs has elapsed, ensuring the Switch sees the
    // pressed state across multiple 0x30 report cycles.
    static constexpr uint32_t kMinHoldMs = 48;
    uint32_t _lastPressMs = 0;       // millis() of last press() call
    uint16_t _pendingReleaseMask = 0; // buttons waiting to be released
    uint8_t _pendingDpad = 0xFF;      // pending dpad release (0xFF = none)

    // 8-bit axis state (0x80 = center, NSGamepad-compatible API)
    uint16_t _buttons = 0;
    uint8_t _dpad = 0x0F;  // centered
    uint8_t _lx = 0x80;
    uint8_t _ly = 0x80;
    uint8_t _rx = 0x80;
    uint8_t _ry = 0x80;
};

// Global pointer for the linker --wrap callback to route 0x80 reports.
// Set by usb_hid_bridge.cpp when Switch Pro USB mode is active.
extern SwitchProUSB* g_switchProUsbDevice;

#endif  // CONFIG_TINYUSB_HID_ENABLED
#endif  // CONFIG_IDF_TARGET_ESP32S3
