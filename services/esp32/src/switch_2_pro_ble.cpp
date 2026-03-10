#include "switch_2_pro_ble.h"
#include <cstring>
#include <esp_mac.h>

// ============================================================
// Nintendo Switch 2 Pro Controller BLE GATT UUIDs
//
// Reference: NSO GC BLE Protocol Guide (RyanCopley), BlueRetro sw2.c,
// nRF Connect real controller capture, switch2bridge-macos.
//
// GATT handle map from NSO GC Protocol Guide:
//   0x0005: Service enable (secondary svc char — write 0x01 0x00 to activate)
//   0x000A: Input report format 0 (NOTIFY)
//   0x000E: Input report format 3 — 63-byte reports (NOTIFY)
//   0x0012: Vibration/rumble output (WRITE_NR)
//   0x0014: Command channel (WRITE_NR)
//   0x0016: Combined command+rumble (WRITE_NR)
//   0x001A: Command response/ACK (NOTIFY)
// ============================================================

// Primary Nintendo service and characteristics
static const NimBLEUUID kNintendoServiceUUID("ab7de9be-89fe-49ad-828f-118f09df7fd0");
static const NimBLEUUID kInputCharUUID("ab7de9be-89fe-49ad-828f-118f09df7fd2");
static const NimBLEUUID kOutCmdCharUUID("7492866c-ec3e-4619-8258-32755ffcc0f8");
static const NimBLEUUID kWriteChar1UUID("cc483f51-9258-427d-a939-630c31f72b05");
static const NimBLEUUID kWriteChar2UUID("649d4ac9-8eb7-4e6c-af44-1ea54fe5f005");
static const NimBLEUUID kWriteChar3UUID("3dacbc7e-6955-40b5-8eaf-6f9809e8b379");
static const NimBLEUUID kWriteChar4UUID("4147423d-fdae-4df7-a4f7-d23e5df59f8d");
static const NimBLEUUID kNotifyChar1UUID("c765a961-d9d8-4d36-a20a-5315b111836a");
static const NimBLEUUID kNotifyChar2UUID("506d9f7d-4278-4e95-a549-326ba77657e0");
static const NimBLEUUID kNotifyChar3UUID("d3bd69d2-841c-4241-ab15-f86f406d2a80");
static const NimBLEUUID kCharFDEUUID("ab7de9be-89fe-49ad-828f-118f09df7fde");
static const NimBLEUUID kCharFDFUUID("ab7de9be-89fe-49ad-828f-118f09df7fdf");

// Secondary service — "service control" (NSO GC Protocol Guide handle 0x0005)
// Writing 0x01 0x00 to bd282 activates the SW2 proprietary service.
static const NimBLEUUID kSecondaryServiceUUID("00c5af5d-1964-4e30-8f51-1956f96bd280");
static const NimBLEUUID kSecChar1UUID("00c5af5d-1964-4e30-8f51-1956f96bd281");
static const NimBLEUUID kSecChar2UUID("00c5af5d-1964-4e30-8f51-1956f96bd282");
static const NimBLEUUID kSecChar3UUID("00c5af5d-1964-4e30-8f51-1956f96bd283");

// ============================================================
// BLE advertising manufacturer data
//
// Captured from real Switch 2 Pro Controller via nRF Connect:
//   AD type 0xFF, company ID 0x0553 (Nintendo BLE) in first 2 bytes,
//   followed by 24-byte payload.
//
// BlueRetro hci.c detection: company_id==0x0553 && VID==0x057E at payload offset 4.
// NS2-Connect checks: payload[2]==0x03 && payload[3]==0x7E.
//
// Total manufacturer AD: 2 (company) + 24 (payload) = 26 bytes.
// With AD header (length + type): 28 bytes.
// Plus 3-byte flags AD = 31 bytes total (exact BLE adv limit).
// Device name "Pro Controller" goes in scan response.
// ============================================================
static const uint8_t kManufacturerData[] = {
    0x53, 0x05,                          // Company ID: 0x0553 (little-endian)
    0x01, 0x00,                          // [0-1]: type/version
    0x03,                                // [2]:   magic marker
    0x7E, 0x05,                          // [3-4]: VID 0x057E (LE)
    0x69, 0x20,                          // [5-6]: PID 0x2069 (LE)
    0x00,                                // [7]:   padding
    0x01,                                // [8]:   state/flags
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, // [9-14]: zeros
    0x00,                                // [15]:  zero
    0x0F,                                // [16]:  status byte
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, // [17-22]: zeros
    0x00,                                // [23]:  zero
};

// MAC for BT pairing responses — will be populated from actual BLE address in begin()
static uint8_t kPairingMAC[] = { 0xD8, 0x6B, 0xF7, 0x01, 0x02, 0x03 };

// ============================================================
// Button remapping: internal SW2_BTN_* → BLE report byte layout
//
// NSO GC BLE Protocol Guide input report (63 bytes):
//   [0]:    Counter
//   [1]:    Flags (0x20 at idle)
//   [2]:    Buttons byte 1: B(0x01) A(0x02) Y(0x04) X(0x08) R(0x10) ZR(0x20) +(0x40) RS(0x80)
//   [3]:    Buttons byte 2: Down(0x01) Right(0x02) Left(0x04) Up(0x08) L(0x10) ZL(0x20) -(0x40) LS(0x80)
//   [4]:    Buttons byte 3: Home(0x01) Capture(0x02) GR(0x04) GL(0x08) Chat(0x10)
//   [5-7]:  Left stick (12-bit packed)
//   [8-10]: Right stick (12-bit packed)
//   [11]:   Unknown (0x30 at idle)
//   [12]:   Left trigger (analog)
//   [13]:   Right trigger (analog)
//   [14-62]: IMU / padding
//
// We map from SW2_BTN_* enum to {byte_index, bit_mask} in the 3-byte button field.
// ============================================================
struct BleButtonMapping {
    uint8_t byteOffset;  // 0, 1, or 2 (relative to report[2])
    uint8_t bitMask;
};

static const BleButtonMapping kBtnMap[SW2_BTN_COUNT] = {
    {0, 0x01},  // SW2_BTN_B(0)       → byte2 bit0
    {0, 0x02},  // SW2_BTN_A(1)       → byte2 bit1
    {0, 0x04},  // SW2_BTN_Y(2)       → byte2 bit2
    {0, 0x08},  // SW2_BTN_X(3)       → byte2 bit3
    {0, 0x10},  // SW2_BTN_R(4)       → byte2 bit4
    {0, 0x20},  // SW2_BTN_ZR(5)      → byte2 bit5
    {0, 0x40},  // SW2_BTN_PLUS(6)    → byte2 bit6
    {0, 0x80},  // SW2_BTN_R3(7)      → byte2 bit7 (RS)
    {1, 0x01},  // SW2_BTN_DDOWN(8)   → byte3 bit0
    {1, 0x02},  // SW2_BTN_DRIGHT(9)  → byte3 bit1
    {1, 0x04},  // SW2_BTN_DLEFT(10)  → byte3 bit2
    {1, 0x08},  // SW2_BTN_DUP(11)    → byte3 bit3
    {1, 0x10},  // SW2_BTN_L(12)      → byte3 bit4
    {1, 0x20},  // SW2_BTN_ZL(13)     → byte3 bit5
    {1, 0x40},  // SW2_BTN_MINUS(14)  → byte3 bit6
    {1, 0x80},  // SW2_BTN_L3(15)     → byte3 bit7 (LS)
    {2, 0x01},  // SW2_BTN_HOME(16)   → byte4 bit0
    {2, 0x02},  // SW2_BTN_CAPTURE(17)→ byte4 bit1
    {2, 0x04},  // SW2_BTN_R4(18)     → byte4 bit2 (GR)
    {2, 0x08},  // SW2_BTN_L4(19)     → byte4 bit3 (GL)
    {2, 0x10},  // SW2_BTN_SQUARE(20) → byte4 bit4 (Chat)
};

// Legacy remap function kept for API compat (unused in new report builder)
uint32_t Switch2ProBLE::remapButtonsUsbToBle(uint32_t usbButtons) {
    uint32_t ble = 0;
    for (uint8_t i = 0; i < SW2_BTN_COUNT; i++) {
        if (usbButtons & (1UL << i)) {
            ble |= (1UL << i);  // identity for now
        }
    }
    return ble;
}

// ============================================================
// begin() — Initialize NimBLE GATT server + advertising
//
// Key requirements from NSO GC BLE Protocol Guide:
//   1. SMP Legacy "Just Works" (sc=false, mitm=false, bonding=true)
//      init_key_dist=0x02 (Identity Key only)
//      resp_key_dist=0x01 (Encryption Key only)
//      → Most stacks default to 0x03/0x03 which the Switch REJECTS (error 0x05)
//   2. MTU >= 185 (63-byte reports silently dropped at default 23-byte MTU)
//   3. Connectable advertising with company ID 0x0553 manufacturer data
//   4. Service enable: host writes 0x01 0x00 to secondary service char
// ============================================================
bool Switch2ProBLE::begin() {
    if (_active) return true;

    Serial.println("[SW2BLE] Initializing BLE Switch 2 Pro Controller...");

    // --- Nintendo OUI PUBLIC BLE address ---
    // NSO GC Protocol Guide: "Connect to the controller's **public** BLE address
    // using LE 1M PHY." Real controllers use PUBLIC addresses with Nintendo OUI.
    // We override the ESP32 base MAC before NimBLE init so the BLE public address
    // gets a Nintendo prefix. esp_base_mac_addr_set() only affects interfaces
    // that haven't been initialized yet, so WiFi (already running) keeps its MAC.
    //
    // Known Nintendo OUIs: 98:B6:E9, D8:6B:F7, 7C:BB:8A, 58:2F:40, etc.
    // Using 98:B6:E9 (common on Pro Controllers, avoids the 0xD8 random-static ambiguity).
    //
    // ESP-IDF derives BLE public addr = base_mac + 2, so we set base_mac accordingly.
    {
        uint8_t factoryMac[6] = {0};
        esp_read_mac(factoryMac, ESP_MAC_BT);  // read factory BT MAC for unique suffix
        uint8_t baseMac[6] = {
            0x98, 0xB6, 0xE9,           // Nintendo OUI
            factoryMac[3], factoryMac[4],
            static_cast<uint8_t>(factoryMac[5] - 2)  // base = bt_mac - 2 (ESP-IDF adds 2 for BT)
        };
        esp_base_mac_addr_set(baseMac);
        // Expected BLE public addr: 98:B6:E9:XX:XX:YY (YY = factoryMac[5])
        uint8_t expectedBle[6] = { 0x98, 0xB6, 0xE9, factoryMac[3], factoryMac[4], factoryMac[5] };
        memcpy(kPairingMAC, expectedBle, 6);
        Serial.printf("[SW2BLE] Base MAC set for Nintendo public BLE addr %02X:%02X:%02X:%02X:%02X:%02X\n",
                      expectedBle[0], expectedBle[1], expectedBle[2],
                      expectedBle[3], expectedBle[4], expectedBle[5]);
    }

    // Real Switch 2 controllers use "DeviceName" as their GAP Device Name
    // (observed via nRF Connect on actual Pro Controller, confirmed in NSO GC Protocol Guide)
    NimBLEDevice::init("DeviceName");
    NimBLEDevice::setPower(9, NimBLETxPowerType::All);

    // Use PUBLIC address type — real controllers use public BLE addresses.
    NimBLEDevice::setOwnAddrType(BLE_OWN_ADDR_PUBLIC);

    // --- SMP pairing configuration (CRITICAL) ---
    // NSO GC Protocol Guide: "the single most important detail"
    // Switch 2 requires Legacy Just Works with specific key distribution.
    // Secure Connections MUST be false — controller rejects SC.
    NimBLEDevice::setSecurityAuth(true, false, false);  // bonding=true, mitm=false, sc=false
    NimBLEDevice::setSecurityIOCap(BLE_HS_IO_NO_INPUT_OUTPUT);
    NimBLEDevice::setSecurityInitKey(0x02);  // Identity Key only (NOT default 0x03)
    NimBLEDevice::setSecurityRespKey(0x01);  // Encryption Key only (NOT default 0x03)

    // --- MTU (63-byte reports need MTU >= 185) ---
    NimBLEDevice::setMTU(185);

    _server = NimBLEDevice::createServer();
    _server->setCallbacks(this);

    // Create secondary service — "service control"
    // Host writes 0x01 0x00 to the second characteristic to activate the controller.
    NimBLEService* secSvc = _server->createService(kSecondaryServiceUUID);
    secSvc->createCharacteristic(kSecChar1UUID, NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::WRITE);
    _svcEnableChar = secSvc->createCharacteristic(kSecChar2UUID, NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::WRITE);
    _svcEnableChar->setCallbacks(this);
    secSvc->createCharacteristic(kSecChar3UUID, NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::WRITE);
    secSvc->start();

    // Create Nintendo GATT service with all characteristics matching real controller.
    NimBLEService* svc = _server->createService(kNintendoServiceUUID);

    // Input report (NOTIFY) — 63-byte controller reports
    _inputChar = svc->createCharacteristic(
        kInputCharUUID,
        NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY
    );
    _inputChar->setCallbacks(this);

    // Output/Cmd (NOTIFY + WRITE) — host sends commands and reads ACKs here
    _outCmdChar = svc->createCharacteristic(
        kOutCmdCharUUID,
        NIMBLE_PROPERTY::NOTIFY | NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::WRITE_NR
    );
    _outCmdChar->setCallbacks(this);

    // Additional write characteristics (handle all writes for command routing)
    auto* wc1 = svc->createCharacteristic(kWriteChar1UUID, NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::WRITE_NR);
    wc1->setCallbacks(this);
    auto* wc2 = svc->createCharacteristic(kWriteChar2UUID, NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::WRITE_NR);
    wc2->setCallbacks(this);
    auto* wc3 = svc->createCharacteristic(kWriteChar3UUID, NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::WRITE_NR);
    wc3->setCallbacks(this);
    auto* wc4 = svc->createCharacteristic(kWriteChar4UUID, NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::WRITE_NR);
    wc4->setCallbacks(this);

    // Additional notify characteristics
    svc->createCharacteristic(kNotifyChar1UUID, NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY);
    svc->createCharacteristic(kNotifyChar2UUID, NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY);
    svc->createCharacteristic(kNotifyChar3UUID, NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY);

    // FDE (NOTIFY) — may be used for ACKs by some hosts
    _ackChar = svc->createCharacteristic(
        kCharFDEUUID,
        NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY
    );
    _ackChar->setCallbacks(this);
    svc->createCharacteristic(kCharFDFUUID, NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::WRITE);

    svc->start();

    // --- Advertising ---
    // BLE adv packet max = 31 bytes.
    // Flags (3) + manufacturer data (1+1+26=28) = 31 → exactly fits.
    // Name goes in scan response.
    NimBLEAdvertising* pAdv = NimBLEDevice::getAdvertising();

    NimBLEAdvertisementData advData;
    // Real controller may support BR/EDR, so don't set BREDR_UNSUP.
    // Use only LE General Discoverable flag.
    advData.setFlags(BLE_HS_ADV_F_DISC_GEN);
    advData.setManufacturerData(
        std::string(reinterpret_cast<const char*>(kManufacturerData), sizeof(kManufacturerData)));
    pAdv->setAdvertisementData(advData);

    // Scan response with device name matching real controller
    NimBLEAdvertisementData scanRsp;
    scanRsp.setName("DeviceName");
    pAdv->setScanResponseData(scanRsp);

    pAdv->setMinInterval(0x20);  // 20ms
    pAdv->setMaxInterval(0x28);  // ~25ms (real controller: 20.51ms avg)
    pAdv->start();

    _active = true;
    _connected = false;
    _inputSubscribed = false;
    _ackSubscribed = false;
    _serviceEnabled = false;
    _buttons = 0;
    _lx = _ly = _rx = _ry = 0x80;
    _timer = 0;
    _lastReportMs = 0;
    _pendingReleaseMask = 0;

    // Log advertising details for diagnostics
    Serial.println("[SW2BLE] BLE Switch 2 Pro Controller advertising started");
    Serial.printf("[SW2BLE] Manufacturer data: %d bytes (company ID 0x0553)\n",
                  (int)sizeof(kManufacturerData));
    Serial.printf("[SW2BLE] Flags: LE General Discoverable (no BR/EDR Not Supported)\n");
    Serial.printf("[SW2BLE] Scan response name: DeviceName\n");
    Serial.printf("[SW2BLE] Address type: PUBLIC (Nintendo OUI 98:B6:E9)\n");
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
    _serviceEnabled = false;

    if (NimBLEDevice::isInitialized()) {
        NimBLEDevice::getAdvertising()->stop();
        NimBLEDevice::deinit(true);
    }
    _server = nullptr;
    _inputChar = nullptr;
    _ackChar = nullptr;
    _outCmdChar = nullptr;
    _svcEnableChar = nullptr;

    Serial.println("[SW2BLE] BLE shut down");
}

// ============================================================
// NimBLE server callbacks
// ============================================================
void Switch2ProBLE::onConnect(NimBLEServer* pServer, NimBLEConnInfo& connInfo) {
    _connected = true;
    Serial.printf("[SW2BLE] Host connected (addr: %s, type: %d)\n",
                  connInfo.getAddress().toString().c_str(),
                  connInfo.getAddress().getType());
}

void Switch2ProBLE::onDisconnect(NimBLEServer* pServer, NimBLEConnInfo& connInfo, int reason) {
    _connected = false;
    _inputSubscribed = false;
    _ackSubscribed = false;
    _serviceEnabled = false;
    Serial.printf("[SW2BLE] Host disconnected (reason: %d)\n", reason);

    if (_active) {
        NimBLEDevice::getAdvertising()->start();
        Serial.println("[SW2BLE] Re-advertising...");
    }
}

// ============================================================
// Characteristic subscribe callback — track CCCD notifications
// ============================================================
void Switch2ProBLE::onSubscribe(NimBLECharacteristic* pChar,
                                NimBLEConnInfo& connInfo,
                                uint16_t subValue) {
    bool notify = (subValue & 0x01) != 0;
    const char* uuid = pChar->getUUID().toString().c_str();

    if (pChar == _inputChar) {
        _inputSubscribed = notify;
        Serial.printf("[SW2BLE] Input CCCD %s\n", notify ? "ENABLED → sending reports" : "disabled");
    } else if (pChar == _outCmdChar) {
        _ackSubscribed = notify;
        Serial.printf("[SW2BLE] OutCmd CCCD %s\n", notify ? "ENABLED" : "disabled");
    } else if (pChar == _ackChar) {
        Serial.printf("[SW2BLE] FDE CCCD %s\n", notify ? "ENABLED" : "disabled");
    } else {
        Serial.printf("[SW2BLE] CCCD %s on %s\n", notify ? "ENABLED" : "disabled", uuid);
    }
}

// ============================================================
// Characteristic write callback (host → controller)
//
// Handles writes on ANY characteristic:
//   - Service enable (secondary svc char): 0x01 0x00 activates controller
//   - Command channel: SW2 protocol commands
//   - Combined rumble+cmd: command embedded at offset 27
//   - Rumble: silently accepted
// ============================================================
void Switch2ProBLE::onWrite(NimBLECharacteristic* pChar,
                            NimBLEConnInfo& connInfo) {
    NimBLEAttValue val = pChar->getValue();
    const uint8_t* data = val.data();
    uint16_t len = val.size();

    // Service enable write (secondary service characteristic)
    if (pChar == _svcEnableChar) {
        if (len >= 2 && data[0] == 0x01 && data[1] == 0x00) {
            _serviceEnabled = true;
            Serial.println("[SW2BLE] Service ENABLED by host (0x01 0x00)");
        } else {
            Serial.printf("[SW2BLE] Service enable write: len=%d", len);
            for (uint16_t i = 0; i < len && i < 8; i++) Serial.printf(" %02X", data[i]);
            Serial.println();
        }
        return;
    }

    if (len < 4) {
        Serial.printf("[SW2BLE] Short write (%d bytes) on %s\n", len,
                      pChar->getUUID().toString().c_str());
        return;
    }

    // Direct command format: [cmd, 0x91, 0x01(BLE), subcmd, ...]
    if (data[1] == 0x91 && (data[2] == 0x01 || data[2] == 0x00)) {
        handleCommand(data, len);
        return;
    }

    // Combined rumble+cmd format: command at offset 27
    if (len >= 31 && data[28] == 0x91 && (data[29] == 0x01 || data[29] == 0x00)) {
        handleCommand(data + 27, len - 27);
        return;
    }

    // Log unrecognized writes for debugging
    Serial.printf("[SW2BLE] Write on %s len=%d:",
                  pChar->getUUID().toString().c_str(), len);
    for (uint16_t i = 0; i < len && i < 16; i++) Serial.printf(" %02X", data[i]);
    if (len > 16) Serial.printf("...");
    Serial.println();
}

// ============================================================
// Command handling
//
// Format: [cmd, 0x91(REQ), interface(0x00=USB/0x01=BLE), subcmd, ...]
// ============================================================
void Switch2ProBLE::handleCommand(const uint8_t* data, uint16_t len) {
    if (len < 4) return;

    uint8_t cmd = data[0];
    uint8_t subcmd = data[3];

    Serial.printf("[SW2BLE] CMD 0x%02X sub 0x%02X len=%d", cmd, subcmd, len);
    if (len > 4) {
        Serial.printf(" data:");
        for (uint16_t i = 4; i < len && i < 24; i++) Serial.printf(" %02X", data[i]);
        if (len > 24) Serial.printf("...");
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

        case 0x03:  // Init/Enable
            if (subcmd == 0x0D) {
                Serial.println("[SW2BLE] Init: HID reports enabled (0x03/0x0D)");
            } else if (subcmd == 0x0A) {
                Serial.println("[SW2BLE] Init: Haptics enabled (0x03/0x0A)");
            }
            sendAck(cmd, subcmd, nullptr, 0);
            break;

        case 0x07:  // Unknown init cmd (seen in procon2tool sequence)
            Serial.printf("[SW2BLE] Init cmd 0x07 sub 0x%02X\n", subcmd);
            sendAck(cmd, subcmd, nullptr, 0);
            break;

        case 0x09:  // Set LED
            if (subcmd == 0x07 && len >= 9) {
                Serial.printf("[SW2BLE] Set LED pattern=0x%02X\n", data[8]);
            }
            sendAck(cmd, subcmd, nullptr, 0);
            break;

        case 0x0C:  // IMU config
            Serial.printf("[SW2BLE] IMU config sub=0x%02X\n", subcmd);
            sendAck(cmd, subcmd, nullptr, 0);
            break;

        case 0x11:  // Unknown (seen in procon2tool)
            Serial.printf("[SW2BLE] Cmd 0x11 sub=0x%02X\n", subcmd);
            sendAck(cmd, subcmd, nullptr, 0);
            break;

        case 0x15:  // Proprietary pairing (4-step crypto handshake)
            handlePairingCommand(data, len, subcmd);
            break;

        case 0x16:  // Unknown init cmd (seen in procon2tool)
            Serial.printf("[SW2BLE] Cmd 0x16 sub=0x%02X\n", subcmd);
            sendAck(cmd, subcmd, nullptr, 0);
            break;

        default:
            Serial.printf("[SW2BLE] Unknown CMD 0x%02X → generic ACK\n", cmd);
            sendAck(cmd, subcmd, nullptr, 0);
            break;
    }
}

// ============================================================
// Handle proprietary pairing (cmd 0x15)
//
// NSO GC Protocol Guide: 4-step handshake
//   Step 1 (sub 0x01): Host sends its BLE address → controller ACKs with its MAC
//   Step 2 (sub 0x04): Crypto nonce
//   Step 3 (sub 0x02): Crypto nonce
//   Step 4 (sub 0x03): Finalize
// ============================================================
void Switch2ProBLE::handlePairingCommand(const uint8_t* data, uint16_t len, uint8_t subcmd) {
    Serial.printf("[SW2BLE] Pairing step sub=0x%02X\n", subcmd);

    switch (subcmd) {
        case 0x01: {
            // Host sends its address — respond with our MAC
            uint8_t val[16];
            memset(val, 0, sizeof(val));
            // Echo some of the request params
            if (len > 4) {
                size_t copyLen = (len - 4 > 12) ? 12 : (len - 4);
                memcpy(val, data + 4, copyLen);
            }
            // Place our MAC at expected offset
            memcpy(val + 4, kPairingMAC, 6);
            sendAck(0x15, subcmd, val, 16);
            break;
        }
        case 0x02:  // LTK/crypto step
        case 0x03:  // Finalize
        case 0x04:  // Crypto nonce step
        default:
            // ACK with zeroed value — crypto details TBD
            sendAck(0x15, subcmd, nullptr, 0);
            break;
    }
}

// ============================================================
// Send ACK on the outCmd characteristic (NOTIFY)
//
// Format: [cmd, 0x01(RSP), 0x01(BLE), subcmd, ...value]
// ============================================================
void Switch2ProBLE::sendAck(uint8_t cmd, uint8_t subcmd,
                            const uint8_t* valuePayload, size_t valueLen) {
    if (!_outCmdChar || !_connected) return;

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

    _outCmdChar->setValue(buf, total);
    _outCmdChar->notify();
}

// ============================================================
// Handle SPI flash read — return canned data at correct offsets
//
// Request format: [0x02, 0x91, iface, 0x04, flags(2), pad(2), readLen, region(3), addr(4)]
//
// ACK value: echo request params[4..15] in value[0..11], SPI data at value[12+]
// ============================================================
void Switch2ProBLE::handleSpiRead(const uint8_t* data, uint16_t len) {
    if (len < 16) return;

    uint8_t readLen = data[8];
    uint32_t addr = (uint32_t)data[12] | ((uint32_t)data[13] << 8) |
                    ((uint32_t)data[14] << 16) | ((uint32_t)data[15] << 24);

    Serial.printf("[SW2BLE] SPI read addr=0x%06lX len=%d\n",
                  static_cast<unsigned long>(addr), readLen);

    uint8_t value[60];
    memset(value, 0, sizeof(value));

    // Echo request params data[4..15] → value[0..11]
    uint8_t echoLen = (len >= 16) ? 12 : (len - 4);
    memcpy(value, data + 4, echoLen);

    size_t spiDataOffset = 12;
    size_t maxSpi = sizeof(value) - spiDataOffset;
    size_t spiLen = (readLen > maxSpi) ? maxSpi : readLen;

    switch (addr) {
        case 0x00013000: {
            // Device info — VID at spiData[18], PID at spiData[20]
            size_t vidOff = 18;
            size_t pidOff = 20;
            if (vidOff + 2 <= spiLen) {
                value[spiDataOffset + vidOff]     = 0x7E;
                value[spiDataOffset + vidOff + 1] = 0x05;
            }
            if (pidOff + 2 <= spiLen) {
                value[spiDataOffset + pidOff]     = 0x69;
                value[spiDataOffset + pidOff + 1] = 0x20;
            }
            break;
        }

        case 0x001fa000: // Pairing data (LTK at offset 0x1A within block)
        case 0x001fa01a: // Direct LTK read
            // Zeroed = no stored pairing key
            break;

        case 0x00013080: {
            // Left factory stick calibration at spiData[40]
            size_t calOff = 40;
            if (calOff + 9 <= spiLen) {
                uint8_t* cal = &value[spiDataOffset + calOff];
                cal[0] = 0x00; cal[1] = 0x08; cal[2] = 0x80;  // neutral 0x800
                cal[3] = 0x00; cal[4] = 0x06; cal[5] = 0x60;  // max range
                cal[6] = 0x00; cal[7] = 0x06; cal[8] = 0x60;  // min range
            }
            break;
        }

        case 0x000130c0: {
            // Right factory stick calibration
            size_t calOff = 40;
            if (calOff + 9 <= spiLen) {
                uint8_t* cal = &value[spiDataOffset + calOff];
                cal[0] = 0x00; cal[1] = 0x08; cal[2] = 0x80;
                cal[3] = 0x00; cal[4] = 0x06; cal[5] = 0x60;
                cal[6] = 0x00; cal[7] = 0x06; cal[8] = 0x60;
            }
            break;
        }

        case 0x001fc040: {
            // User calibration — 0xFF = no user cal
            size_t leftOff = 2;
            size_t rightOff = 34;
            if (leftOff < spiLen) value[spiDataOffset + leftOff] = 0xFF;
            if (rightOff < spiLen) value[spiDataOffset + rightOff] = 0xFF;
            break;
        }

        default:
            Serial.printf("[SW2BLE] Unknown SPI addr 0x%06lX → zeroed\n",
                          static_cast<unsigned long>(addr));
            break;
    }

    size_t totalValueLen = spiDataOffset + spiLen;
    if (totalValueLen > sizeof(value)) totalValueLen = sizeof(value);
    sendAck(0x02, 0x04, value, totalValueLen);
}

// ============================================================
// Input report builder (63 bytes, NSO GC BLE Protocol Guide format)
//
// [0]:    Counter
// [1]:    Flags (0x20 at idle)
// [2-4]:  Buttons (3 bytes)
// [5-7]:  Left stick (12-bit packed)
// [8-10]: Right stick (12-bit packed)
// [11]:   0x30 (idle marker)
// [12]:   Left trigger
// [13]:   Right trigger
// [14-62]: IMU / padding
// ============================================================
void Switch2ProBLE::sendInputReport() {
    if (!_inputChar || !_connected) return;

    uint8_t report[63];
    memset(report, 0, sizeof(report));

    report[0] = _timer++;
    report[1] = 0x20;  // flags byte (idle)

    // Buttons: map from internal SW2_BTN_* to 3-byte field at [2-4]
    for (uint8_t i = 0; i < SW2_BTN_COUNT; i++) {
        if (_buttons & (1UL << i)) {
            report[2 + kBtnMap[i].byteOffset] |= kBtnMap[i].bitMask;
        }
    }

    // Scale 8-bit (0x00-0xFF) to 12-bit (0x000-0xFF0)
    uint16_t lx12 = static_cast<uint16_t>(_lx) << 4;
    uint16_t ly12 = static_cast<uint16_t>(_ly) << 4;
    uint16_t rx12 = static_cast<uint16_t>(_rx) << 4;
    uint16_t ry12 = static_cast<uint16_t>(_ry) << 4;

    // [5-7]: left stick (12-bit packed)
    report[5] = lx12 & 0xFF;
    report[6] = ((lx12 >> 8) & 0x0F) | ((ly12 & 0x0F) << 4);
    report[7] = (ly12 >> 4) & 0xFF;

    // [8-10]: right stick (12-bit packed)
    report[8] = rx12 & 0xFF;
    report[9] = ((rx12 >> 8) & 0x0F) | ((ry12 & 0x0F) << 4);
    report[10] = (ry12 >> 4) & 0xFF;

    report[11] = 0x30;  // idle marker

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

    // Process deferred releases
    if (_pendingReleaseMask && (millis() - _lastPressMs >= kMinHoldMs)) {
        _buttons &= ~_pendingReleaseMask;
        _pendingReleaseMask = 0;
    }

    // 8ms cadence (~125 Hz)
    if (millis() - _lastReportMs < 8) return;
    _lastReportMs = millis();

    sendInputReport();
}
