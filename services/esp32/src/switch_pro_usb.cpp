#include "switch_pro_usb.h"

#if defined(CONFIG_IDF_TARGET_ESP32S3)
#if CONFIG_TINYUSB_HID_ENABLED

#include <cstring>
#include "tusb.h"
#include "class/hid/hid_device.h"
#include "class/vendor/vendor_device.h"

// Global instance pointer for the --wrap callbacks.
SwitchProUSB* g_switchProUsbDevice = nullptr;

// C-linkage bridge for switch_pro_vendor.cpp (separate TU to get strong symbols).
// switch_pro_vendor.cpp cannot include switch_pro_usb.h (it would pull in
// tusb.h → vendor_device.h → TU_ATTR_WEAK on the vendor callbacks).
extern "C" void switch_pro_vendor_bridge_rx(const uint8_t* data, uint16_t len) {
    if (g_switchProUsbDevice) {
        g_switchProUsbDevice->onVendorRx(data, len);
    }
}

// ============================================================
// Switch 2 Pro Controller HID descriptor — byte-for-byte match of the real
// Nintendo Switch 2 Pro Controller (PID 0x2069, 97 bytes).
// Source: usbhid-dump -d 057e:2069 -e descriptor on Linux
//
// Report IDs:
//   0x05 (Input, 63B): Vendor-defined full state
//   0x09 (Input, 63B): 2B vendor + 21 buttons + 4x12-bit sticks + 52B vendor
//   0x02 (Output, 63B): Host commands (haptics/rumble)
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

static_assert(sizeof(kSwitch2ProDescriptor) == 97, "Switch 2 Pro descriptor must be 97 bytes");

// ============================================================
// Switch 2 Pro Controller Configuration Descriptor — 80 bytes.
// Exact match of the real device: 2 interfaces + 2 IADs.
// Source: lsusb -v -d 057e:2069 on Linux
//
// Interface 0: HID (Game Pad)
//   IAD → bFunctionClass=3 (HID)
//   EP 0x81 IN Interrupt 64B bInterval=4 (4ms report interval)
//   EP 0x01 OUT Interrupt 64B bInterval=4
//
// Interface 1: Vendor Specific (Bulk)
//   IAD → bFunctionClass=255 (Vendor)
//   EP 0x02 OUT Bulk 64B  — Switch sends init commands here
//   EP 0x82 IN Bulk 64B   — Controller ACKs here
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
    0x04,        // bInterval (4ms)

    // --- EP 0x01 OUT Interrupt (7 bytes) ---
    0x07,        // bLength
    0x05,        // bDescriptorType (Endpoint)
    0x01,        // bEndpointAddress (OUT 1)
    0x03,        // bmAttributes (Interrupt)
    0x40, 0x00,  // wMaxPacketSize (64)
    0x04,        // bInterval (4ms)

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

    // Vendor Bulk callbacks are in switch_pro_vendor.cpp (separate TU to avoid
    // inheriting TU_ATTR_WEAK from vendor_device.h included via tusb.h).
}

// Report body size (report ID sent separately by TinyUSB)
static constexpr size_t kReportLen = 63;

// ============================================================
// Vendor Bulk Init Protocol
//
// The Switch 2 console sends init commands via Bulk OUT (EP 0x02)
// after USB enumeration. Commands follow the format:
//   [cmd_id, 0x91, 0x00, arg, ...]
//
// The real controller responds on Bulk IN (EP 0x82) with ACK data.
// Based on procon2tool (HandHeldLegend) and joypad-os init sequence:
//
// Commands sent by the Switch (host → device):
//  0x03 (0x0D): Start HID output at 4ms intervals
//  0x07       : Unknown
//  0x16       : Unknown
//  0x15 (0x01): Request controller MAC
//  0x15 (0x02): LTK (Long-Term Key) request
//  0x15 (0x03): Unknown
//  0x09       : LED init / Set Player LED
//  0x0C (0x02): IMU config
//  0x0C (0x04): IMU config
//  0x03 (0x0A): Enable haptics
//  0x11       : Unknown
//  0x0A (0x08): Unknown
//  0x10       : Unknown
//  0x01       : Unknown
//  0x03 (0x01): Unknown
//  0x0A (0x02): Unknown
//
// ACK format: echo the command header bytes back with status.
// The joypad-os driver works WITHOUT reading responses, suggesting
// the Switch doesn't strictly require ACKs. But we provide them
// for full protocol compliance.
// ============================================================

// Fake MAC address for BT pairing coordination responses
static const uint8_t kFakeMAC[] = { 0xCC, 0xCC, 0x01, 0x02, 0x03, 0x04 };

void SwitchProUSB::onVendorRx(const uint8_t* data, uint16_t len) {
    if (len == 0) return;

    _vendorCmdCount++;
    uint8_t cmdId = data[0];

    Serial.printf("[Switch2Pro] vendor bulk cmd 0x%02X len=%d (#%d)", cmdId, len, _vendorCmdCount);
    if (len > 1) {
        Serial.printf(" data:");
        for (uint16_t i = 0; i < len && i < 16; i++) {
            Serial.printf(" %02X", data[i]);
        }
        if (len > 16) Serial.printf("...");
    }
    Serial.println();

    // Build ACK response — echo command structure with status bytes.
    // Format: [cmd_id, 0x91, 0x00, arg, 0x00, ack_len, 0x00, 0x00, ...ack_data...]
    uint8_t ack[32];
    memset(ack, 0, sizeof(ack));
    uint8_t ackLen = 8;  // default minimal ACK

    // Copy command header as base for ACK
    uint8_t copyLen = (len < 8) ? len : 8;
    memcpy(ack, data, copyLen);

    // Command-specific response data
    uint8_t arg = (len >= 4) ? data[3] : 0;

    switch (cmdId) {
        case 0x15:
            if (arg == 0x01) {
                // MAC request — respond with our fake MAC
                ackLen = 16;
                memcpy(ack + 8, kFakeMAC, 6);
                ack[14] = 0x00;
                ack[15] = 0x00;
            } else if (arg == 0x02) {
                // LTK request — respond with zeroed key (USB mode, no BT pairing)
                ackLen = 24;
                // Key bytes already zeroed from memset
            }
            break;
        case 0x03:
            if (arg == 0x0D) {
                // Start HID output — we're already sending reports, just ACK
                _vendorInitReceived = true;
                Serial.println("[Switch2Pro] HID output start command received");
            }
            break;
        default:
            // Generic ACK — echo first bytes
            break;
    }

    tud_vendor_write(ack, ackLen);
    tud_vendor_write_flush();
}

// ============================================================
// Constructor / begin / end
// ============================================================
SwitchProUSB::SwitchProUSB() : _hid() {
    static bool registered = false;
    // Switch 2 Pro Controller USB identity — exact match of real device
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
// Output report callback — Report 0x02 (Haptics)
//
// The Switch sends haptic/rumble data via HID Output Report 0x02:
//   Byte 0: Report ID (0x02) — already stripped by TinyUSB
//   Byte 1: Counter (0x50-0x5F, cycles 0-F)
//   Bytes 2-6: Left motor haptic data (5 bytes)
//   Bytes 7-16: Padding
//   Byte 17: Counter (duplicate of byte 1)
//   Bytes 18-22: Right motor haptic data (5 bytes)
//   Bytes 23-63: Padding
// ============================================================
void SwitchProUSB::_onOutput(uint8_t report_id, const uint8_t* buffer, uint16_t len) {
    if (report_id == 0x02 && len >= 22) {
        // Haptic output — acknowledge silently.
        // We don't have motors, but accepting without error is correct behavior.
        return;
    }

    // Log any unexpected output reports
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
// Exact match of the real Switch 2 Pro Controller report layout
// as documented in joypad-os switch2_pro.h:
//
//   Offset 0:     Counter (uint8_t, increments each report)
//   Offset 1:     Fixed vendor byte (0x00)
//   Offset 2:     Buttons byte 3: B(0), A(1), Y(2), X(3), R(4), ZR(5), +(6), R3(7)
//   Offset 3:     Buttons byte 4: DD(0), DR(1), DL(2), DU(3), L(4), ZL(5), -(6), L3(7)
//   Offset 4:     Buttons byte 5: Home(0), Capture(1), R4(2), L4(3), Square(4), pad(5-7)
//   Offset 5-7:   Left stick  (packed 12-bit X[11:0], Y[11:0])
//   Offset 8-10:  Right stick (packed 12-bit X[11:0], Y[11:0])
//   Offset 11-62: IMU/vendor data (52 bytes, zeroed — no IMU emulation)
//
// 12-bit stick packing (matches real controller):
//   stick[0] = X[7:0]
//   stick[1] = Y[3:0] << 4 | X[11:8]
//   stick[2] = Y[11:4]
//
// Stick center: 0x80 → 0x800 (2048) in 12-bit space
// ============================================================
void SwitchProUSB::sendReport09() {
    uint8_t payload[kReportLen];
    memset(payload, 0, sizeof(payload));

    // Byte 0: incrementing counter
    payload[0] = _timer++;

    // Byte 1: fixed vendor byte
    payload[1] = 0x00;

    // Bytes 2-4: 21 buttons + 3 bits padding
    // _buttons bits 0-20 map directly to the report bitfield.
    payload[2] = static_cast<uint8_t>(_buttons & 0xFF);
    payload[3] = static_cast<uint8_t>((_buttons >> 8) & 0xFF);
    payload[4] = static_cast<uint8_t>((_buttons >> 16) & 0x1F);

    // Scale 8-bit (0x00-0xFF) to 12-bit (0x000-0xFF0)
    // Center: 0x80 → 0x800 (2048) — matches real controller center
    uint16_t lx12 = static_cast<uint16_t>(_lx) << 4;
    uint16_t ly12 = static_cast<uint16_t>(_ly) << 4;
    uint16_t rx12 = static_cast<uint16_t>(_rx) << 4;
    uint16_t ry12 = static_cast<uint16_t>(_ry) << 4;

    // Pack left stick (12-bit X, 12-bit Y) into 3 bytes
    payload[5] = lx12 & 0xFF;
    payload[6] = ((lx12 >> 8) & 0x0F) | ((ly12 & 0x0F) << 4);
    payload[7] = (ly12 >> 4) & 0xFF;

    // Pack right stick (12-bit X, 12-bit Y) into 3 bytes
    payload[8]  = rx12 & 0xFF;
    payload[9]  = ((rx12 >> 8) & 0x0F) | ((ry12 & 0x0F) << 4);
    payload[10] = (ry12 >> 4) & 0xFF;

    // Bytes 11-62: IMU/vendor suffix (zeroed — no IMU emulation)

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
// D-pad: convert hat value (0-7=directions, 0x0F=center) to
// individual direction bits in the button word.
//
// The real Switch 2 Pro Controller reports D-pad as individual
// button bits (not a hat switch), matching the HID descriptor's
// 21-button layout.
// ============================================================
void SwitchProUSB::dPad(uint8_t d) {
    // Clear all dpad bits first
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
        default: break;  // 0x0F or other = center, no bits set
    }

    if (dpadBits != 0) {
        _buttons = cleared | dpadBits;
        // Clear any pending d-pad release
        _pendingReleaseMask &= ~SW2_DPAD_MASK;
        _lastPressMs = millis();
    } else {
        // Release d-pad — enforce minimum hold
        if (millis() - _lastPressMs < kMinHoldMs) {
            _pendingReleaseMask |= (_buttons & SW2_DPAD_MASK);
        } else {
            _buttons = cleared;
            _pendingReleaseMask &= ~SW2_DPAD_MASK;
        }
    }
}

// ============================================================
// Button press/release with minimum hold enforcement
// ============================================================
void SwitchProUSB::press(uint8_t b) {
    if (b >= SW2_BTN_COUNT) return;
    uint32_t mask = 1UL << b;
    _buttons |= mask;
    _pendingReleaseMask &= ~mask;
    _lastPressMs = millis();
}

void SwitchProUSB::release(uint8_t b) {
    if (b >= SW2_BTN_COUNT) return;
    uint32_t mask = 1UL << b;
    if (millis() - _lastPressMs < kMinHoldMs) {
        _pendingReleaseMask |= mask;
    } else {
        _buttons &= ~mask;
        _pendingReleaseMask &= ~mask;
    }
}

void SwitchProUSB::setFullState(uint32_t buttons, uint8_t lx, uint8_t ly, uint8_t rx, uint8_t ry) {
    _buttons = buttons & ((1UL << SW2_BTN_COUNT) - 1);
    _lx = lx;
    _ly = ly;
    _rx = rx;
    _ry = ry;
    _pendingReleaseMask = 0;
    sendReport09();
}

bool SwitchProUSB::isConnected() const {
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

    // Send Report 0x09 at ~4ms cadence (bInterval=4, matches real controller)
    if (millis() - _lastReportMs < 4) return;
    _lastReportMs = millis();

    sendReport09();
}

#endif  // CONFIG_TINYUSB_HID_ENABLED
#endif  // CONFIG_IDF_TARGET_ESP32S3
