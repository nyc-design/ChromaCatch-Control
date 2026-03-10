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

// DISABLED: Using Arduino's USBVendor class instead.
// The framework's USBVendor.cpp provides tud_vendor_rx_cb. Creating a
// USBVendor instance also registers the vendor interface with the framework
// via tinyusb_enable_interface(), which is the ONLY way to get the framework
// to open the vendor bulk endpoints in TinyUSB's DCD layer.
//
// Our previous approach (defining tud_vendor_rx_cb here + --wrap for the
// config descriptor) failed because the framework never called
// tinyusb_enable_interface() for vendor, so the vendor class driver's
// endpoints were never opened internally — even though the host saw them
// in the descriptor we served via --wrap.
//
// Vendor data is now polled via USBVendor::available()/read() in
// SwitchProUSB::loop(), which runs at 4ms cadence.
