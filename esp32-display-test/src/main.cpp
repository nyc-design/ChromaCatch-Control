#include <Arduino.h>
#include <SPI.h>
#include <GxEPD2_BW.h>
#include <Fonts/FreeMonoBold9pt7b.h>

// Wiring provided by user:
// ESP32 -> Display
// 3V3 -> VCC
// GND -> GND
// 18  -> CLK
// 17  -> DIN (MOSI)
// 5   -> CS
// 4   -> DC
// 16  -> RST
// 15  -> BUSY

static constexpr int PIN_CLK  = 18;
static constexpr int PIN_MOSI = 17;
static constexpr int PIN_CS   = 5;
static constexpr int PIN_DC   = 4;
static constexpr int PIN_RST  = 16;
static constexpr int PIN_BUSY = 15;

// 2.13" Waveshare HAT V4 is typically GDEY0213B74 (partial refresh capable).
// If needed, fallback option: GxEPD2_213_BN.
GxEPD2_BW<GxEPD2_213_B74, GxEPD2_213_B74::HEIGHT> display(
  GxEPD2_213_B74(PIN_CS, PIN_DC, PIN_RST, PIN_BUSY)
);

void drawScreen(uint32_t count) {
  display.setFullWindow();
  display.firstPage();
  do {
    display.fillScreen(GxEPD_WHITE);
    display.setTextColor(GxEPD_BLACK);
    display.setFont(&FreeMonoBold9pt7b);

    display.setCursor(4, 22);
    display.print("DISPLAY TEST");

    display.setCursor(4, 50);
    display.print("Count: ");
    display.print(count);

    display.setCursor(4, 78);
    display.print("Millis: ");
    display.print(millis() / 1000);
    display.print("s");

    // Visual markers in corners
    display.fillRect(0, 0, 8, 8, GxEPD_BLACK);
    display.fillRect(display.width() - 8, 0, 8, 8, GxEPD_BLACK);
    display.fillRect(0, display.height() - 8, 8, 8, GxEPD_BLACK);
    display.fillRect(display.width() - 8, display.height() - 8, 8, 8, GxEPD_BLACK);
  } while (display.nextPage());
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n[display-test] boot");

  // No MISO pin used by this panel, so pass -1.
  SPI.begin(PIN_CLK, -1, PIN_MOSI, PIN_CS);

  Serial.println("[display-test] init panel...");
  display.init(115200, true, 50, false);
  display.setRotation(1);

  drawScreen(0);
  Serial.println("[display-test] drew initial screen");
}

void loop() {
  static uint32_t count = 1;
  delay(4000);
  drawScreen(count++);
  Serial.println("[display-test] refreshed");
}
