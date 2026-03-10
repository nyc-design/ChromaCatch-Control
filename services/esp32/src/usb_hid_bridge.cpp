#include "usb_hid_bridge.h"

#if defined(CONFIG_IDF_TARGET_ESP32S3)
#include <USB.h>
#include <USBHIDKeyboard.h>
#include <USBHIDMouse.h>
#include <USBHIDGamepad.h>
#include "switch_pro_usb.h"

#if CONFIG_TINYUSB_ENABLED
#include "tusb.h"
#endif

#if CONFIG_TINYUSB_VENDOR_ENABLED
#include "USBVendor.h"
#endif
#endif

namespace UsbHidBridge {

namespace {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
// Use pointers so constructors don't run at global init time.
// Only the devices needed for the active mode get constructed in init().
USBHIDKeyboard* keyboard = nullptr;
USBHIDMouse* mouse = nullptr;
USBHIDGamepad* gamepad = nullptr;
#if CONFIG_TINYUSB_HID_ENABLED
SwitchProUSB* switchProUsb = nullptr;
#if CONFIG_TINYUSB_VENDOR_ENABLED
USBVendor* vendorBulk = nullptr;

bool handleSwitchProVendorControlRequest(uint8_t rhport, uint8_t stage, arduino_usb_control_request_t const *request) {
    if (!request) return false;

    static uint32_t requestCount = 0;
    requestCount++;
    Serial.printf(
        "[Switch2Pro] vendor ctrl req #%lu stage=%u dir=%u type=%u recip=%u bReq=0x%02X wValue=0x%04X wIndex=0x%04X wLen=%u\n",
        static_cast<unsigned long>(requestCount),
        static_cast<unsigned>(stage),
        static_cast<unsigned>(request->bmRequestDirection),
        static_cast<unsigned>(request->bmRequestType),
        static_cast<unsigned>(request->bmRequestRecipient),
        static_cast<unsigned>(request->bRequest),
        static_cast<unsigned>(request->wValue),
        static_cast<unsigned>(request->wIndex),
        static_cast<unsigned>(request->wLength)
    );

    if (!vendorBulk) return false;

    // Respond on setup stage only.
    if (stage != REQUEST_STAGE_SETUP) return true;

    // Accept unknown vendor control requests instead of stalling.
    // If the host requests IN data, return zeroed bytes of requested length.
    if (request->bmRequestDirection == REQUEST_DIRECTION_IN) {
        static uint8_t zeros[64] = {0};
        size_t len = request->wLength;
        if (len > sizeof(zeros)) len = sizeof(zeros);
        return vendorBulk->sendResponse(rhport, request, zeros, len);
    }
    return vendorBulk->sendResponse(rhport, request, nullptr, 0);
}
#endif
#endif
#endif
bool initialized = false;
UsbGamepadProfile gamepadProfile = USB_GAMEPAD_PROFILE_GENERIC;

int8_t clampInt8(int value) {
    if (value > 127) return 127;
    if (value < -127) return -127;
    return static_cast<int8_t>(value);
}

} // namespace

void setGamepadProfile(UsbGamepadProfile profile) {
    gamepadProfile = profile;
#if defined(CONFIG_IDF_TARGET_ESP32S3)
    if (!initialized) return;
#if CONFIG_TINYUSB_HID_ENABLED
    if (gamepadProfile == USB_GAMEPAD_PROFILE_SWITCH_PRO) {
        if (switchProUsb) switchProUsb->begin();
    } else {
        if (gamepad) gamepad->begin();
    }
#else
    if (gamepad) gamepad->begin();
#endif
#else
    (void)profile;
#endif
}

UsbGamepadProfile getGamepadProfile() {
    return gamepadProfile;
}

void init() {
    if (initialized) return;

#if defined(CONFIG_IDF_TARGET_ESP32S3)
#if CONFIG_TINYUSB_HID_ENABLED
    if (gamepadProfile == USB_GAMEPAD_PROFILE_SWITCH_PRO) {
        // SwitchProUSB sets its own VID/PID/manufacturer/product in constructor.
        // Only this device gets registered — no keyboard/mouse/generic gamepad.
        switchProUsb = new SwitchProUSB();
        g_switchProUsbDevice = switchProUsb;  // expose for --wrap callback
        switchProUsb->begin();
#if CONFIG_TINYUSB_VENDOR_ENABLED
        // USBVendor constructor calls tinyusb_enable_interface(USB_INTERFACE_VENDOR)
        // which registers the vendor interface with the framework. Without this,
        // TinyUSB never opens the vendor bulk endpoints (EP 0x02/0x82).
        vendorBulk = new USBVendor();
        vendorBulk->begin();
        vendorBulk->onRequest(handleSwitchProVendorControlRequest);
        switchProUsb->setVendorInterface(vendorBulk);
        Serial.println("[USB] Switch Pro Controller USB mode initialized (HID + Vendor Bulk)");
#else
        Serial.println("[USB] Switch Pro Controller USB mode initialized (HID only)");
#endif
    } else {
#if CONFIG_TINYUSB_ENABLED
        USB.manufacturerName("ChromaCatch");
        USB.productName("ChromaCatch HID");
#endif
        // Normal multi-HID mode: keyboard + mouse + gamepad
        keyboard = new USBHIDKeyboard();
        mouse = new USBHIDMouse();
        gamepad = new USBHIDGamepad();
        keyboard->begin();
        mouse->begin();
        gamepad->begin();
    }
#else
#if CONFIG_TINYUSB_ENABLED
    USB.manufacturerName("ChromaCatch");
    USB.productName("ChromaCatch HID");
#endif
    keyboard = new USBHIDKeyboard();
    mouse = new USBHIDMouse();
    gamepad = new USBHIDGamepad();
    keyboard->begin();
    mouse->begin();
    gamepad->begin();
#endif

#if CONFIG_TINYUSB_ENABLED
    bool usbOk = USB.begin();
    Serial.printf("[USB] USB.begin() %s, profile=%d\n", usbOk ? "OK" : "FAILED", gamepadProfile);
    if (!usbOk) {
        Serial.println("[USB] ERROR: TinyUSB failed to start — check USB PHY and ARDUINO_USB_MODE");
    }
    // Dump device descriptor for verification
    const uint8_t* devDesc = tud_descriptor_device_cb();
    if (devDesc) {
        uint16_t vid = devDesc[8] | (devDesc[9] << 8);
        uint16_t pid = devDesc[10] | (devDesc[11] << 8);
        uint8_t devClass = devDesc[4];
        uint8_t devSubClass = devDesc[5];
        uint8_t devProtocol = devDesc[6];
        uint16_t bcdUSB = devDesc[2] | (devDesc[3] << 8);
        Serial.printf("[USB] Device descriptor: VID=%04X PID=%04X class=%d/%d/%d bcdUSB=%04X\n",
                      vid, pid, devClass, devSubClass, devProtocol, bcdUSB);
    }
#endif
#endif

    initialized = true;
}

bool isMounted() {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
#if CONFIG_TINYUSB_ENABLED
    return tud_mounted() && !tud_suspended();
#else
    return false;
#endif
#else
    return false;
#endif
}

bool isUsbConnected() {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
#if CONFIG_TINYUSB_ENABLED
    // Returns true even when suspended (cable still plugged in).
    // Use for allowing best-effort sends (e.g. Home button wake attempt).
    return tud_mounted();
#else
    return false;
#endif
#else
    return false;
#endif
}

void mouseMove(int dx, int dy) {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
    if (mouse) mouse->move(clampInt8(dx), clampInt8(dy));
#else
    (void)dx;
    (void)dy;
#endif
}

void mouseClick(uint8_t button) {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
    if (mouse) mouse->click(button);
#else
    (void)button;
#endif
}

void mousePress(uint8_t button) {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
    if (mouse) mouse->press(button);
#else
    (void)button;
#endif
}

void mouseRelease(uint8_t button) {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
    if (mouse) mouse->release(button);
#else
    (void)button;
#endif
}

void keyboardPress(uint8_t keyCode) {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
    if (keyboard) keyboard->press(keyCode);
#else
    (void)keyCode;
#endif
}

void keyboardRelease(uint8_t keyCode) {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
    if (keyboard) keyboard->release(keyCode);
#else
    (void)keyCode;
#endif
}

void keyboardPrint(const String& text) {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
    if (keyboard) keyboard->print(text);
#else
    (void)text;
#endif
}

void gamepadPress(uint8_t button) {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
#if CONFIG_TINYUSB_HID_ENABLED
    if (gamepadProfile == USB_GAMEPAD_PROFILE_SWITCH_PRO && switchProUsb) {
        // State change only — loop() sends 0x30 reports at 8ms cadence.
        // No write() here: avoids interfering with the report cadence and
        // works with the minimum hold enforcement in SwitchProUSB.
        switchProUsb->press(button);
        return;
    }
#endif
    if (gamepad) gamepad->pressButton(button);
#else
    (void)button;
#endif
}

void gamepadRelease(uint8_t button) {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
#if CONFIG_TINYUSB_HID_ENABLED
    if (gamepadProfile == USB_GAMEPAD_PROFILE_SWITCH_PRO && switchProUsb) {
        // Release may be deferred by SwitchProUSB's minimum hold logic.
        switchProUsb->release(button);
        return;
    }
#endif
    if (gamepad) gamepad->releaseButton(button);
#else
    (void)button;
#endif
}

void gamepadHat(uint8_t hat) {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
#if CONFIG_TINYUSB_HID_ENABLED
    if (gamepadProfile == USB_GAMEPAD_PROFILE_SWITCH_PRO && switchProUsb) {
        // USB_HAT_* uses 0=center, 1-8=directions (1-indexed).
        // SwitchProUSB::dPad() expects 0-7=directions, 0x0F=center.
        // It converts hat to individual SW2_BTN_D* bits internally.
        uint8_t stdHat = (hat == 0) ? 0x0F : (hat - 1);
        switchProUsb->dPad(stdHat);
        return;
    }
#endif
    if (gamepad) gamepad->hat(hat);
#else
    (void)hat;
#endif
}

void gamepadLeftStick(int x, int y) {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
#if CONFIG_TINYUSB_HID_ENABLED
    if (gamepadProfile == USB_GAMEPAD_PROFILE_SWITCH_PRO && switchProUsb) {
        uint8_t mappedX = static_cast<uint8_t>(clampInt8(x) + 128);
        uint8_t mappedY = static_cast<uint8_t>(clampInt8(y) + 128);
        switchProUsb->leftXAxis(mappedX);
        switchProUsb->leftYAxis(mappedY);
        return;
    }
#endif
    if (gamepad) gamepad->leftStick(clampInt8(x), clampInt8(y));
#else
    (void)x;
    (void)y;
#endif
}

void gamepadRightStick(int x, int y) {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
#if CONFIG_TINYUSB_HID_ENABLED
    if (gamepadProfile == USB_GAMEPAD_PROFILE_SWITCH_PRO && switchProUsb) {
        uint8_t mappedX = static_cast<uint8_t>(clampInt8(x) + 128);
        uint8_t mappedY = static_cast<uint8_t>(clampInt8(y) + 128);
        switchProUsb->rightXAxis(mappedX);
        switchProUsb->rightYAxis(mappedY);
        return;
    }
#endif
    if (gamepad) gamepad->rightStick(clampInt8(x), clampInt8(y));
#else
    (void)x;
    (void)y;
#endif
}

void gamepadSetFullState(uint16_t buttons, uint8_t hat, uint8_t lx, uint8_t ly, uint8_t rx, uint8_t ry) {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
#if CONFIG_TINYUSB_HID_ENABLED
    if (gamepadProfile == USB_GAMEPAD_PROFILE_SWITCH_PRO && switchProUsb) {
        // Remap PA wired button format (Y=b0,B=b1,...Cap=b13) to
        // Switch 2 Pro report bitfield (SW2_BTN_* positions).
        uint32_t sw2 = 0;
        // PA byte 0: Y=b0, B=b1, A=b2, X=b3, L=b4, R=b5, ZL=b6, ZR=b7
        if (buttons & (1 << 0))  sw2 |= (1UL << SW2_BTN_Y);
        if (buttons & (1 << 1))  sw2 |= (1UL << SW2_BTN_B);
        if (buttons & (1 << 2))  sw2 |= (1UL << SW2_BTN_A);
        if (buttons & (1 << 3))  sw2 |= (1UL << SW2_BTN_X);
        if (buttons & (1 << 4))  sw2 |= (1UL << SW2_BTN_L);
        if (buttons & (1 << 5))  sw2 |= (1UL << SW2_BTN_R);
        if (buttons & (1 << 6))  sw2 |= (1UL << SW2_BTN_ZL);
        if (buttons & (1 << 7))  sw2 |= (1UL << SW2_BTN_ZR);
        // PA byte 1: Minus=b0, Plus=b1, L3=b2, R3=b3, Home=b4, Cap=b5
        if (buttons & (1 << 8))  sw2 |= (1UL << SW2_BTN_MINUS);
        if (buttons & (1 << 9))  sw2 |= (1UL << SW2_BTN_PLUS);
        if (buttons & (1 << 10)) sw2 |= (1UL << SW2_BTN_L3);
        if (buttons & (1 << 11)) sw2 |= (1UL << SW2_BTN_R3);
        if (buttons & (1 << 12)) sw2 |= (1UL << SW2_BTN_HOME);
        if (buttons & (1 << 13)) sw2 |= (1UL << SW2_BTN_CAPTURE);

        // Remap hat (0-7=directions, 0x0F=center) to individual D-pad bits
        switch (hat) {
            case 0: sw2 |= (1UL << SW2_BTN_DUP); break;
            case 1: sw2 |= (1UL << SW2_BTN_DUP) | (1UL << SW2_BTN_DRIGHT); break;
            case 2: sw2 |= (1UL << SW2_BTN_DRIGHT); break;
            case 3: sw2 |= (1UL << SW2_BTN_DDOWN) | (1UL << SW2_BTN_DRIGHT); break;
            case 4: sw2 |= (1UL << SW2_BTN_DDOWN); break;
            case 5: sw2 |= (1UL << SW2_BTN_DDOWN) | (1UL << SW2_BTN_DLEFT); break;
            case 6: sw2 |= (1UL << SW2_BTN_DLEFT); break;
            case 7: sw2 |= (1UL << SW2_BTN_DUP) | (1UL << SW2_BTN_DLEFT); break;
            default: break;  // 0x0F = center, no D-pad bits
        }

        switchProUsb->setFullState(sw2, lx, ly, rx, ry);
        return;
    }
#endif
    if (gamepad) {
        for (uint8_t i = 0; i < 15; i++) {
            if (buttons & (1 << i)) gamepad->pressButton(i);
            else gamepad->releaseButton(i);
        }
        gamepad->hat(hat);
        gamepad->leftStick(static_cast<int8_t>(lx - 128), static_cast<int8_t>(ly - 128));
        gamepad->rightStick(static_cast<int8_t>(rx - 128), static_cast<int8_t>(ry - 128));
    }
#else
    (void)buttons; (void)hat; (void)lx; (void)ly; (void)rx; (void)ry;
#endif
}

void tick() {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
#if CONFIG_TINYUSB_HID_ENABLED
    if (!initialized) return;
    if (gamepadProfile == USB_GAMEPAD_PROFILE_SWITCH_PRO && switchProUsb && isUsbConnected()) {
        switchProUsb->loop();
    }
#endif
#endif
}

} // namespace UsbHidBridge
