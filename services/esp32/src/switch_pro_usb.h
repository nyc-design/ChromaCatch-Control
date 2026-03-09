#pragma once

#include <Arduino.h>

#if defined(CONFIG_IDF_TARGET_ESP32S3)
#include "USB.h"
#include "USBHID.h"

#if CONFIG_TINYUSB_VENDOR_ENABLED
class USBVendor;  // forward declaration (avoid including USBVendor.h → tusb.h here)
#endif

#if CONFIG_TINYUSB_HID_ENABLED

// ============================================================
// Switch 2 Pro Controller button bit positions
// Verified against joycon2cpp (TheFrano/joycon2cpp) which reads
// the real controller's Report 0x09 on Windows.
//
// Report 0x09 layout (64 bytes total, 63 bytes payload after report ID):
//   [0]      Counter (uint8_t, increments each report)
//   [1]      Fixed vendor byte (0x00)
//   [2]      Status byte (0x00)
//   [3]      Buttons A: Y(0), X(1), B(2), A(3), ?(4), ?(5), R(6), ZR(7)
//   [4]      Buttons B: -(0), +(1), R3(2), L3(3), Home(4), Cap(5), R4(6), L4(7)
//   [5]      Buttons C: DD(0), DU(1), DR(2), DL(3), ?(4), ?(5), L(6), ZL(7)
//   [6-8]    Unknown (zeroed — maybe extra buttons/triggers on future FW)
//   [9-11]   Left stick  (packed 12-bit X, Y)
//   [12-14]  Right stick (packed 12-bit X, Y)
//   [15-18]  Optical mouse delta (signed 16-bit X, Y — zeroed if unused)
//   [19-46]  Unknown / reserved
//   [47-58]  IMU (accel XYZ + gyro XYZ, signed 16-bit)
//   [59-62]  Unknown / reserved
// ============================================================
enum Switch2ProButton : uint8_t {
    // Payload byte [3]: face buttons + R shoulder + trigger
    SW2_BTN_Y     = 0,   // Y (left face button)
    SW2_BTN_X     = 1,   // X (top face button)
    SW2_BTN_B     = 2,   // B (bottom face button)
    SW2_BTN_A     = 3,   // A (right face button)
    SW2_BTN_R     = 6,   // R shoulder
    SW2_BTN_ZR    = 7,   // ZR trigger

    // Payload byte [4]: system buttons + stick clicks + paddles
    SW2_BTN_MINUS   = 8,  // - (select)
    SW2_BTN_PLUS    = 9,  // + (start)
    SW2_BTN_R3      = 10, // Right stick click
    SW2_BTN_L3      = 11, // Left stick click
    SW2_BTN_HOME    = 12, // Home
    SW2_BTN_CAPTURE = 13, // Capture
    SW2_BTN_R4      = 14, // Rear right paddle
    SW2_BTN_L4      = 15, // Rear left paddle

    // Payload byte [5]: d-pad + L shoulder + trigger
    SW2_BTN_DDOWN  = 16, // D-pad down
    SW2_BTN_DUP    = 17, // D-pad up
    SW2_BTN_DRIGHT = 18, // D-pad right
    SW2_BTN_DLEFT  = 19, // D-pad left
    SW2_BTN_SQUARE = 20, // Square button (new on Switch 2)
    SW2_BTN_L      = 22, // L shoulder
    SW2_BTN_ZL     = 23, // ZL trigger

    SW2_BTN_COUNT  = 24,
};

// D-pad mask for clearing all direction bits at once
static constexpr uint32_t SW2_DPAD_MASK =
    (1UL << SW2_BTN_DUP) | (1UL << SW2_BTN_DRIGHT) |
    (1UL << SW2_BTN_DDOWN) | (1UL << SW2_BTN_DLEFT);

/// Nintendo Switch 2 Pro Controller over USB.
/// Emulates PID 0x2069 with byte-for-byte matching of:
///   - USB device descriptor (VID/PID/class/strings)
///   - Configuration descriptor (HID + Vendor Bulk, 80 bytes)
///   - HID report descriptor (97 bytes)
///   - Report 0x09 format (button bitfield, 12-bit sticks, IMU placeholder)
///   - Vendor Bulk Interface 1 init protocol (18-step handshake)
///   - Report 0x02 haptic output handling
class SwitchProUSB : public USBHIDDevice {
public:
    SwitchProUSB();

    void begin();
    void end();
    bool write();
    void loop();

    // --- Button API (uses SW2_BTN_* constants directly) ---
    void press(uint8_t b);
    void release(uint8_t b);
    inline void releaseAll() { _buttons = 0; }

    // Set all buttons at once (bits match SW2_BTN_* positions).
    // D-pad directions are part of the button word (bits 16-19).
    inline void setButtons(uint32_t b) { _buttons = b & 0x00FFFFFF; }

    // D-pad: hat value 0-7=directions, 0x0F=center.
    // Sets individual direction bits in the button word.
    void dPad(uint8_t d);

    // --- Stick API (8-bit, 0x80 = center) ---
    inline void leftXAxis(uint8_t a)  { _lx = a; }
    inline void leftYAxis(uint8_t a)  { _ly = a; }
    inline void rightXAxis(uint8_t a) { _rx = a; }
    inline void rightYAxis(uint8_t a) { _ry = a; }

    // Atomic full-state set + immediate report send.
    // buttons: SW2_BTN_* bitmask (including d-pad bits 8-11).
    // lx/ly/rx/ry: 0x00-0xFF (0x80=center).
    void setFullState(uint32_t buttons, uint8_t lx, uint8_t ly, uint8_t rx, uint8_t ry);

    bool isConnected() const;

    // --- Vendor Bulk Interface 1 ---
    // Set the USBVendor interface for polling vendor bulk data.
    // The USBVendor instance is created in usb_hid_bridge.cpp.
#if CONFIG_TINYUSB_VENDOR_ENABLED
    void setVendorInterface(USBVendor* vendor) { _vendor = vendor; }
#endif
    // Called when vendor bulk data is received from the host.
    void onVendorRx(const uint8_t* data, uint16_t len);

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

    // Minimum hold enforcement (48ms — matches Pokemon Automation timing)
    static constexpr uint32_t kMinHoldMs = 48;
    uint32_t _lastPressMs = 0;
    uint32_t _pendingReleaseMask = 0;  // button bits pending release

    // Button state — bits 0-23 match SW2_BTN_* positions directly.
    // Bits 0-7 → payload[3], 8-15 → payload[4], 16-23 → payload[5].
    // D-pad directions are individual button bits (16-19), not a separate field.
    uint32_t _buttons = 0;

    // Stick state (8-bit, 0x80 = center, maps to 12-bit 0x800 = 2048 center)
    uint8_t _lx = 0x80;
    uint8_t _ly = 0x80;
    uint8_t _rx = 0x80;
    uint8_t _ry = 0x80;

    // Vendor bulk init tracking
    // The real controller starts SILENT — no HID reports until the host sends
    // the init command (0x03, arg 0x0D) via Vendor Bulk. We mirror this
    // behavior: only send Report 0x09 after _hidOutputEnabled is set.
    bool _hidOutputEnabled = false;
    uint8_t _vendorCmdCount = 0;

#if CONFIG_TINYUSB_VENDOR_ENABLED
    USBVendor* _vendor = nullptr;
    void pollVendorRx();
#endif
};

// Global pointer for the linker --wrap callbacks.
// Set by usb_hid_bridge.cpp when Switch Pro USB mode is active.
extern SwitchProUSB* g_switchProUsbDevice;

#endif  // CONFIG_TINYUSB_HID_ENABLED
#endif  // CONFIG_IDF_TARGET_ESP32S3
