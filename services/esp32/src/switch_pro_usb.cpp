#include "switch_pro_usb.h"

#if defined(CONFIG_IDF_TARGET_ESP32S3)
#if CONFIG_TINYUSB_HID_ENABLED

#include <cstring>

// ============================================================
// Pro Controller USB HID descriptor
// Matches the real Pro Controller descriptor with vendor-defined
// report IDs for input (0x30, 0x21, 0x81) and output (0x01, 0x10, 0x80, 0x82).
// Reference: radiantwf/esp32-circuitpython-switch-joystick
// ============================================================
static const uint8_t kProControllerDescriptor[] = {
    0x05, 0x01,                     // Usage Page (Generic Desktop)
    0x15, 0x00,                     // Logical Minimum (0)
    0x09, 0x04,                     // Usage (Joystick)
    0xA1, 0x01,                     // Collection (Application)

    // --- Report ID 0x30: Standard full input report ---
    0x85, 0x30,                     //   Report ID (0x30)
    0x05, 0x01,                     //   Usage Page (Generic Desktop)
    0x05, 0x09,                     //   Usage Page (Button)
    0x19, 0x01,                     //   Usage Minimum (1)
    0x29, 0x0A,                     //   Usage Maximum (10)
    0x15, 0x00,                     //   Logical Minimum (0)
    0x25, 0x01,                     //   Logical Maximum (1)
    0x75, 0x01,                     //   Report Size (1)
    0x95, 0x0A,                     //   Report Count (10)
    0x55, 0x00,                     //   Unit Exponent (0)
    0x65, 0x00,                     //   Unit (None)
    0x81, 0x02,                     //   Input (Data,Var,Abs)
    0x05, 0x09,                     //   Usage Page (Button)
    0x19, 0x0B,                     //   Usage Minimum (11)
    0x29, 0x0E,                     //   Usage Maximum (14)
    0x15, 0x00,                     //   Logical Minimum (0)
    0x25, 0x01,                     //   Logical Maximum (1)
    0x75, 0x01,                     //   Report Size (1)
    0x95, 0x04,                     //   Report Count (4)
    0x81, 0x02,                     //   Input (Data,Var,Abs)
    0x75, 0x01,                     //   Report Size (1)
    0x95, 0x02,                     //   Report Count (2)
    0x81, 0x03,                     //   Input (Const)
    0x0B, 0x01, 0x00, 0x01, 0x00,  //   Usage (Generic Desktop: Pointer)
    0xA1, 0x00,                     //   Collection (Physical)
    0x0B, 0x30, 0x00, 0x01, 0x00,  //     Usage (X)
    0x0B, 0x31, 0x00, 0x01, 0x00,  //     Usage (Y)
    0x0B, 0x32, 0x00, 0x01, 0x00,  //     Usage (Z)
    0x0B, 0x35, 0x00, 0x01, 0x00,  //     Usage (Rz)
    0x15, 0x00,                     //     Logical Minimum (0)
    0x27, 0xFF, 0xFF, 0x00, 0x00,  //     Logical Maximum (65534)
    0x75, 0x10,                     //     Report Size (16)
    0x95, 0x04,                     //     Report Count (4)
    0x81, 0x02,                     //     Input (Data,Var,Abs)
    0xC0,                           //   End Collection
    0x0B, 0x39, 0x00, 0x01, 0x00,  //   Usage (Hat Switch)
    0x15, 0x00,                     //   Logical Minimum (0)
    0x25, 0x07,                     //   Logical Maximum (7)
    0x35, 0x00,                     //   Physical Minimum (0)
    0x46, 0x3B, 0x01,              //   Physical Maximum (315)
    0x65, 0x14,                     //   Unit (Degrees)
    0x75, 0x04,                     //   Report Size (4)
    0x95, 0x01,                     //   Report Count (1)
    0x81, 0x02,                     //   Input (Data,Var,Abs)
    0x05, 0x09,                     //   Usage Page (Button)
    0x19, 0x0F,                     //   Usage Minimum (15)
    0x29, 0x12,                     //   Usage Maximum (18)
    0x15, 0x00,                     //   Logical Minimum (0)
    0x25, 0x01,                     //   Logical Maximum (1)
    0x75, 0x01,                     //   Report Size (1)
    0x95, 0x04,                     //   Report Count (4)
    0x81, 0x02,                     //   Input (Data,Var,Abs)
    0x75, 0x08,                     //   Report Size (8)
    0x95, 0x34,                     //   Report Count (52)
    0x81, 0x03,                     //   Input (Const) — IMU data placeholder

    // --- Report ID 0x21: Subcommand reply ---
    0x06, 0x00, 0xFF,              //   Usage Page (Vendor Defined 0xFF00)
    0x85, 0x21,                     //   Report ID (0x21)
    0x09, 0x01,                     //   Usage (Vendor 0x01)
    0x75, 0x08,                     //   Report Size (8)
    0x95, 0x3F,                     //   Report Count (63)
    0x81, 0x03,                     //   Input (Const)

    // --- Report ID 0x81: USB command reply ---
    0x85, 0x81,                     //   Report ID (0x81)
    0x09, 0x02,                     //   Usage (Vendor 0x02)
    0x75, 0x08,                     //   Report Size (8)
    0x95, 0x3F,                     //   Report Count (63)
    0x81, 0x03,                     //   Input (Const)

    // --- Report ID 0x01: Subcommand output ---
    0x85, 0x01,                     //   Report ID (0x01)
    0x09, 0x03,                     //   Usage (Vendor 0x03)
    0x75, 0x08,                     //   Report Size (8)
    0x95, 0x3F,                     //   Report Count (63)
    0x91, 0x83,                     //   Output (Const,Var,Abs,Vol)

    // --- Report ID 0x10: Rumble output ---
    0x85, 0x10,                     //   Report ID (0x10)
    0x09, 0x04,                     //   Usage (Vendor 0x04)
    0x75, 0x08,                     //   Report Size (8)
    0x95, 0x3F,                     //   Report Count (63)
    0x91, 0x83,                     //   Output (Const,Var,Abs,Vol)

    // --- Report ID 0x80: USB handshake output ---
    0x85, 0x80,                     //   Report ID (0x80)
    0x09, 0x05,                     //   Usage (Vendor 0x05)
    0x75, 0x08,                     //   Report Size (8)
    0x95, 0x3F,                     //   Report Count (63)
    0x91, 0x83,                     //   Output (Const,Var,Abs,Vol)

    // --- Report ID 0x82: Additional output ---
    0x85, 0x82,                     //   Report ID (0x82)
    0x09, 0x06,                     //   Usage (Vendor 0x06)
    0x75, 0x08,                     //   Report Size (8)
    0x95, 0x3F,                     //   Report Count (63)
    0x91, 0x83,                     //   Output (Const,Var,Abs,Vol)

    0xC0,                           // End Collection
};

// MAC address for USB handshake
static const uint8_t kMacAddr[6] = {0x00, 0x00, 0x5E, 0x00, 0x53, 0x5E};

// ============================================================
// SPI flash data tables (same as switch_pro_bt.cpp)
// ============================================================
static const uint8_t kSpiSerial[16] = {
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
};

static const uint8_t kSpiUserStickCal[22] = {
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
};

static const uint8_t kSpiColor[13] = {
    0x32, 0x32, 0x32,  // body (grey)
    0xFF, 0xFF, 0xFF,  // buttons (white)
    0xFF, 0xFF, 0xFF,  // left grip
    0xFF, 0xFF, 0xFF,  // right grip
    0xFF,
};

static const uint8_t kSpiFactoryStickCal[24] = {
    0x50, 0xFD, 0x00, 0x00, 0xC6, 0x0F,
    0x0F, 0x30, 0x61, 0x96, 0x30, 0xF3,
    0xD4, 0x14, 0x54, 0x41, 0x15, 0x54,
    0xC7, 0x79, 0x9C, 0x33, 0x36, 0x63,
};

static const uint8_t kSpiFactorySensorStick[24] = {
    0x50, 0xFD, 0x00, 0x00, 0xC6, 0x0F,
    0x00, 0x40, 0x00, 0x40, 0x00, 0x40,
    0xFA, 0xFF, 0xD0, 0xFF, 0xC7, 0xFF,
    0x3B, 0x34, 0x3B, 0x34, 0x3B, 0x34,
};

static const uint8_t kSpiFactoryStickCal2[18] = {
    0x0F, 0x30, 0x61, 0x96, 0x30, 0xF3,
    0xD4, 0x14, 0x54, 0x41, 0x15, 0x54,
    0xC7, 0x79, 0x9C, 0x33, 0x36, 0x63,
};

static const uint8_t kSpiFactorySensorCal[24] = {
    0xBE, 0xFF, 0x3E, 0x00, 0xF0, 0x01,
    0x00, 0x40, 0x00, 0x40, 0x00, 0x40,
    0xFE, 0xFF, 0xFE, 0xFF, 0x08, 0x00,
    0xE7, 0x3B, 0xE7, 0x3B, 0xE7, 0x3B,
};

static const uint8_t kSpiUserMotionCal[24] = {
    0x09, 0x01, 0x18, 0xFF, 0xED, 0xFF,
    0x00, 0x40, 0x00, 0x40, 0x00, 0x40,
    0xFA, 0xFF, 0xD0, 0xFF, 0xC7, 0xFF,
    0x3B, 0x34, 0x3B, 0x34, 0x3B, 0x34,
};

// Report size: 63 bytes (report ID is sent separately by TinyUSB)
static constexpr size_t kReportLen = 63;

// ============================================================
// Constructor / begin / end
// ============================================================
SwitchProUSB::SwitchProUSB() : _hid() {
    static bool registered = false;
    // Set Pro Controller USB identity BEFORE begin()
    USB.VID(0x057E);
    USB.PID(0x2009);
    USB.usbClass(0);
    USB.usbSubClass(0);
    USB.usbProtocol(0);
    USB.manufacturerName("Nintendo Co., Ltd.");
    USB.productName("Pro Controller");
    if (!registered) {
        registered = true;
        _hid.addDevice(this, sizeof(kProControllerDescriptor));
    }
    end();
}

void SwitchProUSB::begin() {
    _hid.begin();
    end();
}

void SwitchProUSB::end() {
    _buttons = 0;
    _dpad = 0x0F;
    _lx = _ly = _rx = _ry = 0x80;
    _connected = false;
}

// ============================================================
// Descriptor callback
// ============================================================
uint16_t SwitchProUSB::_onGetDescriptor(uint8_t* buffer) {
    memcpy(buffer, kProControllerDescriptor, sizeof(kProControllerDescriptor));
    return sizeof(kProControllerDescriptor);
}

// ============================================================
// Output report callback — Switch sends us commands here
// ============================================================
void SwitchProUSB::_onOutput(uint8_t report_id, const uint8_t* buffer, uint16_t len) {
    switch (report_id) {
        case 0x80:
            handleUsbCommand(buffer, len);
            break;
        case 0x01:
            handleSubcommand(buffer, len);
            break;
        case 0x10:
            // Rumble-only output — just ACK by continuing to send 0x30 reports
            break;
        default:
            Serial.printf("[SwitchProUSB] unknown output report 0x%02X len=%d\n", report_id, len);
            break;
    }
}

// ============================================================
// USB-specific 0x80 handshake (not used in BT)
// ============================================================
void SwitchProUSB::handleUsbCommand(const uint8_t* data, uint16_t len) {
    if (len < 1) return;
    uint8_t cmd = data[0];

    uint8_t reply[kReportLen];
    memset(reply, 0, sizeof(reply));

    switch (cmd) {
        case 0x01:
            // MAC address request
            Serial.println("[SwitchProUSB] 0x80 handshake: MAC request");
            reply[0] = 0x01;
            reply[1] = 0x00;
            reply[2] = 0x03;
            memcpy(&reply[3], kMacAddr, 6);
            sendInputReport(0x81, reply, sizeof(reply));
            break;
        case 0x02:
            // Handshake
            Serial.println("[SwitchProUSB] 0x80 handshake: handshake");
            reply[0] = 0x02;
            sendInputReport(0x81, reply, sizeof(reply));
            break;
        case 0x03:
            // Baudrate? / 3rd handshake step
            Serial.println("[SwitchProUSB] 0x80 handshake: step 3");
            reply[0] = 0x03;
            sendInputReport(0x81, reply, sizeof(reply));
            break;
        case 0x04:
            // USB connected — start sending standard reports
            Serial.println("[SwitchProUSB] 0x80: connected!");
            _connected = true;
            break;
        case 0x05:
            // USB disconnected
            Serial.println("[SwitchProUSB] 0x80: disconnected");
            _connected = false;
            break;
        default:
            Serial.printf("[SwitchProUSB] 0x80 unknown cmd 0x%02X\n", cmd);
            reply[0] = cmd;
            sendInputReport(0x81, reply, sizeof(reply));
            break;
    }
}

// ============================================================
// Subcommand handler (report ID 0x01)
// Same protocol as Bluetooth — data[0] = counter, [1..8] = rumble,
// data[9] = subcommand ID, data[10+] = subcommand params
// ============================================================
void SwitchProUSB::handleSubcommand(const uint8_t* data, uint16_t len) {
    if (len < 10) return;
    uint8_t subcmd = data[9];

    switch (subcmd) {
        case 0x01:  // Manual pairing
            sendSubcommandReply(0x01, 0x81, nullptr, 0);
            break;
        case 0x02: {
            // Device info
            uint8_t info[12] = {0};
            info[0] = 0x04; info[1] = 0x21;  // FW version
            info[2] = 0x03;                   // Pro Controller
            info[3] = 0x02;
            memcpy(&info[4], kMacAddr, 6);
            info[10] = 0x01;
            info[11] = 0x01;                  // SPI color available
            sendSubcommandReply(0x02, 0x82, info, sizeof(info));
            break;
        }
        case 0x03:  // Set input mode
        case 0x08:  // Set shipment state
        case 0x30:  // Set player lights
        case 0x33:  // Set player flash
        case 0x38:  // Set HOME light
        case 0x40:  // Enable IMU
        case 0x41:  // Set IMU sensitivity
        case 0x48:  // Enable vibration
            sendSubcommandReply(subcmd, 0x80, nullptr, 0);
            break;
        case 0x04: {
            // Trigger buttons elapsed time
            uint8_t td[14];
            uint16_t t = static_cast<uint16_t>((millis() / 10) & 0xFFFF);
            for (int i = 0; i < 7; i++) { td[i*2] = t & 0xFF; td[i*2+1] = (t >> 8) & 0xFF; }
            sendSubcommandReply(0x04, 0x83, td, sizeof(td));
            break;
        }
        case 0x10:
            // SPI flash read
            handleSpiFlashRead(data, len);
            break;
        case 0x21:  // Set NFC/IR MCU configuration
        {
            uint8_t nfcReply[8] = {0x01, 0x00, 0xFF, 0x00, 0x08, 0x00, 0x1B, 0x01};
            sendSubcommandReply(0x21, 0xA0, nfcReply, sizeof(nfcReply));
            break;
        }
        case 0x22:  // Set NFC/IR MCU state
            sendSubcommandReply(subcmd, 0x80, nullptr, 0);
            break;
        default:
            Serial.printf("[SwitchProUSB] unknown subcmd 0x%02X\n", subcmd);
            sendSubcommandReply(subcmd, 0x80, nullptr, 0);
            break;
    }
}

// ============================================================
// SPI flash read handler
// ============================================================
void SwitchProUSB::handleSpiFlashRead(const uint8_t* data, uint16_t len) {
    if (len < 15) { sendSubcommandReply(0x10, 0x80, nullptr, 0); return; }

    uint32_t addr = static_cast<uint32_t>(data[10]) | (static_cast<uint32_t>(data[11]) << 8) |
                    (static_cast<uint32_t>(data[12]) << 16) | (static_cast<uint32_t>(data[13]) << 24);
    uint8_t reqLen = data[14];
    if (reqLen > 29) reqLen = 29;

    uint8_t resp[34] = {0};
    resp[0] = addr & 0xFF;
    resp[1] = (addr >> 8) & 0xFF;
    resp[2] = (addr >> 16) & 0xFF;
    resp[3] = (addr >> 24) & 0xFF;
    resp[4] = reqLen;

    const uint8_t* src = nullptr;
    size_t srcLen = 0;
    switch (addr) {
        case 0x6000: src = kSpiSerial;            srcLen = sizeof(kSpiSerial); break;
        case 0x6020: src = kSpiFactorySensorStick; srcLen = sizeof(kSpiFactorySensorStick); break;
        case 0x603D: src = kSpiUserStickCal;      srcLen = sizeof(kSpiUserStickCal); break;
        case 0x6050: src = kSpiColor;             srcLen = sizeof(kSpiColor); break;
        case 0x6080: src = kSpiFactoryStickCal;   srcLen = sizeof(kSpiFactoryStickCal); break;
        case 0x6086: src = kSpiFactorySensorStick; srcLen = sizeof(kSpiFactorySensorStick); break;
        case 0x6098: src = kSpiFactoryStickCal2;  srcLen = sizeof(kSpiFactoryStickCal2); break;
        case 0x8010: src = kSpiFactorySensorCal;  srcLen = sizeof(kSpiFactorySensorCal); break;
        case 0x8026: // User 6-axis motion sensor cal — some hosts read from here
        case 0x6020 + 6: // Alternate offset
            src = kSpiUserMotionCal; srcLen = sizeof(kSpiUserMotionCal); break;
        default: break;
    }

    if (src) {
        size_t copyLen = (reqLen < srcLen) ? reqLen : srcLen;
        memcpy(&resp[5], src, copyLen);
        if (copyLen < reqLen) memset(&resp[5 + copyLen], 0xFF, reqLen - copyLen);
    } else {
        memset(&resp[5], 0xFF, reqLen);
    }

    sendSubcommandReply(0x10, 0x90, resp, 5 + reqLen);
}

// ============================================================
// Report senders
// ============================================================
void SwitchProUSB::sendInputReport(uint8_t reportId, const uint8_t* data, size_t len) {
    _hid.SendReport(reportId, data, len);
}

void SwitchProUSB::fillInputHeader(uint8_t* buf) {
    // Convert 8-bit axes to 12-bit for Pro Controller encoding
    uint16_t lx12 = static_cast<uint16_t>(_lx) << 4;  // 0x00→0, 0x80→2048, 0xFF→4080
    uint16_t ly12 = static_cast<uint16_t>(255 - _ly) << 4;  // Y inverted for Pro Controller
    uint16_t rx12 = static_cast<uint16_t>(_rx) << 4;
    uint16_t ry12 = static_cast<uint16_t>(255 - _ry) << 4;

    // Convert NSGamepad-style 16-bit buttons + dpad to Pro Controller 3-byte format
    uint8_t btnRight = 0, btnShared = 0, btnLeft = 0;

    // NSGamepad button bits → Pro Controller right byte
    if (_buttons & (1 << 0))  btnRight |= 0x01;  // Y
    if (_buttons & (1 << 1))  btnRight |= 0x04;  // B
    if (_buttons & (1 << 2))  btnRight |= 0x08;  // A
    if (_buttons & (1 << 3))  btnRight |= 0x02;  // X
    if (_buttons & (1 << 4))  btnRight |= 0x40;  // L → R shoulder? No: L trigger
    if (_buttons & (1 << 5))  btnRight |= 0x40;  // R
    if (_buttons & (1 << 6))  btnRight |= 0x80;  // ZL → mapped to ZR byte? No
    if (_buttons & (1 << 7))  btnRight |= 0x80;  // ZR

    // Shared byte
    if (_buttons & (1 << 8))  btnShared |= 0x01;  // Minus
    if (_buttons & (1 << 9))  btnShared |= 0x02;  // Plus
    if (_buttons & (1 << 10)) btnShared |= 0x08;  // LStick
    if (_buttons & (1 << 11)) btnShared |= 0x04;  // RStick
    if (_buttons & (1 << 12)) btnShared |= 0x10;  // Home
    if (_buttons & (1 << 13)) btnShared |= 0x20;  // Capture

    // Left byte — dpad as bitmask
    if (_dpad == 0 || _dpad == 1 || _dpad == 7) btnLeft |= 0x02;  // Up
    if (_dpad == 2 || _dpad == 1 || _dpad == 3) btnLeft |= 0x04;  // Right
    if (_dpad == 4 || _dpad == 3 || _dpad == 5) btnLeft |= 0x01;  // Down
    if (_dpad == 6 || _dpad == 5 || _dpad == 7) btnLeft |= 0x08;  // Left

    // L/ZL on left byte
    if (_buttons & (1 << 4))  btnLeft |= 0x40;   // L
    if (_buttons & (1 << 6))  btnLeft |= 0x80;   // ZL

    buf[0] = _timer++;
    buf[1] = 0x8E;       // battery full, Pro Controller, USB powered
    buf[2] = btnRight;
    buf[3] = btnShared;
    buf[4] = btnLeft;
    packStick12bit(lx12, ly12, &buf[5]);
    packStick12bit(rx12, ry12, &buf[8]);
    buf[11] = 0x08;      // vibration ACK
    // Bytes 12+ = IMU data (zeros = stationary)
}

void SwitchProUSB::sendStandardInputReport() {
    uint8_t payload[kReportLen];
    memset(payload, 0, sizeof(payload));
    fillInputHeader(payload);
    sendInputReport(0x30, payload, sizeof(payload));
}

void SwitchProUSB::sendSubcommandReply(uint8_t subcmd, uint8_t ackByte, const uint8_t* data, size_t dataLen) {
    uint8_t buf[kReportLen];
    memset(buf, 0, sizeof(buf));
    fillInputHeader(buf);
    buf[12] = ackByte;
    buf[13] = subcmd;
    if (data && dataLen > 0) {
        size_t maxCopy = kReportLen - 14;
        memcpy(&buf[14], data, (dataLen < maxCopy) ? dataLen : maxCopy);
    }
    sendInputReport(0x21, buf, sizeof(buf));
}

// ============================================================
// Stick packing (12-bit LE, 3 bytes for 2 values)
// ============================================================
void SwitchProUSB::packStick12bit(uint16_t x, uint16_t y, uint8_t out[3]) {
    out[0] = x & 0xFF;
    out[1] = static_cast<uint8_t>(((x >> 8) & 0x0F) | ((y & 0x0F) << 4));
    out[2] = static_cast<uint8_t>((y >> 4) & 0xFF);
}

// ============================================================
// Button press/release (NSGamepad-compatible bit indices)
// ============================================================
void SwitchProUSB::press(uint8_t b) {
    if (b > 15) b = 15;
    _buttons |= (uint16_t)1 << b;
}

void SwitchProUSB::release(uint8_t b) {
    if (b > 15) b = 15;
    _buttons &= ~((uint16_t)1 << b);
}

// ============================================================
// write / loop
// ============================================================
bool SwitchProUSB::write() {
    sendStandardInputReport();
    return true;
}

void SwitchProUSB::loop() {
    // Send 0x30 reports at ~8ms cadence when connected
    if (!_connected) return;
    if (millis() - _lastReportMs < 8) return;
    _lastReportMs = millis();
    sendStandardInputReport();
}

#endif  // CONFIG_TINYUSB_HID_ENABLED
#endif  // CONFIG_IDF_TARGET_ESP32S3
