#pragma once

#include <Arduino.h>

#if defined(CONFIG_IDF_TARGET_ESP32)

#include "esp_bt.h"
#include "esp_bt_main.h"
#include "esp_bt_device.h"
#include "esp_gap_bt_api.h"
#include "esp_hid_common.h"
#include "esp_hidd.h"
#include "esp_hidd_api.h"

class SwitchProBT {
public:
    bool begin();
    void end();
    bool isActive() const;
    bool isConnected() const;
    bool isDiscoverable() const;

    bool setButton(const String& name, bool pressed);
    bool setStick(const String& stickId, int x, int y);
    bool setHat(const String& direction);
    void setRawState(uint8_t btnRight, uint8_t btnShared, uint8_t btnLeft,
                     uint16_t lx, uint16_t ly, uint16_t rx, uint16_t ry);
    void sendState();
    void tick();

private:
    static void hiddEventCallback(void* handlerArgs, esp_event_base_t base, int32_t id, void* eventData);
    static void legacyHiddCallback(esp_hidd_cb_event_t event, esp_hidd_cb_param_t* param);
    static void gapEventCallback(esp_bt_gap_cb_event_t event, esp_bt_gap_cb_param_t* param);
    void onHiddEvent(esp_hidd_event_t event, esp_hidd_event_data_t* param);
    void onLegacyHiddEvent(esp_hidd_cb_event_t event, esp_hidd_cb_param_t* param);
    void handleOutputReport(uint8_t reportId, const uint8_t* data, uint16_t len);
    void fillReplyHeader(uint8_t* buf);
    void sendSubcommandReply(uint8_t subcmd, uint8_t ackByte, const uint8_t* data, size_t dataLen);
    void handleSpiFlashRead(uint32_t addr, uint8_t readLen);
    void sendStandardInputReport();
    bool beginLegacyHid();
    void endLegacyHid();

    static void packStick(uint16_t x, uint16_t y, uint8_t out[3]);
    static uint8_t buttonMask(const String& name, uint8_t& byteIndex);
    static uint8_t hatToLeftByteMask(const String& direction);

    esp_hidd_dev_t* _dev = nullptr;
    bool _active = false;
    bool _connected = false;
    bool _discoverable = false;
    bool _started = false;
    bool _legacyHid = false;
    uint8_t _timer = 0;
    unsigned long _lastTickMs = 0;
    unsigned long _lastDiscoverableRefreshMs = 0;

    uint8_t _btnRight = 0;
    uint8_t _btnShared = 0;
    uint8_t _btnLeft = 0;
    uint16_t _lx = 2048;
    uint16_t _ly = 2048;
    uint16_t _rx = 2048;
    uint16_t _ry = 2048;
};

#endif  // defined(CONFIG_IDF_TARGET_ESP32)
