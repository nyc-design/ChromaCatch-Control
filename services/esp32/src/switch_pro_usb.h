#pragma once

#include <Arduino.h>

#if defined(CONFIG_IDF_TARGET_ESP32S3)
#include "USB.h"
#include "USBHID.h"

#if CONFIG_TINYUSB_HID_ENABLED

/// Nintendo Switch 2 Pro Controller over USB HID.
/// Emulates PID 0x2069 with the real 97-byte HID descriptor.
/// Uses Report 0x09 for input (21 buttons + 4x12-bit sticks + vendor bytes)
/// and Report 0x02 for output. No handshake protocol — connected as soon as
/// USB is mounted.
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
    inline void releaseAll() { _buttons = 0; _dpadBits = 0; }
    inline void leftXAxis(uint8_t a) { _lx = a; }
    inline void leftYAxis(uint8_t a) { _ly = a; }
    inline void rightXAxis(uint8_t a) { _rx = a; }
    inline void rightYAxis(uint8_t a) { _ry = a; }
    // D-pad: hat value 0-7=directions, 0x0F=center (converted to button bits internally)
    void dPad(uint8_t d);

    bool isConnected() const;

    // USBHIDDevice overrides
    uint16_t _onGetDescriptor(uint8_t* buffer) override;
    void _onOutput(uint8_t report_id, const uint8_t* buffer, uint16_t len) override;

private:
    void sendReport09();
    bool trySendReport(uint8_t reportId, const uint8_t* data, size_t len);
    void flushPendingReplies();

    USBHID _hid;
    uint32_t _lastReportMs = 0;
    uint8_t _timer = 0;

    // Reply queue: callbacks can't block, so buffer replies for loop() to flush.
    static constexpr size_t kMaxPending = 8;
    static constexpr size_t kReportBufLen = 63;
    struct PendingReport {
        uint8_t reportId;
        uint8_t data[kReportBufLen];
        size_t len;
    };
    PendingReport _pendingQueue[kMaxPending];
    volatile uint8_t _pendingCount = 0;

    // Minimum hold enforcement (48ms)
    static constexpr uint32_t kMinHoldMs = 48;
    uint32_t _lastPressMs = 0;
    uint16_t _pendingReleaseMask = 0;  // face/shoulder buttons pending release
    uint8_t _pendingDpadRelease = 0;   // dpad bits pending release

    // Button state
    // _buttons: bits 0-13 = Y,B,A,X,L,R,ZL,ZR,-,+,LStick,RStick,Home,Capture
    // _dpadBits: bits 0-3 = Up,Right,Down,Left
    uint16_t _buttons = 0;
    uint8_t _dpadBits = 0;
    uint8_t _lx = 0x80;
    uint8_t _ly = 0x80;
    uint8_t _rx = 0x80;
    uint8_t _ry = 0x80;
};

// Global pointer for the linker --wrap callbacks.
// Set by usb_hid_bridge.cpp when Switch Pro USB mode is active.
extern SwitchProUSB* g_switchProUsbDevice;

#endif  // CONFIG_TINYUSB_HID_ENABLED
#endif  // CONFIG_IDF_TARGET_ESP32S3
