#include "switch_pro_bt.h"

#if defined(CONFIG_IDF_TARGET_ESP32)

#include <cstring>

namespace {

SwitchProBT* g_switchProInstance = nullptr;

// Switch Pro classic-BT HID descriptor (report IDs 0x21/0x30/0x31/0x32/0x33/0x3f, output 0x01/0x10/0x11/0x12).
static uint8_t kSwitchProHidDescriptor[] = {
    0x05, 0x01, 0x09, 0x05, 0xA1, 0x01, 0x06, 0x01, 0xFF, 0x85, 0x21, 0x09,
    0x21, 0x75, 0x08, 0x95, 0x30, 0x81, 0x02, 0x85, 0x30, 0x09, 0x30, 0x75,
    0x08, 0x95, 0x30, 0x81, 0x02, 0x85, 0x31, 0x09, 0x31, 0x75, 0x08, 0x96,
    0x69, 0x01, 0x81, 0x02, 0x85, 0x32, 0x09, 0x32, 0x75, 0x08, 0x96, 0x69,
    0x01, 0x81, 0x02, 0x85, 0x33, 0x09, 0x33, 0x75, 0x08, 0x96, 0x69, 0x01,
    0x81, 0x02, 0x85, 0x3F, 0x05, 0x09, 0x19, 0x01, 0x29, 0x10, 0x15, 0x00,
    0x25, 0x01, 0x75, 0x01, 0x95, 0x10, 0x81, 0x02, 0x05, 0x01, 0x09, 0x39,
    0x15, 0x00, 0x25, 0x07, 0x75, 0x04, 0x95, 0x01, 0x81, 0x42, 0x05, 0x09,
    0x75, 0x04, 0x95, 0x01, 0x81, 0x01, 0x05, 0x01, 0x09, 0x30, 0x09, 0x31,
    0x09, 0x33, 0x09, 0x34, 0x16, 0x00, 0x00, 0x27, 0xFF, 0xFF, 0x00, 0x00,
    0x75, 0x10, 0x95, 0x04, 0x81, 0x02, 0x06, 0x01, 0xFF, 0x85, 0x01, 0x09,
    0x01, 0x75, 0x08, 0x95, 0x30, 0x91, 0x02, 0x85, 0x10, 0x09, 0x10, 0x75,
    0x08, 0x95, 0x30, 0x91, 0x02, 0x85, 0x11, 0x09, 0x11, 0x75, 0x08, 0x95,
    0x30, 0x91, 0x02, 0x85, 0x12, 0x09, 0x12, 0x75, 0x08, 0x95, 0x30, 0x91,
    0x02, 0xC0
};

static esp_hid_raw_report_map_t kReportMap[] = {
    {
        .data = kSwitchProHidDescriptor,
        .len = sizeof(kSwitchProHidDescriptor),
    }
};

static esp_hid_device_config_t kBtHidConfig = {
    .vendor_id = 0x057E,
    .product_id = 0x2009,
    .version = 0x0100,
    .device_name = "Pro Controller",
    .manufacturer_name = "Nintendo Co., Ltd.",
    .serial_number = "000000000001",
    .report_maps = kReportMap,
    .report_maps_len = 1,
};

// 0x21 replies (payload contains report id + report body).
static uint8_t kReply02[] = {
    0x21, 0x01, 0x8E, 0x84, 0x00, 0x12, 0x00, 0x08, 0x80, 0x00, 0x08, 0x80,
    0x00, 0x82, 0x02, 0x03, 0x48, 0x03, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x03, 0x01, 0x00, 0x00
};
static uint8_t kReply03[] = {
    0x21, 0x05, 0x8E, 0x84, 0x00, 0x12, 0x01, 0x18, 0x80, 0x01, 0x18, 0x80,
    0x80, 0x80, 0x03, 0x00, 0x00, 0x00, 0x00
};
static uint8_t kReply04[] = {
    0x21, 0x06, 0x8E, 0x00, 0x00, 0x00, 0x01, 0x18, 0x80, 0x01, 0x18, 0x80,
    0x80, 0x83, 0x04, 0x00, 0x6a, 0x01, 0xbb
};
static uint8_t kReply08[] = {
    0x21, 0x02, 0x8E, 0x00, 0x00, 0x00, 0x01, 0x18, 0x80, 0x01, 0x18, 0x80,
    0x80, 0x80, 0x08, 0x00, 0x00, 0x00, 0x00
};
static uint8_t kReply3001[] = {
    0x21, 0x04, 0x8E, 0x00, 0x00, 0x00, 0x01, 0x18, 0x80, 0x01, 0x18, 0x80,
    0x80, 0x80, 0x30, 0x00, 0x00, 0x00, 0x00
};
static uint8_t kReply4001[] = {
    0x21, 0x04, 0x8E, 0x00, 0x00, 0x00, 0x01, 0x18, 0x80, 0x01, 0x18, 0x80,
    0x80, 0x80, 0x40, 0x00, 0x00, 0x00, 0x00
};
static uint8_t kReply4801[] = {
    0x21, 0x04, 0x8E, 0x00, 0x00, 0x00, 0x01, 0x18, 0x80, 0x01, 0x18, 0x80,
    0x80, 0x80, 0x48, 0x00, 0x00, 0x00, 0x00
};
static uint8_t kReply3401[] = {
    0x21, 0x12, 0x8E, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x08, 0x80,
    0x00, 0x80, 0x22, 0x00, 0x00, 0x00, 0x00
};
static uint8_t kReply2100[] = {
    0x21, 0x03, 0x8E, 0x84, 0x00, 0x12, 0x01, 0x18, 0x80, 0x01, 0x18, 0x80,
    0x80, 0x80, 0x21, 0x00, 0x00, 0x00, 0x00
};
static uint8_t kReply1060[] = {
    0x21, 0x03, 0x8E, 0x84, 0x00, 0x12, 0x01, 0x18, 0x80, 0x01, 0x18, 0x80,
    0x80, 0x90, 0x10, 0x00, 0x60, 0x00, 0x00, 0x10, 0x00, 0x00, 0x00
};
static uint8_t kReply1020[] = {
    0x21, 0x04, 0x8E, 0x84, 0x00, 0x12, 0x01, 0x18, 0x80, 0x01, 0x18, 0x80,
    0x80, 0x90, 0x10, 0x20, 0x60, 0x00, 0x00, 0x18, 0x00, 0x00
};
static uint8_t kReply1010[] = {
    0x21, 0x04, 0x8E, 0x84, 0x00, 0x12, 0x01, 0x18, 0x80, 0x01, 0x18, 0x80,
    0x80, 0x90, 0x10, 0x10, 0x80, 0x00, 0x00, 0x18, 0x00, 0x00
};
static uint8_t kReply3333[] = {
    0x21, 0x31, 0x8E, 0x00, 0x00, 0x00, 0x00, 0x08, 0x80, 0x00, 0x08, 0x80,
    0x00, 0xA0, 0x21, 0x01, 0x00, 0x00, 0x00
};

}  // namespace

bool SwitchProBT::begin() {
    if (_active) return true;

#if !defined(CONFIG_BT_HID_ENABLED) || !(CONFIG_BT_HID_ENABLED)
    // Pokemon Automation's BT Switch path relies on Classic HID support in the
    // underlying ESP-IDF build. Arduino-ESP32 prebuilt libs in this workspace
    // currently ship without CONFIG_BT_HID_ENABLED, so classic HID init cannot
    // succeed.
    Serial.println("[SwitchProBT] CONFIG_BT_HID_ENABLED is not enabled in current core build.");
    return false;
#endif

    g_switchProInstance = this;
    _connected = false;
    _discoverable = false;
    _started = false;
    _timer = 0;
    _btnRight = 0;
    _btnShared = 0;
    _btnLeft = 0;
    _lx = 2048;
    _ly = 2048;
    _rx = 2048;
    _ry = 2048;

    const uint8_t* btAddr = esp_bt_dev_get_address();
    if (btAddr != nullptr) {
        for (int i = 0; i < 6; i++) {
            // Switch expects host-visible address bytes embedded in the device info reply.
            kReply02[19 + i] = btAddr[i];
        }
    }

    esp_bt_controller_status_t ctrlStatus = esp_bt_controller_get_status();
    Serial.printf("[SwitchProBT] controller status (pre): %d\n", static_cast<int>(ctrlStatus));
    if (ctrlStatus == ESP_BT_CONTROLLER_STATUS_IDLE) {
        esp_bt_controller_config_t cfg = BT_CONTROLLER_INIT_CONFIG_DEFAULT();
        esp_err_t err = esp_bt_controller_init(&cfg);
        if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
            Serial.printf("[SwitchProBT] controller_init failed: %d\n", static_cast<int>(err));
            return false;
        }
    }

    ctrlStatus = esp_bt_controller_get_status();
    Serial.printf("[SwitchProBT] controller status (post-init): %d\n", static_cast<int>(ctrlStatus));
    if (ctrlStatus == ESP_BT_CONTROLLER_STATUS_INITED) {
        esp_err_t err = esp_bt_controller_enable(ESP_BT_MODE_BTDM);
        if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
            Serial.printf("[SwitchProBT] controller_enable(BTDM) failed: %d\n", static_cast<int>(err));
            return false;
        }
    }
    Serial.printf("[SwitchProBT] controller status (post-enable): %d\n", static_cast<int>(esp_bt_controller_get_status()));

    if (esp_bluedroid_get_status() == ESP_BLUEDROID_STATUS_UNINITIALIZED) {
        esp_err_t err = esp_bluedroid_init();
        if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
            Serial.printf("[SwitchProBT] bluedroid_init failed: %d\n", static_cast<int>(err));
            return false;
        }
    }
    if (esp_bluedroid_get_status() == ESP_BLUEDROID_STATUS_INITIALIZED) {
        esp_err_t err = esp_bluedroid_enable();
        if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
            Serial.printf("[SwitchProBT] bluedroid_enable failed: %d\n", static_cast<int>(err));
            return false;
        }
    }

    esp_bt_dev_set_device_name(kBtHidConfig.device_name);
    esp_bt_cod_t cod = {0};
    cod.major = 5;
    cod.minor = 2;
    cod.service = 1;
    esp_err_t codErr = esp_bt_gap_set_cod(cod, ESP_BT_SET_COD_ALL);
    if (codErr != ESP_OK) {
        Serial.printf("[SwitchProBT] set_cod failed: %d\n", static_cast<int>(codErr));
    }

    esp_err_t gapErr = esp_bt_gap_register_callback(&SwitchProBT::gapEventCallback);
    if (gapErr != ESP_OK && gapErr != ESP_ERR_INVALID_STATE) {
        Serial.printf("[SwitchProBT] gap_register_callback failed: %d\n", static_cast<int>(gapErr));
    }

    esp_bt_pin_type_t pinType = ESP_BT_PIN_TYPE_VARIABLE;
    esp_bt_pin_code_t pinCode = {0};
    esp_bt_gap_set_pin(pinType, 0, pinCode);

    esp_err_t err = esp_hidd_dev_init(&kBtHidConfig, ESP_HID_TRANSPORT_BT, &SwitchProBT::hiddEventCallback, &_dev);
    if (err != ESP_OK) {
        Serial.printf("[SwitchProBT] hidd_dev_init failed: %d\n", static_cast<int>(err));
        return false;
    }

    _active = true;
    _discoverable = true;
    _lastTickMs = millis();
    _lastDiscoverableRefreshMs = millis();
    return true;
}

void SwitchProBT::end() {
    if (!_active) return;
    if (_dev != nullptr) {
        esp_hidd_dev_deinit(_dev);
    }
    if (esp_bluedroid_get_status() == ESP_BLUEDROID_STATUS_ENABLED) {
        esp_bluedroid_disable();
    }
    if (esp_bluedroid_get_status() == ESP_BLUEDROID_STATUS_INITIALIZED) {
        esp_bluedroid_deinit();
    }
    if (esp_bt_controller_get_status() == ESP_BT_CONTROLLER_STATUS_ENABLED) {
        esp_bt_controller_disable();
    }
    _dev = nullptr;
    _active = false;
    _connected = false;
    _discoverable = false;
    _started = false;
    g_switchProInstance = nullptr;
}

bool SwitchProBT::isActive() const {
    return _active;
}

bool SwitchProBT::isConnected() const {
    return _connected;
}

bool SwitchProBT::isDiscoverable() const {
    return _discoverable;
}

void SwitchProBT::hiddEventCallback(void* handlerArgs, esp_event_base_t base, int32_t id, void* eventData) {
    (void)handlerArgs;
    (void)base;
    if (g_switchProInstance == nullptr) return;
    g_switchProInstance->onHiddEvent(static_cast<esp_hidd_event_t>(id), static_cast<esp_hidd_event_data_t*>(eventData));
}

void SwitchProBT::gapEventCallback(esp_bt_gap_cb_event_t event, esp_bt_gap_cb_param_t* param) {
    if (event == ESP_BT_GAP_PIN_REQ_EVT) {
        esp_bt_pin_code_t pinCode = {'1', '2', '3', '4'};
        esp_bt_gap_pin_reply(param->pin_req.bda, true, 4, pinCode);
    } else if (event == ESP_BT_GAP_CFM_REQ_EVT) {
        esp_bt_gap_ssp_confirm_reply(param->cfm_req.bda, true);
    }
}

void SwitchProBT::onHiddEvent(esp_hidd_event_t event, esp_hidd_event_data_t* param) {
    switch (event) {
        case ESP_HIDD_START_EVENT:
            _started = (param != nullptr && param->start.status == ESP_OK);
            if (_started) {
                esp_bt_gap_set_scan_mode(ESP_BT_CONNECTABLE, ESP_BT_GENERAL_DISCOVERABLE);
                _discoverable = true;
            }
            break;
        case ESP_HIDD_CONNECT_EVENT:
            _connected = (param != nullptr && param->connect.status == ESP_OK);
            if (_connected) {
                esp_bt_gap_set_scan_mode(ESP_BT_NON_CONNECTABLE, ESP_BT_NON_DISCOVERABLE);
                _discoverable = false;
            }
            break;
        case ESP_HIDD_DISCONNECT_EVENT:
            _connected = false;
            esp_bt_gap_set_scan_mode(ESP_BT_CONNECTABLE, ESP_BT_GENERAL_DISCOVERABLE);
            _discoverable = true;
            break;
        case ESP_HIDD_OUTPUT_EVENT:
            if (param != nullptr) {
                handleOutputReport(
                    static_cast<uint8_t>(param->output.report_id),
                    param->output.data,
                    param->output.length
                );
            }
            break;
        case ESP_HIDD_FEATURE_EVENT:
            if (param != nullptr) {
                handleOutputReport(
                    static_cast<uint8_t>(param->feature.report_id),
                    param->feature.data,
                    param->feature.length
                );
            }
            break;
        default:
            break;
    }
}

void SwitchProBT::handleOutputReport(uint8_t reportId, const uint8_t* data, uint16_t len) {
    // Report 0x01 = rumble + subcommand.
    if (reportId != 0x01 || data == nullptr || len < 10) return;

    uint8_t subcmd = data[9];
    switch (subcmd) {
        case 0x02: sendSubcommandReply(kReply02, sizeof(kReply02)); break;
        case 0x03: sendSubcommandReply(kReply03, sizeof(kReply03)); break;
        case 0x04: sendSubcommandReply(kReply04, sizeof(kReply04)); break;
        case 0x08: sendSubcommandReply(kReply08, sizeof(kReply08)); break;
        case 0x22: sendSubcommandReply(kReply3401, sizeof(kReply3401)); break;
        case 0x30: sendSubcommandReply(kReply3001, sizeof(kReply3001)); break;
        case 0x40: sendSubcommandReply(kReply4001, sizeof(kReply4001)); break;
        case 0x48: sendSubcommandReply(kReply4801, sizeof(kReply4801)); break;
        case 0x21:
            if (len > 10 && data[10] == 0x21) sendSubcommandReply(kReply3333, sizeof(kReply3333));
            else sendSubcommandReply(kReply2100, sizeof(kReply2100));
            break;
        case 0x10: {
            if (len < 15) break;
            uint32_t addr = static_cast<uint32_t>(data[10]) |
                            (static_cast<uint32_t>(data[11]) << 8) |
                            (static_cast<uint32_t>(data[12]) << 16) |
                            (static_cast<uint32_t>(data[13]) << 24);
            switch (addr) {
                case 0x6000: sendSubcommandReply(kReply1060, sizeof(kReply1060)); break;
                case 0x6050: sendSubcommandReply(kReply1060, sizeof(kReply1060)); break;
                case 0x6080: sendSubcommandReply(kReply1010, sizeof(kReply1010)); break;
                case 0x6098: sendSubcommandReply(kReply1010, sizeof(kReply1010)); break;
                case 0x603D: sendSubcommandReply(kReply1020, sizeof(kReply1020)); break;
                case 0x6020: sendSubcommandReply(kReply1020, sizeof(kReply1020)); break;
                case 0x8010: sendSubcommandReply(kReply1010, sizeof(kReply1010)); break;
                default: break;
            }
            break;
        }
        default:
            break;
    }
}

void SwitchProBT::sendSubcommandReply(uint8_t* payload, size_t len) {
    if (_dev == nullptr || !_connected || !_started || payload == nullptr || len < 2) return;
    payload[1] = _timer++;
    esp_hidd_dev_input_set(_dev, 0, payload[0], payload + 1, len - 1);
}

uint16_t SwitchProBT::toSwitchAxis12(int value) {
    long mapped = map(value, -32768, 32767, 0, 4095);
    if (mapped < 0) mapped = 0;
    if (mapped > 4095) mapped = 4095;
    return static_cast<uint16_t>(mapped);
}

void SwitchProBT::packStick(uint16_t x, uint16_t y, uint8_t out[3]) {
    out[0] = x & 0xFF;
    out[1] = static_cast<uint8_t>(((x >> 8) & 0x0F) | ((y & 0x0F) << 4));
    out[2] = static_cast<uint8_t>((y >> 4) & 0xFF);
}

uint8_t SwitchProBT::buttonMask(const String& name, uint8_t& byteIndex) {
    byteIndex = 0xFF;

    // Right byte
    if (name.equalsIgnoreCase("Y")) { byteIndex = 0; return 1 << 0; }
    if (name.equalsIgnoreCase("X")) { byteIndex = 0; return 1 << 1; }
    if (name.equalsIgnoreCase("B")) { byteIndex = 0; return 1 << 2; }
    if (name.equalsIgnoreCase("A")) { byteIndex = 0; return 1 << 3; }
    if (name.equalsIgnoreCase("R") || name.equalsIgnoreCase("RB")) { byteIndex = 0; return 1 << 6; }
    if (name.equalsIgnoreCase("ZR") || name.equalsIgnoreCase("RT")) { byteIndex = 0; return 1 << 7; }

    // Shared byte
    if (name.equalsIgnoreCase("minus") || name.equalsIgnoreCase("select")) { byteIndex = 1; return 1 << 0; }
    if (name.equalsIgnoreCase("plus") || name.equalsIgnoreCase("start")) { byteIndex = 1; return 1 << 1; }
    if (name.equalsIgnoreCase("R3") || name.equalsIgnoreCase("rstick")) { byteIndex = 1; return 1 << 2; }
    if (name.equalsIgnoreCase("L3") || name.equalsIgnoreCase("lstick")) { byteIndex = 1; return 1 << 3; }
    if (name.equalsIgnoreCase("home")) { byteIndex = 1; return 1 << 4; }
    if (name.equalsIgnoreCase("capture")) { byteIndex = 1; return 1 << 5; }

    // Left byte
    if (name.equalsIgnoreCase("down") || name.equalsIgnoreCase("ddown")) { byteIndex = 2; return 1 << 0; }
    if (name.equalsIgnoreCase("up") || name.equalsIgnoreCase("dup")) { byteIndex = 2; return 1 << 1; }
    if (name.equalsIgnoreCase("right") || name.equalsIgnoreCase("dright")) { byteIndex = 2; return 1 << 2; }
    if (name.equalsIgnoreCase("left") || name.equalsIgnoreCase("dleft")) { byteIndex = 2; return 1 << 3; }
    if (name.equalsIgnoreCase("L") || name.equalsIgnoreCase("LB")) { byteIndex = 2; return 1 << 6; }
    if (name.equalsIgnoreCase("ZL") || name.equalsIgnoreCase("LT")) { byteIndex = 2; return 1 << 7; }

    return 0;
}

uint8_t SwitchProBT::hatToLeftByteMask(const String& direction) {
    if (direction.equalsIgnoreCase("center") || direction.equalsIgnoreCase("none")) return 0;
    if (direction.equalsIgnoreCase("up")) return 1 << 1;
    if (direction.equalsIgnoreCase("down")) return 1 << 0;
    if (direction.equalsIgnoreCase("left")) return 1 << 3;
    if (direction.equalsIgnoreCase("right")) return 1 << 2;
    if (direction.equalsIgnoreCase("up_left")) return static_cast<uint8_t>((1 << 1) | (1 << 3));
    if (direction.equalsIgnoreCase("up_right")) return static_cast<uint8_t>((1 << 1) | (1 << 2));
    if (direction.equalsIgnoreCase("down_left")) return static_cast<uint8_t>((1 << 0) | (1 << 3));
    if (direction.equalsIgnoreCase("down_right")) return static_cast<uint8_t>((1 << 0) | (1 << 2));
    return 0;
}

bool SwitchProBT::setButton(const String& name, bool pressed) {
    uint8_t byteIndex = 0xFF;
    uint8_t mask = buttonMask(name, byteIndex);
    if (mask == 0 || byteIndex == 0xFF) return false;

    uint8_t* target = nullptr;
    if (byteIndex == 0) target = &_btnRight;
    else if (byteIndex == 1) target = &_btnShared;
    else target = &_btnLeft;

    if (pressed) *target |= mask;
    else *target &= ~mask;
    sendStandardInputReport();
    return true;
}

bool SwitchProBT::setStick(const String& stickId, int x, int y) {
    if (stickId.equalsIgnoreCase("left")) {
        _lx = toSwitchAxis12(x);
        _ly = toSwitchAxis12(y);
    } else if (stickId.equalsIgnoreCase("right")) {
        _rx = toSwitchAxis12(x);
        _ry = toSwitchAxis12(y);
    } else {
        return false;
    }
    sendStandardInputReport();
    return true;
}

bool SwitchProBT::setHat(const String& direction) {
    _btnLeft &= 0xF0;
    _btnLeft |= hatToLeftByteMask(direction);
    sendStandardInputReport();
    return true;
}

void SwitchProBT::sendStandardInputReport() {
    if (_dev == nullptr || !_connected || !_started) return;
    uint8_t payload[12] = {0};
    payload[0] = _timer++;
    payload[1] = 0x8E;      // battery + wired bit pattern used by common emulators
    payload[2] = _btnRight;
    payload[3] = _btnShared;
    payload[4] = _btnLeft;
    packStick(_lx, _ly, &payload[5]);
    packStick(_rx, _ry, &payload[8]);
    payload[11] = 0x08;     // vibrator report marker
    esp_hidd_dev_input_set(_dev, 0, 0x30, payload, sizeof(payload));
}

void SwitchProBT::sendState() {
    sendStandardInputReport();
}

void SwitchProBT::tick() {
    if (!_started) return;

    if (!_connected) {
        if (millis() - _lastDiscoverableRefreshMs > 2000) {
            _lastDiscoverableRefreshMs = millis();
            esp_bt_gap_set_scan_mode(ESP_BT_CONNECTABLE, ESP_BT_GENERAL_DISCOVERABLE);
            _discoverable = true;
        }
        return;
    }

    if (millis() - _lastTickMs < 16) return;
    _lastTickMs = millis();
    sendStandardInputReport();
}

#endif  // defined(CONFIG_IDF_TARGET_ESP32)
