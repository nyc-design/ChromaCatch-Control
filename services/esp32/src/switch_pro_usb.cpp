#include "switch_pro_usb.h"

#if defined(CONFIG_IDF_TARGET_ESP32S3)
#if CONFIG_TINYUSB_HID_ENABLED

#include <cstring>
#include "class/hid/hid_device.h"

// Global instance pointer for the --wrap callbacks.
SwitchProUSB* g_switchProUsbDevice = nullptr;

// ============================================================
// Pro Controller USB HID descriptor — 203 bytes, matching the real
// Nintendo Switch Pro Controller wired USB descriptor.
// Source: https://gist.github.com/ToadKing/b883a8ccfa26adcc6ba9905e75aeb4f2
//
// NOTE: This is the USB descriptor, NOT the Bluetooth one.
// The USB descriptor uses Joystick usage, vendor page 0xFF00,
// 63-byte reports, and includes 0x80/0x81/0x82 report IDs.
//
// PARSER COMPAT: The real descriptor has Logical Minimum (0x15,0x00)
// between Usage Page and Usage at the top level, but ESP-IDF's HID
// parser rejects that ("expected USAGE, got 0x14"). We move it to
// just inside the Collection where the parser silently ignores it.
// Semantically identical — Logical Minimum is a Global item that
// propagates into sub-scopes either way, and it's overridden by each
// report section anyway. Keeps the descriptor at exactly 203 bytes.
// ============================================================
static const uint8_t kProControllerDescriptor[] = {
    0x05, 0x01,        // Usage Page (Generic Desktop)
    0x09, 0x04,        // Usage (Joystick)
    0xA1, 0x01,        // Collection (Application)
    0x15, 0x00,        // Logical Minimum (0) — moved here from before Usage for parser compat

    // --- Report ID 0x30: Standard full input report (63 bytes) ---
    // HID-standard button/axis/hat layout for device enumeration.
    // Actual data uses vendor-format (3-byte buttons + 12-bit sticks).
    0x85, 0x30,                    //   Report ID (0x30)
    0x05, 0x01,                    //   Usage Page (Generic Desktop)
    0x05, 0x09,                    //   Usage Page (Button)
    0x19, 0x01,                    //   Usage Minimum (1)
    0x29, 0x0A,                    //   Usage Maximum (10)
    0x15, 0x00,                    //   Logical Minimum (0)
    0x25, 0x01,                    //   Logical Maximum (1)
    0x75, 0x01,                    //   Report Size (1)
    0x95, 0x0A,                    //   Report Count (10)
    0x55, 0x00,                    //   Unit Exponent (0)
    0x65, 0x00,                    //   Unit (None)
    0x81, 0x02,                    //   Input (Data,Var,Abs)
    0x05, 0x09,                    //   Usage Page (Button)
    0x19, 0x0B,                    //   Usage Minimum (11)
    0x29, 0x0E,                    //   Usage Maximum (14)
    0x15, 0x00,                    //   Logical Minimum (0)
    0x25, 0x01,                    //   Logical Maximum (1)
    0x75, 0x01,                    //   Report Size (1)
    0x95, 0x04,                    //   Report Count (4)
    0x81, 0x02,                    //   Input (Data,Var,Abs)
    0x75, 0x01,                    //   Report Size (1)
    0x95, 0x02,                    //   Report Count (2) — padding
    0x81, 0x03,                    //   Input (Const,Var,Abs)
    0x0B, 0x01, 0x00, 0x01, 0x00, //   Usage (Generic Desktop: Pointer)
    0xA1, 0x00,                    //   Collection (Physical)
    0x0B, 0x30, 0x00, 0x01, 0x00, //     Usage (X)
    0x0B, 0x31, 0x00, 0x01, 0x00, //     Usage (Y)
    0x0B, 0x32, 0x00, 0x01, 0x00, //     Usage (Z)
    0x0B, 0x35, 0x00, 0x01, 0x00, //     Usage (Rz)
    0x15, 0x00,                    //     Logical Minimum (0)
    0x27, 0xFF, 0xFF, 0x00, 0x00, //     Logical Maximum (65534)
    0x75, 0x10,                    //     Report Size (16)
    0x95, 0x04,                    //     Report Count (4)
    0x81, 0x02,                    //     Input (Data,Var,Abs)
    0xC0,                          //   End Collection
    0x0B, 0x39, 0x00, 0x01, 0x00, //   Usage (Hat Switch)
    0x15, 0x00,                    //   Logical Minimum (0)
    0x25, 0x07,                    //   Logical Maximum (7)
    0x35, 0x00,                    //   Physical Minimum (0)
    0x46, 0x3B, 0x01,             //   Physical Maximum (315)
    0x65, 0x14,                    //   Unit (Eng Rot: Degree)
    0x75, 0x04,                    //   Report Size (4)
    0x95, 0x01,                    //   Report Count (1)
    0x81, 0x02,                    //   Input (Data,Var,Abs)
    0x05, 0x09,                    //   Usage Page (Button)
    0x19, 0x0F,                    //   Usage Minimum (15)
    0x29, 0x12,                    //   Usage Maximum (18)
    0x15, 0x00,                    //   Logical Minimum (0)
    0x25, 0x01,                    //   Logical Maximum (1)
    0x75, 0x01,                    //   Report Size (1)
    0x95, 0x04,                    //   Report Count (4)
    0x81, 0x02,                    //   Input (Data,Var,Abs)
    0x75, 0x08,                    //   Report Size (8)
    0x95, 0x34,                    //   Report Count (52) — padding/IMU
    0x81, 0x03,                    //   Input (Const,Var,Abs)

    // --- Vendor-defined reports (Usage Page 0xFF00) ---
    0x06, 0x00, 0xFF,             //   Usage Page (Vendor Defined 0xFF00)

    // Report ID 0x21: Subcommand reply input (63 bytes)
    0x85, 0x21,                    //   Report ID (0x21)
    0x09, 0x01,                    //   Usage (0x01)
    0x75, 0x08,                    //   Report Size (8)
    0x95, 0x3F,                    //   Report Count (63)
    0x81, 0x03,                    //   Input (Const,Var,Abs)

    // Report ID 0x81: USB handshake reply input (63 bytes)
    0x85, 0x81,                    //   Report ID (0x81)
    0x09, 0x02,                    //   Usage (0x02)
    0x75, 0x08,                    //   Report Size (8)
    0x95, 0x3F,                    //   Report Count (63)
    0x81, 0x03,                    //   Input (Const,Var,Abs)

    // Report ID 0x01: Subcommand output (63 bytes)
    0x85, 0x01,                    //   Report ID (0x01)
    0x09, 0x03,                    //   Usage (0x03)
    0x75, 0x08,                    //   Report Size (8)
    0x95, 0x3F,                    //   Report Count (63)
    0x91, 0x83,                    //   Output (Const,Var,Abs,Volatile)

    // Report ID 0x10: Rumble output (63 bytes)
    0x85, 0x10,                    //   Report ID (0x10)
    0x09, 0x04,                    //   Usage (0x04)
    0x75, 0x08,                    //   Report Size (8)
    0x95, 0x3F,                    //   Report Count (63)
    0x91, 0x83,                    //   Output (Const,Var,Abs,Volatile)

    // Report ID 0x80: USB handshake output (63 bytes)
    0x85, 0x80,                    //   Report ID (0x80)
    0x09, 0x05,                    //   Usage (0x05)
    0x75, 0x08,                    //   Report Size (8)
    0x95, 0x3F,                    //   Report Count (63)
    0x91, 0x83,                    //   Output (Const,Var,Abs,Volatile)

    // Report ID 0x82: USB handshake output 2 (63 bytes)
    0x85, 0x82,                    //   Report ID (0x82)
    0x09, 0x06,                    //   Usage (0x06)
    0x75, 0x08,                    //   Report Size (8)
    0x95, 0x3F,                    //   Report Count (63)
    0x91, 0x83,                    //   Output (Const,Var,Abs,Volatile)

    0xC0,                          // End Collection
};

// ============================================================
// Linker --wrap overrides for Pro Controller USB
//
// 1) tud_hid_set_report_cb: Safety net for 0x80 reports. The 0x80
//    report ID is now in the HID descriptor so TinyUSB should route
//    it natively, but some hosts send it without a report_id prefix
//    (raw SET_REPORT with report_id=0). The wrap catches that case.
//
// 2) tud_descriptor_bos_cb: Return a minimal empty BOS descriptor.
//    Arduino/TinyUSB generates a BOS with WebUSB + MS OS descriptors
//    which real Pro Controllers don't have. Combined with USB 2.0
//    (bcdUSB=0x0200), the host shouldn't request BOS at all, but
//    this provides a safe fallback.
//
// Build flags: -Wl,--wrap=tud_hid_set_report_cb,--wrap=tud_descriptor_bos_cb
// ============================================================

// Minimal BOS descriptor: just the header with 0 capabilities
static const uint8_t kEmptyBosDescriptor[] = {
    5,                     // bLength
    0x0F,                  // bDescriptorType = BOS
    5, 0,                  // wTotalLength = 5 (just the header)
    0,                     // bNumDeviceCaps = 0
};

extern "C" {
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

    // Override BOS descriptor: return empty BOS (no WebUSB/MS OS capabilities)
    // Real Pro Controllers are USB 2.0 with no BOS. Returning NULL crashes TinyUSB,
    // so we return a valid but empty BOS header instead.
    uint8_t const* __real_tud_descriptor_bos_cb(void);
    uint8_t const* __wrap_tud_descriptor_bos_cb(void) {
        if (g_switchProUsbDevice) {
            return kEmptyBosDescriptor;
        }
        return __real_tud_descriptor_bos_cb();
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

// Report body size: 63 bytes (report ID sent separately by TinyUSB).
// All USB Pro Controller reports are 63 bytes per the HID descriptor.
static constexpr size_t kReportLen = 63;

// ============================================================
// Constructor / begin / end
// ============================================================
SwitchProUSB::SwitchProUSB() : _hid() {
    static bool registered = false;
    // Set Pro Controller USB identity — must match real device exactly
    USB.VID(0x057E);
    USB.PID(0x2009);
    USB.usbVersion(0x0200);        // USB 2.0 — prevents BOS descriptor generation
    USB.firmwareVersion(0x0200);    // bcdDevice — matches real Pro Controller
    USB.usbClass(0);
    USB.usbSubClass(0);
    USB.usbProtocol(0);
    USB.manufacturerName("Nintendo Co., Ltd.");
    USB.productName("Pro Controller");
    USB.serialNumber("000000000001");
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
// Report ID 0x80 is in the USB HID descriptor as a vendor output.
// The Switch sends 0x80 commands during the USB handshake.
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
            // Device info — match nuxbt/PA values
            uint8_t info[12] = {0};
            info[0] = 0x03; info[1] = 0x8B;  // FW version (nuxbt: 0x03, 0x8B)
            info[2] = 0x03;                   // Pro Controller
            info[3] = 0x00;                   // connection_info: 0x00 for Pro Controller (nuxbt)
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
            sendSubcommandReply(subcmd, 0x80, nullptr, 0);
            break;
        case 0x48:  // Enable vibration — nuxbt returns 0x82
            sendSubcommandReply(subcmd, 0x82, nullptr, 0);
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
        case 0x21: {
            // Set NFC/IR MCU configuration — nuxbt returns 0xA0 with MCU state data
            uint8_t mcuData[34] = {0};
            mcuData[0] = 0x01; mcuData[1] = 0x00; mcuData[2] = 0xFF;
            mcuData[3] = 0x00; mcuData[4] = 0x08; mcuData[5] = 0x00;
            mcuData[6] = 0x1B; mcuData[7] = 0x01;
            // Byte index 36 (offset 36-14=22 in extra data) = 0xC8 checksum
            // In nuxbt: report[49] = 0xC8, which is buf[49-14=35] of data
            mcuData[33] = 0xC8;
            sendSubcommandReply(0x21, 0xA0, mcuData, sizeof(mcuData));
            break;
        }
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
    if (reqLen > 44) reqLen = 44;  // max data in 63-byte report: 63 - 14 (header) - 5 (SPI header) = 44

    Serial.printf("[SwitchProUSB] SPI read 0x%04X len=%d\n", addr, reqLen);

    uint8_t resp[49] = {0};  // 5 (SPI header) + up to 44 data bytes
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
    buf[1] = 0x90;       // battery full + connection_info 0x00 (nuxbt: 0x90 for Pro Controller)
    buf[2] = btnRight;
    buf[3] = btnShared;
    buf[4] = btnLeft;
    packStick12bit(lx12, ly12, &buf[5]);
    packStick12bit(rx12, ry12, &buf[8]);
    buf[11] = 0x80;      // vibration ACK (nuxbt uses 0x80-0xC0 cycle; 0x80 is safe default)
    // Bytes 12-47 = IMU data (zeros = stationary)
}

void SwitchProUSB::sendStandardInputReport() {
    uint8_t payload[kReportLen];
    memset(payload, 0, sizeof(payload));
    fillInputHeader(payload);
    sendInputReport(0x30, payload, sizeof(payload));
}

// Note: 0x3F simple report is NOT part of the USB descriptor.
// It exists only in the Bluetooth descriptor. Over USB, the Switch
// initiates the 0x80 handshake directly — no pre-handshake reports needed.

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
// Button press/release with minimum hold enforcement.
// PA uses 48ms hold to ensure the Switch sees the button across
// multiple report cycles. Release is deferred if called too soon.
// ============================================================
void SwitchProUSB::press(uint8_t b) {
    if (b > 15) b = 15;
    uint16_t mask = (uint16_t)1 << b;
    _buttons |= mask;
    _pendingReleaseMask &= ~mask;  // cancel any pending release for this button
    _lastPressMs = millis();
}

void SwitchProUSB::release(uint8_t b) {
    if (b > 15) b = 15;
    uint16_t mask = (uint16_t)1 << b;
    if (millis() - _lastPressMs < kMinHoldMs) {
        // Too soon — defer the release until loop() processes it
        _pendingReleaseMask |= mask;
    } else {
        _buttons &= ~mask;
        _pendingReleaseMask &= ~mask;
    }
}

void SwitchProUSB::dPad(uint8_t d) {
    if (d != 0x0F) {
        // Pressing a direction — apply immediately, record press time
        _dpad = d;
        _pendingDpad = 0xFF;
        _lastPressMs = millis();
    } else {
        // Centering (release) — defer if too soon after last press
        if (millis() - _lastPressMs < kMinHoldMs) {
            _pendingDpad = 0x0F;
        } else {
            _dpad = 0x0F;
            _pendingDpad = 0xFF;
        }
    }
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

    // Process deferred button/dpad releases after minimum hold time
    if (_pendingReleaseMask && (millis() - _lastPressMs >= kMinHoldMs)) {
        _buttons &= ~_pendingReleaseMask;
        _pendingReleaseMask = 0;
    }
    if (_pendingDpad != 0xFF && (millis() - _lastPressMs >= kMinHoldMs)) {
        _dpad = _pendingDpad;
        _pendingDpad = 0xFF;
    }

    // Over USB, the Switch initiates the 0x80 handshake first.
    // Before handshake: do nothing (no 0x3F — it's BT-only).
    // After handshake: send 0x30 full reports at ~8ms cadence.
    if (!_connected) return;

    if (millis() - _lastReportMs < 8) return;
    _lastReportMs = millis();

    sendStandardInputReport();
}

#endif  // CONFIG_TINYUSB_HID_ENABLED
#endif  // CONFIG_IDF_TARGET_ESP32S3
