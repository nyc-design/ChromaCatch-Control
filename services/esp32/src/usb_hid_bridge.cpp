#include "usb_hid_bridge.h"

#include <USB.h>
#include <USBHIDKeyboard.h>
#include <USBHIDMouse.h>
#include <USBHIDGamepad.h>

#if CONFIG_TINYUSB_ENABLED
#include "tusb.h"
#endif

namespace UsbHidBridge {

namespace {
USBHIDKeyboard keyboard;
USBHIDMouse mouse;
USBHIDGamepad gamepad;
bool initialized = false;

int8_t clampInt8(int value) {
    if (value > 127) return 127;
    if (value < -127) return -127;
    return static_cast<int8_t>(value);
}

} // namespace

void init() {
    if (initialized) return;

#if CONFIG_TINYUSB_ENABLED
    USB.manufacturerName("ChromaCatch");
    USB.productName("ChromaCatch HID");
    USB.begin();
#endif

    keyboard.begin();
    mouse.begin();
    gamepad.begin();

    initialized = true;
}

bool isMounted() {
#if CONFIG_TINYUSB_ENABLED
    return tud_mounted() && !tud_suspended();
#else
    return false;
#endif
}

void mouseMove(int dx, int dy) {
    mouse.move(clampInt8(dx), clampInt8(dy));
}

void mouseClick(uint8_t button) {
    mouse.click(button);
}

void mousePress(uint8_t button) {
    mouse.press(button);
}

void mouseRelease(uint8_t button) {
    mouse.release(button);
}

void keyboardPress(uint8_t keyCode) {
    keyboard.press(keyCode);
}

void keyboardRelease(uint8_t keyCode) {
    keyboard.release(keyCode);
}

void keyboardPrint(const String& text) {
    keyboard.print(text);
}

void gamepadPress(uint8_t button) {
    gamepad.pressButton(button);
}

void gamepadRelease(uint8_t button) {
    gamepad.releaseButton(button);
}

void gamepadHat(uint8_t hat) {
    gamepad.hat(hat);
}

void gamepadLeftStick(int x, int y) {
    gamepad.leftStick(clampInt8(x), clampInt8(y));
}

void gamepadRightStick(int x, int y) {
    gamepad.rightStick(clampInt8(x), clampInt8(y));
}

} // namespace UsbHidBridge
