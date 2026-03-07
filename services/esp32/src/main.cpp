/**
 * ChromaCatch ESP32 Firmware (single codebase, dual targets)
 *
 * Board targeting:
 *  - ESP32-S3: USB wired + BLE modes, wired > websocket > http input priority.
 *  - ESP32-WROOM: BLE-only modes, websocket > http input priority.
 *
 * Emulation modes:
 *  - bluetooth_combo
 *  - bluetooth_mouse_only
 *  - bluetooth_keyboard_only
 *  - wired_combo (S3 only)
 *  - wired_mouse_only (S3 only)
 *  - wired_keyboard_only (S3 only)
 *  - bluetooth_xbox_controller
 *  - bluetooth_switch_pro_controller (ESP32 only)
 *  - wired_switch_pro_controller (S3 only)
 */

#include <Arduino.h>
#include <WiFi.h>
#include <ESPmDNS.h>
#include <WebServer.h>
#include <WebSocketsServer.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <GxEPD2_BW.h>
#include <Fonts/FreeMonoBold9pt7b.h>
#include <string>
#include <vector>
#include <deque>

#include <NimBLEDevice.h>
#include <BleCombo.h>
#include <BleCompositeHID.h>
#include <GamepadDevice.h>
#include <XboxGamepadDevice.h>
#include <XboxGamepadConfiguration.h>
#include "esp_mac.h"

#include "usb_hid_bridge.h"
#include "switch_pro_bt.h"

// ============================================================
// USER CONFIGURATION -- Edit before flashing
// ============================================================
#ifndef CC_WIFI_SSID
#define CC_WIFI_SSID YOUR_WIFI_SSID
#endif

#ifndef CC_WIFI_PASSWORD
#define CC_WIFI_PASSWORD YOUR_WIFI_PASSWORD
#endif

#define CC_STRINGIFY_INNER(x) #x
#define CC_STRINGIFY(x) CC_STRINGIFY_INNER(x)

const char* WIFI_SSID_RAW       = CC_STRINGIFY(CC_WIFI_SSID);
const char* WIFI_PASSWORD_RAW   = CC_STRINGIFY(CC_WIFI_PASSWORD);
const int   HTTP_PORT       = 80;
const int   WS_PORT         = 81;
const char* DEVICE_NAME     = "ChromaCatch";

// GPIO pins for 3-button menu (active LOW with pull-up)
const int GPIO_UP   = 35;
const int GPIO_DOWN = 34;
const int GPIO_SEL  = 33;

// Waveshare 2.13" E-Ink Display HAT V4 (250x122) SPI wiring:
// ESP32 -> Display
// 18 -> CLK, 17 -> DIN(MOSI), 5 -> CS, 4 -> DC, 16 -> RST, 15 -> BUSY
const int EPD_CLK  = 18;
const int EPD_DIN  = 17;
const int EPD_CS   = 5;
const int EPD_DC   = 4;
const int EPD_RST  = 16;
const int EPD_BUSY = 15;
const int EPD_ROTATION = 1; // landscape rotated layout

// Command priority windows
const unsigned long WIRED_PRIORITY_WINDOW_MS = 250;
const unsigned long WS_PRIORITY_WINDOW_MS = 200;

// Board capabilities
#if defined(CONFIG_IDF_TARGET_ESP32S3)
constexpr bool BOARD_IS_ESP32S3 = true;
constexpr bool BOARD_IS_ESP32 = false;
#elif defined(CONFIG_IDF_TARGET_ESP32)
constexpr bool BOARD_IS_ESP32S3 = false;
constexpr bool BOARD_IS_ESP32 = true;
#else
constexpr bool BOARD_IS_ESP32S3 = false;
constexpr bool BOARD_IS_ESP32 = false;
#endif

constexpr bool BOARD_SUPPORTS_WIRED_OUTPUT = BOARD_IS_ESP32S3;
constexpr bool BOARD_SUPPORTS_WIRED_INPUT = BOARD_IS_ESP32S3;
constexpr bool BOARD_SUPPORTS_BT_SWITCH_PRO_MODE = BOARD_IS_ESP32;

#if defined(ARDUINO_USB_CDC_ON_BOOT)
constexpr bool BUILD_USB_CDC_ON_BOOT = (ARDUINO_USB_CDC_ON_BOOT != 0);
#else
constexpr bool BUILD_USB_CDC_ON_BOOT = false;
#endif

// Onboard status LED configuration:
// Override these if your board uses different RGB data pins.
const int STATUS_RGB_PIN_ESP32 = 2;
const int STATUS_RGB_PIN_ESP32_ALT = -1;
const int STATUS_RGB_PIN_ESP32S3 = 48;
const int STATUS_RGB_PIN_ESP32S3_ALT = -1;
const bool STATUS_LED_ENABLE_MONO_FALLBACK = false;
const int MONO_STATUS_LED_PIN = 2;
const bool MONO_STATUS_LED_ACTIVE_HIGH = true;
const unsigned long STATUS_LED_BLINK_MS = 450;

// ============================================================
// Mode enums
// ============================================================
enum DeviceMode {
    MODE_COMBO = 0,
    MODE_KEYBOARD_ONLY,
    MODE_MOUSE_ONLY,
    MODE_GAMEPAD,
    MODE_SWITCH_CONTROLLER,
    MODE_COUNT
};

enum DeliveryPolicy {
    DELIVERY_AUTO = 0,       // USB if available, else BLE
    DELIVERY_FORCE_USB,
    DELIVERY_FORCE_BLE,
};

enum RuntimeDelivery {
    RUNTIME_NONE = 0,
    RUNTIME_USB,
    RUNTIME_BLE,
};

enum EmulationMode {
    EMU_BLUETOOTH_COMBO = 0,
    EMU_BLUETOOTH_MOUSE_ONLY,
    EMU_BLUETOOTH_KEYBOARD_ONLY,
    EMU_WIRED_COMBO,
    EMU_WIRED_MOUSE_ONLY,
    EMU_WIRED_KEYBOARD_ONLY,
    EMU_BLUETOOTH_XBOX_CONTROLLER,
    EMU_BLUETOOTH_SWITCH_PRO_CONTROLLER,
    EMU_WIRED_SWITCH_PRO_CONTROLLER,
    EMU_MODE_COUNT
};

enum CommandSource {
    SOURCE_WIFI = 0,
    SOURCE_WEBSOCKET,
    SOURCE_WIRED,
};

enum InputPolicy {
    INPUT_POLICY_AUTO = 0,      // board default priority behavior
    INPUT_POLICY_WIRED_ONLY,    // accept only wired serial input
    INPUT_POLICY_WEBSOCKET_ONLY,// accept only websocket input
    INPUT_POLICY_HTTP_ONLY,     // accept only http input
};

// ============================================================
// Globals
// ============================================================
WebServer server(HTTP_PORT);
WebSocketsServer wsServer(WS_PORT);
Preferences prefs;
bool prefsReady = false;
constexpr const char* PREFS_NAMESPACE = "ccctl";
constexpr const char* PREF_KEY_MODE = "mode";
constexpr const char* PREF_KEY_INPUT_POLICY = "in_policy";

// BLE outputs (created/destroyed on mode changes)
BleComboKeyboard* bleComboKb = nullptr;
BleComboMouse*    bleComboMouse = nullptr;
BleCompositeHID*  bleCompositeHid = nullptr;
GamepadDevice*    bleGenericGamepad = nullptr;
XboxGamepadDevice* bleXboxGamepad = nullptr;
#if defined(CONFIG_IDF_TARGET_ESP32)
SwitchProBT switchProBt;
#endif

DeviceMode currentMode = MODE_COMBO;
DeliveryPolicy deliveryPolicy = DELIVERY_AUTO;
EmulationMode currentEmulationMode = EMU_BLUETOOTH_COMBO;
InputPolicy inputPolicy = INPUT_POLICY_AUTO;

String serialBuffer = "";
std::vector<uint8_t> serialBinaryBuffer;
bool serialTextMode = false;
unsigned long lastWiredCommandAtMs = 0;
unsigned long lastWSCommandAtMs = 0;
uint32_t wsMsgCounter = 0;
size_t wsConnectedClients = 0;

// SerialPABotBase compatibility (PA-aligned framing + core message IDs)
constexpr uint8_t PABB_PROTOCOL_OVERHEAD = 6;
constexpr uint8_t PABB_PROTOCOL_MAX_PACKET_SIZE = 64;

constexpr uint8_t PABB_MSG_ERROR_INVALID_TYPE = 0x03;
constexpr uint8_t PABB_MSG_ERROR_INVALID_REQUEST = 0x04;
constexpr uint8_t PABB_MSG_ERROR_COMMAND_DROPPED = 0x06;
constexpr uint8_t PABB_MSG_ACK_COMMAND = 0x10;
constexpr uint8_t PABB_MSG_ACK_REQUEST = 0x11;
constexpr uint8_t PABB_MSG_ACK_REQUEST_I8 = 0x12;
constexpr uint8_t PABB_MSG_ACK_REQUEST_I32 = 0x14;
constexpr uint8_t PABB_MSG_ACK_REQUEST_DATA = 0x1f;

constexpr uint8_t PABB_MSG_REQUEST_PROTOCOL_VERSION = 0x41;
constexpr uint8_t PABB_MSG_REQUEST_PROGRAM_VERSION = 0x42;
constexpr uint8_t PABB_MSG_REQUEST_PROGRAM_ID = 0x43;
constexpr uint8_t PABB_MSG_REQUEST_PROGRAM_NAME = 0x44;
constexpr uint8_t PABB_MSG_REQUEST_CONTROLLER_LIST = 0x45;
constexpr uint8_t PABB_MSG_REQUEST_QUEUE_SIZE = 0x46;
constexpr uint8_t PABB_MSG_REQUEST_READ_CONTROLLER_MODE = 0x47;
constexpr uint8_t PABB_MSG_REQUEST_CHANGE_CONTROLLER_MODE = 0x48;
constexpr uint8_t PABB_MSG_REQUEST_RESET_TO_CONTROLLER = 0x49;
constexpr uint8_t PABB_MSG_REQUEST_COMMAND_FINISHED = 0x4a;
constexpr uint8_t PABB_MSG_REQUEST_STATUS = 0x50;
constexpr uint8_t PABB_MSG_REQUEST_READ_MAC_ADDRESS = 0x51;
constexpr uint8_t PABB_MSG_REQUEST_PAIRED_MAC_ADDRESS = 0x52;
constexpr uint8_t PABB_MSG_REQUEST_NS1_OEM_CONTROLLER_READ_SPI = 0x60;
constexpr uint8_t PABB_MSG_REQUEST_NS1_OEM_CONTROLLER_WRITE_SPI = 0x61;
constexpr uint8_t PABB_MSG_REQUEST_NS1_OEM_CONTROLLER_PLAYER_LIGHTS = 0x62;

constexpr uint8_t PABB_MSG_COMMAND_HID_KEYBOARD_STATE = 0x82;
constexpr uint8_t PABB_MSG_COMMAND_NS_WIRED_CONTROLLER_STATE = 0x90;
constexpr uint8_t PABB_MSG_COMMAND_NS1_OEM_CONTROLLER_BUTTONS = 0xa0;
constexpr uint8_t PABB_MSG_COMMAND_NS1_OEM_CONTROLLER_FULL_STATE = 0xa1;

constexpr uint8_t PABB_PID_PABOTBASE_ESP32 = 0x10;
constexpr uint8_t PABB_PID_PABOTBASE_ESP32S3 = 0x12;
constexpr uint32_t PABB_PROTOCOL_VERSION = 2025120800;
constexpr uint32_t PABB_PROGRAM_VERSION = 2025120800;
constexpr uint8_t PABB_QUEUE_SIZE = 4;
constexpr uint32_t PABB_CID_NONE = 0x0000;
constexpr uint32_t PABB_CID_StandardHid_Keyboard = 0x0100;
constexpr uint32_t PABB_CID_NintendoSwitch_WiredController = 0x1000;
constexpr uint32_t PABB_CID_NintendoSwitch_WiredProController = 0x1100;
constexpr uint32_t PABB_CID_NintendoSwitch_WirelessProController = 0x1180;

uint32_t pabbOutgoingSeq = 1;

struct PabbTimedCommand {
    uint8_t type;
    uint32_t seq;
    uint16_t milliseconds;
    std::vector<uint8_t> payload;
};
std::deque<PabbTimedCommand> pabbCommandQueue;
bool pabbCommandActive = false;
PabbTimedCommand pabbActiveCommand{};
unsigned long pabbActiveCommandEndMs = 0;
bool rebootPending = false;
unsigned long rebootAtMs = 0;

// Menu
int menuIndex = 0;
const int MENU_ITEMS = 1;
const char* menuLabels[] = {"Mode"};
unsigned long lastButtonPress = 0;
const unsigned long DEBOUNCE_MS = 200;

// Display state (stubbed renderer)
struct DisplayState {
    String line1;
    String line2;
    String line3;
    bool sticky = true;
    unsigned long expiresAtMs = 0;
};
DisplayState displayState;
bool einkReady = false;
bool customDisplayActive = false;
GxEPD2_BW<GxEPD2_213_B74, GxEPD2_213_B74::HEIGHT> eink(GxEPD2_213_B74(EPD_CS, EPD_DC, EPD_RST, EPD_BUSY));

struct LedColor {
    uint8_t r;
    uint8_t g;
    uint8_t b;
};

LedColor lastLedColor = {0, 0, 0};
bool lastLedOn = false;
bool ledInitialized = false;
int statusLedRgbPinPrimary = -1;
int statusLedRgbPinSecondary = -1;
bool statusLedMonoEnabled = false;

// Forward declarations used by display renderer.
bool isUSBMounted();
bool isBLEConnected();
RuntimeDelivery chooseRuntimeDelivery();
void processPabbMessage(uint8_t type, const uint8_t* body, size_t bodyLen);
void servicePabbCommandQueue();

// ============================================================
// Helpers
// ============================================================
bool strEqIgnoreCase(const String& a, const char* b) {
    String rhs = String(b);
    return a.equalsIgnoreCase(rhs);
}

String decodeBuildMacroString(const char* raw) {
    String value = raw == nullptr ? "" : String(raw);
    // If define came in already quoted, CC_STRINGIFY produces "\"value\"".
    // Strip one layer of escaped outer quotes.
    if (value.length() >= 4 &&
        value[0] == '\\' &&
        value[1] == '"' &&
        value[value.length() - 2] == '\\' &&
        value[value.length() - 1] == '"') {
        value = value.substring(2, value.length() - 2);
    }
    return value;
}

int8_t clampInt8(int value) {
    if (value > 127) return 127;
    if (value < -127) return -127;
    return static_cast<int8_t>(value);
}

bool isSwitchMode(DeviceMode mode) {
    return mode == MODE_SWITCH_CONTROLLER;
}

bool isXboxMode(DeviceMode mode) {
    return mode == MODE_GAMEPAD;
}

bool modeAllowsMouse(DeviceMode mode) {
    return mode == MODE_COMBO || mode == MODE_MOUSE_ONLY;
}

bool modeAllowsKeyboard(DeviceMode mode) {
    return mode == MODE_COMBO || mode == MODE_KEYBOARD_ONLY;
}

bool modeAllowsGamepad(DeviceMode mode) {
    return mode == MODE_GAMEPAD || mode == MODE_SWITCH_CONTROLLER;
}

bool modeUsesBleOutput(DeviceMode mode) {
    if (deliveryPolicy == DELIVERY_FORCE_USB) return false;
    return mode == MODE_COMBO || mode == MODE_KEYBOARD_ONLY || mode == MODE_MOUSE_ONLY || mode == MODE_GAMEPAD || mode == MODE_SWITCH_CONTROLLER;
}

bool modeUsesBleCombo(DeviceMode mode) {
    if (!modeUsesBleOutput(mode)) return false;
    return mode == MODE_COMBO || mode == MODE_KEYBOARD_ONLY || mode == MODE_MOUSE_ONLY;
}

bool modeUsesBleGamepad(DeviceMode mode) {
    if (!modeUsesBleOutput(mode)) return false;
    return mode == MODE_GAMEPAD;
}

bool emulationModeSupported(EmulationMode mode) {
    switch (mode) {
        case EMU_BLUETOOTH_COMBO:
        case EMU_BLUETOOTH_MOUSE_ONLY:
        case EMU_BLUETOOTH_KEYBOARD_ONLY:
        case EMU_BLUETOOTH_XBOX_CONTROLLER:
            return true;
        case EMU_BLUETOOTH_SWITCH_PRO_CONTROLLER:
            return BOARD_SUPPORTS_BT_SWITCH_PRO_MODE;
        case EMU_WIRED_COMBO:
        case EMU_WIRED_MOUSE_ONLY:
        case EMU_WIRED_KEYBOARD_ONLY:
        case EMU_WIRED_SWITCH_PRO_CONTROLLER:
            return BOARD_SUPPORTS_WIRED_OUTPUT;
        default:
            return false;
    }
}

bool emulationModeBlockedByBuild(EmulationMode mode) {
    // Switch USB compatibility requires a USB build without CDC-on-boot.
    if (mode == EMU_WIRED_SWITCH_PRO_CONTROLLER && BOARD_IS_ESP32S3 && BUILD_USB_CDC_ON_BOOT) {
        return true;
    }
    return false;
}

UsbHidBridge::UsbGamepadProfile emulationModeToUsbProfile(EmulationMode mode) {
    if (mode == EMU_WIRED_SWITCH_PRO_CONTROLLER) {
        return UsbHidBridge::USB_GAMEPAD_PROFILE_SWITCH_PRO;
    }
    return UsbHidBridge::USB_GAMEPAD_PROFILE_GENERIC;
}

const char* emulationModeToString(EmulationMode mode) {
    switch (mode) {
        case EMU_BLUETOOTH_COMBO: return "bluetooth_combo";
        case EMU_BLUETOOTH_MOUSE_ONLY: return "bluetooth_mouse_only";
        case EMU_BLUETOOTH_KEYBOARD_ONLY: return "bluetooth_keyboard_only";
        case EMU_WIRED_COMBO: return "wired_combo";
        case EMU_WIRED_MOUSE_ONLY: return "wired_mouse_only";
        case EMU_WIRED_KEYBOARD_ONLY: return "wired_keyboard_only";
        case EMU_BLUETOOTH_XBOX_CONTROLLER: return "bluetooth_xbox_controller";
        case EMU_BLUETOOTH_SWITCH_PRO_CONTROLLER: return "bluetooth_switch_pro_controller";
        case EMU_WIRED_SWITCH_PRO_CONTROLLER: return "wired_switch_pro_controller";
        default: return "bluetooth_combo";
    }
}

const char* modeToString(DeviceMode mode) {
    switch (mode) {
        case MODE_COMBO: return "combo";
        case MODE_KEYBOARD_ONLY: return "keyboard_only";
        case MODE_MOUSE_ONLY: return "mouse_only";
        case MODE_GAMEPAD: return "xbox_controller";
        case MODE_SWITCH_CONTROLLER: return "switch_controller";
        default: return "combo";
    }
}

const char* deliveryPolicyToString(DeliveryPolicy p) {
    switch (p) {
        case DELIVERY_AUTO: return "auto";
        case DELIVERY_FORCE_USB: return "wired";
        case DELIVERY_FORCE_BLE: return "bluetooth";
        default: return "auto";
    }
}

const char* runtimeDeliveryToString(RuntimeDelivery d) {
    switch (d) {
        case RUNTIME_USB: return "wired";
        case RUNTIME_BLE: return "bluetooth";
        default: return "none";
    }
}

String getBleAdvertisementName() {
    if (!modeUsesBleOutput(currentMode)) return "n/a";
    switch (currentEmulationMode) {
        case EMU_BLUETOOTH_COMBO: return "ChromaCatch K + B";
        case EMU_BLUETOOTH_MOUSE_ONLY: return "ChromaCatch Mouse";
        case EMU_BLUETOOTH_KEYBOARD_ONLY: return "ChromaCatch Keyboard";
        case EMU_BLUETOOTH_XBOX_CONTROLLER: return "Xbox Wireless Controller";
        case EMU_BLUETOOTH_SWITCH_PRO_CONTROLLER: return "Pro Controller";
        default: return String(DEVICE_NAME);
    }
}

String getBleProfileLabel() {
    switch (currentEmulationMode) {
        case EMU_BLUETOOTH_COMBO: return "ble_combo";
        case EMU_BLUETOOTH_MOUSE_ONLY: return "ble_mouse";
        case EMU_BLUETOOTH_KEYBOARD_ONLY: return "ble_keyboard";
        case EMU_BLUETOOTH_XBOX_CONTROLLER: return "ble_xbox";
        case EMU_BLUETOOTH_SWITCH_PRO_CONTROLLER: return "bt_classic_switch_pro";
        default: return "none";
    }
}

String getModeHeaderLabel() {
    switch (currentEmulationMode) {
        case EMU_BLUETOOTH_COMBO: return "K+B";
        case EMU_BLUETOOTH_MOUSE_ONLY: return "MOUSE";
        case EMU_BLUETOOTH_KEYBOARD_ONLY: return "KBD";
        case EMU_WIRED_COMBO: return "WIRED K+B";
        case EMU_WIRED_MOUSE_ONLY: return "WIRED MOUSE";
        case EMU_WIRED_KEYBOARD_ONLY: return "WIRED KBD";
        case EMU_BLUETOOTH_XBOX_CONTROLLER: return "XBOX";
        case EMU_BLUETOOTH_SWITCH_PRO_CONTROLLER: return "SWITCH BT";
        case EMU_WIRED_SWITCH_PRO_CONTROLLER: return "SWITCH USB";
        default: return "UNKNOWN";
    }
}

const char* inputPolicyToString(InputPolicy p) {
    switch (p) {
        case INPUT_POLICY_WIRED_ONLY: return "wired";
        case INPUT_POLICY_WEBSOCKET_ONLY: return "websocket";
        case INPUT_POLICY_HTTP_ONLY: return "http";
        case INPUT_POLICY_AUTO:
        default:
            return "auto";
    }
}

InputPolicy parseInputPolicy(const String& rawInput) {
    String raw = rawInput;
    raw.toLowerCase();
    raw.trim();
    if (raw == "wired" || raw == "serial" || raw == "wired_only") return INPUT_POLICY_WIRED_ONLY;
    if (raw == "websocket" || raw == "ws" || raw == "websocket_only") return INPUT_POLICY_WEBSOCKET_ONLY;
    if (raw == "http" || raw == "wifi" || raw == "http_only") return INPUT_POLICY_HTTP_ONLY;
    return INPUT_POLICY_AUTO;
}

bool isSourceAllowed(CommandSource source) {
    switch (inputPolicy) {
        case INPUT_POLICY_WIRED_ONLY: return source == SOURCE_WIRED;
        case INPUT_POLICY_WEBSOCKET_ONLY: return source == SOURCE_WEBSOCKET;
        case INPUT_POLICY_HTTP_ONLY: return source == SOURCE_WIFI;
        case INPUT_POLICY_AUTO:
        default:
            return true;
    }
}

int centeredCursorX(const String& text, int rectX, int rectW, int charPx = 6) {
    int textW = static_cast<int>(text.length()) * charPx;
    int x = rectX + ((rectW - textW) / 2);
    if (x < rectX + 2) x = rectX + 2;
    return x;
}

int centeredBaselineY(int rectY, int rectH) {
    // Default built-in font is ~8px tall; baseline near bottom.
    return rectY + ((rectH - 8) / 2) + 7;
}

void initPreferences() {
    if (prefsReady) return;
    prefsReady = prefs.begin(PREFS_NAMESPACE, false);
}

void persistRuntimeConfig() {
    initPreferences();
    if (!prefsReady) return;
    prefs.putInt(PREF_KEY_MODE, static_cast<int>(currentEmulationMode));
    prefs.putInt(PREF_KEY_INPUT_POLICY, static_cast<int>(inputPolicy));
}

void loadPersistedRuntimeConfig() {
    initPreferences();
    if (!prefsReady) return;

    int savedModeRaw = prefs.getInt(PREF_KEY_MODE, static_cast<int>(EMU_BLUETOOTH_COMBO));
    int savedInputPolicyRaw = prefs.getInt(PREF_KEY_INPUT_POLICY, static_cast<int>(INPUT_POLICY_AUTO));

    EmulationMode savedMode = static_cast<EmulationMode>(savedModeRaw);
    if (savedModeRaw >= 0 &&
        savedModeRaw < static_cast<int>(EMU_MODE_COUNT) &&
        emulationModeSupported(savedMode) &&
        !emulationModeBlockedByBuild(savedMode)) {
        currentEmulationMode = savedMode;
    }

    if (savedInputPolicyRaw >= static_cast<int>(INPUT_POLICY_AUTO) &&
        savedInputPolicyRaw <= static_cast<int>(INPUT_POLICY_HTTP_ONLY)) {
        InputPolicy savedPolicy = static_cast<InputPolicy>(savedInputPolicyRaw);
        if (!(savedPolicy == INPUT_POLICY_WIRED_ONLY && !BOARD_SUPPORTS_WIRED_INPUT)) {
            inputPolicy = savedPolicy;
        }
    }
}

void deriveModeSpecificBleAddress(uint8_t out[6], EmulationMode mode) {
    uint8_t baseAddr[6] = {0};
    esp_read_mac(baseAddr, ESP_MAC_BT);
    memcpy(out, baseAddr, 6);

    uint8_t modeByte = static_cast<uint8_t>(mode);
    out[1] ^= static_cast<uint8_t>(0x31 + modeByte * 17);
    out[2] ^= static_cast<uint8_t>(0x59 + modeByte * 29);
    out[3] ^= static_cast<uint8_t>(0xC7 + modeByte * 11);
    out[4] ^= static_cast<uint8_t>(0xA3 + modeByte * 7);
    out[5] ^= static_cast<uint8_t>(0x7D + modeByte * 5);

    // static random address type (two MSBs must be 1).
    out[0] = static_cast<uint8_t>((out[0] & 0x3F) | 0xC0);
}

String formatMac(const uint8_t addr[6]) {
    char out[18];
    snprintf(out, sizeof(out), "%02X:%02X:%02X:%02X:%02X:%02X",
             addr[0], addr[1], addr[2], addr[3], addr[4], addr[5]);
    return String(out);
}

String getModeSpecificBleMacString() {
    uint8_t modeAddr[6] = {0};
    deriveModeSpecificBleAddress(modeAddr, currentEmulationMode);
    return formatMac(modeAddr);
}

void configureNimBLEIdentityForCurrentMode() {
    if (!modeUsesBleOutput(currentMode)) return;
    // NimBLE address APIs require the host stack to be initialized on this build;
    // calling them pre-init causes mutex asserts on ESP32.
    if (!NimBLEDevice::isInitialized()) return;
    uint8_t modeAddr[6] = {0};
    deriveModeSpecificBleAddress(modeAddr, currentEmulationMode);
    NimBLEDevice::setOwnAddrType(BLE_OWN_ADDR_RANDOM);
    NimBLEDevice::setOwnAddr(modeAddr);
}

LedColor modeToLedColor(EmulationMode mode) {
    switch (mode) {
        case EMU_BLUETOOTH_COMBO: return {0, 150, 150};           // cyan
        case EMU_BLUETOOTH_MOUSE_ONLY: return {0, 60, 180};       // blue
        case EMU_BLUETOOTH_KEYBOARD_ONLY: return {160, 0, 160};   // purple
        case EMU_WIRED_COMBO: return {0, 180, 0};                 // green
        case EMU_WIRED_MOUSE_ONLY: return {120, 180, 0};          // yellow-green
        case EMU_WIRED_KEYBOARD_ONLY: return {180, 140, 0};       // amber
        case EMU_BLUETOOTH_XBOX_CONTROLLER: return {0, 220, 0};   // xbox green
        case EMU_BLUETOOTH_SWITCH_PRO_CONTROLLER: return {220, 0, 0}; // red
        case EMU_WIRED_SWITCH_PRO_CONTROLLER: return {220, 70, 0}; // orange
        default: return {80, 80, 80};
    }
}

void writeStatusLed(const LedColor& color, bool on) {
    if (statusLedRgbPinPrimary >= 0) {
        if (on) neopixelWrite(statusLedRgbPinPrimary, color.r, color.g, color.b);
        else neopixelWrite(statusLedRgbPinPrimary, 0, 0, 0);
    }
    if (statusLedRgbPinSecondary >= 0) {
        if (on) neopixelWrite(statusLedRgbPinSecondary, color.r, color.g, color.b);
        else neopixelWrite(statusLedRgbPinSecondary, 0, 0, 0);
    }
    if (statusLedMonoEnabled) {
        bool high = on ? MONO_STATUS_LED_ACTIVE_HIGH : !MONO_STATUS_LED_ACTIVE_HIGH;
        digitalWrite(MONO_STATUS_LED_PIN, high ? HIGH : LOW);
    }
}

void initStatusLed() {
    if (BOARD_IS_ESP32S3) {
        statusLedRgbPinPrimary = STATUS_RGB_PIN_ESP32S3;
        statusLedRgbPinSecondary = STATUS_RGB_PIN_ESP32S3_ALT;
    } else if (BOARD_IS_ESP32) {
        statusLedRgbPinPrimary = STATUS_RGB_PIN_ESP32;
        statusLedRgbPinSecondary = STATUS_RGB_PIN_ESP32_ALT;
    }
    statusLedMonoEnabled = STATUS_LED_ENABLE_MONO_FALLBACK;

    if (statusLedMonoEnabled) {
        pinMode(MONO_STATUS_LED_PIN, OUTPUT);
        digitalWrite(MONO_STATUS_LED_PIN, MONO_STATUS_LED_ACTIVE_HIGH ? LOW : HIGH);
    }
    writeStatusLed({0, 0, 0}, false);
    ledInitialized = true;
}

void updateStatusLed() {
    if (!ledInitialized) return;

    LedColor color = modeToLedColor(currentEmulationMode);
    bool outputConnected = chooseRuntimeDelivery() != RUNTIME_NONE;
    bool shouldBlink = !outputConnected;
    bool on = !shouldBlink || (((millis() / STATUS_LED_BLINK_MS) % 2) == 0);

    bool changed = (on != lastLedOn) ||
                   (color.r != lastLedColor.r) ||
                   (color.g != lastLedColor.g) ||
                   (color.b != lastLedColor.b);
    if (!changed) return;

    writeStatusLed(color, on);
    lastLedColor = color;
    lastLedOn = on;
}

void configureNimBLEForCurrentMode() {
    if (!modeUsesBleOutput(currentMode)) return;

    // Improve discoverability on phones/scanners.
    NimBLEDevice::setPower(9, NimBLETxPowerType::All);

    if (currentEmulationMode == EMU_BLUETOOTH_XBOX_CONTROLLER) {
        // iOS/macOS tend to be stricter for controller-like peripherals.
        NimBLEDevice::setSecurityAuth(true, false, true);  // bonding + secure connections
        NimBLEDevice::setSecurityIOCap(BLE_HS_IO_NO_INPUT_OUTPUT);
    } else {
        NimBLEDevice::setSecurityAuth(true, false, false); // keyboard/mouse/gamepad baseline
    }
}

void applyEmulationMode(EmulationMode mode) {
    if (!emulationModeSupported(mode) || emulationModeBlockedByBuild(mode)) return;

    currentEmulationMode = mode;
    switch (mode) {
        case EMU_BLUETOOTH_COMBO:
            currentMode = MODE_COMBO;
            deliveryPolicy = DELIVERY_FORCE_BLE;
            break;
        case EMU_BLUETOOTH_MOUSE_ONLY:
            currentMode = MODE_MOUSE_ONLY;
            deliveryPolicy = DELIVERY_FORCE_BLE;
            break;
        case EMU_BLUETOOTH_KEYBOARD_ONLY:
            currentMode = MODE_KEYBOARD_ONLY;
            deliveryPolicy = DELIVERY_FORCE_BLE;
            break;
        case EMU_WIRED_COMBO:
            currentMode = MODE_COMBO;
            deliveryPolicy = DELIVERY_FORCE_USB;
            break;
        case EMU_WIRED_MOUSE_ONLY:
            currentMode = MODE_MOUSE_ONLY;
            deliveryPolicy = DELIVERY_FORCE_USB;
            break;
        case EMU_WIRED_KEYBOARD_ONLY:
            currentMode = MODE_KEYBOARD_ONLY;
            deliveryPolicy = DELIVERY_FORCE_USB;
            break;
        case EMU_BLUETOOTH_XBOX_CONTROLLER:
            currentMode = MODE_GAMEPAD;
            deliveryPolicy = DELIVERY_FORCE_BLE;
            break;
        case EMU_BLUETOOTH_SWITCH_PRO_CONTROLLER:
            currentMode = MODE_SWITCH_CONTROLLER;
            deliveryPolicy = DELIVERY_FORCE_BLE;
            break;
        case EMU_WIRED_SWITCH_PRO_CONTROLLER:
            currentMode = MODE_SWITCH_CONTROLLER;
            deliveryPolicy = DELIVERY_FORCE_USB;
            break;
        default:
            break;
    }
    UsbHidBridge::setGamepadProfile(emulationModeToUsbProfile(mode));
    pabbCommandQueue.clear();
    pabbCommandActive = false;
    persistRuntimeConfig();
}

bool wiredPriorityActive() {
    if (!BOARD_SUPPORTS_WIRED_INPUT) return false;
    return (millis() - lastWiredCommandAtMs) <= WIRED_PRIORITY_WINDOW_MS;
}

bool wsPriorityActive() {
    return (millis() - lastWSCommandAtMs) <= WS_PRIORITY_WINDOW_MS;
}

EmulationMode parseEmulationModeString(const String& rawInput) {
    String raw = rawInput;
    raw.toLowerCase();
    raw.trim();

    if (raw == "bluetooth_combo" || raw == "combo" || raw == "mouse_keyboard") return EMU_BLUETOOTH_COMBO;
    if (raw == "bluetooth_mouse_only" || raw == "mouse" || raw == "mouse_only") return EMU_BLUETOOTH_MOUSE_ONLY;
    if (raw == "bluetooth_keyboard_only" || raw == "keyboard" || raw == "keyboard_only") return EMU_BLUETOOTH_KEYBOARD_ONLY;

    if (raw == "wired_combo") return EMU_WIRED_COMBO;
    if (raw == "wired_mouse_only") return EMU_WIRED_MOUSE_ONLY;
    if (raw == "wired_keyboard_only") return EMU_WIRED_KEYBOARD_ONLY;

    if (raw == "bluetooth_xbox_controller" || raw == "gamepad" || raw == "general_gamepad") return EMU_BLUETOOTH_XBOX_CONTROLLER;
    if (raw == "bluetooth_switch_pro_controller") return EMU_BLUETOOTH_SWITCH_PRO_CONTROLLER;
    if (raw == "switch" || raw == "switch_pro" || raw == "switch_controller") {
        return BOARD_SUPPORTS_BT_SWITCH_PRO_MODE ? EMU_BLUETOOTH_SWITCH_PRO_CONTROLLER : EMU_WIRED_SWITCH_PRO_CONTROLLER;
    }
    if (raw == "wired_switch_pro_controller" || raw == "switch_wired") return EMU_WIRED_SWITCH_PRO_CONTROLLER;

    return currentEmulationMode;
}

DeliveryPolicy parseDeliveryPolicy(const String& rawInput) {
    String raw = rawInput;
    raw.toLowerCase();
    raw.trim();

    if (raw == "wired" || raw == "usb" || raw == "force_wired") return DELIVERY_FORCE_USB;
    if (raw == "bluetooth" || raw == "ble" || raw == "force_bluetooth") return DELIVERY_FORCE_BLE;
    return DELIVERY_AUTO;
}

EmulationMode inferEmulationMode(DeviceMode mode, DeliveryPolicy policy) {
    if (mode == MODE_COMBO && policy == DELIVERY_FORCE_BLE) return EMU_BLUETOOTH_COMBO;
    if (mode == MODE_MOUSE_ONLY && policy == DELIVERY_FORCE_BLE) return EMU_BLUETOOTH_MOUSE_ONLY;
    if (mode == MODE_KEYBOARD_ONLY && policy == DELIVERY_FORCE_BLE) return EMU_BLUETOOTH_KEYBOARD_ONLY;
    if (mode == MODE_COMBO && policy == DELIVERY_FORCE_USB) return EMU_WIRED_COMBO;
    if (mode == MODE_MOUSE_ONLY && policy == DELIVERY_FORCE_USB) return EMU_WIRED_MOUSE_ONLY;
    if (mode == MODE_KEYBOARD_ONLY && policy == DELIVERY_FORCE_USB) return EMU_WIRED_KEYBOARD_ONLY;
    if (mode == MODE_GAMEPAD && policy == DELIVERY_FORCE_BLE) return EMU_BLUETOOTH_XBOX_CONTROLLER;
    if (mode == MODE_SWITCH_CONTROLLER && policy == DELIVERY_FORCE_USB) return EMU_WIRED_SWITCH_PRO_CONTROLLER;
    if (mode == MODE_SWITCH_CONTROLLER && policy == DELIVERY_FORCE_BLE) return EMU_BLUETOOTH_SWITCH_PRO_CONTROLLER;
    if (mode == MODE_SWITCH_CONTROLLER) return BOARD_SUPPORTS_BT_SWITCH_PRO_MODE ? EMU_BLUETOOTH_SWITCH_PRO_CONTROLLER : EMU_WIRED_SWITCH_PRO_CONTROLLER;
    if (mode == MODE_GAMEPAD) return EMU_BLUETOOTH_XBOX_CONTROLLER;
    if (mode == MODE_COMBO) return BOARD_SUPPORTS_WIRED_OUTPUT ? EMU_WIRED_COMBO : EMU_BLUETOOTH_COMBO;
    if (mode == MODE_MOUSE_ONLY) return BOARD_SUPPORTS_WIRED_OUTPUT ? EMU_WIRED_MOUSE_ONLY : EMU_BLUETOOTH_MOUSE_ONLY;
    if (mode == MODE_KEYBOARD_ONLY) return BOARD_SUPPORTS_WIRED_OUTPUT ? EMU_WIRED_KEYBOARD_ONLY : EMU_BLUETOOTH_KEYBOARD_ONLY;
    return EMU_BLUETOOTH_COMBO;
}

bool modeTransitionRequiresUsbReboot(EmulationMode fromMode, EmulationMode toMode) {
    if (!BOARD_SUPPORTS_WIRED_OUTPUT) return false;
    bool fromSwitchUsb = emulationModeToUsbProfile(fromMode) == UsbHidBridge::USB_GAMEPAD_PROFILE_SWITCH_PRO;
    bool toSwitchUsb = emulationModeToUsbProfile(toMode) == UsbHidBridge::USB_GAMEPAD_PROFILE_SWITCH_PRO;
    return fromSwitchUsb != toSwitchUsb;
}

void scheduleDeviceReboot(unsigned long delayMs = 250) {
    rebootPending = true;
    rebootAtMs = millis() + delayMs;
}

// ============================================================
// E-ink display (Waveshare via GxEPD2)
// ============================================================
void displayInit() {
    Serial.println("[E-ink] Initializing...");
    SPI.begin(EPD_CLK, -1, EPD_DIN, EPD_CS); // CLK, MISO(unused), MOSI, SS
    eink.init(115200, true, 50, false);
    eink.setRotation(EPD_ROTATION);
    eink.setTextColor(GxEPD_BLACK);
    eink.setFont(&FreeMonoBold9pt7b);
    einkReady = true;
    Serial.println("[E-ink] Initialized");
}

String trimForDisplay(const String& input, size_t maxLen) {
    if (input.length() <= maxLen) return input;
    if (maxLen <= 3) return input.substring(0, maxLen);
    return input.substring(0, maxLen - 3) + "...";
}

void renderDashboardNow() {
    if (!einkReady) return;

    RuntimeDelivery active = chooseRuntimeDelivery();
    bool wifiUp = WiFi.status() == WL_CONNECTED;
    bool bleAvailable = modeUsesBleOutput(currentMode);
    bool bleConnected = isBLEConnected();

    String ip = wifiUp ? WiFi.localIP().toString() : "offline";
    String modeHeader = getModeHeaderLabel();
    String inputPol = String(inputPolicyToString(inputPolicy));
    String activeDelivery = String(runtimeDeliveryToString(active));
    String usbState = isUSBMounted() ? "mounted" : "not-mounted";
    String bleState = !bleAvailable ? "disabled" : (bleConnected ? "connected" : "advertising");
    String advName = getBleAdvertisementName();
    String wsState = wsConnectedClients > 0 ? (String(wsConnectedClients) + " client(s)") : "idle";

    int w = eink.width();
    int h = eink.height();

    eink.setFullWindow();
    eink.firstPage();
    do {
        eink.fillScreen(GxEPD_WHITE);

        // Outer border
        eink.drawRoundRect(0, 0, w, h, 6, GxEPD_BLACK);

        // Header
        const int headerH = 20;
        const int modeBoxW = 96;
        eink.fillRect(0, 0, w, headerH, GxEPD_BLACK);
        eink.drawFastVLine(w - modeBoxW, 0, headerH, GxEPD_WHITE);
        eink.setFont();
        eink.setTextSize(1);
        eink.setTextColor(GxEPD_WHITE);

        String title = "ChromaCatch Control";
        int titleX = centeredCursorX(title, 0, w - modeBoxW);
        int titleY = centeredBaselineY(0, headerH);
        eink.setCursor(titleX, titleY);
        eink.print(title);

        int modeX = centeredCursorX(modeHeader, w - modeBoxW, modeBoxW);
        int modeY = centeredBaselineY(0, headerH);
        eink.setCursor(modeX, modeY);
        eink.print(modeHeader);

        // Body
        eink.setTextColor(GxEPD_BLACK);
        int y = 32;
        eink.setCursor(6, y);   eink.print("IP: "); eink.print(ip);
        y += 12;
        eink.setCursor(6, y);   eink.print("HTTP: "); eink.print(HTTP_PORT); eink.print("  WS: "); eink.print(WS_PORT);
        y += 12;
        eink.setCursor(6, y);   eink.print("WS LINK: "); eink.print(wsState);
        y += 12;
        eink.setCursor(6, y);   eink.print("DELIVERY: "); eink.print(activeDelivery);
        y += 12;
        eink.setCursor(6, y);   eink.print("INPUT: "); eink.print(inputPol);

        // Right info panel
        int rightX = w / 2 + 2;
        int rightW = w - rightX - 4;
        eink.drawRoundRect(rightX, 22, rightW, h - 26, 4, GxEPD_BLACK);
        String panelTitle = "CONNECTIONS";
        int panelTitleX = centeredCursorX(panelTitle, rightX + 4, rightW - 8);
        eink.setCursor(panelTitleX, 33);
        eink.print(panelTitle);
        eink.drawFastHLine(rightX + 4, 38, rightW - 8, GxEPD_BLACK);
        eink.setCursor(rightX + 6, 51); eink.print("USB: "); eink.print(usbState);
        eink.setCursor(rightX + 6, 63); eink.print("BLE: "); eink.print(bleState);
        eink.setCursor(rightX + 6, 75); eink.print("ADV: "); eink.print(trimForDisplay(advName, 13));
        eink.setCursor(rightX + 6, 87); eink.print("PRIO: ");
        if (wiredPriorityActive()) eink.print("wired");
        else if (wsPriorityActive()) eink.print("websocket");
        else eink.print("none");
    } while (eink.nextPage());
}

void renderCustomDisplayNow() {
    Serial.println("\n=== E-ink ===");
    Serial.println(displayState.line1);
    Serial.println(displayState.line2);
    Serial.println(displayState.line3);
    Serial.println("=============");

    if (!einkReady) return;

    eink.setFullWindow();
    eink.firstPage();
    do {
        eink.fillScreen(GxEPD_WHITE);

        int w = eink.width();
        int h = eink.height();
        eink.drawRoundRect(0, 0, w, h, 6, GxEPD_BLACK);
        eink.fillRect(0, 0, w, 18, GxEPD_BLACK);
        eink.setFont();
        eink.setTextSize(1);
        eink.setTextColor(GxEPD_WHITE);
        eink.setCursor(6, 12);
        eink.print("Display Message");

        eink.setTextColor(GxEPD_BLACK);
        eink.setCursor(6, 32);
        eink.print(displayState.line1);
        eink.setCursor(6, 52);
        eink.print(displayState.line2);
        eink.setCursor(6, 72);
        eink.print(displayState.line3);
    } while (eink.nextPage());
}

void displaySet(const String& l1, const String& l2 = "", const String& l3 = "", bool sticky = true, unsigned long ttlMs = 0) {
    displayState.line1 = l1;
    displayState.line2 = l2;
    displayState.line3 = l3;
    displayState.sticky = sticky;
    displayState.expiresAtMs = sticky ? 0 : (millis() + ttlMs);
    customDisplayActive = true;
    renderCustomDisplayNow();
}

void displayClear() {
    displayState.line1 = "";
    displayState.line2 = "";
    displayState.line3 = "";
    displayState.sticky = true;
    displayState.expiresAtMs = 0;
    customDisplayActive = false;
    renderDashboardNow();
}

void displayMenu() {
    // Default screen: live dashboard unless a sticky custom message is active.
    if (customDisplayActive && displayState.sticky) {
        renderCustomDisplayNow();
        return;
    }
    customDisplayActive = false;
    renderDashboardNow();
}

void displayStatus(const String& l1, const String& l2 = "", const String& l3 = "") {
    // Do not override user-pinned sticky custom message.
    if (customDisplayActive && displayState.sticky) return;
    displaySet(l1, l2, l3, false, 1500);
}

void updateDisplayExpiry() {
    if (customDisplayActive && !displayState.sticky && displayState.expiresAtMs > 0 && millis() >= displayState.expiresAtMs) {
        customDisplayActive = false;
        displayMenu();
    }
}

// ============================================================
// USB HID init & state
// ============================================================
void initUSBHID() {
    if (!BOARD_SUPPORTS_WIRED_OUTPUT) {
        Serial.println("USB HID not supported on this board");
        return;
    }
    UsbHidBridge::init();
    Serial.println("USB HID initialized");
}

bool isUSBMounted() {
    return UsbHidBridge::isMounted();
}

// ============================================================
// BLE init and state
// ============================================================
void stopBLE() {
#if defined(CONFIG_IDF_TARGET_ESP32)
    if (switchProBt.isActive()) {
        switchProBt.end();
    }
#endif
    if (bleComboMouse) { delete bleComboMouse; bleComboMouse = nullptr; }
    if (bleComboKb)    { delete bleComboKb;    bleComboKb = nullptr; }
    if (bleCompositeHid) {
        bleCompositeHid->end();
    }
#if defined(__GNUC__)
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wdelete-non-virtual-dtor"
#endif
    if (bleXboxGamepad) { delete bleXboxGamepad; bleXboxGamepad = nullptr; }
    if (bleGenericGamepad) { delete bleGenericGamepad; bleGenericGamepad = nullptr; }
    if (bleCompositeHid) { delete bleCompositeHid; bleCompositeHid = nullptr; }
#if defined(__GNUC__)
#pragma GCC diagnostic pop
#endif
    if (NimBLEDevice::isInitialized()) {
        NimBLEDevice::deinit(true);
    }
}

void initBLE() {
    stopBLE();

    if (!modeUsesBleOutput(currentMode)) {
        Serial.println("BLE output disabled for current mode");
        return;
    }

#if defined(CONFIG_IDF_TARGET_ESP32)
    if (BOARD_SUPPORTS_BT_SWITCH_PRO_MODE && isSwitchMode(currentMode)) {
        if (switchProBt.begin()) {
            Serial.println("Bluetooth Switch Pro profile started (classic BT)");
        } else {
            Serial.println("Failed to start Bluetooth Switch Pro profile");
        }
        return;
    }
#endif

    String advName = getBleAdvertisementName();
    configureNimBLEForCurrentMode();
    configureNimBLEIdentityForCurrentMode();

    if (modeUsesBleCombo(currentMode)) {
        bleComboKb = new BleComboKeyboard(advName.c_str(), "ChromaCatch", 100);
        bleComboMouse = new BleComboMouse(bleComboKb);
        bleComboKb->begin();
        Serial.println("BLE combo (keyboard+mouse) started as: " + advName);
        return;
    }

    if (modeUsesBleGamepad(currentMode)) {
        bleCompositeHid = new BleCompositeHID(std::string(advName.c_str()), "ChromaCatch", 100);
        if (isXboxMode(currentMode)) {
            auto* config = new XboxOneSControllerDeviceConfiguration();
            BLEHostConfiguration hostConfig = config->getIdealHostConfiguration();
            hostConfig.setModelNumber("1708");
            hostConfig.setHardwareRevision("1.0");
            hostConfig.setFirmwareRevision("5.17");
            hostConfig.setSoftwareRevision("5.17");
            hostConfig.setQueuedSending(true);
            hostConfig.setQueueSendRate(240);
            bleXboxGamepad = new XboxGamepadDevice(config);
            bleCompositeHid->addDevice(bleXboxGamepad);
            bleCompositeHid->begin(hostConfig);
            Serial.println("BLE Xbox controller profile started as: " + advName);
        } else {
            bleGenericGamepad = new GamepadDevice();
            bleCompositeHid->addDevice(bleGenericGamepad);
            bleCompositeHid->begin();
            Serial.println("BLE generic gamepad started as: " + advName);
        }
        return;
    }
}

bool isBLEConnected() {
#if defined(CONFIG_IDF_TARGET_ESP32)
    if (BOARD_SUPPORTS_BT_SWITCH_PRO_MODE && isSwitchMode(currentMode)) {
        return switchProBt.isConnected();
    }
#endif
    if (modeUsesBleCombo(currentMode)) {
        return bleComboKb && bleComboKb->isConnected();
    }
    if (modeUsesBleGamepad(currentMode)) {
        return bleCompositeHid && bleCompositeHid->isConnected();
    }
    return false;
}

RuntimeDelivery chooseRuntimeDelivery() {
    bool usbAvailable = isUSBMounted();
    bool bleAvailable = isBLEConnected();

    switch (deliveryPolicy) {
        case DELIVERY_FORCE_USB:
            return usbAvailable ? RUNTIME_USB : RUNTIME_NONE;
        case DELIVERY_FORCE_BLE:
            return bleAvailable ? RUNTIME_BLE : RUNTIME_NONE;
        case DELIVERY_AUTO:
        default:
            if (usbAvailable) return RUNTIME_USB;
            if (bleAvailable) return RUNTIME_BLE;
            return RUNTIME_NONE;
    }
}

// ============================================================
// HID key helpers
// ============================================================
uint8_t parseKeyCode(const String& key) {
    if (key.length() == 1) return static_cast<uint8_t>(key[0]);

    if (strEqIgnoreCase(key, "enter") || strEqIgnoreCase(key, "return")) return KEY_RETURN;
    if (strEqIgnoreCase(key, "esc") || strEqIgnoreCase(key, "escape")) return KEY_ESC;
    if (strEqIgnoreCase(key, "tab")) return KEY_TAB;
    if (strEqIgnoreCase(key, "space")) return ' ';
    if (strEqIgnoreCase(key, "up")) return KEY_UP_ARROW;
    if (strEqIgnoreCase(key, "down")) return KEY_DOWN_ARROW;
    if (strEqIgnoreCase(key, "left")) return KEY_LEFT_ARROW;
    if (strEqIgnoreCase(key, "right")) return KEY_RIGHT_ARROW;
    if (strEqIgnoreCase(key, "backspace")) return KEY_BACKSPACE;
    if (strEqIgnoreCase(key, "delete")) return KEY_DELETE;
    if (strEqIgnoreCase(key, "home")) return KEY_HOME;
    if (strEqIgnoreCase(key, "end")) return KEY_END;
    if (strEqIgnoreCase(key, "page_up")) return KEY_PAGE_UP;
    if (strEqIgnoreCase(key, "page_down")) return KEY_PAGE_DOWN;

    return 0;
}

uint8_t parseMouseButton(JsonDocument& doc) {
    String name = doc["button"].as<String>();
    if (name.length() == 0 || strEqIgnoreCase(name, "left")) return UsbHidBridge::USB_MOUSE_BTN_LEFT;
    if (strEqIgnoreCase(name, "right")) return UsbHidBridge::USB_MOUSE_BTN_RIGHT;
    if (strEqIgnoreCase(name, "middle")) return UsbHidBridge::USB_MOUSE_BTN_MIDDLE;
    return UsbHidBridge::USB_MOUSE_BTN_LEFT;
}

int mapDPadToBleHat(const String& name) {
    if (strEqIgnoreCase(name, "up") || strEqIgnoreCase(name, "DUP")) return 0;
    if (strEqIgnoreCase(name, "right") || strEqIgnoreCase(name, "DRIGHT")) return 2;
    if (strEqIgnoreCase(name, "down") || strEqIgnoreCase(name, "DDOWN")) return 4;
    if (strEqIgnoreCase(name, "left") || strEqIgnoreCase(name, "DLEFT")) return 6;
    return -1;
}

uint8_t mapDPadToUsbHat(const String& name) {
    if (strEqIgnoreCase(name, "up") || strEqIgnoreCase(name, "DUP")) return UsbHidBridge::USB_HAT_UP;
    if (strEqIgnoreCase(name, "right") || strEqIgnoreCase(name, "DRIGHT")) return UsbHidBridge::USB_HAT_RIGHT;
    if (strEqIgnoreCase(name, "down") || strEqIgnoreCase(name, "DDOWN")) return UsbHidBridge::USB_HAT_DOWN;
    if (strEqIgnoreCase(name, "left") || strEqIgnoreCase(name, "DLEFT")) return UsbHidBridge::USB_HAT_LEFT;
    return UsbHidBridge::USB_HAT_CENTER;
}

uint8_t mapGenericGamepadButtonBLE(const String& name, bool switchLayout) {
    // Generic BLE gamepad uses button numbers 1..N
    if (strEqIgnoreCase(name, "A")) return switchLayout ? 2 : 1;
    if (strEqIgnoreCase(name, "B")) return switchLayout ? 1 : 2;
    if (strEqIgnoreCase(name, "X")) return switchLayout ? 4 : 3;
    if (strEqIgnoreCase(name, "Y")) return switchLayout ? 3 : 4;

    if (strEqIgnoreCase(name, "L") || strEqIgnoreCase(name, "LB")) return 5;
    if (strEqIgnoreCase(name, "R") || strEqIgnoreCase(name, "RB")) return 6;
    if (strEqIgnoreCase(name, "ZL") || strEqIgnoreCase(name, "LT")) return 7;
    if (strEqIgnoreCase(name, "ZR") || strEqIgnoreCase(name, "RT")) return 8;
    if (strEqIgnoreCase(name, "minus") || strEqIgnoreCase(name, "select")) return 9;
    if (strEqIgnoreCase(name, "plus") || strEqIgnoreCase(name, "start")) return 10;
    if (strEqIgnoreCase(name, "L3") || strEqIgnoreCase(name, "lstick")) return 11;
    if (strEqIgnoreCase(name, "R3") || strEqIgnoreCase(name, "rstick")) return 12;
    if (strEqIgnoreCase(name, "home")) return 13;
    if (strEqIgnoreCase(name, "capture")) return 14;

    return 0;
}

uint16_t mapXboxButtonBLE(const String& name) {
    if (strEqIgnoreCase(name, "A")) return XBOX_BUTTON_A;
    if (strEqIgnoreCase(name, "B")) return XBOX_BUTTON_B;
    if (strEqIgnoreCase(name, "X")) return XBOX_BUTTON_X;
    if (strEqIgnoreCase(name, "Y")) return XBOX_BUTTON_Y;
    if (strEqIgnoreCase(name, "L") || strEqIgnoreCase(name, "LB")) return XBOX_BUTTON_LB;
    if (strEqIgnoreCase(name, "R") || strEqIgnoreCase(name, "RB")) return XBOX_BUTTON_RB;
    if (strEqIgnoreCase(name, "minus") || strEqIgnoreCase(name, "select")) return XBOX_BUTTON_SELECT;
    if (strEqIgnoreCase(name, "plus") || strEqIgnoreCase(name, "start")) return XBOX_BUTTON_START;
    if (strEqIgnoreCase(name, "home")) return XBOX_BUTTON_HOME;
    if (strEqIgnoreCase(name, "L3") || strEqIgnoreCase(name, "lstick")) return XBOX_BUTTON_LS;
    if (strEqIgnoreCase(name, "R3") || strEqIgnoreCase(name, "rstick")) return XBOX_BUTTON_RS;
    return 0;
}

bool isXboxLeftTriggerName(const String& name) {
    return strEqIgnoreCase(name, "ZL") || strEqIgnoreCase(name, "LT");
}

bool isXboxRightTriggerName(const String& name) {
    return strEqIgnoreCase(name, "ZR") || strEqIgnoreCase(name, "RT");
}

uint8_t mapDPadToXbox(const String& name) {
    if (strEqIgnoreCase(name, "up") || strEqIgnoreCase(name, "DUP")) return XBOX_BUTTON_DPAD_NORTH;
    if (strEqIgnoreCase(name, "right") || strEqIgnoreCase(name, "DRIGHT")) return XBOX_BUTTON_DPAD_EAST;
    if (strEqIgnoreCase(name, "down") || strEqIgnoreCase(name, "DDOWN")) return XBOX_BUTTON_DPAD_SOUTH;
    if (strEqIgnoreCase(name, "left") || strEqIgnoreCase(name, "DLEFT")) return XBOX_BUTTON_DPAD_WEST;
    return XBOX_BUTTON_DPAD_NONE;
}

uint8_t mapGamepadButtonUSB(const String& name, bool switchLayout) {
    // USB gamepad buttons are 0-based symbolic constants.
    if (strEqIgnoreCase(name, "A")) return switchLayout ? UsbHidBridge::USB_BUTTON_EAST : UsbHidBridge::USB_BUTTON_SOUTH;
    if (strEqIgnoreCase(name, "B")) return switchLayout ? UsbHidBridge::USB_BUTTON_SOUTH : UsbHidBridge::USB_BUTTON_EAST;
    if (strEqIgnoreCase(name, "X")) return switchLayout ? UsbHidBridge::USB_BUTTON_NORTH : UsbHidBridge::USB_BUTTON_WEST;
    if (strEqIgnoreCase(name, "Y")) return switchLayout ? UsbHidBridge::USB_BUTTON_WEST : UsbHidBridge::USB_BUTTON_NORTH;

    if (strEqIgnoreCase(name, "L") || strEqIgnoreCase(name, "LB")) return UsbHidBridge::USB_BUTTON_TL;
    if (strEqIgnoreCase(name, "R") || strEqIgnoreCase(name, "RB")) return UsbHidBridge::USB_BUTTON_TR;
    if (strEqIgnoreCase(name, "ZL") || strEqIgnoreCase(name, "LT")) return UsbHidBridge::USB_BUTTON_TL2;
    if (strEqIgnoreCase(name, "ZR") || strEqIgnoreCase(name, "RT")) return UsbHidBridge::USB_BUTTON_TR2;
    if (strEqIgnoreCase(name, "minus") || strEqIgnoreCase(name, "select")) return UsbHidBridge::USB_BUTTON_SELECT;
    if (strEqIgnoreCase(name, "plus") || strEqIgnoreCase(name, "start")) return UsbHidBridge::USB_BUTTON_START;
    if (strEqIgnoreCase(name, "home")) return UsbHidBridge::USB_BUTTON_MODE;
    if (strEqIgnoreCase(name, "L3") || strEqIgnoreCase(name, "lstick")) return UsbHidBridge::USB_BUTTON_THUMBL;
    if (strEqIgnoreCase(name, "R3") || strEqIgnoreCase(name, "rstick")) return UsbHidBridge::USB_BUTTON_THUMBR;

    // No dedicated capture in generic USB gamepad descriptor.
    if (strEqIgnoreCase(name, "capture")) return UsbHidBridge::USB_BUTTON_MODE;

    return 0xFF;
}

// ============================================================
// Command execution by category & transport
// ============================================================
void executeMouseCommand(RuntimeDelivery transport, const String& action, JsonDocument& doc, JsonDocument& response) {
    if (!modeAllowsMouse(currentMode)) {
        response["status"] = "error";
        response["error"] = "mouse actions not allowed in current mode";
        return;
    }

    if (transport == RUNTIME_BLE) {
        if (!bleComboKb || !bleComboKb->isConnected() || !bleComboMouse) {
            response["status"] = "error";
            response["error"] = "BLE combo not connected";
            return;
        }

        if (action == "move") {
            int dx = doc["dx"] | 0;
            int dy = doc["dy"] | 0;
            bleComboMouse->move(dx, dy);
            response["status"] = "ok";
            return;
        }

        if (action == "click") {
            if (doc["x"].is<int>() || doc["y"].is<int>()) {
                int x = doc["x"] | 0;
                int y = doc["y"] | 0;
                bleComboMouse->move(x, y);
                delay(5);
            }
            bleComboMouse->click(parseMouseButton(doc));
            response["status"] = "ok";
            return;
        }

        if (action == "swipe") {
            int x1 = doc["x1"] | 0;
            int y1 = doc["y1"] | 0;
            int x2 = doc["x2"] | 0;
            int y2 = doc["y2"] | 0;
            int duration = doc["duration_ms"] | 300;
            int steps = max(1, duration / 10);
            int stepX = (x2 - x1) / steps;
            int stepY = (y2 - y1) / steps;

            bleComboMouse->move(x1, y1);
            delay(5);
            bleComboMouse->press(parseMouseButton(doc));
            for (int i = 0; i < steps; i++) {
                bleComboMouse->move(stepX, stepY);
                delay(10);
            }
            bleComboMouse->release(parseMouseButton(doc));
            response["status"] = "ok";
            response["steps"] = steps;
            return;
        }

        if (action == "press") {
            bleComboMouse->press(parseMouseButton(doc));
            response["status"] = "ok";
            return;
        }

        if (action == "release") {
            bleComboMouse->release(parseMouseButton(doc));
            response["status"] = "ok";
            return;
        }
    }

    if (transport == RUNTIME_USB) {
        if (action == "move") {
            int dx = doc["dx"] | 0;
            int dy = doc["dy"] | 0;
            UsbHidBridge::mouseMove(dx, dy);
            response["status"] = "ok";
            return;
        }

        if (action == "click") {
            if (doc["x"].is<int>() || doc["y"].is<int>()) {
                int x = doc["x"] | 0;
                int y = doc["y"] | 0;
                UsbHidBridge::mouseMove(x, y);
                delay(5);
            }
            UsbHidBridge::mouseClick(parseMouseButton(doc));
            response["status"] = "ok";
            return;
        }

        if (action == "swipe") {
            int x1 = doc["x1"] | 0;
            int y1 = doc["y1"] | 0;
            int x2 = doc["x2"] | 0;
            int y2 = doc["y2"] | 0;
            int duration = doc["duration_ms"] | 300;
            int steps = max(1, duration / 10);
            int stepX = (x2 - x1) / steps;
            int stepY = (y2 - y1) / steps;

            UsbHidBridge::mouseMove(x1, y1);
            delay(5);
            UsbHidBridge::mousePress(parseMouseButton(doc));
            for (int i = 0; i < steps; i++) {
                UsbHidBridge::mouseMove(stepX, stepY);
                delay(10);
            }
            UsbHidBridge::mouseRelease(parseMouseButton(doc));
            response["status"] = "ok";
            response["steps"] = steps;
            return;
        }

        if (action == "press") {
            UsbHidBridge::mousePress(parseMouseButton(doc));
            response["status"] = "ok";
            return;
        }

        if (action == "release") {
            UsbHidBridge::mouseRelease(parseMouseButton(doc));
            response["status"] = "ok";
            return;
        }
    }

    response["status"] = "error";
    response["error"] = "unknown mouse action";
}

void executeKeyboardCommand(RuntimeDelivery transport, const String& action, JsonDocument& doc, JsonDocument& response) {
    if (!modeAllowsKeyboard(currentMode)) {
        response["status"] = "error";
        response["error"] = "keyboard actions not allowed in current mode";
        return;
    }

    if (action == "text") {
        String text = doc["text"].as<String>();
        if (transport == RUNTIME_BLE) {
            if (!bleComboKb || !bleComboKb->isConnected()) {
                response["status"] = "error";
                response["error"] = "BLE combo not connected";
                return;
            }
            bleComboKb->print(text);
        } else {
            UsbHidBridge::keyboardPrint(text);
        }
        response["status"] = "ok";
        response["count"] = text.length();
        return;
    }

    String keyName = doc["key"].as<String>();
    uint8_t keyCode = parseKeyCode(keyName);
    if (keyCode == 0) {
        response["status"] = "error";
        response["error"] = "invalid key";
        return;
    }

    if (transport == RUNTIME_BLE) {
        if (!bleComboKb || !bleComboKb->isConnected()) {
            response["status"] = "error";
            response["error"] = "BLE combo not connected";
            return;
        }

        if (action == "key_press") {
            bleComboKb->press(keyCode);
            response["status"] = "ok";
            return;
        }
        if (action == "key_release") {
            bleComboKb->release(keyCode);
            response["status"] = "ok";
            return;
        }
        if (action == "key_tap") {
            bleComboKb->press(keyCode);
            delay(10);
            bleComboKb->release(keyCode);
            response["status"] = "ok";
            return;
        }
    } else {
        if (action == "key_press") {
            UsbHidBridge::keyboardPress(keyCode);
            response["status"] = "ok";
            return;
        }
        if (action == "key_release") {
            UsbHidBridge::keyboardRelease(keyCode);
            response["status"] = "ok";
            return;
        }
        if (action == "key_tap") {
            UsbHidBridge::keyboardPress(keyCode);
            delay(10);
            UsbHidBridge::keyboardRelease(keyCode);
            response["status"] = "ok";
            return;
        }
    }

    response["status"] = "error";
    response["error"] = "unknown keyboard action";
}

void executeGamepadCommand(RuntimeDelivery transport, const String& action, JsonDocument& doc, JsonDocument& response) {
    if (!modeAllowsGamepad(currentMode)) {
        response["status"] = "error";
        response["error"] = "gamepad actions not allowed in current mode";
        return;
    }

    bool switchLayout = isSwitchMode(currentMode);

    if (transport == RUNTIME_BLE) {
#if defined(CONFIG_IDF_TARGET_ESP32)
        if (BOARD_SUPPORTS_BT_SWITCH_PRO_MODE && isSwitchMode(currentMode)) {
            if (!switchProBt.isConnected()) {
                response["status"] = "error";
                response["error"] = "switch_pro_bt_not_connected";
                return;
            }

            if (action == "button_press" || action == "button_release") {
                String button = doc["button"].as<String>();
                bool pressed = action == "button_press";
                bool ok = switchProBt.setButton(button, pressed);
                if (!ok) {
                    response["status"] = "error";
                    response["error"] = "unknown button";
                    return;
                }
                response["status"] = "ok";
                return;
            }

            if (action == "stick") {
                String stick = doc["stick_id"].as<String>();
                int x = doc["x"] | 0;
                int y = doc["y"] | 0;
                bool ok = switchProBt.setStick(stick, x, y);
                if (!ok) {
                    response["status"] = "error";
                    response["error"] = "unknown stick";
                    return;
                }
                response["status"] = "ok";
                return;
            }

            if (action == "hat") {
                String dir = doc["direction"].as<String>();
                switchProBt.setHat(dir);
                response["status"] = "ok";
                return;
            }
        }
#endif

        if (!bleCompositeHid || !bleCompositeHid->isConnected()) {
            response["status"] = "error";
            response["error"] = "BLE gamepad not connected";
            return;
        }

        bool xboxProfile = isXboxMode(currentMode) && bleXboxGamepad != nullptr;
        bool genericProfile = bleGenericGamepad != nullptr;

        if (action == "button_press") {
            String button = doc["button"].as<String>();
            if (xboxProfile) {
                uint8_t dpad = mapDPadToXbox(button);
                if (dpad != XBOX_BUTTON_DPAD_NONE) {
                    bleXboxGamepad->pressDPadDirection(dpad);
                } else if (isXboxLeftTriggerName(button)) {
                    bleXboxGamepad->setLeftTrigger(XBOX_TRIGGER_MAX);
                } else if (isXboxRightTriggerName(button)) {
                    bleXboxGamepad->setRightTrigger(XBOX_TRIGGER_MAX);
                } else if (strEqIgnoreCase(button, "capture")) {
                    bleXboxGamepad->pressShare();
                } else {
                    uint16_t btn = mapXboxButtonBLE(button);
                    if (btn == 0) {
                        response["status"] = "error";
                        response["error"] = "unknown button";
                        return;
                    }
                    bleXboxGamepad->press(btn);
                }
                bleXboxGamepad->sendGamepadReport();
                response["status"] = "ok";
                return;
            }

            if (!genericProfile) {
                response["status"] = "error";
                response["error"] = "generic gamepad profile unavailable";
                return;
            }

            int hat = mapDPadToBleHat(button);
            if (hat >= 0) {
                bleGenericGamepad->setHat1(hat);
            } else {
                int btn = mapGenericGamepadButtonBLE(button, switchLayout);
                if (btn <= 0) {
                    response["status"] = "error";
                    response["error"] = "unknown button";
                    return;
                }
                bleGenericGamepad->press(btn);
            }
            bleGenericGamepad->sendGamepadReport();
            response["status"] = "ok";
            return;
        }

        if (action == "button_release") {
            String button = doc["button"].as<String>();
            if (xboxProfile) {
                uint8_t dpad = mapDPadToXbox(button);
                if (dpad != XBOX_BUTTON_DPAD_NONE) {
                    bleXboxGamepad->releaseDPad();
                } else if (isXboxLeftTriggerName(button)) {
                    bleXboxGamepad->setLeftTrigger(XBOX_TRIGGER_MIN);
                } else if (isXboxRightTriggerName(button)) {
                    bleXboxGamepad->setRightTrigger(XBOX_TRIGGER_MIN);
                } else if (strEqIgnoreCase(button, "capture")) {
                    bleXboxGamepad->releaseShare();
                } else {
                    uint16_t btn = mapXboxButtonBLE(button);
                    if (btn == 0) {
                        response["status"] = "error";
                        response["error"] = "unknown button";
                        return;
                    }
                    bleXboxGamepad->release(btn);
                }
                bleXboxGamepad->sendGamepadReport();
                response["status"] = "ok";
                return;
            }

            if (!genericProfile) {
                response["status"] = "error";
                response["error"] = "generic gamepad profile unavailable";
                return;
            }

            int hat = mapDPadToBleHat(button);
            if (hat >= 0) {
                bleGenericGamepad->setHat1(-1);
            } else {
                int btn = mapGenericGamepadButtonBLE(button, switchLayout);
                if (btn <= 0) {
                    response["status"] = "error";
                    response["error"] = "unknown button";
                    return;
                }
                bleGenericGamepad->release(btn);
            }
            bleGenericGamepad->sendGamepadReport();
            response["status"] = "ok";
            return;
        }

        if (action == "stick") {
            String stick = doc["stick_id"].as<String>();
            int x = doc["x"] | 0;
            int y = doc["y"] | 0;
            if (xboxProfile) {
                if (strEqIgnoreCase(stick, "left")) {
                    bleXboxGamepad->setLeftThumb(x, y);
                } else {
                    bleXboxGamepad->setRightThumb(x, y);
                }
                bleXboxGamepad->sendGamepadReport();
                response["status"] = "ok";
                return;
            }

            if (!genericProfile) {
                response["status"] = "error";
                response["error"] = "generic gamepad profile unavailable";
                return;
            }

            int mappedX = map(x, -32768, 32767, 0, 32767);
            int mappedY = map(y, -32768, 32767, 0, 32767);
            if (strEqIgnoreCase(stick, "left")) {
                bleGenericGamepad->setLeftThumb(mappedX, mappedY);
            } else {
                bleGenericGamepad->setRightThumb(mappedX, mappedY);
            }
            bleGenericGamepad->sendGamepadReport();
            response["status"] = "ok";
            return;
        }

        if (action == "hat") {
            String dir = doc["direction"].as<String>();
            if (xboxProfile) {
                uint8_t dpad = mapDPadToXbox(dir);
                if (dpad == XBOX_BUTTON_DPAD_NONE) bleXboxGamepad->releaseDPad();
                else bleXboxGamepad->pressDPadDirection(dpad);
                bleXboxGamepad->sendGamepadReport();
                response["status"] = "ok";
                return;
            }
            if (!genericProfile) {
                response["status"] = "error";
                response["error"] = "generic gamepad profile unavailable";
                return;
            }
            int hat = mapDPadToBleHat(dir);
            bleGenericGamepad->setHat1(hat >= 0 ? hat : -1);
            bleGenericGamepad->sendGamepadReport();
            response["status"] = "ok";
            return;
        }
    }

    if (transport == RUNTIME_USB) {
        if (action == "button_press") {
            String button = doc["button"].as<String>();
            uint8_t hat = mapDPadToUsbHat(button);
            if (hat != UsbHidBridge::USB_HAT_CENTER || strEqIgnoreCase(button, "up") || strEqIgnoreCase(button, "down") || strEqIgnoreCase(button, "left") || strEqIgnoreCase(button, "right") || strEqIgnoreCase(button, "DUP") || strEqIgnoreCase(button, "DDOWN") || strEqIgnoreCase(button, "DLEFT") || strEqIgnoreCase(button, "DRIGHT")) {
                UsbHidBridge::gamepadHat(hat);
                response["status"] = "ok";
                return;
            }

            uint8_t btn = mapGamepadButtonUSB(button, switchLayout);
            if (btn == 0xFF) {
                response["status"] = "error";
                response["error"] = "unknown button";
                return;
            }
            UsbHidBridge::gamepadPress(btn);
            response["status"] = "ok";
            return;
        }

        if (action == "button_release") {
            String button = doc["button"].as<String>();
            uint8_t hat = mapDPadToUsbHat(button);
            if (hat != UsbHidBridge::USB_HAT_CENTER || strEqIgnoreCase(button, "up") || strEqIgnoreCase(button, "down") || strEqIgnoreCase(button, "left") || strEqIgnoreCase(button, "right") || strEqIgnoreCase(button, "DUP") || strEqIgnoreCase(button, "DDOWN") || strEqIgnoreCase(button, "DLEFT") || strEqIgnoreCase(button, "DRIGHT")) {
                UsbHidBridge::gamepadHat(UsbHidBridge::USB_HAT_CENTER);
                response["status"] = "ok";
                return;
            }

            uint8_t btn = mapGamepadButtonUSB(button, switchLayout);
            if (btn == 0xFF) {
                response["status"] = "error";
                response["error"] = "unknown button";
                return;
            }
            UsbHidBridge::gamepadRelease(btn);
            response["status"] = "ok";
            return;
        }

        if (action == "stick") {
            String stick = doc["stick_id"].as<String>();
            int x = doc["x"] | 0;
            int y = doc["y"] | 0;
            int8_t mappedX = clampInt8(map(x, -32768, 32767, -127, 127));
            int8_t mappedY = clampInt8(map(y, -32768, 32767, -127, 127));
            // Pokemon Automation wired-controller path uses inverted Y for Switch
            // wired reports (see NintendoSwitch_SerialPABotBase_WiredController.cpp).
            if (switchLayout) {
                mappedY = -mappedY;
            }
            if (strEqIgnoreCase(stick, "left")) {
                UsbHidBridge::gamepadLeftStick(mappedX, mappedY);
            } else {
                UsbHidBridge::gamepadRightStick(mappedX, mappedY);
            }
            response["status"] = "ok";
            return;
        }

        if (action == "hat") {
            String dir = doc["direction"].as<String>();
            uint8_t hat = mapDPadToUsbHat(dir);
            UsbHidBridge::gamepadHat(hat);
            response["status"] = "ok";
            return;
        }
    }

    response["status"] = "error";
    response["error"] = "unknown gamepad action";
}

void executeCommand(JsonDocument& doc, JsonDocument& response, CommandSource source) {
    String action = doc["action"].as<String>();
    response["action"] = action;
    response["mode"] = emulationModeToString(currentEmulationMode);
    response["legacy_mode"] = modeToString(currentMode);
    response["input_policy"] = inputPolicyToString(inputPolicy);
    if (source == SOURCE_WIRED) response["source"] = "wired";
    else if (source == SOURCE_WEBSOCKET) response["source"] = "websocket";
    else response["source"] = "wifi";

    // Control-plane actions (do not require active HID output transport)
    if (action == "set_mode") {
        EmulationMode previous = currentEmulationMode;
        bool rebootRequired = false;
        if (doc["mode"].is<const char*>()) {
            EmulationMode next = parseEmulationModeString(doc["mode"].as<String>());
            if (!emulationModeSupported(next)) {
                response["status"] = "error";
                response["error"] = "mode_not_supported_on_this_board";
                return;
            }
            if (emulationModeBlockedByBuild(next)) {
                response["status"] = "error";
                response["error"] = "mode_not_supported_by_this_firmware_build";
                response["hint"] = "build env esp32s3_switch (ARDUINO_USB_CDC_ON_BOOT=0) for wired switch mode";
                return;
            }
            applyEmulationMode(next);
            rebootRequired = modeTransitionRequiresUsbReboot(previous, currentEmulationMode);
            if (!rebootRequired) {
                initBLE();
                displayMenu();
            } else {
                scheduleDeviceReboot(300);
            }
        }
        response["status"] = "ok";
        response["mode"] = emulationModeToString(currentEmulationMode);
        response["delivery_policy"] = deliveryPolicyToString(deliveryPolicy);
        response["rebooting"] = rebootRequired;
        if (rebootRequired) {
            response["reason"] = "usb_descriptor_change_requires_reboot";
        }
        return;
    }
    if (action == "restart_ble") {
        initBLE();
        displayMenu();
        response["status"] = "ok";
        response["mode"] = emulationModeToString(currentEmulationMode);
        response["ble_advertisement_name"] = getBleAdvertisementName();
        return;
    }
    if (action == "clear_ble_bonds") {
        if (!modeUsesBleOutput(currentMode)) {
            response["status"] = "error";
            response["error"] = "ble_disabled_for_current_mode";
            return;
        }
#if defined(CONFIG_IDF_TARGET_ESP32)
        if (BOARD_SUPPORTS_BT_SWITCH_PRO_MODE && isSwitchMode(currentMode)) {
            response["status"] = "error";
            response["error"] = "not_supported_for_bt_classic_switch_mode";
            return;
        }
#endif
        bool cleared = NimBLEDevice::deleteAllBonds();
        initBLE();
        displayMenu();
        response["status"] = cleared ? "ok" : "error";
        if (!cleared) response["error"] = "failed_to_clear_ble_bonds";
        response["ble_advertisement_name"] = getBleAdvertisementName();
        return;
    }
    if (action == "set_delivery_policy") {
        InputPolicy nextPolicy = inputPolicy;
        if (doc["delivery_policy"].is<const char*>()) {
            nextPolicy = parseInputPolicy(doc["delivery_policy"].as<String>());
        } else if (doc["input_policy"].is<const char*>()) {
            nextPolicy = parseInputPolicy(doc["input_policy"].as<String>());
        } else if (doc["policy"].is<const char*>()) {
            nextPolicy = parseInputPolicy(doc["policy"].as<String>());
        } else if (doc["value"].is<const char*>()) {
            nextPolicy = parseInputPolicy(doc["value"].as<String>());
        }
        if (nextPolicy == INPUT_POLICY_WIRED_ONLY && !BOARD_SUPPORTS_WIRED_INPUT) {
            response["status"] = "error";
            response["error"] = "wired_input_policy_not_supported_on_this_board";
            return;
        }
        inputPolicy = nextPolicy;
        persistRuntimeConfig();
        displayMenu();
        response["status"] = "ok";
        response["input_policy"] = inputPolicyToString(inputPolicy);
        response["mode"] = emulationModeToString(currentEmulationMode);
        return;
    }
    if (action == "set_led_pins") {
        if (doc["rgb_pin_primary"].is<int>()) {
            statusLedRgbPinPrimary = doc["rgb_pin_primary"].as<int>();
        }
        if (doc["rgb_pin_secondary"].is<int>()) {
            statusLedRgbPinSecondary = doc["rgb_pin_secondary"].as<int>();
        }
        if (doc["mono_enabled"].is<bool>()) {
            statusLedMonoEnabled = doc["mono_enabled"].as<bool>();
            if (statusLedMonoEnabled) {
                pinMode(MONO_STATUS_LED_PIN, OUTPUT);
            }
        }
        updateStatusLed();
        response["status"] = "ok";
        response["status_led_rgb_pin_primary"] = statusLedRgbPinPrimary;
        response["status_led_rgb_pin_secondary"] = statusLedRgbPinSecondary;
        response["status_led_mono_enabled"] = statusLedMonoEnabled;
        return;
    }
    if (action == "set_input_policy") {
        InputPolicy nextPolicy = inputPolicy;
        if (doc["input_policy"].is<const char*>()) {
            nextPolicy = parseInputPolicy(doc["input_policy"].as<String>());
        } else if (doc["policy"].is<const char*>()) {
            nextPolicy = parseInputPolicy(doc["policy"].as<String>());
        } else if (doc["value"].is<const char*>()) {
            nextPolicy = parseInputPolicy(doc["value"].as<String>());
        }
        if (nextPolicy == INPUT_POLICY_WIRED_ONLY && !BOARD_SUPPORTS_WIRED_INPUT) {
            response["status"] = "error";
            response["error"] = "wired_input_policy_not_supported_on_this_board";
            return;
        }
        inputPolicy = nextPolicy;
        persistRuntimeConfig();
        displayMenu();
        response["status"] = "ok";
        response["input_policy"] = inputPolicyToString(inputPolicy);
        response["mode"] = emulationModeToString(currentEmulationMode);
        return;
    }

    RuntimeDelivery delivery = chooseRuntimeDelivery();
    response["delivery"] = runtimeDeliveryToString(delivery);

    if (delivery == RUNTIME_NONE) {
        response["status"] = "error";
        response["error"] = "no available output transport (USB not mounted and BLE not connected)";
        return;
    }

    if (action == "move" || action == "click" || action == "swipe" || action == "press" || action == "release") {
        executeMouseCommand(delivery, action, doc, response);
    }
    else if (action == "key_press" || action == "key_release" || action == "key_tap" || action == "text") {
        executeKeyboardCommand(delivery, action, doc, response);
    }
    else if (action == "button_press" || action == "button_release" || action == "stick" || action == "hat") {
        executeGamepadCommand(delivery, action, doc, response);
    }
    else {
        response["status"] = "error";
        response["error"] = "unknown action";
    }
}

// ============================================================
// HTTP handlers
// ============================================================
void sendJson(int code, JsonDocument& doc) {
    String out;
    serializeJson(doc, out);
    server.send(code, "application/json", out);
}

void handlePing() {
    server.send(200, "application/json", "{\"status\":\"ok\"}");
}

void handleStatus() {
    JsonDocument doc;
    LedColor ledColor = modeToLedColor(currentEmulationMode);
    doc["device_name"] = DEVICE_NAME;
    doc["ble_advertisement_name"] = getBleAdvertisementName();
    doc["ble_profile"] = getBleProfileLabel();
    doc["ble_mode_scoped_mac"] = getModeSpecificBleMacString();
    doc["build_usb_cdc_on_boot"] = BUILD_USB_CDC_ON_BOOT;
    doc["wired_switch_build_compatible"] = !(BOARD_IS_ESP32S3 && BUILD_USB_CDC_ON_BOOT);
    doc["ip"] = WiFi.localIP().toString();
    doc["mode"] = emulationModeToString(currentEmulationMode);
    doc["legacy_mode"] = modeToString(currentMode);
    doc["delivery_policy"] = deliveryPolicyToString(deliveryPolicy);
    doc["input_policy"] = inputPolicyToString(inputPolicy);
    doc["active_delivery"] = runtimeDeliveryToString(chooseRuntimeDelivery());
    doc["usb_mounted"] = isUSBMounted();
    doc["ble_connected"] = isBLEConnected();
    doc["board"] = BOARD_IS_ESP32S3 ? "esp32s3" : (BOARD_IS_ESP32 ? "esp32" : "unknown");
    doc["supports_wired_output"] = BOARD_SUPPORTS_WIRED_OUTPUT;
    doc["supports_wired_input"] = BOARD_SUPPORTS_WIRED_INPUT;
    doc["supports_bt_switch_pro_mode"] = BOARD_SUPPORTS_BT_SWITCH_PRO_MODE;
    doc["build_usb_cdc_on_boot"] = BUILD_USB_CDC_ON_BOOT;
    doc["wired_switch_build_compatible"] = !(BOARD_IS_ESP32S3 && BUILD_USB_CDC_ON_BOOT);
    doc["input_priority"] = BOARD_SUPPORTS_WIRED_INPUT ? "wired>websocket>http" : "websocket>http";
    doc["wired_priority_active"] = wiredPriorityActive();
    doc["ws_priority_active"] = wsPriorityActive();
    doc["wired_priority_window_ms"] = WIRED_PRIORITY_WINDOW_MS;
    doc["ws_priority_window_ms"] = WS_PRIORITY_WINDOW_MS;
    doc["ws_port"] = WS_PORT;
    doc["ws_connected_clients"] = wsConnectedClients;
    doc["usb_gamepad_profile"] = (UsbHidBridge::getGamepadProfile() == UsbHidBridge::USB_GAMEPAD_PROFILE_SWITCH_PRO) ? "switch_pro" : "generic";
    doc["status_led_rgb"] = String(ledColor.r) + "," + String(ledColor.g) + "," + String(ledColor.b);
    doc["status_led_behavior"] = (chooseRuntimeDelivery() == RUNTIME_NONE) ? "blinking" : "solid";
    doc["status_led_rgb_pin_primary"] = statusLedRgbPinPrimary;
    doc["status_led_rgb_pin_secondary"] = statusLedRgbPinSecondary;
    doc["status_led_mono_enabled"] = statusLedMonoEnabled;
#if defined(CONFIG_IDF_TARGET_ESP32)
    if (BOARD_SUPPORTS_BT_SWITCH_PRO_MODE && isSwitchMode(currentMode)) {
        doc["bt_classic_discoverable"] = switchProBt.isDiscoverable();
    }
#endif
    sendJson(200, doc);
}

void handleGetMode() {
    JsonDocument doc;
    doc["mode"] = emulationModeToString(currentEmulationMode);
    doc["legacy_mode"] = modeToString(currentMode);
    doc["delivery_policy"] = deliveryPolicyToString(deliveryPolicy);
    doc["input_policy"] = inputPolicyToString(inputPolicy);
    doc["ble_advertisement_name"] = getBleAdvertisementName();
    doc["ble_profile"] = getBleProfileLabel();
    doc["ble_mode_scoped_mac"] = getModeSpecificBleMacString();
    // Backward-compatible fields
    doc["output_mode"] = (modeAllowsGamepad(currentMode) ? "gamepad" : "mouse_keyboard");
    doc["output_delivery"] = deliveryPolicyToString(deliveryPolicy);
    doc["input_mode"] = BOARD_SUPPORTS_WIRED_INPUT ? "wired_websocket_http_priority" : "websocket_http_priority";
    JsonArray supported = doc["supported_modes"].to<JsonArray>();
    for (int i = 0; i < static_cast<int>(EMU_MODE_COUNT); i++) {
        EmulationMode candidate = static_cast<EmulationMode>(i);
        if (emulationModeSupported(candidate) && !emulationModeBlockedByBuild(candidate)) {
            supported.add(emulationModeToString(candidate));
        }
    }
    sendJson(200, doc);
}

void applyModeChange(bool modeChanged) {
    if (modeChanged) {
        initBLE();
        displayMenu();
    }
}

void handleSetMode() {
    if (!server.hasArg("plain")) {
        server.send(400, "application/json", "{\"error\":\"no body\"}");
        return;
    }

    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, server.arg("plain"));
    if (err) {
        server.send(400, "application/json", "{\"error\":\"invalid json\"}");
        return;
    }

    EmulationMode nextEmu = currentEmulationMode;
    bool explicitModeRequest = false;

    if (doc["mode"].is<const char*>()) {
        explicitModeRequest = true;
        nextEmu = parseEmulationModeString(doc["mode"].as<String>());
    }
    // backward compatibility aliases
    if (doc["hid_mode"].is<const char*>()) {
        explicitModeRequest = true;
        nextEmu = parseEmulationModeString(doc["hid_mode"].as<String>());
    }
    if (doc["output_mode"].is<const char*>()) {
        explicitModeRequest = true;
        nextEmu = parseEmulationModeString(doc["output_mode"].as<String>());
    }

    // delivery/output transport is deterministic per emulation mode.
    // If policy fields are present, interpret them as INPUT policy only.
    if (doc["delivery_policy"].is<const char*>()) {
        InputPolicy nextInputPolicy = parseInputPolicy(doc["delivery_policy"].as<String>());
        if (!(nextInputPolicy == INPUT_POLICY_WIRED_ONLY && !BOARD_SUPPORTS_WIRED_INPUT)) {
            inputPolicy = nextInputPolicy;
            persistRuntimeConfig();
        }
    }
    if (doc["input_policy"].is<const char*>()) {
        InputPolicy nextInputPolicy = parseInputPolicy(doc["input_policy"].as<String>());
        if (!(nextInputPolicy == INPUT_POLICY_WIRED_ONLY && !BOARD_SUPPORTS_WIRED_INPUT)) {
            inputPolicy = nextInputPolicy;
            persistRuntimeConfig();
        }
    }

    if (!emulationModeSupported(nextEmu)) {
        JsonDocument error;
        error["status"] = "error";
        error["error"] = "mode_not_supported_on_this_board";
        error["requested_mode"] = emulationModeToString(nextEmu);
        sendJson(400, error);
        return;
    }
    if (emulationModeBlockedByBuild(nextEmu)) {
        JsonDocument error;
        error["status"] = "error";
        error["error"] = "mode_not_supported_by_this_firmware_build";
        error["requested_mode"] = emulationModeToString(nextEmu);
        error["hint"] = "build env esp32s3_switch (ARDUINO_USB_CDC_ON_BOOT=0) for wired switch mode";
        sendJson(400, error);
        return;
    }

    EmulationMode previousMode = currentEmulationMode;
    bool forceReinit = doc["force_reinit"].is<bool>() ? doc["force_reinit"].as<bool>() : false;
    bool changed = (nextEmu != currentEmulationMode) || explicitModeRequest || forceReinit;
    applyEmulationMode(nextEmu);
    bool rebootRequired = modeTransitionRequiresUsbReboot(previousMode, currentEmulationMode);
    if (changed && !rebootRequired) {
        applyModeChange(true);
    } else if (rebootRequired) {
        scheduleDeviceReboot(300);
    }

    JsonDocument out;
    out["mode"] = emulationModeToString(currentEmulationMode);
    out["legacy_mode"] = modeToString(currentMode);
    out["delivery_policy"] = deliveryPolicyToString(deliveryPolicy);
    out["input_policy"] = inputPolicyToString(inputPolicy);
    out["ble_advertisement_name"] = getBleAdvertisementName();
    out["ble_profile"] = getBleProfileLabel();
    out["ble_mode_scoped_mac"] = getModeSpecificBleMacString();
    out["rebooting"] = rebootRequired;
    if (rebootRequired) {
        out["reason"] = "usb_descriptor_change_requires_reboot";
    }
    JsonArray supported = out["supported_modes"].to<JsonArray>();
    for (int i = 0; i < static_cast<int>(EMU_MODE_COUNT); i++) {
        EmulationMode candidate = static_cast<EmulationMode>(i);
        if (emulationModeSupported(candidate) && !emulationModeBlockedByBuild(candidate)) {
            supported.add(emulationModeToString(candidate));
        }
    }
    sendJson(200, out);
}

void handleDisplayGet() {
    JsonDocument doc;
    doc["line1"] = displayState.line1;
    doc["line2"] = displayState.line2;
    doc["line3"] = displayState.line3;
    doc["sticky"] = displayState.sticky;
    doc["custom_active"] = customDisplayActive;
    doc["default_screen"] = "dashboard";
    sendJson(200, doc);
}

void handleDisplaySet() {
    if (!server.hasArg("plain")) {
        server.send(400, "application/json", "{\"error\":\"no body\"}");
        return;
    }

    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, server.arg("plain"));
    if (err) {
        server.send(400, "application/json", "{\"error\":\"invalid json\"}");
        return;
    }

    String l1 = doc["line1"].as<String>();
    String l2 = doc["line2"].as<String>();
    String l3 = doc["line3"].as<String>();
    bool sticky = doc["sticky"].is<bool>() ? doc["sticky"].as<bool>() : true;
    unsigned long ttlMs = doc["ttl_ms"] | 1500;

    displaySet(l1, l2, l3, sticky, ttlMs);

    JsonDocument response;
    response["status"] = "ok";
    response["line1"] = l1;
    sendJson(200, response);
}

void handleDisplayClear() {
    displayClear();
    server.send(200, "application/json", "{\"status\":\"ok\"}");
}

void handleCommand() {
    if (!server.hasArg("plain")) {
        server.send(400, "application/json", "{\"error\":\"no body\"}");
        return;
    }

    if (!isSourceAllowed(SOURCE_WIFI)) {
        JsonDocument deferred;
        deferred["status"] = "deferred";
        deferred["reason"] = "input_policy_blocked_http";
        deferred["input_policy"] = inputPolicyToString(inputPolicy);
        sendJson(409, deferred);
        return;
    }

    if (inputPolicy == INPUT_POLICY_AUTO && wiredPriorityActive()) {
        JsonDocument deferred;
        deferred["status"] = "deferred";
        deferred["reason"] = "wired_priority_window_active";
        deferred["window_ms"] = WIRED_PRIORITY_WINDOW_MS;
        sendJson(409, deferred);
        return;
    }
    if (inputPolicy == INPUT_POLICY_AUTO && wsPriorityActive()) {
        JsonDocument deferred;
        deferred["status"] = "deferred";
        deferred["reason"] = "websocket_priority_window_active";
        deferred["window_ms"] = WS_PRIORITY_WINDOW_MS;
        sendJson(409, deferred);
        return;
    }

    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, server.arg("plain"));
    if (err) {
        server.send(400, "application/json", "{\"error\":\"invalid json\"}");
        return;
    }

    JsonDocument response;
    executeCommand(doc, response, SOURCE_WIFI);

    int code = 400;
    String status = response["status"].as<String>();
    if (status == "ok") code = 200;
    else if (status == "deferred") code = 409;

    sendJson(code, response);
}

// ============================================================
// WebSocket command input (priority: wired > websocket > http)
// ============================================================
void sendWSJson(uint8_t clientId, JsonDocument& doc) {
    String out;
    serializeJson(doc, out);
    wsServer.sendTXT(clientId, out);
}

void processWSCommand(uint8_t clientId, JsonDocument& doc) {
    JsonDocument response;

    if (!isSourceAllowed(SOURCE_WEBSOCKET)) {
        response["type"] = "ack";
        response["status"] = "deferred";
        response["reason"] = "input_policy_blocked_websocket";
        response["input_policy"] = inputPolicyToString(inputPolicy);
        if (doc["seq"].is<uint32_t>()) response["seq"] = doc["seq"].as<uint32_t>();
        sendWSJson(clientId, response);
        return;
    }

    if (inputPolicy == INPUT_POLICY_AUTO && wiredPriorityActive()) {
        response["type"] = "ack";
        response["status"] = "deferred";
        response["reason"] = "wired_priority_window_active";
        response["window_ms"] = WIRED_PRIORITY_WINDOW_MS;
        if (doc["seq"].is<uint32_t>()) response["seq"] = doc["seq"].as<uint32_t>();
        sendWSJson(clientId, response);
        return;
    }

    lastWSCommandAtMs = millis();
    executeCommand(doc, response, SOURCE_WEBSOCKET);
    response["type"] = "ack";
    if (doc["seq"].is<uint32_t>()) response["seq"] = doc["seq"].as<uint32_t>();
    sendWSJson(clientId, response);
}

void onWSEvent(uint8_t clientId, WStype_t type, uint8_t* payload, size_t length) {
    switch (type) {
        case WStype_CONNECTED: {
            wsConnectedClients = wsServer.connectedClients();
            Serial.printf("[WS] Client %u connected (%u total)\n", clientId, static_cast<unsigned>(wsConnectedClients));
            displayMenu();
            JsonDocument hello;
            hello["type"] = "hello";
            hello["status"] = "ok";
            hello["mode"] = emulationModeToString(currentEmulationMode);
            hello["legacy_mode"] = modeToString(currentMode);
            hello["delivery_policy"] = deliveryPolicyToString(deliveryPolicy);
            hello["input_policy"] = inputPolicyToString(inputPolicy);
            hello["ble_advertisement_name"] = getBleAdvertisementName();
            hello["ble_profile"] = getBleProfileLabel();
            hello["ble_mode_scoped_mac"] = getModeSpecificBleMacString();
            hello["wired_priority_window_ms"] = WIRED_PRIORITY_WINDOW_MS;
            hello["ws_priority_window_ms"] = WS_PRIORITY_WINDOW_MS;
            sendWSJson(clientId, hello);
            break;
        }
        case WStype_DISCONNECTED:
            wsConnectedClients = wsServer.connectedClients();
            Serial.printf("[WS] Client %u disconnected (%u total)\n", clientId, static_cast<unsigned>(wsConnectedClients));
            displayMenu();
            break;
        case WStype_TEXT: {
            wsMsgCounter++;
            JsonDocument doc;
            DeserializationError err = deserializeJson(doc, payload, length);
            if (err) {
                JsonDocument bad;
                bad["type"] = "ack";
                bad["status"] = "error";
                bad["error"] = "invalid json";
                sendWSJson(clientId, bad);
                return;
            }

            String msgType = doc["type"].as<String>();
            if (msgType == "ping") {
                JsonDocument pong;
                pong["type"] = "pong";
                if (doc["seq"].is<uint32_t>()) pong["seq"] = doc["seq"].as<uint32_t>();
                pong["counter"] = wsMsgCounter;
                sendWSJson(clientId, pong);
                return;
            }

            if (doc["action"].is<const char*>()) {
                processWSCommand(clientId, doc);
                return;
            }

            JsonDocument unsupported;
            unsupported["type"] = "ack";
            unsupported["status"] = "error";
            unsupported["error"] = "missing action";
            if (doc["seq"].is<uint32_t>()) unsupported["seq"] = doc["seq"].as<uint32_t>();
            sendWSJson(clientId, unsupported);
            break;
        }
        default:
            break;
    }
}

// ============================================================
// Serial input handling (wired command channel)
// ============================================================
namespace {

uint32_t readLE32(const uint8_t* ptr) {
    return static_cast<uint32_t>(ptr[0]) |
           (static_cast<uint32_t>(ptr[1]) << 8) |
           (static_cast<uint32_t>(ptr[2]) << 16) |
           (static_cast<uint32_t>(ptr[3]) << 24);
}

void writeLE32(uint8_t* ptr, uint32_t value) {
    ptr[0] = static_cast<uint8_t>(value & 0xff);
    ptr[1] = static_cast<uint8_t>((value >> 8) & 0xff);
    ptr[2] = static_cast<uint8_t>((value >> 16) & 0xff);
    ptr[3] = static_cast<uint8_t>((value >> 24) & 0xff);
}

uint32_t pabbCRC32C(const uint8_t* data, size_t len) {
    uint32_t crc = 0xffffffffu;
    for (size_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (int b = 0; b < 8; b++) {
            if (crc & 1) crc = (crc >> 1) ^ 0x82F63B78u;
            else crc >>= 1;
        }
    }
    return crc;
}

void sendPabbMessage(uint8_t type, const uint8_t* body, size_t bodyLen) {
    if (bodyLen + PABB_PROTOCOL_OVERHEAD > PABB_PROTOCOL_MAX_PACKET_SIZE) return;

    const size_t packetLen = bodyLen + PABB_PROTOCOL_OVERHEAD;
    uint8_t packet[PABB_PROTOCOL_MAX_PACKET_SIZE] = {0};
    packet[0] = static_cast<uint8_t>(~packetLen);
    packet[1] = type;
    if (body != nullptr && bodyLen > 0) {
        memcpy(packet + 2, body, bodyLen);
    }
    uint32_t crc = pabbCRC32C(packet, packetLen - sizeof(uint32_t));
    writeLE32(packet + packetLen - sizeof(uint32_t), crc);
    Serial.write(packet, packetLen);
}

void sendPabbErrorType(uint8_t type) {
    sendPabbMessage(PABB_MSG_ERROR_INVALID_TYPE, &type, 1);
}

void sendPabbErrorSeq(uint8_t errType, uint32_t seq) {
    uint8_t body[4];
    writeLE32(body, seq);
    sendPabbMessage(errType, body, sizeof(body));
}

void sendPabbAckRequest(uint32_t seq) {
    uint8_t body[4];
    writeLE32(body, seq);
    sendPabbMessage(PABB_MSG_ACK_REQUEST, body, sizeof(body));
}

void sendPabbAckCommand(uint32_t seq) {
    uint8_t body[4];
    writeLE32(body, seq);
    sendPabbMessage(PABB_MSG_ACK_COMMAND, body, sizeof(body));
}

void sendPabbAckRequestI8(uint32_t seq, uint8_t value) {
    uint8_t body[5];
    writeLE32(body, seq);
    body[4] = value;
    sendPabbMessage(PABB_MSG_ACK_REQUEST_I8, body, sizeof(body));
}

void sendPabbAckRequestI32(uint32_t seq, uint32_t value) {
    uint8_t body[8];
    writeLE32(body, seq);
    writeLE32(body + 4, value);
    sendPabbMessage(PABB_MSG_ACK_REQUEST_I32, body, sizeof(body));
}

void sendPabbAckRequestData(uint32_t seq, const uint8_t* data, size_t dataLen) {
    if (dataLen + sizeof(uint32_t) > 56) return;
    uint8_t body[60] = {0};
    writeLE32(body, seq);
    if (data != nullptr && dataLen > 0) {
        memcpy(body + sizeof(uint32_t), data, dataLen);
    }
    sendPabbMessage(PABB_MSG_ACK_REQUEST_DATA, body, sizeof(uint32_t) + dataLen);
}

uint8_t boardProgramId() {
    return BOARD_IS_ESP32S3 ? PABB_PID_PABOTBASE_ESP32S3 : PABB_PID_PABOTBASE_ESP32;
}

uint32_t emulationModeToPabbControllerId(EmulationMode mode) {
    switch (mode) {
        case EMU_WIRED_SWITCH_PRO_CONTROLLER:
            return PABB_CID_NintendoSwitch_WiredProController;
        case EMU_BLUETOOTH_SWITCH_PRO_CONTROLLER:
            return PABB_CID_NintendoSwitch_WirelessProController;
        case EMU_BLUETOOTH_KEYBOARD_ONLY:
        case EMU_WIRED_KEYBOARD_ONLY:
            return PABB_CID_StandardHid_Keyboard;
        default:
            return PABB_CID_NONE;
    }
}

EmulationMode pabbControllerIdToEmulationMode(uint32_t controllerId) {
    switch (controllerId) {
        case PABB_CID_StandardHid_Keyboard:
            return BOARD_SUPPORTS_WIRED_OUTPUT ? EMU_WIRED_KEYBOARD_ONLY : EMU_BLUETOOTH_KEYBOARD_ONLY;
        case PABB_CID_NintendoSwitch_WiredController:
        case PABB_CID_NintendoSwitch_WiredProController:
            return BOARD_SUPPORTS_WIRED_OUTPUT ? EMU_WIRED_SWITCH_PRO_CONTROLLER : EMU_BLUETOOTH_SWITCH_PRO_CONTROLLER;
        case PABB_CID_NintendoSwitch_WirelessProController:
            return BOARD_SUPPORTS_BT_SWITCH_PRO_MODE ? EMU_BLUETOOTH_SWITCH_PRO_CONTROLLER : EMU_WIRED_SWITCH_PRO_CONTROLLER;
        default:
            return currentEmulationMode;
    }
}

bool pabbControllerIdKnown(uint32_t controllerId) {
    switch (controllerId) {
        case PABB_CID_NONE:
        case PABB_CID_StandardHid_Keyboard:
        case PABB_CID_NintendoSwitch_WiredController:
        case PABB_CID_NintendoSwitch_WiredProController:
        case PABB_CID_NintendoSwitch_WirelessProController:
            return true;
        default:
            return false;
    }
}

std::vector<uint32_t> pabbSupportedControllers() {
    std::vector<uint32_t> ids;
    ids.push_back(PABB_CID_NONE);
    ids.push_back(PABB_CID_StandardHid_Keyboard);
    if (BOARD_SUPPORTS_WIRED_OUTPUT && !emulationModeBlockedByBuild(EMU_WIRED_SWITCH_PRO_CONTROLLER)) {
        ids.push_back(PABB_CID_NintendoSwitch_WiredController);
        ids.push_back(PABB_CID_NintendoSwitch_WiredProController);
    }
    if (BOARD_SUPPORTS_BT_SWITCH_PRO_MODE) {
        ids.push_back(PABB_CID_NintendoSwitch_WirelessProController);
    }
    return ids;
}

uint8_t pabbWiredButtons0 = 0;
uint8_t pabbWiredButtons1 = 0;

void applyPAWiredState(const uint8_t* report, size_t reportLen) {
    if (!BOARD_SUPPORTS_WIRED_OUTPUT || report == nullptr || reportLen < 7) return;
    if (currentEmulationMode != EMU_WIRED_SWITCH_PRO_CONTROLLER) return;

    const uint8_t buttons0 = report[0];
    const uint8_t buttons1 = report[1];
    const uint8_t dpadByte = report[2];
    const int lx = static_cast<int>(report[3]) - 128;
    const int ly = static_cast<int>(report[4]) - 128;
    const int rx = static_cast<int>(report[5]) - 128;
    const int ry = static_cast<int>(report[6]) - 128;

    auto updateButton = [&](uint8_t prevMaskByte, uint8_t nextMaskByte, uint8_t bit, uint8_t mapped) {
        bool prevPressed = (prevMaskByte & (1 << bit)) != 0;
        bool nextPressed = (nextMaskByte & (1 << bit)) != 0;
        if (prevPressed == nextPressed) return;
        if (nextPressed) UsbHidBridge::gamepadPress(mapped);
        else UsbHidBridge::gamepadRelease(mapped);
    };

    // buttons0: Y/B/A/X/L/R/ZL/ZR
    updateButton(pabbWiredButtons0, buttons0, 0, UsbHidBridge::USB_BUTTON_WEST);   // Y
    updateButton(pabbWiredButtons0, buttons0, 1, UsbHidBridge::USB_BUTTON_SOUTH);  // B
    updateButton(pabbWiredButtons0, buttons0, 2, UsbHidBridge::USB_BUTTON_EAST);   // A
    updateButton(pabbWiredButtons0, buttons0, 3, UsbHidBridge::USB_BUTTON_NORTH);  // X
    updateButton(pabbWiredButtons0, buttons0, 4, UsbHidBridge::USB_BUTTON_TL);     // L
    updateButton(pabbWiredButtons0, buttons0, 5, UsbHidBridge::USB_BUTTON_TR);     // R
    updateButton(pabbWiredButtons0, buttons0, 6, UsbHidBridge::USB_BUTTON_TL2);    // ZL
    updateButton(pabbWiredButtons0, buttons0, 7, UsbHidBridge::USB_BUTTON_TR2);    // ZR

    // buttons1: minus/plus/l3/r3/home/capture/gr/gl
    updateButton(pabbWiredButtons1, buttons1, 0, UsbHidBridge::USB_BUTTON_SELECT); // minus
    updateButton(pabbWiredButtons1, buttons1, 1, UsbHidBridge::USB_BUTTON_START);  // plus
    updateButton(pabbWiredButtons1, buttons1, 2, UsbHidBridge::USB_BUTTON_THUMBL); // l3
    updateButton(pabbWiredButtons1, buttons1, 3, UsbHidBridge::USB_BUTTON_THUMBR); // r3
    updateButton(pabbWiredButtons1, buttons1, 4, UsbHidBridge::USB_BUTTON_MODE);   // home
    updateButton(pabbWiredButtons1, buttons1, 5, UsbHidBridge::USB_BUTTON_MODE);   // capture (best effort)
    updateButton(pabbWiredButtons1, buttons1, 6, UsbHidBridge::USB_BUTTON_MODE);   // GR
    updateButton(pabbWiredButtons1, buttons1, 7, UsbHidBridge::USB_BUTTON_MODE);   // GL

    uint8_t hat = UsbHidBridge::USB_HAT_CENTER;
    switch (dpadByte & 0x0f) {
        case 0: hat = UsbHidBridge::USB_HAT_UP; break;
        case 1: hat = UsbHidBridge::USB_HAT_UP_RIGHT; break;
        case 2: hat = UsbHidBridge::USB_HAT_RIGHT; break;
        case 3: hat = UsbHidBridge::USB_HAT_DOWN_RIGHT; break;
        case 4: hat = UsbHidBridge::USB_HAT_DOWN; break;
        case 5: hat = UsbHidBridge::USB_HAT_DOWN_LEFT; break;
        case 6: hat = UsbHidBridge::USB_HAT_LEFT; break;
        case 7: hat = UsbHidBridge::USB_HAT_UP_LEFT; break;
        default: hat = UsbHidBridge::USB_HAT_CENTER; break;
    }
    UsbHidBridge::gamepadHat(hat);
    UsbHidBridge::gamepadLeftStick(lx, ly);
    UsbHidBridge::gamepadRightStick(rx, ry);

    pabbWiredButtons0 = buttons0;
    pabbWiredButtons1 = buttons1;
}

void neutralizePAWiredState() {
    uint8_t neutral[7] = {0, 0, 8, 128, 128, 128, 128};
    applyPAWiredState(neutral, sizeof(neutral));
}

uint8_t pabbKeyboardModifiers = 0;
uint8_t pabbKeyboardKeys[6] = {0};

uint8_t pabbModifierToKeycode(uint8_t bit) {
    switch (bit) {
        // Standard HID keyboard modifier keycodes (USBHIDKeyboard-compatible)
        case 0: return 0x80; // Left Ctrl
        case 1: return 0x81; // Left Shift
        case 2: return 0x82; // Left Alt
        case 3: return 0x83; // Left GUI
        case 4: return 0x84; // Right Ctrl
        case 5: return 0x85; // Right Shift
        case 6: return 0x86; // Right Alt
        case 7: return 0x87; // Right GUI
        default: return 0;
    }
}

void applyPAKeyboardState(const uint8_t* report, size_t reportLen) {
    if (!BOARD_SUPPORTS_WIRED_OUTPUT || report == nullptr || reportLen < 8) return;
    if (!(currentEmulationMode == EMU_WIRED_KEYBOARD_ONLY ||
          currentEmulationMode == EMU_WIRED_COMBO ||
          currentEmulationMode == EMU_BLUETOOTH_KEYBOARD_ONLY ||
          currentEmulationMode == EMU_BLUETOOTH_COMBO)) return;

    uint8_t nextModifiers = report[0];
    const uint8_t* nextKeys = report + 2;

    for (uint8_t bit = 0; bit < 8; bit++) {
        bool prevPressed = (pabbKeyboardModifiers & (1 << bit)) != 0;
        bool nextPressed = (nextModifiers & (1 << bit)) != 0;
        if (prevPressed == nextPressed) continue;
        uint8_t key = pabbModifierToKeycode(bit);
        if (key == 0) continue;
        if (nextPressed) UsbHidBridge::keyboardPress(key);
        else UsbHidBridge::keyboardRelease(key);
    }

    for (uint8_t i = 0; i < 6; i++) {
        uint8_t key = pabbKeyboardKeys[i];
        if (key == 0) continue;
        bool stillPresent = false;
        for (uint8_t j = 0; j < 6; j++) {
            if (nextKeys[j] == key) {
                stillPresent = true;
                break;
            }
        }
        if (!stillPresent) {
            UsbHidBridge::keyboardRelease(key);
        }
    }

    for (uint8_t i = 0; i < 6; i++) {
        uint8_t key = nextKeys[i];
        if (key == 0) continue;
        bool alreadyPressed = false;
        for (uint8_t j = 0; j < 6; j++) {
            if (pabbKeyboardKeys[j] == key) {
                alreadyPressed = true;
                break;
            }
        }
        if (!alreadyPressed) {
            UsbHidBridge::keyboardPress(key);
        }
    }

    pabbKeyboardModifiers = nextModifiers;
    memcpy(pabbKeyboardKeys, nextKeys, 6);
}

void neutralizePAKeyboardState() {
    uint8_t neutral[8] = {0};
    applyPAKeyboardState(neutral, sizeof(neutral));
}

void decodeOemJoystick(const uint8_t in[3], uint16_t& x, uint16_t& y) {
    x = static_cast<uint16_t>(in[0] | ((in[1] & 0x0f) << 8));
    y = static_cast<uint16_t>((in[1] >> 4) | (in[2] << 4));
}

void applyPAOemButtonsState(const uint8_t* payload, size_t payloadLen) {
#if defined(CONFIG_IDF_TARGET_ESP32)
    if (!BOARD_SUPPORTS_BT_SWITCH_PRO_MODE || payload == nullptr || payloadLen < 10) return;
    if (currentEmulationMode != EMU_BLUETOOTH_SWITCH_PRO_CONTROLLER) return;

    uint16_t lx = 2048, ly = 2048, rx = 2048, ry = 2048;
    decodeOemJoystick(payload + 3, lx, ly);
    decodeOemJoystick(payload + 6, rx, ry);
    switchProBt.setRawState(payload[0], payload[1], payload[2], lx, ly, rx, ry);
#else
    (void)payload;
    (void)payloadLen;
#endif
}

void neutralizePAOemState() {
#if defined(CONFIG_IDF_TARGET_ESP32)
    if (!BOARD_SUPPORTS_BT_SWITCH_PRO_MODE) return;
    switchProBt.setRawState(0, 0, 0, 2048, 2048, 2048, 2048);
#endif
}

void sendPabbCommandFinished(uint32_t originalSeq) {
    uint8_t body[12];
    writeLE32(body, pabbOutgoingSeq++);
    writeLE32(body + 4, originalSeq);
    writeLE32(body + 8, millis());
    sendPabbMessage(PABB_MSG_REQUEST_COMMAND_FINISHED, body, sizeof(body));
}

void enqueuePabbTimedCommand(uint8_t type, uint32_t seq, uint16_t milliseconds, const uint8_t* payload, size_t payloadLen) {
    if (pabbCommandQueue.size() >= PABB_QUEUE_SIZE) {
        sendPabbErrorSeq(PABB_MSG_ERROR_COMMAND_DROPPED, seq);
        return;
    }
    PabbTimedCommand command;
    command.type = type;
    command.seq = seq;
    command.milliseconds = milliseconds;
    command.payload.assign(payload, payload + payloadLen);
    pabbCommandQueue.push_back(std::move(command));
    sendPabbAckCommand(seq);
}

void processPabbRequest(uint8_t type, const uint8_t* body, size_t bodyLen) {
    if (bodyLen < sizeof(uint32_t)) {
        sendPabbErrorType(type);
        return;
    }
    uint32_t seq = readLE32(body);

    switch (type) {
        case PABB_MSG_REQUEST_PROTOCOL_VERSION:
            sendPabbAckRequestI32(seq, PABB_PROTOCOL_VERSION);
            return;
        case PABB_MSG_REQUEST_PROGRAM_VERSION:
            sendPabbAckRequestI32(seq, PABB_PROGRAM_VERSION);
            return;
        case PABB_MSG_REQUEST_PROGRAM_ID:
            sendPabbAckRequestI8(seq, boardProgramId());
            return;
        case PABB_MSG_REQUEST_PROGRAM_NAME: {
            static const char kName[] = "ChromaCatch";
            sendPabbAckRequestData(seq, reinterpret_cast<const uint8_t*>(kName), sizeof(kName) - 1);
            return;
        }
        case PABB_MSG_REQUEST_CONTROLLER_LIST: {
            std::vector<uint32_t> ids = pabbSupportedControllers();
            std::vector<uint8_t> bytes(ids.size() * sizeof(uint32_t), 0);
            for (size_t i = 0; i < ids.size(); i++) {
                writeLE32(bytes.data() + i * 4, ids[i]);
            }
            sendPabbAckRequestData(seq, bytes.data(), bytes.size());
            return;
        }
        case PABB_MSG_REQUEST_QUEUE_SIZE:
            sendPabbAckRequestI8(seq, PABB_QUEUE_SIZE);
            return;
        case PABB_MSG_REQUEST_READ_CONTROLLER_MODE:
            sendPabbAckRequestI32(seq, emulationModeToPabbControllerId(currentEmulationMode));
            return;
        case PABB_MSG_REQUEST_CHANGE_CONTROLLER_MODE:
        case PABB_MSG_REQUEST_RESET_TO_CONTROLLER: {
            if (bodyLen < 8) {
                sendPabbErrorSeq(PABB_MSG_ERROR_INVALID_REQUEST, seq);
                return;
            }
            uint32_t requestedController = readLE32(body + 4);
            if (!pabbControllerIdKnown(requestedController)) {
                sendPabbErrorSeq(PABB_MSG_ERROR_INVALID_REQUEST, seq);
                return;
            }
            EmulationMode requestedMode = pabbControllerIdToEmulationMode(requestedController);
            if (!emulationModeSupported(requestedMode) || emulationModeBlockedByBuild(requestedMode)) {
                sendPabbErrorSeq(PABB_MSG_ERROR_INVALID_REQUEST, seq);
                return;
            }
            applyEmulationMode(requestedMode);
            initBLE();
            displayMenu();
            sendPabbAckRequestI32(seq, emulationModeToPabbControllerId(currentEmulationMode));
            return;
        }
        case PABB_MSG_REQUEST_STATUS: {
            uint32_t status = 0;
            bool connected = chooseRuntimeDelivery() != RUNTIME_NONE;
            if (connected) status |= 1; // connected
            if (connected) status |= 2; // ready
            if (isBLEConnected()) status |= 4; // paired
            sendPabbAckRequestI32(seq, status);
            return;
        }
        case PABB_MSG_REQUEST_READ_MAC_ADDRESS:
        case PABB_MSG_REQUEST_PAIRED_MAC_ADDRESS: {
            uint8_t mac[6] = {0};
            uint8_t modeAddr[6] = {0};
            deriveModeSpecificBleAddress(modeAddr, currentEmulationMode);
            memcpy(mac, modeAddr, sizeof(mac));
            if (type == PABB_MSG_REQUEST_PAIRED_MAC_ADDRESS && !isBLEConnected()) {
                memset(mac, 0, sizeof(mac));
            }
            sendPabbAckRequestData(seq, mac, sizeof(mac));
            return;
        }
        case PABB_MSG_REQUEST_NS1_OEM_CONTROLLER_READ_SPI: {
            // BT Switch SPI reads are handled on the Switch HID output-report side.
            // PA serial request path receives a best-effort empty ACK payload.
            sendPabbAckRequestData(seq, nullptr, 0);
            return;
        }
        case PABB_MSG_REQUEST_NS1_OEM_CONTROLLER_WRITE_SPI:
            sendPabbAckRequest(seq);
            return;
        case PABB_MSG_REQUEST_NS1_OEM_CONTROLLER_PLAYER_LIGHTS:
            sendPabbAckRequestI8(seq, 0x01);
            return;
        case PABB_MSG_REQUEST_COMMAND_FINISHED:
        case PABB_MSG_ACK_REQUEST:
        case PABB_MSG_ACK_COMMAND:
        case PABB_MSG_ACK_REQUEST_I8:
        case PABB_MSG_ACK_REQUEST_I32:
        case PABB_MSG_ACK_REQUEST_DATA:
            // Host-side acks/command-finished are accepted and ignored.
            return;
        default:
            sendPabbErrorType(type);
            return;
    }
}

void processPabbCommand(uint8_t type, const uint8_t* body, size_t bodyLen) {
    if (bodyLen < 6) {
        sendPabbErrorType(type);
        return;
    }
    uint32_t seq = readLE32(body);
    uint16_t milliseconds = static_cast<uint16_t>(body[4] | (body[5] << 8));
    const uint8_t* payload = body + 6;
    size_t payloadLen = bodyLen - 6;

    switch (type) {
        case PABB_MSG_COMMAND_HID_KEYBOARD_STATE:
            enqueuePabbTimedCommand(type, seq, milliseconds, payload, payloadLen);
            return;
        case PABB_MSG_COMMAND_NS_WIRED_CONTROLLER_STATE:
            enqueuePabbTimedCommand(type, seq, milliseconds, payload, payloadLen);
            return;
        case PABB_MSG_COMMAND_NS1_OEM_CONTROLLER_BUTTONS:
            enqueuePabbTimedCommand(type, seq, milliseconds, payload, payloadLen);
            return;
        case PABB_MSG_COMMAND_NS1_OEM_CONTROLLER_FULL_STATE:
            enqueuePabbTimedCommand(type, seq, milliseconds, payload, payloadLen);
            return;
        default:
            sendPabbErrorType(type);
            return;
    }
}

} // namespace

void processPabbMessage(uint8_t type, const uint8_t* body, size_t bodyLen) {
    if (!BOARD_SUPPORTS_WIRED_INPUT) return;
    if (!isSourceAllowed(SOURCE_WIRED)) return;

    lastWiredCommandAtMs = millis();

    if ((type & 0xc0) == 0x40 || (type & 0xf0) == 0x10 || (type & 0xf0) == 0x00) {
        processPabbRequest(type, body, bodyLen);
        return;
    }
    if (type >= 0x80) {
        processPabbCommand(type, body, bodyLen);
        return;
    }
    sendPabbErrorType(type);
}

void servicePabbCommandQueue() {
    if (!BOARD_SUPPORTS_WIRED_INPUT) return;

    if (!pabbCommandActive && !pabbCommandQueue.empty()) {
        pabbActiveCommand = std::move(pabbCommandQueue.front());
        pabbCommandQueue.pop_front();
        pabbCommandActive = true;
        pabbActiveCommandEndMs = millis() + pabbActiveCommand.milliseconds;

        switch (pabbActiveCommand.type) {
            case PABB_MSG_COMMAND_HID_KEYBOARD_STATE:
                applyPAKeyboardState(pabbActiveCommand.payload.data(), pabbActiveCommand.payload.size());
                break;
            case PABB_MSG_COMMAND_NS_WIRED_CONTROLLER_STATE:
                applyPAWiredState(pabbActiveCommand.payload.data(), pabbActiveCommand.payload.size());
                break;
            case PABB_MSG_COMMAND_NS1_OEM_CONTROLLER_BUTTONS:
                applyPAOemButtonsState(pabbActiveCommand.payload.data(), pabbActiveCommand.payload.size());
                break;
            case PABB_MSG_COMMAND_NS1_OEM_CONTROLLER_FULL_STATE:
                applyPAOemButtonsState(pabbActiveCommand.payload.data(), pabbActiveCommand.payload.size());
                break;
            default:
                break;
        }
    }

    if (!pabbCommandActive) return;
    if (static_cast<int32_t>(millis() - pabbActiveCommandEndMs) < 0) return;

    switch (pabbActiveCommand.type) {
        case PABB_MSG_COMMAND_HID_KEYBOARD_STATE:
            neutralizePAKeyboardState();
            break;
        case PABB_MSG_COMMAND_NS_WIRED_CONTROLLER_STATE:
            neutralizePAWiredState();
            break;
        case PABB_MSG_COMMAND_NS1_OEM_CONTROLLER_BUTTONS:
        case PABB_MSG_COMMAND_NS1_OEM_CONTROLLER_FULL_STATE:
            neutralizePAOemState();
            break;
        default:
            break;
    }
    sendPabbCommandFinished(pabbActiveCommand.seq);
    pabbCommandActive = false;
}

void processSerialCommand(const String& line) {
    if (!BOARD_SUPPORTS_WIRED_INPUT) {
        Serial.println("{\"status\":\"error\",\"error\":\"wired_input_not_supported_on_this_board\"}");
        return;
    }
    if (!isSourceAllowed(SOURCE_WIRED)) {
        Serial.println("{\"status\":\"deferred\",\"reason\":\"input_policy_blocked_wired\"}");
        return;
    }

    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, line);
    if (err) {
        Serial.println("{\"status\":\"error\",\"error\":\"invalid json\"}");
        return;
    }

    lastWiredCommandAtMs = millis();

    JsonDocument response;
    executeCommand(doc, response, SOURCE_WIRED);

    String out;
    serializeJson(response, out);
    Serial.println(out);
}

void processSerialBinaryBuffer() {
    while (!serialBinaryBuffer.empty()) {
        uint8_t lead = serialBinaryBuffer[0];
        if (lead == 0x00) {
            serialBinaryBuffer.erase(serialBinaryBuffer.begin());
            continue;
        }

        uint8_t packetLen = static_cast<uint8_t>(~lead);
        if (packetLen < PABB_PROTOCOL_OVERHEAD || packetLen > PABB_PROTOCOL_MAX_PACKET_SIZE) {
            serialBinaryBuffer.erase(serialBinaryBuffer.begin());
            continue;
        }
        if (serialBinaryBuffer.size() < packetLen) {
            return;
        }

        uint32_t expected = readLE32(serialBinaryBuffer.data() + packetLen - sizeof(uint32_t));
        uint32_t actual = pabbCRC32C(serialBinaryBuffer.data(), packetLen - sizeof(uint32_t));
        if (expected != actual) {
            serialBinaryBuffer.erase(serialBinaryBuffer.begin());
            continue;
        }

        uint8_t type = serialBinaryBuffer[1];
        size_t bodyLen = packetLen - PABB_PROTOCOL_OVERHEAD;
        const uint8_t* body = serialBinaryBuffer.data() + 2;
        processPabbMessage(type, body, bodyLen);
        serialBinaryBuffer.erase(serialBinaryBuffer.begin(), serialBinaryBuffer.begin() + packetLen);
    }
}

void handleSerialInput() {
    if (!BOARD_SUPPORTS_WIRED_INPUT) return;

    while (Serial.available()) {
        uint8_t byte = static_cast<uint8_t>(Serial.read());

        if (serialTextMode) {
            char c = static_cast<char>(byte);
            if (c == '\n' || c == '\r') {
                if (serialBuffer.length() > 0) {
                    processSerialCommand(serialBuffer);
                    serialBuffer = "";
                }
                serialTextMode = false;
            } else {
                serialBuffer += c;
                if (serialBuffer.length() > 2048) {
                    Serial.println("{\"status\":\"error\",\"error\":\"serial buffer overflow\"}");
                    serialBuffer = "";
                    serialTextMode = false;
                }
            }
            continue;
        }

        if (serialBinaryBuffer.empty() && serialBuffer.length() == 0 && (byte == '{' || byte == '[')) {
            serialTextMode = true;
            serialBuffer += static_cast<char>(byte);
            continue;
        }

        serialBinaryBuffer.push_back(byte);
        if (serialBinaryBuffer.size() > 512) {
            serialBinaryBuffer.clear();
        }
        processSerialBinaryBuffer();
    }
}

// ============================================================
// Buttons / menu
// ============================================================
void cycleModeForward() {
    int start = static_cast<int>(currentEmulationMode);
    for (int i = 1; i <= static_cast<int>(EMU_MODE_COUNT); i++) {
        EmulationMode next = static_cast<EmulationMode>((start + i) % static_cast<int>(EMU_MODE_COUNT));
        if (emulationModeSupported(next) && !emulationModeBlockedByBuild(next)) {
            applyEmulationMode(next);
            initBLE();
            displayMenu();
            return;
        }
    }
}

void handleButtons() {
    if (millis() - lastButtonPress < DEBOUNCE_MS) return;

    if (digitalRead(GPIO_UP) == LOW) {
        lastButtonPress = millis();
        menuIndex = (menuIndex - 1 + MENU_ITEMS) % MENU_ITEMS;
        displayMenu();
    }
    else if (digitalRead(GPIO_DOWN) == LOW) {
        lastButtonPress = millis();
        menuIndex = (menuIndex + 1) % MENU_ITEMS;
        displayMenu();
    }
    else if (digitalRead(GPIO_SEL) == LOW) {
        lastButtonPress = millis();
        cycleModeForward();
    }
}

// ============================================================
// WiFi setup
// ============================================================
void setupWiFi() {
    const String wifiSsid = decodeBuildMacroString(WIFI_SSID_RAW);
    const String wifiPassword = decodeBuildMacroString(WIFI_PASSWORD_RAW);

    Serial.print("Connecting to WiFi: ");
    Serial.println(wifiSsid);

    WiFi.mode(WIFI_STA);
    WiFi.begin(wifiSsid.c_str(), wifiPassword.c_str());

    int attempts = 0;
    while (WiFi.status() != WL_CONNECTED && attempts < 30) {
        delay(500);
        Serial.print(".");
        attempts++;
    }

    if (WiFi.status() == WL_CONNECTED) {
        Serial.println("\nWiFi connected!");
        Serial.print("IP address: ");
        Serial.println(WiFi.localIP());
        if (MDNS.begin("chromacatch")) {
            Serial.println("mDNS: chromacatch.local");
        }
    } else {
        Serial.println("\nWiFi connection failed!");
    }
}

// ============================================================
// Setup / Loop
// ============================================================
void setup() {
    Serial.begin(115200);
    Serial.println("\n=== ChromaCatch ESP32 Firmware ===");

    pinMode(GPIO_UP, INPUT_PULLUP);
    pinMode(GPIO_DOWN, INPUT_PULLUP);
    pinMode(GPIO_SEL, INPUT_PULLUP);
    initStatusLed();

    displayInit();
    loadPersistedRuntimeConfig();

    setupWiFi();
    applyEmulationMode(currentEmulationMode);
    initUSBHID();
    initBLE();

    server.on("/ping", HTTP_GET, handlePing);
    server.on("/status", HTTP_GET, handleStatus);
    server.on("/mode", HTTP_GET, handleGetMode);
    server.on("/mode", HTTP_POST, handleSetMode);
    server.on("/command", HTTP_POST, handleCommand);
    server.on("/display", HTTP_GET, handleDisplayGet);
    server.on("/display", HTTP_POST, handleDisplaySet);
    server.on("/display/clear", HTTP_POST, handleDisplayClear);
    server.begin();
    wsServer.begin();
    wsServer.onEvent(onWSEvent);

    Serial.println("HTTP server started on port " + String(HTTP_PORT));
    Serial.println("WebSocket server started on port " + String(WS_PORT));
    displayMenu();
    updateStatusLed();
    Serial.println(BOARD_SUPPORTS_WIRED_INPUT ? "Ready for commands (wired > websocket > http)" : "Ready for commands (websocket > http)");
}

void loop() {
    server.handleClient();
    wsServer.loop();
    UsbHidBridge::tick();
    handleButtons();
    handleSerialInput();
    servicePabbCommandQueue();
    updateDisplayExpiry();
    updateStatusLed();
#if defined(CONFIG_IDF_TARGET_ESP32)
    if (BOARD_SUPPORTS_BT_SWITCH_PRO_MODE && isSwitchMode(currentMode)) {
        switchProBt.tick();
    }
#endif

    static bool lastBle = false;
    static bool lastUsb = false;

    bool bleNow = isBLEConnected();
    bool usbNow = isUSBMounted();

    if (bleNow != lastBle) {
        Serial.println(bleNow ? "BLE connected" : "BLE disconnected");
        displayStatus(bleNow ? "BLE connected" : "BLE disconnected");
        lastBle = bleNow;
    }

    if (usbNow != lastUsb) {
        Serial.println(usbNow ? "USB host mounted" : "USB host unmounted");
        displayStatus(usbNow ? "USB mounted" : "USB unmounted");
        lastUsb = usbNow;
    }

    if (rebootPending && static_cast<int32_t>(millis() - rebootAtMs) >= 0) {
        Serial.println("Rebooting to apply USB HID descriptor change...");
        delay(50);
        ESP.restart();
    }

    delay(1);
}
