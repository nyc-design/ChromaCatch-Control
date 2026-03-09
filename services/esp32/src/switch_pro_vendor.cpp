// switch_pro_vendor.cpp
// Vendor Bulk class callbacks for the Switch 2 Pro Controller USB emulation.
//
// These callbacks MUST be in a separate translation unit that does NOT include
// tusb.h or any header that transitively includes class/vendor/vendor_device.h.
// vendor_device.h declares tud_vendor_rx_cb / tud_vendor_tx_cb with TU_ATTR_WEAK.
// Once a function is declared weak in a TU, any definition in the same TU stays
// weak. By defining them here (without any TinyUSB headers), they compile as
// strong symbols that override TinyUSB's default weak empty implementations.
//
// IMPORTANT: Do NOT add #include "switch_pro_usb.h" or any Arduino/TinyUSB
// header here — it would pull in tusb.h → vendor_device.h → weak attributes.

#include <stdint.h>

// CONFIG_TINYUSB_VENDOR_ENABLED is set by platformio.ini build_flags for esp32s3.
// On esp32 builds this file compiles to nothing.
#if defined(CONFIG_TINYUSB_VENDOR_ENABLED) && CONFIG_TINYUSB_VENDOR_ENABLED

// These are the actual linker symbols for the TinyUSB vendor class.
// The non-_n names (tud_vendor_read etc.) are inline wrappers in vendor_device.h
// that we can't include here.
extern "C" {
    uint32_t tud_vendor_n_read(uint8_t itf, void* buffer, uint32_t bufsize);
}

// C-linkage bridge function defined in switch_pro_usb.cpp.
// Forwards vendor bulk data to SwitchProUSB::onVendorRx() without
// requiring us to include switch_pro_usb.h (which pulls in tusb.h).
extern "C" void switch_pro_vendor_bridge_rx(const uint8_t* data, uint16_t len);

extern "C" {
    // Called when the Switch console sends data on Bulk OUT (EP 0x02).
    // The Switch sends 18+ init commands during controller setup.
    void tud_vendor_rx_cb(uint8_t itf) {
        uint8_t buf[64];
        uint32_t count = tud_vendor_n_read(itf, buf, sizeof(buf));
        if (count > 0) {
            switch_pro_vendor_bridge_rx(buf, static_cast<uint16_t>(count));
        }
    }

    // Called when our ACK response has been sent on Bulk IN (EP 0x82).
    void tud_vendor_tx_cb(uint8_t itf, uint32_t sent_bytes) {
        (void)itf;
        (void)sent_bytes;
    }
}

#endif  // CONFIG_TINYUSB_VENDOR_ENABLED
