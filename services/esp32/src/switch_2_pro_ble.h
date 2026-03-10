#pragma once

#include <Arduino.h>
#include <NimBLEDevice.h>

// Reuse SW2_BTN_* enum and SW2_DPAD_MASK from switch_pro_usb.h when available,
// otherwise define them here for non-S3 builds.
#if defined(CONFIG_IDF_TARGET_ESP32S3) && __has_include("switch_pro_usb.h")
#include "switch_pro_usb.h"
#else
// Standalone Switch 2 Pro button definitions for ESP32-WROOM builds
enum Switch2ProButton : uint8_t {
    SW2_BTN_B     = 0,
    SW2_BTN_A     = 1,
    SW2_BTN_Y     = 2,
    SW2_BTN_X     = 3,
    SW2_BTN_R     = 4,
    SW2_BTN_ZR    = 5,
    SW2_BTN_PLUS  = 6,
    SW2_BTN_R3    = 7,
    SW2_BTN_DDOWN  = 8,
    SW2_BTN_DRIGHT = 9,
    SW2_BTN_DLEFT  = 10,
    SW2_BTN_DUP    = 11,
    SW2_BTN_L      = 12,
    SW2_BTN_ZL     = 13,
    SW2_BTN_MINUS  = 14,
    SW2_BTN_L3     = 15,
    SW2_BTN_HOME    = 16,
    SW2_BTN_CAPTURE = 17,
    SW2_BTN_R4      = 18,
    SW2_BTN_L4      = 19,
    SW2_BTN_SQUARE  = 20,
    SW2_BTN_COUNT  = 21,
};
static constexpr uint32_t SW2_DPAD_MASK =
    (1UL << SW2_BTN_DUP) | (1UL << SW2_BTN_DRIGHT) |
    (1UL << SW2_BTN_DDOWN) | (1UL << SW2_BTN_DLEFT);
#endif

// ============================================================
// BLE Switch 2 Pro Controller Emulation
//
// Emulates a Nintendo Switch 2 Pro Controller (PID 0x2069) over BLE
// using Nintendo's custom GATT protocol (NOT standard HOGP).
//
// Protocol reference: BlueRetro sw2.h/sw2.c, NS2-Connect.py, joypad-os
//
// GATT structure (Nintendo custom service):
//   Service UUID:  ab7de9be-89fe-49ad-828f-118f09df7fd0
//   Input char:    ab7de9be-89fe-49ad-828f-118f09df7fd2 (NOTIFY)
//   ACK char:      ab7de9be-89fe-49ad-828f-118f09df7fd5 (NOTIFY)
//   Output/Cmd:    ab7de9be-89fe-49ad-828f-118f09df7fd3 (WRITE|WRITE_NR)
//
// Advertising: company ID 0x0553, magic 0x03 0x7E, PID 0x2069 LE
//
// Command protocol (host writes to output/cmd char):
//   Direct:   [cmd, 0x91, 0x01(BLE), subcmd, value...]
//   Combined: [pad, left_LRA(13), right_LRA(13), cmd, 0x91, 0x01, subcmd, value...]
//
// ACK protocol (controller notifies on ACK char):
//   [cmd, 0x01(RSP), 0x01(BLE), subcmd, value...]
//
// Input report (63 bytes, NOTIFY on input char):
//   [0-3]   Counter + header
//   [4-7]   Buttons (32-bit LE, BLE bit order)
//   [8-9]   Padding
//   [10-15] Sticks (4x 12-bit packed)
//   [16-62] IMU/padding (zeroed)
// ============================================================

class Switch2ProBLE : public NimBLEServerCallbacks,
                      public NimBLECharacteristicCallbacks {
public:
    bool begin();
    void end();
    void loop();
    bool isConnected() const { return _connected; }
    bool isActive() const { return _active; }

    // --- Button API (uses SW2_BTN_* constants, same as SwitchProUSB) ---
    void press(uint8_t b);
    void release(uint8_t b);
    inline void releaseAll() { _buttons = 0; }
    inline void setButtons(uint32_t b) { _buttons = b & 0x001FFFFF; }
    void dPad(uint8_t d);

    // --- Stick API (8-bit, 0x80 = center) ---
    inline void leftXAxis(uint8_t a)  { _lx = a; }
    inline void leftYAxis(uint8_t a)  { _ly = a; }
    inline void rightXAxis(uint8_t a) { _rx = a; }
    inline void rightYAxis(uint8_t a) { _ry = a; }

    // Atomic full-state set + immediate report send.
    void setFullState(uint32_t buttons, uint8_t lx, uint8_t ly, uint8_t rx, uint8_t ry);

    // NimBLEServerCallbacks
    void onConnect(NimBLEServer* pServer, NimBLEConnInfo& connInfo) override;
    void onDisconnect(NimBLEServer* pServer, NimBLEConnInfo& connInfo, int reason) override;

    // NimBLECharacteristicCallbacks
    void onWrite(NimBLECharacteristic* pCharacteristic, NimBLEConnInfo& connInfo) override;
    void onSubscribe(NimBLECharacteristic* pChar, NimBLEConnInfo& connInfo, uint16_t subValue) override;

private:
    void sendInputReport();
    void handleCommand(const uint8_t* data, uint16_t len);
    void sendAck(uint8_t cmd, uint8_t subcmd, const uint8_t* valuePayload, size_t valueLen);
    void handleSpiRead(const uint8_t* data, uint16_t len);

    // Remap USB SW2_BTN_* button word to BLE bit order
    static uint32_t remapButtonsUsbToBle(uint32_t usbButtons);

    NimBLEServer* _server = nullptr;
    NimBLECharacteristic* _inputChar = nullptr;   // NOTIFY — 63-byte input reports
    NimBLECharacteristic* _ackChar = nullptr;      // NOTIFY — command ACKs
    NimBLECharacteristic* _outCmdChar = nullptr;   // WRITE|WRITE_NR — combined output/cmd from host

    uint32_t _buttons = 0;
    uint8_t _lx = 0x80, _ly = 0x80, _rx = 0x80, _ry = 0x80;
    uint8_t _timer = 0;
    uint32_t _lastReportMs = 0;
    bool _connected = false;
    bool _inputSubscribed = false;
    bool _ackSubscribed = false;
    bool _active = false;

    // Minimum hold enforcement (48ms — matches Pokemon Automation timing)
    static constexpr uint32_t kMinHoldMs = 48;
    uint32_t _lastPressMs = 0;
    uint32_t _pendingReleaseMask = 0;
};
