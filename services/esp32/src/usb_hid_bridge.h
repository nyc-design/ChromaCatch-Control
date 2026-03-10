#pragma once

#include <Arduino.h>

namespace UsbHidBridge {

// Mouse buttons
constexpr uint8_t USB_MOUSE_BTN_LEFT = 0x01;
constexpr uint8_t USB_MOUSE_BTN_RIGHT = 0x02;
constexpr uint8_t USB_MOUSE_BTN_MIDDLE = 0x04;

// Gamepad hat values (USBHIDGamepad)
constexpr uint8_t USB_HAT_CENTER = 0;
constexpr uint8_t USB_HAT_UP = 1;
constexpr uint8_t USB_HAT_UP_RIGHT = 2;
constexpr uint8_t USB_HAT_RIGHT = 3;
constexpr uint8_t USB_HAT_DOWN_RIGHT = 4;
constexpr uint8_t USB_HAT_DOWN = 5;
constexpr uint8_t USB_HAT_DOWN_LEFT = 6;
constexpr uint8_t USB_HAT_LEFT = 7;
constexpr uint8_t USB_HAT_UP_LEFT = 8;

// Gamepad button values (USBHIDGamepad)
constexpr uint8_t USB_BUTTON_A = 0;
constexpr uint8_t USB_BUTTON_B = 1;
constexpr uint8_t USB_BUTTON_C = 2;
constexpr uint8_t USB_BUTTON_X = 3;
constexpr uint8_t USB_BUTTON_Y = 4;
constexpr uint8_t USB_BUTTON_Z = 5;
constexpr uint8_t USB_BUTTON_TL = 6;
constexpr uint8_t USB_BUTTON_TR = 7;
constexpr uint8_t USB_BUTTON_TL2 = 8;
constexpr uint8_t USB_BUTTON_TR2 = 9;
constexpr uint8_t USB_BUTTON_SELECT = 10;
constexpr uint8_t USB_BUTTON_START = 11;
constexpr uint8_t USB_BUTTON_MODE = 12;
constexpr uint8_t USB_BUTTON_THUMBL = 13;
constexpr uint8_t USB_BUTTON_THUMBR = 14;

constexpr uint8_t USB_BUTTON_SOUTH = USB_BUTTON_A;
constexpr uint8_t USB_BUTTON_EAST = USB_BUTTON_B;
constexpr uint8_t USB_BUTTON_NORTH = USB_BUTTON_X;
constexpr uint8_t USB_BUTTON_WEST = USB_BUTTON_Y;

enum UsbGamepadProfile : uint8_t {
    USB_GAMEPAD_PROFILE_GENERIC = 0,
    USB_GAMEPAD_PROFILE_SWITCH_PRO = 1,
};

void setGamepadProfile(UsbGamepadProfile profile);
UsbGamepadProfile getGamepadProfile();

void init();
bool isMounted();
bool isUsbConnected();  // true even when suspended (cable connected)

void mouseMove(int dx, int dy);
void mouseClick(uint8_t button);
void mousePress(uint8_t button);
void mouseRelease(uint8_t button);

void keyboardPress(uint8_t keyCode);
void keyboardRelease(uint8_t keyCode);
void keyboardPrint(const String& text);

void gamepadPress(uint8_t button);
void gamepadRelease(uint8_t button);
void gamepadHat(uint8_t hat);
void gamepadLeftStick(int x, int y);   // expects -127..127
void gamepadRightStick(int x, int y);  // expects -127..127
// Atomic full-state set for Switch Pro wired mode (PA wired report format).
// buttons: 14-bit NSGamepad button word (Y=b0..Capture=b13)
// hat: NSGAMEPAD_DPAD_* value (0-7=directions, 0x0F=centered)
// lx/ly/rx/ry: 0x00-0xFF axis values (0x80=center)
void gamepadSetFullState(uint16_t buttons, uint8_t hat, uint8_t lx, uint8_t ly, uint8_t rx, uint8_t ry);
void tick();

} // namespace UsbHidBridge
