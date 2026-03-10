#include "switch_2_pro_ble.h"
#include <cstring>

// ============================================================
// Nintendo Switch 2 Pro Controller BLE GATT UUIDs
//
// Service: ab7de9be-89fe-49ad-828f-118f09df7fd0
// Input:   ab7de9be-89fe-49ad-828f-118f09df7fd2 (NOTIFY — confirmed by NS2-Connect.py)
// Out/Cmd: ab7de9be-89fe-49ad-828f-118f09df7fd3 (WRITE|WRITE_NR — first writable in service)
// ACK:     ab7de9be-89fe-49ad-828f-118f09df7fd5 (NOTIFY — second notify in service)
//
// NS2-Connect discovers characteristics by property within the Nintendo
// service: first NOTIFY = input, first WRITE = output/cmd. The Switch
// console likely does the same. Characteristic order in GATT matters.
// ============================================================
static const NimBLEUUID kNintendoServiceUUID("ab7de9be-89fe-49ad-828f-118f09df7fd0");
static const NimBLEUUID kInputCharUUID("ab7de9be-89fe-49ad-828f-118f09df7fd2");
static const NimBLEUUID kOutCmdCharUUID("ab7de9be-89fe-49ad-828f-118f09df7fd3");
static const NimBLEUUID kAckCharUUID("ab7de9be-89fe-49ad-828f-118f09df7fd5");

// ============================================================
// BLE advertising manufacturer data
//
// Format (from NS2-Connect.py extract_nintendo_info):
//   Bytes 0-1: Company ID 0x0553 (LE) — included by NimBLE in AD type
//   Payload bytes (after company ID):
//     [0-1]: Unknown header bytes
//     [2]:   0x03 (magic marker)
//     [3]:   0x7E (part of vendor ID 0x057E)
//     [4-5]: PID (LE) — 0x69, 0x20 for PID 0x2069
//
// NS2-Connect checks: data[2]==0x03 && data[3]==0x7E → Nintendo
//   then reads PID from (data[5]<<8)|data[4]
//
// BlueRetro hci.c detects Switch 2 by company ID 0x0553 alone.
// ============================================================
static const uint8_t kManufacturerData[] = {
    0x53, 0x05,  // Company ID: 0x0553 (little-endian)
    0x00, 0x00,  // Payload [0-1]: header (padding)
    0x03, 0x7E,  // Payload [2-3]: magic marker
    0x69, 0x20,  // Payload [4-5]: PID 0x2069 (Switch 2 Pro Controller, LE)
};

// Fake MAC for BT pairing responses
static const uint8_t kFakeMAC[] = { 0xCC, 0xCC, 0x02, 0x03, 0x04, 0x05 };

// ============================================================
// Button remapping: USB Report 0x09 bit positions → BLE bit positions
//
// USB (SW2_BTN_*) internal state → BLE 32-bit button word at report [4-7].
//
// BLE bit layout (from joypad-os switch2_ble.c, NS2-Connect.py SW2 enum):
//   0:Y   1:X   2:B   3:A   4:R_SR  5:R_SL  6:R   7:ZR
//   8:Minus 9:Plus 10:RJ 11:LJ 12:Home 13:Cap 14:C 15:unused
//   16:DD 17:DU 18:DR 19:DL 20:L_SR 21:L_SL 22:L 23:ZL
//   24:GR 25:GL
// ============================================================
static const uint8_t kUsbToBleMap[SW2_BTN_COUNT] = {
    2,   // SW2_BTN_B(0)       → BLE bit 2
    3,   // SW2_BTN_A(1)       → BLE bit 3
    0,   // SW2_BTN_Y(2)       → BLE bit 0
    1,   // SW2_BTN_X(3)       → BLE bit 1
    6,   // SW2_BTN_R(4)       → BLE bit 6
    7,   // SW2_BTN_ZR(5)      → BLE bit 7
    9,   // SW2_BTN_PLUS(6)    → BLE bit 9
    10,  // SW2_BTN_R3(7)      → BLE bit 10 (RJ)
    16,  // SW2_BTN_DDOWN(8)   → BLE bit 16
    18,  // SW2_BTN_DRIGHT(9)  → BLE bit 18
    19,  // SW2_BTN_DLEFT(10)  → BLE bit 19
    17,  // SW2_BTN_DUP(11)    → BLE bit 17
    22,  // SW2_BTN_L(12)      → BLE bit 22
    23,  // SW2_BTN_ZL(13)     → BLE bit 23
    8,   // SW2_BTN_MINUS(14)  → BLE bit 8
    11,  // SW2_BTN_L3(15)     → BLE bit 11 (LJ)
    12,  // SW2_BTN_HOME(16)   → BLE bit 12
    13,  // SW2_BTN_CAPTURE(17)→ BLE bit 13
    24,  // SW2_BTN_R4(18)     → BLE bit 24 (GR)
    25,  // SW2_BTN_L4(19)     → BLE bit 25 (GL)
    14,  // SW2_BTN_SQUARE(20) → BLE bit 14 (C)
};

uint32_t Switch2ProBLE::remapButtonsUsbToBle(uint32_t usbButtons) {
    uint32_t ble = 0;
    for (uint8_t i = 0; i < SW2_BTN_COUNT; i++) {
        if (usbButtons & (1UL << i)) {
            ble |= (1UL << kUsbToBleMap[i]);
        }
    }
    return ble;
}

// ============================================================
// begin() — Initialize NimBLE GATT server + advertising
// ============================================================
bool Switch2ProBLE::begin() {
    if (_active) return true;

    Serial.println("[SW2BLE] Initializing BLE Switch 2 Pro Controller...");

    NimBLEDevice::init("Pro Controller");
    NimBLEDevice::setPower(9, NimBLETxPowerType::All);

    _server = NimBLEDevice::createServer();
    _server->setCallbacks(this);

    // Create Nintendo custom GATT service
    NimBLEService* svc = _server->createService(kNintendoServiceUUID);

    // IMPORTANT: Characteristic creation order determines GATT handle order.
    // NS2-Connect discovers by property: first NOTIFY = input, first WRITE = output/cmd.
    // The Switch console likely does the same.

    // 1. Input characteristic: controller → host (NOTIFY) — MUST be first NOTIFY
    _inputChar = svc->createCharacteristic(
        kInputCharUUID,
        NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY
    );
    _inputChar->setCallbacks(this);

    // 2. Combined output/cmd: host → controller (WRITE + WRITE_NR) — MUST be first WRITE
    //    Handles both rumble output and init commands in one characteristic.
    //    BlueRetro uses separate handles (0x0012 for rumble, 0x0014 for cmd, 0x0016 for combined),
    //    but NS2-Connect sends everything to the first writable characteristic.
    _outCmdChar = svc->createCharacteristic(
        kOutCmdCharUUID,
        NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::WRITE_NR
    );
    _outCmdChar->setCallbacks(this);

    // 3. ACK characteristic: controller → host (NOTIFY) — second NOTIFY
    _ackChar = svc->createCharacteristic(
        kAckCharUUID,
        NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY
    );
    _ackChar->setCallbacks(this);

    svc->start();

    // Configure advertising
    NimBLEAdvertising* pAdv = NimBLEDevice::getAdvertising();

    NimBLEAdvertisementData advData;
    advData.setName("Pro Controller");
    advData.setAppearance(0x03C4);  // HID Gamepad
    advData.setManufacturerData(
        std::string(reinterpret_cast<const char*>(kManufacturerData), sizeof(kManufacturerData)));
    pAdv->setAdvertisementData(advData);

    // Scan response with Nintendo service UUID
    NimBLEAdvertisementData scanRsp;
    scanRsp.addServiceUUID(kNintendoServiceUUID);
    pAdv->setScanResponseData(scanRsp);

    pAdv->setMinInterval(0x20);  // 20ms
    pAdv->setMaxInterval(0x40);  // 40ms
    pAdv->start();

    _active = true;
    _connected = false;
    _inputSubscribed = false;
    _ackSubscribed = false;
    _buttons = 0;
    _lx = _ly = _rx = _ry = 0x80;
    _timer = 0;
    _lastReportMs = 0;
    _pendingReleaseMask = 0;

    Serial.println("[SW2BLE] BLE Switch 2 Pro Controller advertising started");
    return true;
}

// ============================================================
// end() — Shutdown BLE
// ============================================================
void Switch2ProBLE::end() {
    if (!_active) return;
    _active = false;
    _connected = false;
    _inputSubscribed = false;
    _ackSubscribed = false;

    if (NimBLEDevice::isInitialized()) {
        NimBLEDevice::getAdvertising()->stop();
        NimBLEDevice::deinit(true);
    }
    _server = nullptr;
    _inputChar = nullptr;
    _ackChar = nullptr;
    _outCmdChar = nullptr;

    Serial.println("[SW2BLE] BLE shut down");
}

// ============================================================
// NimBLE server callbacks
// ============================================================
void Switch2ProBLE::onConnect(NimBLEServer* pServer, NimBLEConnInfo& connInfo) {
    _connected = true;
    Serial.printf("[SW2BLE] Host connected (addr: %s)\n",
                  connInfo.getAddress().toString().c_str());
    // Reports start when host subscribes to input notifications (via CCCD).
    // NimBLE will call onSubscribe when the host enables notifications.
}

void Switch2ProBLE::onDisconnect(NimBLEServer* pServer, NimBLEConnInfo& connInfo, int reason) {
    _connected = false;
    _inputSubscribed = false;
    _ackSubscribed = false;
    Serial.printf("[SW2BLE] Host disconnected (reason: %d)\n", reason);

    if (_active) {
        NimBLEDevice::getAdvertising()->start();
        Serial.println("[SW2BLE] Re-advertising...");
    }
}

// ============================================================
// Characteristic subscribe callback — track CCCD notifications
//
// BlueRetro init flow:
//   1. Host subscribes to ACK notifications (CCCD → 0x0001)
//   2. Host sends init commands (SPI reads, pairing, LED)
//   3. Host subscribes to input notifications (CCCD → 0x0001)
//   4. Host unsubscribes from ACK notifications (CCCD → 0x0000)
//   5. Controller starts sending input reports
// ============================================================
void Switch2ProBLE::onSubscribe(NimBLECharacteristic* pChar,
                                NimBLEConnInfo& connInfo,
                                uint16_t subValue) {
    bool notify = (subValue & 0x01) != 0;

    if (pChar == _inputChar) {
        _inputSubscribed = notify;
        Serial.printf("[SW2BLE] Input notifications %s\n",
                      notify ? "ENABLED → sending reports" : "disabled");
    } else if (pChar == _ackChar) {
        _ackSubscribed = notify;
        Serial.printf("[SW2BLE] ACK notifications %s\n",
                      notify ? "ENABLED" : "disabled");
    }
}

// ============================================================
// Characteristic write callback (host → controller)
//
// Incoming data can be:
//   1. Direct command: [cmd, 0x91, 0x01, subcmd, value...]
//      - data[1]==0x91 identifies this format
//   2. Combined rumble+cmd (Pro2): 41 bytes
//      - [pad(1), left_LRA(13), right_LRA(13), cmd, 0x91, 0x01, subcmd, value...]
//      - Command at byte offset 27
//   3. Pure rumble: LRA data without command trailer
// ============================================================
void Switch2ProBLE::onWrite(NimBLECharacteristic* pChar,
                            NimBLEConnInfo& connInfo) {
    if (pChar != _outCmdChar) return;

    NimBLEAttValue val = pChar->getValue();
    const uint8_t* data = val.data();
    uint16_t len = val.size();
    if (len < 4) return;

    // Check for direct command format: data[1] == 0x91 (REQUEST type)
    if (data[1] == 0x91 && data[2] == 0x01) {
        handleCommand(data, len);
        return;
    }

    // Check for combined rumble+cmd format (Pro2: 41 bytes)
    // Command starts at byte 27: [cmd, 0x91, 0x01, subcmd, ...]
    if (len >= 31 && data[28] == 0x91 && data[29] == 0x01) {
        // Extract command portion starting at offset 27
        handleCommand(data + 27, len - 27);
        return;
    }

    // Pure rumble/haptic data — silently accept (no motors)
}

// ============================================================
// Command handling
//
// Format: [cmd, 0x91(REQ), 0x01(BLE), subcmd, value...]
//
// Commands from BlueRetro sw2.c init state machine:
//   0x02/0x04: SPI flash read (device info, LTK, calibration)
//   0x03/0x0D: Enable HID reports at 4ms
//   0x03/0x0A: Enable haptics
//   0x09/0x07: Set player LED
//   0x0C/xx:   IMU config
//   0x15/0x01: Pairing step 1 (SET_BDADDR)
//   0x15/0x02: Pairing step 3
//   0x15/0x03: Pairing step 4
//   0x15/0x04: Pairing step 2
// ============================================================
void Switch2ProBLE::handleCommand(const uint8_t* data, uint16_t len) {
    if (len < 4) return;

    uint8_t cmd = data[0];
    uint8_t subcmd = data[3];

    Serial.printf("[SW2BLE] CMD 0x%02X subcmd 0x%02X len=%d", cmd, subcmd, len);
    if (len > 4) {
        Serial.printf(" val:");
        for (uint16_t i = 4; i < len && i < 20; i++) {
            Serial.printf(" %02X", data[i]);
        }
        if (len > 20) Serial.printf("...");
    }
    Serial.println();

    switch (cmd) {
        case 0x02:  // SPI/Flash read
            if (subcmd == 0x04 && len >= 16) {
                handleSpiRead(data, len);
            } else {
                sendAck(cmd, subcmd, nullptr, 0);
            }
            break;

        case 0x03:  // Mode control
            if (subcmd == 0x0D) {
                Serial.println("[SW2BLE] Reports enabled by host (0x03/0x0D)");
            } else if (subcmd == 0x0A) {
                Serial.println("[SW2BLE] Haptics enabled (0x03/0x0A)");
            }
            sendAck(cmd, subcmd, nullptr, 0);
            break;

        case 0x09:  // Set LED
            if (subcmd == 0x07 && len >= 9) {
                Serial.printf("[SW2BLE] Set LED pattern=0x%02X\n", data[8]);
            }
            sendAck(cmd, subcmd, nullptr, 0);
            break;

        case 0x0C:  // IMU config
            Serial.printf("[SW2BLE] IMU config subcmd=0x%02X\n", subcmd);
            sendAck(cmd, subcmd, nullptr, 0);
            break;

        case 0x15:  // Pairing
            Serial.printf("[SW2BLE] Pairing step subcmd=0x%02X\n", subcmd);
            sendAck(cmd, subcmd, nullptr, 0);
            break;

        default:
            Serial.printf("[SW2BLE] Unknown CMD 0x%02X → generic ACK\n", cmd);
            sendAck(cmd, subcmd, nullptr, 0);
            break;
    }
}

// ============================================================
// Send ACK on the ACK characteristic
//
// Format: [cmd, 0x01(RSP), 0x01(BLE), subcmd, ...value]
// ============================================================
void Switch2ProBLE::sendAck(uint8_t cmd, uint8_t subcmd,
                            const uint8_t* valuePayload, size_t valueLen) {
    if (!_ackChar || !_connected) return;

    uint8_t buf[64];
    memset(buf, 0, sizeof(buf));
    buf[0] = cmd;
    buf[1] = 0x01;  // RSP type
    buf[2] = 0x01;  // BLE interface
    buf[3] = subcmd;

    if (valuePayload && valueLen > 0) {
        size_t copyLen = (valueLen > sizeof(buf) - 4) ? sizeof(buf) - 4 : valueLen;
        memcpy(buf + 4, valuePayload, copyLen);
    }

    size_t total = 4 + valueLen;
    if (total > sizeof(buf)) total = sizeof(buf);

    _ackChar->setValue(buf, total);
    _ackChar->notify();
}

// ============================================================
// Handle SPI flash read — return canned data at correct offsets
//
// Request format (16 bytes):
//   [0]:    cmd (0x02)
//   [1]:    type (0x91)
//   [2]:    interface (0x01)
//   [3]:    subcmd (0x04)
//   [4-5]:  flags (0x00, 0x08)
//   [6-7]:  padding
//   [8]:    read length
//   [9-11]: SPI flash region (0x7E, 0x00, 0x00 typically)
//   [12-15]: SPI address (little-endian)
//
// ACK response value format:
//   value[0-11]: echoed request value bytes (data[4..15])
//   value[12+]:  SPI data (read_len bytes)
//
// BlueRetro reads from ACK value at these offsets:
//   READ_INFO:        VID at value[30], PID at value[32]
//   LEFT_CALIB:       calib at value[52]
//   RIGHT_CALIB:      calib at value[52]
//   USER_CALIB:       left at value[14], right at value[46]
//   READ_LTK:         LTK at value[12] (16 bytes)
// ============================================================
void Switch2ProBLE::handleSpiRead(const uint8_t* data, uint16_t len) {
    if (len < 16) return;

    uint8_t readLen = data[8];
    uint32_t addr = (uint32_t)data[12] | ((uint32_t)data[13] << 8) |
                    ((uint32_t)data[14] << 16) | ((uint32_t)data[15] << 24);

    Serial.printf("[SW2BLE] SPI read addr=0x%06lX len=%d\n",
                  static_cast<unsigned long>(addr), readLen);

    // Build ACK value: echo request params + SPI data
    uint8_t value[60];
    memset(value, 0, sizeof(value));

    // Echo request value bytes (data[4..15]) into value[0..11]
    uint8_t echoLen = (len >= 16) ? 12 : (len - 4);
    memcpy(value, data + 4, echoLen);

    // SPI data starts at value[12]
    // Total value length = 12 + readLen (capped at buffer size)
    size_t spiDataOffset = 12;
    size_t maxSpi = sizeof(value) - spiDataOffset;
    size_t spiLen = (readLen > maxSpi) ? maxSpi : readLen;

    switch (addr) {
        case 0x00013000: {
            // Device info block — BlueRetro reads VID at value[30], PID at value[32]
            // value[30] = spiData[18], value[32] = spiData[20]
            size_t vidOff = 18;  // offset within SPI data
            size_t pidOff = 20;
            if (vidOff + 2 <= spiLen) {
                value[spiDataOffset + vidOff]     = 0x7E;  // VID 0x057E LE low
                value[spiDataOffset + vidOff + 1] = 0x05;  // VID 0x057E LE high
            }
            if (pidOff + 2 <= spiLen) {
                value[spiDataOffset + pidOff]     = 0x69;  // PID 0x2069 LE low
                value[spiDataOffset + pidOff + 1] = 0x20;  // PID 0x2069 LE high
            }
            break;
        }

        case 0x001fa01a:
            // LTK — value[12..27] = 16-byte key (zeroed = no pairing key)
            // Already zeroed from memset
            break;

        case 0x00013080: {
            // Left factory stick calibration — BlueRetro reads at value[52]
            // value[52] = spiData[40]
            // Calibration: 9 bytes packed 12-bit (neutral, max, min for X and Y)
            // Default centered values: neutral=0x800, range=±0x7FF
            size_t calOff = 40;  // offset within SPI data
            if (calOff + 9 <= spiLen) {
                uint8_t* cal = &value[spiDataOffset + calOff];
                // Neutral X=0x800, Y=0x800 (packed 12-bit)
                cal[0] = 0x00;          // X neutral low 8
                cal[1] = 0x08;          // X neutral high 4 | Y neutral low 4
                cal[2] = 0x80;          // Y neutral high 8
                // Max relative = 0x600
                cal[3] = 0x00;
                cal[4] = 0x06;
                cal[5] = 0x60;
                // Min relative = 0x600
                cal[6] = 0x00;
                cal[7] = 0x06;
                cal[8] = 0x60;
            }
            break;
        }

        case 0x000130c0: {
            // Right factory stick calibration — same offset as left
            size_t calOff = 40;
            if (calOff + 9 <= spiLen) {
                uint8_t* cal = &value[spiDataOffset + calOff];
                cal[0] = 0x00;
                cal[1] = 0x08;
                cal[2] = 0x80;
                cal[3] = 0x00;
                cal[4] = 0x06;
                cal[5] = 0x60;
                cal[6] = 0x00;
                cal[7] = 0x06;
                cal[8] = 0x60;
            }
            break;
        }

        case 0x001fc040: {
            // User calibration — BlueRetro reads left at value[14], right at value[46]
            // value[14] = spiData[2], value[46] = spiData[34]
            // Return 0xFF at first byte to indicate "no user calibration"
            size_t leftOff = 2;   // offset within SPI data
            size_t rightOff = 34;
            if (leftOff < spiLen) {
                value[spiDataOffset + leftOff] = 0xFF;  // flag: no user cal
            }
            if (rightOff < spiLen) {
                value[spiDataOffset + rightOff] = 0xFF;  // flag: no user cal
            }
            break;
        }

        default:
            Serial.printf("[SW2BLE] Unknown SPI addr 0x%06lX → zeroed response\n",
                          static_cast<unsigned long>(addr));
            break;
    }

    size_t totalValueLen = spiDataOffset + spiLen;
    if (totalValueLen > sizeof(value)) totalValueLen = sizeof(value);

    sendAck(0x02, 0x04, value, totalValueLen);
}

// ============================================================
// Input report builder and sender (63 bytes, BLE format)
//
// BLE report layout (joypad-os switch2_ble.c, NS2-Connect.py):
//   [0]:     Counter
//   [1-3]:   Header (fixed 0x00)
//   [4-7]:   Buttons (32-bit LE, BLE bit order)
//   [8-9]:   Padding
//   [10-12]: Left stick (12-bit packed X, Y)
//   [13-15]: Right stick (12-bit packed X, Y)
//   [16-62]: IMU / padding (zeroed)
//
// Stick packing (same as USB, same as Switch 1):
//   byte[0] = X[7:0]
//   byte[1] = Y[3:0]<<4 | X[11:8]
//   byte[2] = Y[11:4]
//
// Stick center: 0x80 → 0x800 (2048) in 12-bit space
// ============================================================
void Switch2ProBLE::sendInputReport() {
    if (!_inputChar || !_connected) return;

    uint8_t report[63];
    memset(report, 0, sizeof(report));

    // [0]: counter
    report[0] = _timer++;
    // [1-3]: fixed header bytes (all 0x00)

    // [4-7]: 32-bit button word in BLE bit order
    uint32_t bleButtons = remapButtonsUsbToBle(_buttons);
    report[4] = bleButtons & 0xFF;
    report[5] = (bleButtons >> 8) & 0xFF;
    report[6] = (bleButtons >> 16) & 0xFF;
    report[7] = (bleButtons >> 24) & 0xFF;

    // [8-9]: padding (zeros)

    // Scale 8-bit (0x00-0xFF) to 12-bit (0x000-0xFF0)
    uint16_t lx12 = static_cast<uint16_t>(_lx) << 4;
    uint16_t ly12 = static_cast<uint16_t>(_ly) << 4;
    uint16_t rx12 = static_cast<uint16_t>(_rx) << 4;
    uint16_t ry12 = static_cast<uint16_t>(_ry) << 4;

    // [10-12]: left stick (12-bit packed)
    report[10] = lx12 & 0xFF;
    report[11] = ((lx12 >> 8) & 0x0F) | ((ly12 & 0x0F) << 4);
    report[12] = (ly12 >> 4) & 0xFF;

    // [13-15]: right stick (12-bit packed)
    report[13] = rx12 & 0xFF;
    report[14] = ((rx12 >> 8) & 0x0F) | ((ry12 & 0x0F) << 4);
    report[15] = (ry12 >> 4) & 0xFF;

    // [16-62]: IMU / motion (zeroed)

    _inputChar->setValue(report, sizeof(report));
    _inputChar->notify();
}

// ============================================================
// Button press/release with minimum hold enforcement
// ============================================================
void Switch2ProBLE::press(uint8_t b) {
    if (b >= SW2_BTN_COUNT) return;
    uint32_t mask = 1UL << b;
    _buttons |= mask;
    _pendingReleaseMask &= ~mask;
    _lastPressMs = millis();
}

void Switch2ProBLE::release(uint8_t b) {
    if (b >= SW2_BTN_COUNT) return;
    uint32_t mask = 1UL << b;
    if (millis() - _lastPressMs < kMinHoldMs) {
        _pendingReleaseMask |= mask;
    } else {
        _buttons &= ~mask;
        _pendingReleaseMask &= ~mask;
    }
}

// ============================================================
// D-pad
// ============================================================
void Switch2ProBLE::dPad(uint8_t d) {
    uint32_t cleared = _buttons & ~SW2_DPAD_MASK;
    uint32_t dpadBits = 0;
    switch (d) {
        case 0: dpadBits = (1UL << SW2_BTN_DUP); break;
        case 1: dpadBits = (1UL << SW2_BTN_DUP) | (1UL << SW2_BTN_DRIGHT); break;
        case 2: dpadBits = (1UL << SW2_BTN_DRIGHT); break;
        case 3: dpadBits = (1UL << SW2_BTN_DDOWN) | (1UL << SW2_BTN_DRIGHT); break;
        case 4: dpadBits = (1UL << SW2_BTN_DDOWN); break;
        case 5: dpadBits = (1UL << SW2_BTN_DDOWN) | (1UL << SW2_BTN_DLEFT); break;
        case 6: dpadBits = (1UL << SW2_BTN_DLEFT); break;
        case 7: dpadBits = (1UL << SW2_BTN_DUP) | (1UL << SW2_BTN_DLEFT); break;
        default: break;
    }
    if (dpadBits != 0) {
        _buttons = cleared | dpadBits;
        _pendingReleaseMask &= ~SW2_DPAD_MASK;
        _lastPressMs = millis();
    } else {
        if (millis() - _lastPressMs < kMinHoldMs) {
            _pendingReleaseMask |= (_buttons & SW2_DPAD_MASK);
        } else {
            _buttons = cleared;
            _pendingReleaseMask &= ~SW2_DPAD_MASK;
        }
    }
}

// ============================================================
// Atomic full-state set
// ============================================================
void Switch2ProBLE::setFullState(uint32_t buttons, uint8_t lx, uint8_t ly, uint8_t rx, uint8_t ry) {
    _buttons = buttons & 0x001FFFFF;
    _lx = lx;
    _ly = ly;
    _rx = rx;
    _ry = ry;
    _pendingReleaseMask = 0;
    if (_inputSubscribed) sendInputReport();
}

// ============================================================
// loop() — Send periodic input reports + process deferred releases
// ============================================================
void Switch2ProBLE::loop() {
    if (!_active || !_connected || !_inputSubscribed) return;

    // Process deferred releases after minimum hold time
    if (_pendingReleaseMask && (millis() - _lastPressMs >= kMinHoldMs)) {
        _buttons &= ~_pendingReleaseMask;
        _pendingReleaseMask = 0;
    }

    // Send input report at ~8ms cadence (matches PA timing)
    if (millis() - _lastReportMs < 8) return;
    _lastReportMs = millis();

    sendInputReport();
}
