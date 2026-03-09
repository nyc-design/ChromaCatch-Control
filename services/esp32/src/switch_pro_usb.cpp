#include "switch_pro_usb.h"

#if defined(CONFIG_IDF_TARGET_ESP32S3)
#if CONFIG_TINYUSB_HID_ENABLED

#include <cstring>
#include "tusb.h"
#include "class/hid/hid_device.h"

// Global instance pointer for the --wrap callbacks.
SwitchProUSB* g_switchProUsbDevice = nullptr;

// ============================================================
// Switch 2 Pro Controller HID descriptor — byte-for-byte match of the real
// Nintendo Switch 2 Pro Controller (PID 0x2069, 97 bytes).
// Source: usbhid-dump -d 057e:2069 -e descriptor on Linux
//
// Report IDs:
//   0x05 (Input, 63B): Vendor-defined full state
//   0x09 (Input, 63B): 2B vendor + 21 buttons + 4x12-bit sticks + 52B vendor
//   0x02 (Output, 63B): Host commands
//
// This descriptor is ESP-IDF HID parser compatible (standard Usage Page →
// Usage → Collection sequence). The --wrap bypass may not be strictly needed
// but is kept as a safety net.
// ============================================================
static const uint8_t kSwitch2ProDescriptor[] = {
    0x05, 0x01,        // Usage Page (Generic Desktop)
    0x09, 0x05,        // Usage (Game Pad)
    0xA1, 0x01,        // Collection (Application)

    // --- Report ID 0x05: Full vendor state (63 bytes) ---
    0x85, 0x05,        //   Report ID (0x05)
    0x05, 0xFF,        //   Usage Page (Vendor Defined 0xFF)
    0x09, 0x01,        //   Usage (0x01)
    0x15, 0x00,        //   Logical Minimum (0)
    0x26, 0xFF, 0x00,  //   Logical Maximum (255)
    0x95, 0x3F,        //   Report Count (63)
    0x75, 0x08,        //   Report Size (8)
    0x81, 0x02,        //   Input (Data,Var,Abs)

    // --- Report ID 0x09: Standard input (63 bytes) ---
    0x85, 0x09,        //   Report ID (0x09)
    0x09, 0x01,        //   Usage (Vendor 0x01) — 2 byte prefix
    0x95, 0x02,        //   Report Count (2)
    0x81, 0x02,        //   Input (Data,Var,Abs)

    0x05, 0x09,        //   Usage Page (Button)
    0x19, 0x01,        //   Usage Minimum (Button 1)
    0x29, 0x15,        //   Usage Maximum (Button 21)
    0x25, 0x01,        //   Logical Maximum (1)
    0x95, 0x15,        //   Report Count (21)
    0x75, 0x01,        //   Report Size (1)
    0x81, 0x02,        //   Input (Data,Var,Abs)
    0x95, 0x01,        //   Report Count (1)
    0x75, 0x03,        //   Report Size (3) — padding
    0x81, 0x03,        //   Input (Const,Var,Abs)

    0x05, 0x01,        //   Usage Page (Generic Desktop)
    0x09, 0x01,        //   Usage (Pointer)
    0xA1, 0x00,        //   Collection (Physical)
    0x09, 0x30,        //     Usage (X)
    0x09, 0x31,        //     Usage (Y)
    0x09, 0x33,        //     Usage (Rx)
    0x09, 0x35,        //     Usage (Rz)
    0x26, 0xFF, 0x0F,  //     Logical Maximum (4095)
    0x95, 0x04,        //     Report Count (4)
    0x75, 0x0C,        //     Report Size (12)
    0x81, 0x02,        //     Input (Data,Var,Abs)
    0xC0,              //   End Collection (Physical)

    0x05, 0xFF,        //   Usage Page (Vendor Defined)
    0x09, 0x02,        //   Usage (0x02) — 52 byte suffix
    0x26, 0xFF, 0x00,  //   Logical Maximum (255)
    0x95, 0x34,        //   Report Count (52)
    0x75, 0x08,        //   Report Size (8)
    0x81, 0x02,        //   Input (Data,Var,Abs)

    // --- Report ID 0x02: Output (63 bytes) ---
    0x85, 0x02,        //   Report ID (0x02)
    0x09, 0x01,        //   Usage (0x01)
    0x95, 0x3F,        //   Report Count (63)
    0x91, 0x02,        //   Output (Data,Var,Abs)

    0xC0,              // End Collection
};

// Verify descriptor is exactly 97 bytes
static_assert(sizeof(kSwitch2ProDescriptor) == 97, "Switch 2 Pro descriptor must be 97 bytes");

// ============================================================
// Switch 2 Pro Controller Configuration Descriptor — 80 bytes.
// Exact match of the real device: 2 interfaces + 2 IADs.
// Source: lsusb -v -d 057e:2069 on Linux
//
// Interface 0: HID (Game Pad)
//   IAD → bFunctionClass=3 (HID)
//   EP 0x81 IN Interrupt 64B bInterval=4
//   EP 0x01 OUT Interrupt 64B bInterval=4
//
// Interface 1: Vendor Specific (Bulk)
//   IAD → bFunctionClass=255 (Vendor)
//   EP 0x02 OUT Bulk 64B
//   EP 0x82 IN Bulk 64B
// ============================================================
static const uint8_t kSwitch2ProConfigDescriptor[] = {
    // --- Configuration Descriptor (9 bytes) ---
    0x09,        // bLength
    0x02,        // bDescriptorType (Configuration)
    0x50, 0x00,  // wTotalLength (80)
    0x02,        // bNumInterfaces
    0x01,        // bConfigurationValue
    0x00,        // iConfiguration (no string)
    0xC0,        // bmAttributes (Self Powered)
    0xFA,        // MaxPower (500mA = 250 * 2)

    // --- IAD for Interface 0: HID (8 bytes) ---
    0x08,        // bLength
    0x0B,        // bDescriptorType (IAD)
    0x00,        // bFirstInterface
    0x01,        // bInterfaceCount
    0x03,        // bFunctionClass (HID)
    0x00,        // bFunctionSubClass
    0x00,        // bFunctionProtocol
    0x00,        // iFunction

    // --- Interface 0: HID (9 bytes) ---
    0x09,        // bLength
    0x04,        // bDescriptorType (Interface)
    0x00,        // bInterfaceNumber
    0x00,        // bAlternateSetting
    0x02,        // bNumEndpoints
    0x03,        // bInterfaceClass (HID)
    0x00,        // bInterfaceSubClass
    0x00,        // bInterfaceProtocol
    0x00,        // iInterface (no string)

    // --- HID Descriptor (9 bytes) ---
    0x09,        // bLength
    0x21,        // bDescriptorType (HID)
    0x11, 0x01,  // bcdHID (1.11)
    0x00,        // bCountryCode
    0x01,        // bNumDescriptors
    0x22,        // bDescriptorType (Report)
    0x61, 0x00,  // wDescriptorLength (97)

    // --- EP 0x81 IN Interrupt (7 bytes) ---
    0x07,        // bLength
    0x05,        // bDescriptorType (Endpoint)
    0x81,        // bEndpointAddress (IN 1)
    0x03,        // bmAttributes (Interrupt)
    0x40, 0x00,  // wMaxPacketSize (64)
    0x04,        // bInterval (4)

    // --- EP 0x01 OUT Interrupt (7 bytes) ---
    0x07,        // bLength
    0x05,        // bDescriptorType (Endpoint)
    0x01,        // bEndpointAddress (OUT 1)
    0x03,        // bmAttributes (Interrupt)
    0x40, 0x00,  // wMaxPacketSize (64)
    0x04,        // bInterval (4)

    // --- IAD for Interface 1: Vendor (8 bytes) ---
    0x08,        // bLength
    0x0B,        // bDescriptorType (IAD)
    0x01,        // bFirstInterface
    0x01,        // bInterfaceCount
    0xFF,        // bFunctionClass (Vendor)
    0x00,        // bFunctionSubClass
    0x00,        // bFunctionProtocol
    0x00,        // iFunction

    // --- Interface 1: Vendor Specific (9 bytes) ---
    0x09,        // bLength
    0x04,        // bDescriptorType (Interface)
    0x01,        // bInterfaceNumber
    0x00,        // bAlternateSetting
    0x02,        // bNumEndpoints
    0xFF,        // bInterfaceClass (Vendor)
    0x00,        // bInterfaceSubClass
    0x00,        // bInterfaceProtocol
    0x00,        // iInterface (no string)

    // --- EP 0x02 OUT Bulk (7 bytes) ---
    0x07,        // bLength
    0x05,        // bDescriptorType (Endpoint)
    0x02,        // bEndpointAddress (OUT 2)
    0x02,        // bmAttributes (Bulk)
    0x40, 0x00,  // wMaxPacketSize (64)
    0x00,        // bInterval (0)

    // --- EP 0x82 IN Bulk (7 bytes) ---
    0x07,        // bLength
    0x05,        // bDescriptorType (Endpoint)
    0x82,        // bEndpointAddress (IN 2)
    0x02,        // bmAttributes (Bulk)
    0x40, 0x00,  // wMaxPacketSize (64)
    0x00,        // bInterval (0)
};

static_assert(sizeof(kSwitch2ProConfigDescriptor) == 80, "Config descriptor must be 80 bytes");

// ============================================================
// Linker --wrap overrides for Switch 2 Pro Controller USB
//
// 1) tud_hid_descriptor_report_cb: Return exact 97-byte HID report descriptor
// 2) tud_hid_set_report_cb: Route output reports to our device
// 3) tud_descriptor_bos_cb: Empty BOS (USB 2.0, no BOS needed)
// 4) tud_descriptor_configuration_cb: Return exact 80-byte config descriptor
//    with both interfaces (HID + Vendor Bulk) and IADs
//
// Build flags: -Wl,--wrap=tud_hid_descriptor_report_cb,--wrap=tud_hid_set_report_cb,--wrap=tud_descriptor_bos_cb,--wrap=tud_descriptor_configuration_cb
// ============================================================

static const uint8_t kEmptyBosDescriptor[] = {
    5,      // bLength
    0x0F,   // bDescriptorType = BOS
    5, 0,   // wTotalLength = 5
    0,      // bNumDeviceCaps = 0
};

extern "C" {
    // --- 1) Return exact HID report descriptor ---
    uint8_t const* __real_tud_hid_descriptor_report_cb(uint8_t instance);
    uint8_t const* __wrap_tud_hid_descriptor_report_cb(uint8_t instance) {
        if (g_switchProUsbDevice) {
            return kSwitch2ProDescriptor;
        }
        return __real_tud_hid_descriptor_report_cb(instance);
    }

    // --- 2) Route all output reports to our device ---
    void __real_tud_hid_set_report_cb(uint8_t instance, uint8_t report_id,
                                       hid_report_type_t report_type,
                                       uint8_t const* buffer, uint16_t bufsize);
    void __wrap_tud_hid_set_report_cb(uint8_t instance, uint8_t report_id,
                                       hid_report_type_t report_type,
                                       uint8_t const* buffer, uint16_t bufsize) {
        if (g_switchProUsbDevice) {
            if (!report_id && bufsize > 0) {
                uint8_t rid = buffer[0];
                g_switchProUsbDevice->_onOutput(rid, buffer + 1, bufsize - 1);
            } else {
                g_switchProUsbDevice->_onOutput(report_id, buffer, bufsize);
            }
            return;
        }
        __real_tud_hid_set_report_cb(instance, report_id, report_type, buffer, bufsize);
    }

    // --- 3) Empty BOS descriptor ---
    uint8_t const* __real_tud_descriptor_bos_cb(void);
    uint8_t const* __wrap_tud_descriptor_bos_cb(void) {
        if (g_switchProUsbDevice) {
            return kEmptyBosDescriptor;
        }
        return __real_tud_descriptor_bos_cb();
    }

    // --- 4) Exact configuration descriptor with HID + Vendor Bulk ---
    uint8_t const* __real_tud_descriptor_configuration_cb(uint8_t index);
    uint8_t const* __wrap_tud_descriptor_configuration_cb(uint8_t index) {
        if (g_switchProUsbDevice) {
            (void)index;
            return kSwitch2ProConfigDescriptor;
        }
        return __real_tud_descriptor_configuration_cb(index);
    }
}

// Report body size (report ID sent separately by TinyUSB)
static constexpr size_t kReportLen = 63;

// ============================================================
// Constructor / begin / end
// ============================================================
SwitchProUSB::SwitchProUSB() : _hid() {
    static bool registered = false;
    // Switch 2 Pro Controller USB identity
    USB.VID(0x057E);
    USB.PID(0x2069);
    USB.usbVersion(0x0200);
    USB.firmwareVersion(0x0101);   // bcdDevice 1.01
    USB.usbClass(0xEF);            // Miscellaneous Device (IAD composite)
    USB.usbSubClass(2);            // Common Class
    USB.usbProtocol(1);            // Interface Association
    USB.manufacturerName("Nintendo");
    USB.productName("Pro Controller");
    USB.serialNumber("00");
    if (!registered) {
        registered = true;
        _hid.addDevice(this, sizeof(kSwitch2ProDescriptor));
    }
    end();
}

void SwitchProUSB::begin() {
    _hid.begin();
    end();
}

void SwitchProUSB::end() {
    _buttons = 0;
    _dpadBits = 0;
    _lx = _ly = _rx = _ry = 0x80;
}

// ============================================================
// Descriptor callback
// ============================================================
uint16_t SwitchProUSB::_onGetDescriptor(uint8_t* buffer) {
    memcpy(buffer, kSwitch2ProDescriptor, sizeof(kSwitch2ProDescriptor));
    return sizeof(kSwitch2ProDescriptor);
}

// ============================================================
// Output report callback — Report 0x02
// ============================================================
void SwitchProUSB::_onOutput(uint8_t report_id, const uint8_t* buffer, uint16_t len) {
    // Log received output reports for debugging.
    // The Switch 2 protocol for output reports is not yet documented.
    Serial.printf("[Switch2Pro] output report 0x%02X len=%d", report_id, len);
    if (len > 0) {
        Serial.printf(" data:");
        for (uint16_t i = 0; i < len && i < 16; i++) {
            Serial.printf(" %02X", buffer[i]);
        }
        if (len > 16) Serial.printf("...");
    }
    Serial.println();
}

// ============================================================
// Report 0x09 builder and sender
//
// Layout (63 bytes payload, report ID sent separately):
//   [0-1]   Vendor prefix (timer counter)
//   [2-4]   21 buttons + 3 bits padding
//   [5-10]  4x 12-bit stick axes (X, Y, Rx, Rz)
//   [11-62] 52 bytes vendor suffix (zeroed)
// ============================================================
void SwitchProUSB::sendReport09() {
    uint8_t payload[kReportLen];
    memset(payload, 0, sizeof(payload));

    // Vendor prefix: use as a timer/counter (educated guess)
    payload[0] = _timer++;
    payload[1] = 0x00;

    // Pack 21 buttons into 3 bytes (bits 0-20 + 3 bits padding)
    // Bits 0-13: face/shoulder/system buttons from _buttons
    // Bits 14-17: D-pad (Up, Right, Down, Left) from _dpadBits
    // Bits 18-20: unused (SL, SR, etc.)
    uint32_t allButtons = static_cast<uint32_t>(_buttons & 0x3FFF)
                        | (static_cast<uint32_t>(_dpadBits & 0x0F) << 14);
    payload[2] = allButtons & 0xFF;
    payload[3] = (allButtons >> 8) & 0xFF;
    payload[4] = (allButtons >> 16) & 0x1F;  // only bits 16-20

    // Pack 4x 12-bit stick axes
    // Scale 8-bit (0x00-0xFF) to 12-bit (0x000-0xFF0)
    uint16_t lx12 = static_cast<uint16_t>(_lx) << 4;
    uint16_t ly12 = static_cast<uint16_t>(_ly) << 4;
    uint16_t rx12 = static_cast<uint16_t>(_rx) << 4;
    uint16_t ry12 = static_cast<uint16_t>(_ry) << 4;

    // HID packs consecutive 12-bit fields sequentially:
    // X[11:0] | Y[11:0] | Rx[11:0] | Rz[11:0]
    // Byte 5: X[7:0]
    // Byte 6: Y[3:0] << 4 | X[11:8]
    // Byte 7: Y[11:4]
    // Byte 8: Rx[7:0]
    // Byte 9: Rz[3:0] << 4 | Rx[11:8]
    // Byte 10: Rz[11:4]
    payload[5]  = lx12 & 0xFF;
    payload[6]  = ((lx12 >> 8) & 0x0F) | ((ly12 & 0x0F) << 4);
    payload[7]  = (ly12 >> 4) & 0xFF;
    payload[8]  = rx12 & 0xFF;
    payload[9]  = ((rx12 >> 8) & 0x0F) | ((ry12 & 0x0F) << 4);
    payload[10] = (ry12 >> 4) & 0xFF;

    // Bytes 11-62: vendor suffix (zeroed — possibly IMU, vibration state, etc.)

    trySendReport(0x09, payload, sizeof(payload));
}

bool SwitchProUSB::trySendReport(uint8_t reportId, const uint8_t* data, size_t len) {
    return tud_hid_n_report(0, reportId, data, len);
}

void SwitchProUSB::flushPendingReplies() {
    while (_pendingCount > 0) {
        auto& p = _pendingQueue[0];
        if (!trySendReport(p.reportId, p.data, p.len)) break;
        _pendingCount--;
        for (uint8_t i = 0; i < _pendingCount; i++) {
            _pendingQueue[i] = _pendingQueue[i + 1];
        }
    }
}

// ============================================================
// D-pad: convert hat value (0-7=directions, 0x0F=center) to button bits
// ============================================================
void SwitchProUSB::dPad(uint8_t d) {
    // Convert hat to 4 direction bits: bit0=Up, bit1=Right, bit2=Down, bit3=Left
    uint8_t bits = 0;
    switch (d) {
        case 0: bits = 0x01; break;           // Up
        case 1: bits = 0x01 | 0x02; break;    // Up-Right
        case 2: bits = 0x02; break;            // Right
        case 3: bits = 0x02 | 0x04; break;    // Down-Right
        case 4: bits = 0x04; break;            // Down
        case 5: bits = 0x04 | 0x08; break;    // Down-Left
        case 6: bits = 0x08; break;            // Left
        case 7: bits = 0x01 | 0x08; break;    // Up-Left
        default: bits = 0; break;              // Center (0x0F or any other)
    }

    if (bits != 0) {
        _dpadBits = bits;
        _pendingDpadRelease = 0;
        _lastPressMs = millis();
    } else {
        if (millis() - _lastPressMs < kMinHoldMs) {
            _pendingDpadRelease = 0x0F;  // mark all for release
        } else {
            _dpadBits = 0;
            _pendingDpadRelease = 0;
        }
    }
}

// ============================================================
// Button press/release with minimum hold enforcement
// ============================================================
void SwitchProUSB::press(uint8_t b) {
    if (b > 15) b = 15;
    uint16_t mask = (uint16_t)1 << b;
    _buttons |= mask;
    _pendingReleaseMask &= ~mask;
    _lastPressMs = millis();
}

void SwitchProUSB::release(uint8_t b) {
    if (b > 15) b = 15;
    uint16_t mask = (uint16_t)1 << b;
    if (millis() - _lastPressMs < kMinHoldMs) {
        _pendingReleaseMask |= mask;
    } else {
        _buttons &= ~mask;
        _pendingReleaseMask &= ~mask;
    }
}

bool SwitchProUSB::isConnected() const {
    // No handshake protocol — connected as soon as USB is mounted
    return tud_mounted() && !tud_suspended();
}

// ============================================================
// write / loop
// ============================================================
bool SwitchProUSB::write() {
    sendReport09();
    return true;
}

void SwitchProUSB::loop() {
    flushPendingReplies();

    // Process deferred releases after minimum hold time
    if (_pendingReleaseMask && (millis() - _lastPressMs >= kMinHoldMs)) {
        _buttons &= ~_pendingReleaseMask;
        _pendingReleaseMask = 0;
    }
    if (_pendingDpadRelease && (millis() - _lastPressMs >= kMinHoldMs)) {
        _dpadBits = 0;
        _pendingDpadRelease = 0;
    }

    // Send Report 0x09 at ~4ms cadence (bInterval=4)
    if (millis() - _lastReportMs < 4) return;
    _lastReportMs = millis();

    sendReport09();
}

#endif  // CONFIG_TINYUSB_HID_ENABLED
#endif  // CONFIG_IDF_TARGET_ESP32S3
