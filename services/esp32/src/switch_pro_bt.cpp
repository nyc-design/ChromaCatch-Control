#include "switch_pro_bt.h"

#if defined(CONFIG_IDF_TARGET_ESP32)

#include <algorithm>
#include <cmath>
#include <cstring>

namespace {

SwitchProBT* g_switchProInstance = nullptr;
constexpr uint16_t kProControllerJoystickMinThreshold = 1874;
constexpr uint16_t kProControllerJoystickMaxThreshold = 320;

// Full 0x30/0x21 report body is 48 bytes (report ID sent separately by HID API).
constexpr size_t kFullReportLen = 48;

// Switch Pro classic-BT HID descriptor
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
    { .data = kSwitchProHidDescriptor, .len = sizeof(kSwitchProHidDescriptor) }
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

static uint8_t g_btAddr[6] = {0};

// ============================================================
// SPI flash data tables
// ============================================================

// 0x6000: Serial number (16 bytes) — 0xFF = blank
static const uint8_t kSpiSerial[16] = {
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
};

// 0x6020: Factory sensor and stick parameters (24 bytes)
static const uint8_t kSpiFactorySensorStick[24] = {0};

// 0x603D: User stick calibration (22 bytes) — 0xFF = no user cal
static const uint8_t kSpiUserStickCal[22] = {
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
};

// 0x6050: Controller color (body + buttons + grips, 13 bytes)
static const uint8_t kSpiColor[13] = {
    0x32, 0x32, 0x32,  // body (grey)
    0xFF, 0xFF, 0xFF,  // buttons (white)
    0xFF, 0xFF, 0xFF,  // left grip
    0xFF, 0xFF, 0xFF,  // right grip
    0xFF,
};

// 0x6080: Factory stick calibration (24 bytes)
static const uint8_t kSpiFactoryStickCal[24] = {
    0x50, 0xFD, 0x00, 0x00, 0xC6, 0x0F,
    0x0F, 0x30, 0x61, 0x96, 0x30, 0xF3,
    0xD4, 0x14, 0x54, 0x41, 0x15, 0x54,
    0xC7, 0x79, 0x9C, 0x33, 0x36, 0x63,
};

// 0x6098: Factory stick cal #2 / deadzone (18 bytes)
static const uint8_t kSpiFactoryStickCal2[18] = {
    0x0F, 0x30, 0x61, 0x96, 0x30, 0xF3,
    0xD4, 0x14, 0x54, 0x41, 0x15, 0x54,
    0xC7, 0x79, 0x9C, 0x33, 0x36, 0x63,
};

// 0x8010: Factory IMU/sensor calibration (24 bytes)
static const uint8_t kSpiFactorySensorCal[24] = {
    0xBE, 0xFF, 0x3E, 0x00, 0xF0, 0x01,
    0x00, 0x40, 0x00, 0x40, 0x00, 0x40,
    0xFE, 0xFF, 0xFE, 0xFF, 0x08, 0x00,
    0xE7, 0x3B, 0xE7, 0x3B, 0xE7, 0x3B,
};

// ============================================================
// Joystick encoding helpers (PA-compatible)
// ============================================================
double unitFromIntAxis(int value) {
    value = std::max(-32768, std::min(32767, value));
    return (value <= 0) ? static_cast<double>(value) / 32768.0 : static_cast<double>(value) / 32767.0;
}

void maxOutMagnitude(double& x, double& y) {
    if (x * x + y * y == 0.0) return;
    if (std::fabs(x) < std::fabs(y)) {
        x /= std::fabs(y);
        y = y < 0 ? -1.0 : 1.0;
    } else {
        y /= std::fabs(x);
        x = x < 0 ? -1.0 : 1.0;
    }
}

uint16_t linearFloatToU12(double f) {
    if (f <= 0) {
        f = std::max(-1.0, f);
        return static_cast<uint16_t>(f * 2048.0 + 2048.0 + 0.5);
    }
    f = std::min(1.0, f);
    return static_cast<uint16_t>(f * 2047.0 + 2048.0 + 0.5);
}

void encodeProControllerStick(int xIn, int yIn, uint16_t& xOut, uint16_t& yOut) {
    double fx = unitFromIntAxis(xIn);
    double fy = unitFromIntAxis(yIn);
    const double magSq = fx * fx + fy * fy;
    fx = std::max(-1.0, std::min(1.0, fx));
    fy = std::max(-1.0, std::min(1.0, fy));

    if (magSq == 0.0) { xOut = 2048; yOut = 2048; return; }
    if (magSq >= 1.0) {
        maxOutMagnitude(fx, fy);
        xOut = linearFloatToU12(fx);
        yOut = linearFloatToU12(fy);
        return;
    }

    const double lo = 1.0 - static_cast<double>(kProControllerJoystickMinThreshold) / 2048.0;
    const double hi = 1.0 - static_cast<double>(kProControllerJoystickMaxThreshold) / 2048.0;
    const double trueMag = std::sqrt(magSq);
    double reportMag = trueMag * (hi - lo) + lo;
    if (reportMag < -1.0) reportMag = -1.0;
    if (reportMag > 1.0) reportMag = 1.0;
    const double scale = reportMag / trueMag;
    xOut = linearFloatToU12(fx * scale);
    yOut = linearFloatToU12(fy * scale);
}

}  // namespace

// ============================================================
// Lifecycle
// ============================================================
bool SwitchProBT::begin() {
    if (_active) return true;

#if (!defined(CONFIG_BT_HID_DEVICE_ENABLED) || !(CONFIG_BT_HID_DEVICE_ENABLED)) && \
    (!defined(CONFIG_BT_HID_ENABLED) || !(CONFIG_BT_HID_ENABLED))
    Serial.println("[SwitchProBT] BT HID device support not enabled.");
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
    _legacyHid = false;

    const uint8_t* btAddr = esp_bt_dev_get_address();
    if (btAddr != nullptr) memcpy(g_btAddr, btAddr, 6);

    esp_bt_controller_status_t ctrlStatus = esp_bt_controller_get_status();
    if (ctrlStatus == ESP_BT_CONTROLLER_STATUS_IDLE) {
        esp_bt_controller_mem_release(ESP_BT_MODE_BLE);
        esp_bt_controller_config_t cfg = BT_CONTROLLER_INIT_CONFIG_DEFAULT();
        esp_err_t err = esp_bt_controller_init(&cfg);
        if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
            Serial.printf("[SwitchProBT] controller_init failed: %d\n", (int)err);
            return false;
        }
    }
    ctrlStatus = esp_bt_controller_get_status();
    if (ctrlStatus == ESP_BT_CONTROLLER_STATUS_INITED) {
        esp_err_t err = esp_bt_controller_enable(ESP_BT_MODE_CLASSIC_BT);
        if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
            Serial.printf("[SwitchProBT] controller_enable failed: %d\n", (int)err);
            return false;
        }
    }

    if (esp_bluedroid_get_status() == ESP_BLUEDROID_STATUS_UNINITIALIZED) {
        esp_err_t err = esp_bluedroid_init();
        if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
            Serial.printf("[SwitchProBT] bluedroid_init failed: %d\n", (int)err);
            return false;
        }
    }
    if (esp_bluedroid_get_status() == ESP_BLUEDROID_STATUS_INITIALIZED) {
        esp_err_t err = esp_bluedroid_enable();
        if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
            Serial.printf("[SwitchProBT] bluedroid_enable failed: %d\n", (int)err);
            return false;
        }
    }

    esp_bt_dev_set_device_name(kBtHidConfig.device_name);
    esp_bt_cod_t cod = {0};
#if defined(ESP_BT_COD_MAJOR_DEV_PERIPHERAL)
    cod.major = ESP_BT_COD_MAJOR_DEV_PERIPHERAL;
#else
    cod.major = 5;
#endif
#if defined(ESP_BT_COD_MINOR_PERIPHERAL_GAMEPAD)
    cod.minor = ESP_BT_COD_MINOR_PERIPHERAL_GAMEPAD;
#elif defined(ESP_BT_COD_MINOR_PERIPHERAL_POINTING)
    cod.minor = ESP_BT_COD_MINOR_PERIPHERAL_POINTING;
#else
    cod.minor = 2;
#endif
    cod.service = 1;
    esp_bt_gap_set_cod(cod, ESP_BT_SET_COD_ALL);

    esp_bt_gap_register_callback(&SwitchProBT::gapEventCallback);
    esp_bt_pin_type_t pinType = ESP_BT_PIN_TYPE_VARIABLE;
    esp_bt_pin_code_t pinCode = {0};
    esp_bt_gap_set_pin(pinType, 0, pinCode);

    esp_err_t err = esp_hidd_dev_init(&kBtHidConfig, ESP_HID_TRANSPORT_BT, &SwitchProBT::hiddEventCallback, &_dev);
    if (err != ESP_OK) {
        Serial.printf("[SwitchProBT] hidd_dev_init failed: %d, trying legacy\n", (int)err);
        _dev = nullptr;
        if (!beginLegacyHid()) return false;
    }

    _active = true;
    _discoverable = true;
    _lastTickMs = millis();
    _lastDiscoverableRefreshMs = millis();
    Serial.println("[SwitchProBT] started OK");
    return true;
}

void SwitchProBT::end() {
    if (!_active) return;
    if (_legacyHid) endLegacyHid();
    else if (_dev != nullptr) esp_hidd_dev_deinit(_dev);
    if (esp_bluedroid_get_status() == ESP_BLUEDROID_STATUS_ENABLED) esp_bluedroid_disable();
    if (esp_bluedroid_get_status() == ESP_BLUEDROID_STATUS_INITIALIZED) esp_bluedroid_deinit();
    if (esp_bt_controller_get_status() == ESP_BT_CONTROLLER_STATUS_ENABLED) esp_bt_controller_disable();
    _dev = nullptr;
    _active = false;
    _connected = false;
    _discoverable = false;
    _started = false;
    g_switchProInstance = nullptr;
}

bool SwitchProBT::isActive() const { return _active; }
bool SwitchProBT::isConnected() const { return _connected; }
bool SwitchProBT::isDiscoverable() const { return _discoverable; }

// ============================================================
// HID event callbacks
// ============================================================
void SwitchProBT::hiddEventCallback(void* handlerArgs, esp_event_base_t base, int32_t id, void* eventData) {
    (void)handlerArgs; (void)base;
    if (g_switchProInstance) g_switchProInstance->onHiddEvent(static_cast<esp_hidd_event_t>(id), static_cast<esp_hidd_event_data_t*>(eventData));
}

void SwitchProBT::legacyHiddCallback(esp_hidd_cb_event_t event, esp_hidd_cb_param_t* param) {
    if (g_switchProInstance) g_switchProInstance->onLegacyHiddEvent(event, param);
}

void SwitchProBT::gapEventCallback(esp_bt_gap_cb_event_t event, esp_bt_gap_cb_param_t* param) {
    if (event == ESP_BT_GAP_PIN_REQ_EVT) {
        esp_bt_pin_code_t pin = {'1', '2', '3', '4'};
        esp_bt_gap_pin_reply(param->pin_req.bda, true, 4, pin);
    } else if (event == ESP_BT_GAP_CFM_REQ_EVT) {
        esp_bt_gap_ssp_confirm_reply(param->cfm_req.bda, true);
    }
}

void SwitchProBT::onHiddEvent(esp_hidd_event_t event, esp_hidd_event_data_t* param) {
    switch (event) {
        case ESP_HIDD_START_EVENT:
            _started = (param && param->start.status == ESP_OK);
            if (_started) { esp_bt_gap_set_scan_mode(ESP_BT_CONNECTABLE, ESP_BT_GENERAL_DISCOVERABLE); _discoverable = true; }
            break;
        case ESP_HIDD_CONNECT_EVENT:
            _connected = (param && param->connect.status == ESP_OK);
            if (_connected) { esp_bt_gap_set_scan_mode(ESP_BT_NON_CONNECTABLE, ESP_BT_NON_DISCOVERABLE); _discoverable = false; }
            break;
        case ESP_HIDD_DISCONNECT_EVENT:
            _connected = false;
            esp_bt_gap_set_scan_mode(ESP_BT_CONNECTABLE, ESP_BT_GENERAL_DISCOVERABLE);
            _discoverable = true;
            break;
        case ESP_HIDD_OUTPUT_EVENT:
            if (param) handleOutputReport(static_cast<uint8_t>(param->output.report_id), param->output.data, param->output.length);
            break;
        case ESP_HIDD_FEATURE_EVENT:
            if (param) handleOutputReport(static_cast<uint8_t>(param->feature.report_id), param->feature.data, param->feature.length);
            break;
        default: break;
    }
}

void SwitchProBT::onLegacyHiddEvent(esp_hidd_cb_event_t event, esp_hidd_cb_param_t* param) {
    switch (event) {
        case ESP_HIDD_INIT_EVT: {
            static esp_hidd_app_param_t appParam;
            static esp_hidd_qos_param_t qos;
            memset(&appParam, 0, sizeof(appParam));
            memset(&qos, 0, sizeof(qos));
            appParam.name = "Pro Controller";
            appParam.description = "Wireless Gamepad";
            appParam.provider = "Nintendo";
            appParam.subclass = ESP_HID_CLASS_GPD;
            appParam.desc_list = kSwitchProHidDescriptor;
            appParam.desc_list_len = sizeof(kSwitchProHidDescriptor);
            esp_bt_hid_device_register_app(&appParam, &qos, &qos);
            break;
        }
        case ESP_HIDD_REGISTER_APP_EVT:
            if (param && param->register_app.status == ESP_HIDD_SUCCESS) {
                _started = true;
                esp_bt_gap_set_scan_mode(ESP_BT_CONNECTABLE, ESP_BT_GENERAL_DISCOVERABLE);
                _discoverable = true;
            }
            break;
        case ESP_HIDD_OPEN_EVT:
            _connected = (param && param->open.conn_status == ESP_HIDD_CONN_STATE_CONNECTED);
            if (_connected) { esp_bt_gap_set_scan_mode(ESP_BT_NON_CONNECTABLE, ESP_BT_NON_DISCOVERABLE); _discoverable = false; }
            break;
        case ESP_HIDD_CLOSE_EVT:
            _connected = false;
            esp_bt_gap_set_scan_mode(ESP_BT_CONNECTABLE, ESP_BT_GENERAL_DISCOVERABLE);
            _discoverable = true;
            break;
        case ESP_HIDD_INTR_DATA_EVT:
            if (param) handleOutputReport(param->intr_data.report_id, param->intr_data.data, param->intr_data.len);
            break;
        case ESP_HIDD_SET_REPORT_EVT:
            if (param) handleOutputReport(param->set_report.report_id, param->set_report.data, param->set_report.len);
            break;
        default: break;
    }
}

bool SwitchProBT::beginLegacyHid() {
    _legacyHid = true;
    esp_err_t regErr = esp_bt_hid_device_register_callback(&SwitchProBT::legacyHiddCallback);
    if (regErr != ESP_OK && regErr != ESP_ERR_INVALID_STATE) return false;
    esp_err_t initErr = esp_bt_hid_device_init();
    if (initErr != ESP_OK && initErr != ESP_ERR_INVALID_STATE) return false;
    return true;
}

void SwitchProBT::endLegacyHid() {
    if (!_legacyHid) return;
    esp_bt_hid_device_unregister_app();
    esp_bt_hid_device_deinit();
    _legacyHid = false;
}

// ============================================================
// Report helpers
// ============================================================
// Fills bytes [0..11] of a 0x30/0x21 report body with current controller state.
void SwitchProBT::fillReplyHeader(uint8_t* buf) {
    buf[0] = _timer++;
    buf[1] = 0x8E;       // full battery, Pro Controller, USB-powered
    buf[2] = _btnRight;
    buf[3] = _btnShared;
    buf[4] = _btnLeft;
    packStick(_lx, _ly, &buf[5]);
    packStick(_rx, _ry, &buf[8]);
    buf[11] = 0x08;       // vibration ACK byte
}

// Sends a 0x21 subcommand reply (48 bytes total).
//  subcmd:  subcommand ID to echo
//  ackByte: 0x80=ACK, 0x82=device-info ACK, 0x90=ACK+data, etc.
//  data/dataLen: optional response payload (placed at byte 14+)
void SwitchProBT::sendSubcommandReply(uint8_t subcmd, uint8_t ackByte, const uint8_t* data, size_t dataLen) {
    if (!_connected || !_started) return;
    uint8_t buf[kFullReportLen];
    memset(buf, 0, sizeof(buf));
    fillReplyHeader(buf);
    buf[12] = ackByte;
    buf[13] = subcmd;
    if (data && dataLen > 0) {
        size_t maxCopy = kFullReportLen - 14;
        memcpy(&buf[14], data, (dataLen < maxCopy) ? dataLen : maxCopy);
    }
    if (_legacyHid) {
        esp_bt_hid_device_send_report(ESP_HIDD_REPORT_TYPE_INTRDATA, 0x21, sizeof(buf), buf);
    } else if (_dev) {
        esp_hidd_dev_input_set(_dev, 0, 0x21, buf, sizeof(buf));
    }
}

// Handle SPI flash read subcommand (0x10).
// Response format: ACK 0x90 0x10, then addr(4LE) + len(1) + data(len).
void SwitchProBT::handleSpiFlashRead(uint32_t addr, uint8_t reqLen) {
    if (reqLen > 29) reqLen = 29;  // max payload in a 0x21 reply

    uint8_t resp[34] = {0};
    resp[0] = addr & 0xFF;
    resp[1] = (addr >> 8) & 0xFF;
    resp[2] = (addr >> 16) & 0xFF;
    resp[3] = (addr >> 24) & 0xFF;
    resp[4] = reqLen;

    const uint8_t* src = nullptr;
    size_t srcLen = 0;
    switch (addr) {
        case 0x6000: src = kSpiSerial;           srcLen = sizeof(kSpiSerial); break;
        case 0x6020: src = kSpiFactorySensorStick; srcLen = sizeof(kSpiFactorySensorStick); break;
        case 0x603D: src = kSpiUserStickCal;     srcLen = sizeof(kSpiUserStickCal); break;
        case 0x6050: src = kSpiColor;            srcLen = sizeof(kSpiColor); break;
        case 0x6080: src = kSpiFactoryStickCal;  srcLen = sizeof(kSpiFactoryStickCal); break;
        case 0x6098: src = kSpiFactoryStickCal2; srcLen = sizeof(kSpiFactoryStickCal2); break;
        case 0x8010: src = kSpiFactorySensorCal; srcLen = sizeof(kSpiFactorySensorCal); break;
        default: break;
    }

    if (src) {
        size_t copyLen = (reqLen < srcLen) ? reqLen : srcLen;
        memcpy(&resp[5], src, copyLen);
        if (copyLen < reqLen) memset(&resp[5 + copyLen], 0xFF, reqLen - copyLen);
    } else {
        memset(&resp[5], 0xFF, reqLen);
    }

    sendSubcommandReply(0x10, 0x90, resp, 5 + reqLen);
}

// ============================================================
// Output report handler (Switch → controller subcommands)
// ============================================================
void SwitchProBT::handleOutputReport(uint8_t reportId, const uint8_t* data, uint16_t len) {
    if (reportId != 0x01 || !data || len < 10) return;
    uint8_t subcmd = data[9];

    switch (subcmd) {
        case 0x01:  // Manual pairing
            sendSubcommandReply(0x01, 0x81, nullptr, 0);
            break;
        case 0x02: {
            // Request device info: FW(2) + type(1) + unk(1) + MAC(6) + unk(1) + color(1)
            uint8_t info[12] = {0};
            info[0] = 0x03; info[1] = 0x48;  // FW version
            info[2] = 0x03;                   // Pro Controller
            info[3] = 0x02;
            memcpy(&info[4], g_btAddr, 6);
            info[10] = 0x03;
            info[11] = 0x01;                  // SPI color available
            sendSubcommandReply(0x02, 0x82, info, sizeof(info));
            break;
        }
        case 0x03:  // Set input report mode
            sendSubcommandReply(0x03, 0x80, nullptr, 0);
            break;
        case 0x04: {
            // Trigger buttons elapsed time (7 × uint16 LE)
            uint8_t td[14];
            uint16_t t = static_cast<uint16_t>((millis() / 10) & 0xFFFF);
            for (int i = 0; i < 7; i++) { td[i*2] = t & 0xFF; td[i*2+1] = (t >> 8) & 0xFF; }
            sendSubcommandReply(0x04, 0x83, td, sizeof(td));
            break;
        }
        case 0x08:  // Set shipment state
            sendSubcommandReply(0x08, 0x80, nullptr, 0);
            break;
        case 0x10: {
            // SPI flash read
            if (len < 15) { sendSubcommandReply(0x10, 0x80, nullptr, 0); break; }
            uint32_t addr = static_cast<uint32_t>(data[10]) | (static_cast<uint32_t>(data[11]) << 8) |
                            (static_cast<uint32_t>(data[12]) << 16) | (static_cast<uint32_t>(data[13]) << 24);
            uint8_t reqLen = (len > 14) ? data[14] : 0;
            handleSpiFlashRead(addr, reqLen);
            break;
        }
        case 0x21:  // Set NFC/IR MCU configuration
        case 0x22:  // Set NFC/IR MCU state
            sendSubcommandReply(subcmd, 0x80, nullptr, 0);
            break;
        case 0x30:  // Set player lights
        case 0x38:  // Set HOME light
        case 0x40:  // Enable IMU
        case 0x41:  // Set IMU sensitivity
        case 0x48:  // Enable vibration
            sendSubcommandReply(subcmd, 0x80, nullptr, 0);
            break;
        default:
            // Generic ACK for any unhandled subcommand — prevents Switch timeouts.
            Serial.printf("[SwitchProBT] unknown subcmd 0x%02X, generic ACK\n", subcmd);
            sendSubcommandReply(subcmd, 0x80, nullptr, 0);
            break;
    }
}

// ============================================================
// Stick packing / button mapping
// ============================================================
void SwitchProBT::packStick(uint16_t x, uint16_t y, uint8_t out[3]) {
    out[0] = x & 0xFF;
    out[1] = static_cast<uint8_t>(((x >> 8) & 0x0F) | ((y & 0x0F) << 4));
    out[2] = static_cast<uint8_t>((y >> 4) & 0xFF);
}

uint8_t SwitchProBT::buttonMask(const String& name, uint8_t& byteIndex) {
    byteIndex = 0xFF;
    // Right byte (byte 3 of 0x30)
    if (name.equalsIgnoreCase("Y")) { byteIndex = 0; return 1 << 0; }
    if (name.equalsIgnoreCase("X")) { byteIndex = 0; return 1 << 1; }
    if (name.equalsIgnoreCase("B")) { byteIndex = 0; return 1 << 2; }
    if (name.equalsIgnoreCase("A")) { byteIndex = 0; return 1 << 3; }
    if (name.equalsIgnoreCase("R") || name.equalsIgnoreCase("RB")) { byteIndex = 0; return 1 << 6; }
    if (name.equalsIgnoreCase("ZR") || name.equalsIgnoreCase("RT")) { byteIndex = 0; return 1 << 7; }
    // Shared byte (byte 4)
    if (name.equalsIgnoreCase("minus") || name.equalsIgnoreCase("select")) { byteIndex = 1; return 1 << 0; }
    if (name.equalsIgnoreCase("plus") || name.equalsIgnoreCase("start")) { byteIndex = 1; return 1 << 1; }
    if (name.equalsIgnoreCase("R3") || name.equalsIgnoreCase("rstick")) { byteIndex = 1; return 1 << 2; }
    if (name.equalsIgnoreCase("L3") || name.equalsIgnoreCase("lstick")) { byteIndex = 1; return 1 << 3; }
    if (name.equalsIgnoreCase("home")) { byteIndex = 1; return 1 << 4; }
    if (name.equalsIgnoreCase("capture")) { byteIndex = 1; return 1 << 5; }
    // Left byte (byte 5)
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
    if (direction.equalsIgnoreCase("up_left")) return (1 << 1) | (1 << 3);
    if (direction.equalsIgnoreCase("up_right")) return (1 << 1) | (1 << 2);
    if (direction.equalsIgnoreCase("down_left")) return (1 << 0) | (1 << 3);
    if (direction.equalsIgnoreCase("down_right")) return (1 << 0) | (1 << 2);
    return 0;
}

// ============================================================
// Public input API
// ============================================================
bool SwitchProBT::setButton(const String& name, bool pressed) {
    uint8_t byteIndex = 0xFF;
    uint8_t mask = buttonMask(name, byteIndex);
    if (mask == 0 || byteIndex == 0xFF) return false;
    uint8_t* target = (byteIndex == 0) ? &_btnRight : (byteIndex == 1) ? &_btnShared : &_btnLeft;
    if (pressed) *target |= mask; else *target &= ~mask;
    sendStandardInputReport();
    return true;
}

bool SwitchProBT::setStick(const String& stickId, int x, int y) {
    if (stickId.equalsIgnoreCase("left")) encodeProControllerStick(x, y, _lx, _ly);
    else if (stickId.equalsIgnoreCase("right")) encodeProControllerStick(x, y, _rx, _ry);
    else return false;
    sendStandardInputReport();
    return true;
}

bool SwitchProBT::setHat(const String& direction) {
    _btnLeft &= 0xF0;
    _btnLeft |= hatToLeftByteMask(direction);
    sendStandardInputReport();
    return true;
}

void SwitchProBT::setRawState(uint8_t btnRight, uint8_t btnShared, uint8_t btnLeft,
                               uint16_t lx, uint16_t ly, uint16_t rx, uint16_t ry) {
    _btnRight = btnRight; _btnShared = btnShared; _btnLeft = btnLeft;
    _lx = lx; _ly = ly; _rx = rx; _ry = ry;
    sendStandardInputReport();
}

// ============================================================
// 0x30 standard input report (48 bytes)
// ============================================================
void SwitchProBT::sendStandardInputReport() {
    if (!_connected || !_started) return;
    uint8_t payload[kFullReportLen];
    memset(payload, 0, sizeof(payload));
    fillReplyHeader(payload);
    // Bytes 12-47 = IMU data (zeros = stationary, accepted by Switch)
    if (_legacyHid) {
        esp_bt_hid_device_send_report(ESP_HIDD_REPORT_TYPE_INTRDATA, 0x30, sizeof(payload), payload);
    } else if (_dev) {
        esp_hidd_dev_input_set(_dev, 0, 0x30, payload, sizeof(payload));
    }
}

void SwitchProBT::sendState() { sendStandardInputReport(); }

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
    // ~15ms cadence (~66Hz), matching PA wireless controller report rate
    if (millis() - _lastTickMs < 15) return;
    _lastTickMs = millis();
    sendStandardInputReport();
}

#endif  // defined(CONFIG_IDF_TARGET_ESP32)
