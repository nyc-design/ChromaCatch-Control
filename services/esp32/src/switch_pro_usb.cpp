#include "switch_pro_usb.h"

#if defined(CONFIG_IDF_TARGET_ESP32S3)
#if CONFIG_TINYUSB_HID_ENABLED

#include <cstring>
#include "class/hid/hid_device.h"

// Global instance pointer for the --wrap callbacks.
SwitchProUSB* g_switchProUsbDevice = nullptr;

// ============================================================
// Static device descriptor — Pro Controller VID/PID identity.
//
// We only override the device descriptor (VID/PID/strings) via
// --wrap. The config descriptor and BOS are left to Arduino/
// TinyUSB because they must match the actual endpoint allocation
// done by the HID class driver internally.
// ============================================================
static const uint8_t s_deviceDesc[18] = {
    18, 0x01,           // bLength, bDescriptorType = DEVICE
    0x00, 0x02,         // bcdUSB = 0x0200 (USB 2.0 LE)
    0x00, 0x00, 0x00,   // bDeviceClass/SubClass/Protocol = 0
    64,                  // bMaxPacketSize0
    0x5E, 0x07,         // idVendor = 0x057E (LE)
    0x09, 0x20,         // idProduct = 0x2009 (LE)
    0x00, 0x02,         // bcdDevice = 0x0200 (LE)
    0x01, 0x02, 0x00,   // iManufacturer=1, iProduct=2, iSerialNumber=0
    0x01,               // bNumConfigurations = 1
};

// ============================================================
// Pro Controller HID descriptor — identical to the BT descriptor
// used by the real Pro Controller and Pokemon Automation.
// ============================================================
static const uint8_t kProControllerDescriptor[] = {
    0x05, 0x01,        // Usage Page (Generic Desktop)
    0x09, 0x05,        // Usage (Game Pad)
    0xA1, 0x01,        // Collection (Application)

    // --- Vendor-defined input reports ---
    0x06, 0x01, 0xFF,  //   Usage Page (Vendor Defined 0xFF01)

    // Report ID 0x21: Subcommand reply (48 bytes)
    0x85, 0x21,        //   Report ID (0x21)
    0x09, 0x21,        //   Usage (0x21)
    0x75, 0x08,        //   Report Size (8)
    0x95, 0x30,        //   Report Count (48)
    0x81, 0x02,        //   Input (Data,Var,Abs)

    // Report ID 0x30: Standard full input report (48 bytes)
    0x85, 0x30,        //   Report ID (0x30)
    0x09, 0x30,        //   Usage (0x30)
    0x75, 0x08,        //   Report Size (8)
    0x95, 0x30,        //   Report Count (48)
    0x81, 0x02,        //   Input (Data,Var,Abs)

    // Report ID 0x31: NFC/IR MCU FW update (361 bytes)
    0x85, 0x31,        //   Report ID (0x31)
    0x09, 0x31,        //   Usage (0x31)
    0x75, 0x08,        //   Report Size (8)
    0x96, 0x69, 0x01,  //   Report Count (361)
    0x81, 0x02,        //   Input (Data,Var,Abs)

    // Report ID 0x32 (361 bytes)
    0x85, 0x32,        //   Report ID (0x32)
    0x09, 0x32,        //   Usage (0x32)
    0x75, 0x08,        //   Report Size (8)
    0x96, 0x69, 0x01,  //   Report Count (361)
    0x81, 0x02,        //   Input (Data,Var,Abs)

    // Report ID 0x33 (361 bytes)
    0x85, 0x33,        //   Report ID (0x33)
    0x09, 0x33,        //   Usage (0x33)
    0x75, 0x08,        //   Report Size (8)
    0x96, 0x69, 0x01,  //   Report Count (361)
    0x81, 0x02,        //   Input (Data,Var,Abs)

    // Report ID 0x3F: Simple button report (used during initial pairing)
    // 16 buttons + hat + 4 analog sticks (16-bit each)
    0x85, 0x3F,        //   Report ID (0x3F)
    0x05, 0x09,        //   Usage Page (Button)
    0x19, 0x01,        //   Usage Minimum (1)
    0x29, 0x10,        //   Usage Maximum (16)
    0x15, 0x00,        //   Logical Minimum (0)
    0x25, 0x01,        //   Logical Maximum (1)
    0x75, 0x01,        //   Report Size (1)
    0x95, 0x10,        //   Report Count (16)
    0x81, 0x02,        //   Input (Data,Var,Abs)
    0x05, 0x01,        //   Usage Page (Generic Desktop)
    0x09, 0x39,        //   Usage (Hat Switch)
    0x15, 0x00,        //   Logical Minimum (0)
    0x25, 0x07,        //   Logical Maximum (7)
    0x75, 0x04,        //   Report Size (4)
    0x95, 0x01,        //   Report Count (1)
    0x81, 0x42,        //   Input (Data,Var,Abs,Null)
    0x05, 0x09,        //   Usage Page (Button)
    0x75, 0x04,        //   Report Size (4)
    0x95, 0x01,        //   Report Count (1)
    0x81, 0x01,        //   Input (Const)
    0x05, 0x01,        //   Usage Page (Generic Desktop)
    0x09, 0x30,        //   Usage (X)
    0x09, 0x31,        //   Usage (Y)
    0x09, 0x33,        //   Usage (Rx)
    0x09, 0x34,        //   Usage (Ry)
    0x16, 0x00, 0x00,  //   Logical Minimum (0)
    0x27, 0xFF, 0xFF, 0x00, 0x00, // Logical Maximum (65535)
    0x75, 0x10,        //   Report Size (16)
    0x95, 0x04,        //   Report Count (4)
    0x81, 0x02,        //   Input (Data,Var,Abs)

    // --- Vendor-defined output reports ---
    0x06, 0x01, 0xFF,  //   Usage Page (Vendor Defined 0xFF01)

    // Report ID 0x01: Subcommand (48 bytes)
    0x85, 0x01,        //   Report ID (0x01)
    0x09, 0x01,        //   Usage (0x01)
    0x75, 0x08,        //   Report Size (8)
    0x95, 0x30,        //   Report Count (48)
    0x91, 0x02,        //   Output (Data,Var,Abs)

    // Report ID 0x10: Rumble (9 bytes)
    0x85, 0x10,        //   Report ID (0x10)
    0x09, 0x10,        //   Usage (0x10)
    0x75, 0x08,        //   Report Size (8)
    0x95, 0x09,        //   Report Count (9)
    0x91, 0x02,        //   Output (Data,Var,Abs)

    // Report ID 0x11 (48 bytes)
    0x85, 0x11,        //   Report ID (0x11)
    0x09, 0x11,        //   Usage (0x11)
    0x75, 0x08,        //   Report Size (8)
    0x95, 0x30,        //   Report Count (48)
    0x91, 0x02,        //   Output (Data,Var,Abs)

    // Report ID 0x12 (48 bytes)
    0x85, 0x12,        //   Report ID (0x12)
    0x09, 0x12,        //   Usage (0x12)
    0x75, 0x08,        //   Report Size (8)
    0x95, 0x30,        //   Report Count (48)
    0x91, 0x02,        //   Output (Data,Var,Abs)

    0xC0,              // End Collection
};

// ============================================================
// Linker --wrap overrides for TinyUSB callbacks.
//
// Safe to override: device descriptor (VID/PID presentation),
//   HID report descriptor, and report callback (0x80 routing).
// NOT safe to override: config descriptor (must match TinyUSB's
//   internal endpoint allocation) and BOS (NULL crashes stack).
//
// Build flags:
//   -Wl,--wrap=tud_hid_set_report_cb
//   -Wl,--wrap=tud_descriptor_device_cb
//   -Wl,--wrap=tud_hid_descriptor_report_cb
// ============================================================
extern "C" {
    // --- Report callback wrap (routes 0x80 handshake) ---
    void __real_tud_hid_set_report_cb(uint8_t instance, uint8_t report_id,
                                       hid_report_type_t report_type,
                                       uint8_t const* buffer, uint16_t bufsize);

    void __wrap_tud_hid_set_report_cb(uint8_t instance, uint8_t report_id,
                                       hid_report_type_t report_type,
                                       uint8_t const* buffer, uint16_t bufsize) {
        if (!report_id && !report_type && bufsize > 0 && g_switchProUsbDevice) {
            uint8_t rid = buffer[0];
            if (rid == 0x80) {
                g_switchProUsbDevice->_onOutput(rid, buffer + 1, bufsize - 1);
                return;
            }
        }
        if (report_id == 0x80 && g_switchProUsbDevice) {
            g_switchProUsbDevice->_onOutput(report_id, buffer, bufsize);
            return;
        }
        __real_tud_hid_set_report_cb(instance, report_id, report_type, buffer, bufsize);
    }

    // --- Device descriptor wrap (Pro Controller VID/PID) ---
    uint8_t const* __real_tud_descriptor_device_cb(void);

    uint8_t const* __wrap_tud_descriptor_device_cb(void) {
        if (g_switchProUsbDevice) return s_deviceDesc;
        return __real_tud_descriptor_device_cb();
    }

    // --- HID report descriptor wrap ---
    uint8_t const* __real_tud_hid_descriptor_report_cb(uint8_t instance);

    uint8_t const* __wrap_tud_hid_descriptor_report_cb(uint8_t instance) {
        if (g_switchProUsbDevice) return kProControllerDescriptor;
        return __real_tud_hid_descriptor_report_cb(instance);
    }
}

// MAC address for device info response
static const uint8_t kMacAddr[6] = {0x00, 0x00, 0x5E, 0x00, 0x53, 0x5E};

// ============================================================
// SPI flash data tables
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

// Report body size: 48 bytes (report ID sent separately by TinyUSB)
static constexpr size_t kReportLen = 48;

// ============================================================
// Constructor / begin / end
// ============================================================
SwitchProUSB::SwitchProUSB() : _hid() {
    static bool registered = false;
    // Set Pro Controller USB identity
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
        case 0x01:
            handleSubcommand(buffer, len);
            break;
        case 0x10:
            // Rumble-only — acknowledged implicitly by continuing 0x30 reports
            break;
        case 0x80:
            handleUsbCommand(buffer, len);
            break;
        default:
            Serial.printf("[SwitchProUSB] output report 0x%02X len=%d\n", report_id, len);
            break;
    }
}

// ============================================================
// USB-specific 0x80 command handler
// Not in the HID descriptor (handled at USB level), but some
// Switch firmware versions send it. Handle gracefully.
// ============================================================
void SwitchProUSB::handleUsbCommand(const uint8_t* data, uint16_t len) {
    if (len < 1) return;
    uint8_t cmd = data[0];
    Serial.printf("[SwitchProUSB] USB 0x80 cmd=0x%02X len=%d\n", cmd, len);

    uint8_t reply[kReportLen];
    memset(reply, 0, sizeof(reply));

    switch (cmd) {
        case 0x01:
            // MAC address request
            reply[0] = 0x01;
            reply[1] = 0x00;
            reply[2] = 0x03;
            memcpy(&reply[3], kMacAddr, 6);
            sendInputReport(0x81, reply, sizeof(reply));
            Serial.println("[SwitchProUSB] -> 0x81 MAC reply sent");
            break;
        case 0x02:
            reply[0] = cmd;
            sendInputReport(0x81, reply, sizeof(reply));
            Serial.println("[SwitchProUSB] -> 0x81 handshake ACK sent");
            break;
        case 0x03:
            reply[0] = cmd;
            sendInputReport(0x81, reply, sizeof(reply));
            Serial.println("[SwitchProUSB] -> 0x81 baudrate ACK sent");
            break;
        case 0x04:
            _connected = true;
            Serial.println("[SwitchProUSB] USB handshake COMPLETE - connected!");
            break;
        case 0x05:
            _connected = false;
            Serial.println("[SwitchProUSB] USB disconnected");
            break;
        default:
            reply[0] = cmd;
            sendInputReport(0x81, reply, sizeof(reply));
            Serial.printf("[SwitchProUSB] -> 0x81 unknown cmd 0x%02X ACK sent\n", cmd);
            break;
    }
}

// ============================================================
// Subcommand handler (report ID 0x01)
// data[0] = counter, [1..8] = rumble, [9] = subcmd, [10+] = params
// ============================================================
void SwitchProUSB::handleSubcommand(const uint8_t* data, uint16_t len) {
    if (len < 10) return;
    uint8_t subcmd = data[9];
    Serial.printf("[SwitchProUSB] subcmd 0x%02X\n", subcmd);

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
            handleSpiFlashRead(data, len);
            break;
        case 0x21:  // Set NFC/IR MCU configuration — simple ACK (we don't emulate NFC)
        case 0x22:  // Set NFC/IR MCU state
            sendSubcommandReply(subcmd, 0x80, nullptr, 0);
            break;
        default:
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

    Serial.printf("[SwitchProUSB] SPI read 0x%04X len=%d\n", addr, reqLen);

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
        case 0x8026: src = kSpiUserMotionCal;     srcLen = sizeof(kSpiUserMotionCal); break;
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

// Try to send a report immediately via TinyUSB (non-blocking).
bool SwitchProUSB::trySendReport(uint8_t reportId, const uint8_t* data, size_t len) {
    return tud_hid_n_report(0, reportId, data, len);
}

// Called from _onOutput (TinyUSB callback context) — MUST NOT block.
// Try to send immediately; if the IN endpoint is busy, queue for loop().
void SwitchProUSB::sendInputReport(uint8_t reportId, const uint8_t* data, size_t len) {
    if (trySendReport(reportId, data, len)) return;
    // Endpoint busy — buffer the reply for loop() to flush
    if (_pendingCount < kMaxPending) {
        auto& p = _pendingQueue[_pendingCount];
        p.reportId = reportId;
        size_t copyLen = (len < kReportBufLen) ? len : kReportBufLen;
        memcpy(p.data, data, copyLen);
        p.len = copyLen;
        _pendingCount++;
    }
}

// Drain queued replies (called from loop() in main-thread context).
void SwitchProUSB::flushPendingReplies() {
    while (_pendingCount > 0) {
        auto& p = _pendingQueue[0];
        if (!trySendReport(p.reportId, p.data, p.len)) break;  // still busy, try next tick
        // Shift queue down
        _pendingCount--;
        for (uint8_t i = 0; i < _pendingCount; i++) {
            _pendingQueue[i] = _pendingQueue[i + 1];
        }
    }
}

void SwitchProUSB::fillInputHeader(uint8_t* buf) {
    // Convert 8-bit axes to 12-bit for Pro Controller encoding
    uint16_t lx12 = static_cast<uint16_t>(_lx) << 4;
    uint16_t ly12 = static_cast<uint16_t>(256 - _ly) << 4;  // Y inverted (256 so 0x80→2048 center)
    uint16_t rx12 = static_cast<uint16_t>(_rx) << 4;
    uint16_t ry12 = static_cast<uint16_t>(256 - _ry) << 4;  // Y inverted (256 so 0x80→2048 center)

    // Convert NSGamepad-style buttons + dpad to Pro Controller 3-byte format
    // NSButton enum: Y=0, B=1, A=2, X=3, L=4, R=5, ZL=6, ZR=7, Minus=8, Plus=9, LStick=10, RStick=11, Home=12, Capture=13
    uint8_t btnRight = 0, btnShared = 0, btnLeft = 0;

    // Right byte: Y, X, B, A, SR, SL, R, ZR
    if (_buttons & (1 << 0))  btnRight |= 0x01;  // Y
    if (_buttons & (1 << 3))  btnRight |= 0x02;  // X
    if (_buttons & (1 << 1))  btnRight |= 0x04;  // B
    if (_buttons & (1 << 2))  btnRight |= 0x08;  // A
    if (_buttons & (1 << 5))  btnRight |= 0x40;  // R
    if (_buttons & (1 << 7))  btnRight |= 0x80;  // ZR

    // Shared byte: Minus, Plus, RStick, LStick, Home, Capture
    if (_buttons & (1 << 8))  btnShared |= 0x01;  // Minus
    if (_buttons & (1 << 9))  btnShared |= 0x02;  // Plus
    if (_buttons & (1 << 11)) btnShared |= 0x04;  // RStick
    if (_buttons & (1 << 10)) btnShared |= 0x08;  // LStick
    if (_buttons & (1 << 12)) btnShared |= 0x10;  // Home
    if (_buttons & (1 << 13)) btnShared |= 0x20;  // Capture

    // Left byte: Down, Up, Right, Left, SR, SL, L, ZL
    if (_dpad == 0 || _dpad == 1 || _dpad == 7) btnLeft |= 0x02;  // Up
    if (_dpad == 2 || _dpad == 1 || _dpad == 3) btnLeft |= 0x04;  // Right
    if (_dpad == 4 || _dpad == 3 || _dpad == 5) btnLeft |= 0x01;  // Down
    if (_dpad == 6 || _dpad == 5 || _dpad == 7) btnLeft |= 0x08;  // Left
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
    // Bytes 12-47 = IMU data (zeros = stationary)
}

void SwitchProUSB::sendStandardInputReport() {
    uint8_t payload[kReportLen];
    memset(payload, 0, sizeof(payload));
    fillInputHeader(payload);
    sendInputReport(0x30, payload, sizeof(payload));
}

// ============================================================
// 0x3F simple button report — sent during initial pairing
// before the 0x80 USB handshake completes.
// Format from the HID descriptor:
//   16 buttons (2 bytes) + hat (4 bits + 4 pad) + 4 axes (16-bit each)
//   Total: 11 bytes
// ============================================================
void SwitchProUSB::sendSimpleInputReport() {
    uint8_t payload[11];
    memset(payload, 0, sizeof(payload));

    // 16 buttons packed into 2 bytes (same NSGamepad bit order)
    payload[0] = _buttons & 0xFF;
    payload[1] = (_buttons >> 8) & 0xFF;

    // Hat switch (4 bits) + 4 bits padding
    // Our _dpad is already in 0-7 / 0x0F (centered) format
    payload[2] = (_dpad == 0x0F) ? 0x08 : _dpad;  // 0x08 = centered in 0x3F hat

    // 4 analog axes as 16-bit LE (center = 0x8000)
    uint16_t lx16 = static_cast<uint16_t>(_lx) << 8;
    uint16_t ly16 = static_cast<uint16_t>(_ly) << 8;
    uint16_t rx16 = static_cast<uint16_t>(_rx) << 8;
    uint16_t ry16 = static_cast<uint16_t>(_ry) << 8;
    payload[3] = lx16 & 0xFF; payload[4] = (lx16 >> 8) & 0xFF;
    payload[5] = ly16 & 0xFF; payload[6] = (ly16 >> 8) & 0xFF;
    payload[7] = rx16 & 0xFF; payload[8] = (rx16 >> 8) & 0xFF;
    payload[9] = ry16 & 0xFF; payload[10] = (ry16 >> 8) & 0xFF;

    sendInputReport(0x3F, payload, sizeof(payload));
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
    // Flush any queued replies first (from _onOutput callback context)
    flushPendingReplies();

    // Two-phase reporting:
    // Phase 1 (pre-handshake): Send 0x3F simple reports so the Switch
    //   recognises us as a Pro Controller and initiates the 0x80 handshake.
    // Phase 2 (post-handshake): Send 0x30 full reports at ~8ms cadence.
    uint32_t cadence = _connected ? 8 : 16;
    if (millis() - _lastReportMs < cadence) return;
    _lastReportMs = millis();

    if (_connected) {
        sendStandardInputReport();
    } else {
        sendSimpleInputReport();
    }
}

#endif  // CONFIG_TINYUSB_HID_ENABLED
#endif  // CONFIG_IDF_TARGET_ESP32S3
