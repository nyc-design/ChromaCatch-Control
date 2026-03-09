#pragma once

#include <Arduino.h>

#if defined(CONFIG_IDF_TARGET_ESP32S3)
#include "USB.h"
#include "USBHID.h"

#if CONFIG_TINYUSB_HID_ENABLED

// ============================================================
// Switch 2 Pro Controller button bit positions
// These match the EXACT layout of the real controller's Report 0x09
// bytes 3-5 (payload offsets 2-4), as documented in joypad-os
// switch2_pro.h and verified against HandHeldLegend procon2tool.
//
// Report 0x09 layout (64 bytes total, 63 bytes payload after report ID):
//   [0]     Counter (uint8_t, increments each report)
//   [1]     Fixed vendor byte (0x00)
//   [2]     Buttons byte 3: B, A, Y, X, R, ZR, +, R3
//   [3]     Buttons byte 4: DD, DR, DL, DU, L, ZL, -, L3
//   [4]     Buttons byte 5: Home, Capture, R4, L4, Square, pad[3]
//   [5-7]   Left stick  (packed 12-bit X, Y)
//   [8-10]  Right stick (packed 12-bit X, Y)
//   [11-62] IMU/vendor data (52 bytes, zeroed)
// ============================================================
enum Switch2ProButton : uint8_t {
    // Byte 3 of report (payload offset 2)
    SW2_BTN_B     = 0,   // B (bottom face button)
    SW2_BTN_A     = 1,   // A (right face button)
    SW2_BTN_Y     = 2,   // Y (left face button)
    SW2_BTN_X     = 3,   // X (top face button)
    SW2_BTN_R     = 4,   // R shoulder
    SW2_BTN_ZR    = 5,   // ZR trigger
    SW2_BTN_PLUS  = 6,   // + (start)
    SW2_BTN_R3    = 7,   // Right stick press

    // Byte 4 of report (payload offset 3)
    SW2_BTN_DDOWN  = 8,  // D-pad down
    SW2_BTN_DRIGHT = 9,  // D-pad right
    SW2_BTN_DLEFT  = 10, // D-pad left
    SW2_BTN_DUP    = 11, // D-pad up
    SW2_BTN_L      = 12, // L shoulder
    SW2_BTN_ZL     = 13, // ZL trigger
    SW2_BTN_MINUS  = 14, // - (select)
    SW2_BTN_L3     = 15, // Left stick press

    // Byte 5 of report (payload offset 4)
    SW2_BTN_HOME    = 16, // Home
    SW2_BTN_CAPTURE = 17, // Capture
    SW2_BTN_R4      = 18, // Rear right paddle
    SW2_BTN_L4      = 19, // Rear left paddle
    SW2_BTN_SQUARE  = 20, // Square button (new on Switch 2)

    SW2_BTN_COUNT   = 21,
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
    // D-pad directions are part of the button word (bits 8-11).
    inline void setButtons(uint32_t b) { _buttons = b & ((1UL << SW2_BTN_COUNT) - 1); }

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
    // Called from the TinyUSB vendor class rx callback when the Switch
    // console sends init commands via Bulk OUT (EP 0x02).
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

    // Button state — bits 0-20 match SW2_BTN_* positions directly.
    // D-pad directions are individual button bits (8-11), not a separate field.
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
};

// Global pointer for the linker --wrap callbacks.
// Set by usb_hid_bridge.cpp when Switch Pro USB mode is active.
extern SwitchProUSB* g_switchProUsbDevice;

#endif  // CONFIG_TINYUSB_HID_ENABLED
#endif  // CONFIG_IDF_TARGET_ESP32S3
