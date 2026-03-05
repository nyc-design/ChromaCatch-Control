#pragma once

#include <Arduino.h>

#if defined(CONFIG_IDF_TARGET_ESP32)

#include "esp_bt.h"
#include "esp_bt_main.h"
#include "esp_bt_device.h"
#include "esp_gap_bt_api.h"
#include "esp_hid_common.h"
#include "esp_hidd.h"

class SwitchProBT {
public:
    bool begin();
    void end();
    bool isActive() const;
    bool isConnected() const;

    bool setButton(const String& name, bool pressed);
    bool setStick(const String& stickId, int x, int y);
    bool setHat(const String& direction);
    void sendState();
    void tick();

private:
    static void hiddEventCallback(void* handlerArgs, esp_event_base_t base, int32_t id, void* eventData);
    void onHiddEvent(esp_hidd_event_t event, esp_hidd_event_data_t* param);
    void handleOutputReport(uint8_t reportId, const uint8_t* data, uint16_t len);
    void sendSubcommandReply(uint8_t* payload, size_t len);
    void sendStandardInputReport();

    static uint16_t toSwitchAxis12(int value);
    static void packStick(uint16_t x, uint16_t y, uint8_t out[3]);
    static uint8_t buttonMask(const String& name, uint8_t& byteIndex);
    static uint8_t hatToLeftByteMask(const String& direction);

    esp_hidd_dev_t* _dev = nullptr;
    bool _active = false;
    bool _connected = false;
    bool _started = false;
    uint8_t _timer = 0;
    unsigned long _lastTickMs = 0;

    uint8_t _btnRight = 0;
    uint8_t _btnShared = 0;
    uint8_t _btnLeft = 0;
    uint16_t _lx = 2048;
    uint16_t _ly = 2048;
    uint16_t _rx = 2048;
    uint16_t _ry = 2048;
};

#endif  // defined(CONFIG_IDF_TARGET_ESP32)
