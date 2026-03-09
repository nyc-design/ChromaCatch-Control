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
        Serial.println("[USB] Switch Pro Controller USB mode initialized");
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
    USB.begin();
    Serial.printf("[USB] USB.begin() done, profile=%d\n", gamepadProfile);
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
        // SwitchProUSB uses same NSGamepad-style button indices
        switchProUsb->press(button);
        switchProUsb->write();
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
        switchProUsb->release(button);
        switchProUsb->write();
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
        // SwitchProUSB::fillInputHeader expects standard HID hat: 0-7=directions, 0x0F=center.
        uint8_t stdHat = (hat == 0) ? 0x0F : (hat - 1);
        switchProUsb->dPad(stdHat);
        switchProUsb->write();
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
        switchProUsb->write();
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
        switchProUsb->write();
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
        switchProUsb->buttons(buttons);
        switchProUsb->dPad(hat);
        switchProUsb->leftXAxis(lx);
        switchProUsb->leftYAxis(ly);
        switchProUsb->rightXAxis(rx);
        switchProUsb->rightYAxis(ry);
        switchProUsb->write();
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
