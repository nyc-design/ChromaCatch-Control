#include "usb_hid_bridge.h"

#if defined(CONFIG_IDF_TARGET_ESP32S3)
#include <USB.h>
#include <USBHIDKeyboard.h>
#include <USBHIDMouse.h>
#include <USBHIDGamepad.h>
#include <switch_ESP32.h>

#if CONFIG_TINYUSB_ENABLED
#include "tusb.h"
#endif
#endif

namespace UsbHidBridge {

namespace {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
USBHIDKeyboard keyboard;
USBHIDMouse mouse;
USBHIDGamepad gamepad;
#if CONFIG_TINYUSB_HID_ENABLED
NSGamepad switchGamepad;
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
        switchGamepad.begin();
    } else {
        gamepad.begin();
    }
#else
    gamepad.begin();
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
#if CONFIG_TINYUSB_ENABLED
    if (gamepadProfile == USB_GAMEPAD_PROFILE_SWITCH_PRO) {
        USB.manufacturerName("Nintendo Co., Ltd.");
        USB.productName("Pro Controller");
    } else {
        USB.manufacturerName("ChromaCatch");
        USB.productName("ChromaCatch HID");
    }
#endif

#if CONFIG_TINYUSB_HID_ENABLED
    if (gamepadProfile == USB_GAMEPAD_PROFILE_SWITCH_PRO) {
        // Match switch_ESP32 startup sequence: register NS HID device first,
        // then start USB to enumerate with the Switch descriptor/PID.
        switchGamepad.begin();
    } else {
        keyboard.begin();
        mouse.begin();
        gamepad.begin();
    }
#else
    keyboard.begin();
    mouse.begin();
    gamepad.begin();
#endif

#if CONFIG_TINYUSB_ENABLED
    USB.begin();
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

void mouseMove(int dx, int dy) {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
    mouse.move(clampInt8(dx), clampInt8(dy));
#else
    (void)dx;
    (void)dy;
#endif
}

void mouseClick(uint8_t button) {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
    mouse.click(button);
#else
    (void)button;
#endif
}

void mousePress(uint8_t button) {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
    mouse.press(button);
#else
    (void)button;
#endif
}

void mouseRelease(uint8_t button) {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
    mouse.release(button);
#else
    (void)button;
#endif
}

void keyboardPress(uint8_t keyCode) {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
    keyboard.press(keyCode);
#else
    (void)keyCode;
#endif
}

void keyboardRelease(uint8_t keyCode) {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
    keyboard.release(keyCode);
#else
    (void)keyCode;
#endif
}

void keyboardPrint(const String& text) {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
    keyboard.print(text);
#else
    (void)text;
#endif
}

void gamepadPress(uint8_t button) {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
#if CONFIG_TINYUSB_HID_ENABLED
    if (gamepadProfile == USB_GAMEPAD_PROFILE_SWITCH_PRO) {
        uint8_t switchBtn = 0xFF;
        switch (button) {
            case USB_BUTTON_EAST: switchBtn = NSButton_A; break;          // Switch A
            case USB_BUTTON_SOUTH: switchBtn = NSButton_B; break;         // Switch B
            case USB_BUTTON_NORTH: switchBtn = NSButton_X; break;         // Switch X
            case USB_BUTTON_WEST: switchBtn = NSButton_Y; break;          // Switch Y
            case USB_BUTTON_TL: switchBtn = NSButton_LeftTrigger; break;  // L
            case USB_BUTTON_TR: switchBtn = NSButton_RightTrigger; break; // R
            case USB_BUTTON_TL2: switchBtn = NSButton_LeftThrottle; break;  // ZL
            case USB_BUTTON_TR2: switchBtn = NSButton_RightThrottle; break; // ZR
            case USB_BUTTON_SELECT: switchBtn = NSButton_Minus; break;
            case USB_BUTTON_START: switchBtn = NSButton_Plus; break;
            case USB_BUTTON_THUMBL: switchBtn = NSButton_LeftStick; break;
            case USB_BUTTON_THUMBR: switchBtn = NSButton_RightStick; break;
            case USB_BUTTON_MODE: switchBtn = NSButton_Home; break;
            default: break;
        }
        if (switchBtn != 0xFF) {
            switchGamepad.press(switchBtn);
            switchGamepad.write();
        }
        return;
    }
#endif
    gamepad.pressButton(button);
#else
    (void)button;
#endif
}

void gamepadRelease(uint8_t button) {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
#if CONFIG_TINYUSB_HID_ENABLED
    if (gamepadProfile == USB_GAMEPAD_PROFILE_SWITCH_PRO) {
        uint8_t switchBtn = 0xFF;
        switch (button) {
            case USB_BUTTON_EAST: switchBtn = NSButton_A; break;
            case USB_BUTTON_SOUTH: switchBtn = NSButton_B; break;
            case USB_BUTTON_NORTH: switchBtn = NSButton_X; break;
            case USB_BUTTON_WEST: switchBtn = NSButton_Y; break;
            case USB_BUTTON_TL: switchBtn = NSButton_LeftTrigger; break;
            case USB_BUTTON_TR: switchBtn = NSButton_RightTrigger; break;
            case USB_BUTTON_TL2: switchBtn = NSButton_LeftThrottle; break;
            case USB_BUTTON_TR2: switchBtn = NSButton_RightThrottle; break;
            case USB_BUTTON_SELECT: switchBtn = NSButton_Minus; break;
            case USB_BUTTON_START: switchBtn = NSButton_Plus; break;
            case USB_BUTTON_THUMBL: switchBtn = NSButton_LeftStick; break;
            case USB_BUTTON_THUMBR: switchBtn = NSButton_RightStick; break;
            case USB_BUTTON_MODE: switchBtn = NSButton_Home; break;
            default: break;
        }
        if (switchBtn != 0xFF) {
            switchGamepad.release(switchBtn);
            switchGamepad.write();
        }
        return;
    }
#endif
    gamepad.releaseButton(button);
#else
    (void)button;
#endif
}

void gamepadHat(uint8_t hat) {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
#if CONFIG_TINYUSB_HID_ENABLED
    if (gamepadProfile == USB_GAMEPAD_PROFILE_SWITCH_PRO) {
        uint8_t mapped = NSGAMEPAD_DPAD_CENTERED;
        switch (hat) {
            case USB_HAT_UP: mapped = NSGAMEPAD_DPAD_UP; break;
            case USB_HAT_UP_RIGHT: mapped = NSGAMEPAD_DPAD_UP_RIGHT; break;
            case USB_HAT_RIGHT: mapped = NSGAMEPAD_DPAD_RIGHT; break;
            case USB_HAT_DOWN_RIGHT: mapped = NSGAMEPAD_DPAD_DOWN_RIGHT; break;
            case USB_HAT_DOWN: mapped = NSGAMEPAD_DPAD_DOWN; break;
            case USB_HAT_DOWN_LEFT: mapped = NSGAMEPAD_DPAD_DOWN_LEFT; break;
            case USB_HAT_LEFT: mapped = NSGAMEPAD_DPAD_LEFT; break;
            case USB_HAT_UP_LEFT: mapped = NSGAMEPAD_DPAD_UP_LEFT; break;
            default: mapped = NSGAMEPAD_DPAD_CENTERED; break;
        }
        switchGamepad.dPad(mapped);
        switchGamepad.write();
        return;
    }
#endif
    gamepad.hat(hat);
#else
    (void)hat;
#endif
}

void gamepadLeftStick(int x, int y) {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
#if CONFIG_TINYUSB_HID_ENABLED
    if (gamepadProfile == USB_GAMEPAD_PROFILE_SWITCH_PRO) {
        uint8_t mappedX = static_cast<uint8_t>(clampInt8(x) + 128);
        uint8_t mappedY = static_cast<uint8_t>(clampInt8(y) + 128);
        switchGamepad.leftXAxis(mappedX);
        switchGamepad.leftYAxis(mappedY);
        switchGamepad.write();
        return;
    }
#endif
    gamepad.leftStick(clampInt8(x), clampInt8(y));
#else
    (void)x;
    (void)y;
#endif
}

void gamepadRightStick(int x, int y) {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
#if CONFIG_TINYUSB_HID_ENABLED
    if (gamepadProfile == USB_GAMEPAD_PROFILE_SWITCH_PRO) {
        uint8_t mappedX = static_cast<uint8_t>(clampInt8(x) + 128);
        uint8_t mappedY = static_cast<uint8_t>(clampInt8(y) + 128);
        switchGamepad.rightXAxis(mappedX);
        switchGamepad.rightYAxis(mappedY);
        switchGamepad.write();
        return;
    }
#endif
    gamepad.rightStick(clampInt8(x), clampInt8(y));
#else
    (void)x;
    (void)y;
#endif
}

void tick() {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
#if CONFIG_TINYUSB_HID_ENABLED
    if (!initialized) return;
    if (gamepadProfile == USB_GAMEPAD_PROFILE_SWITCH_PRO) {
        // switch_ESP32 expects loop() to periodically flush neutral/updated
        // reports for robust host-side controller presence.
        switchGamepad.loop();
    }
#endif
#endif
}

} // namespace UsbHidBridge
