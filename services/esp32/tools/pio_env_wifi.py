import os
from pathlib import Path
from SCons.Script import Import

Import("env")


def c_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


ssid = os.getenv("CC_WIFI_SSID") or os.getenv("WIFI_SSID")
password = os.getenv("CC_WIFI_PASSWORD") or os.getenv("WIFI_PASSWORD")

cpp_defines = []
if ssid:
    cpp_defines.append(("CC_WIFI_SSID", f'"{c_literal(ssid)}"'))
if password:
    cpp_defines.append(("CC_WIFI_PASSWORD", f'"{c_literal(password)}"'))

if cpp_defines:
    env.Append(CPPDEFINES=cpp_defines)


def _patch_file(path: Path, replacements) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    updated = text
    for src, dst in replacements:
        updated = updated.replace(src, dst)
    if updated != text:
        path.write_text(updated, encoding="utf-8")
        return True
    return False


def _patch_composite_hid_for_idf_werror(*_):
    """
    ESP-IDF builds use -Werror=all. Patch known upstream warnings in
    ESP32-BLE-CompositeHID so dual framework (arduino+espidf) builds stay green.
    """
    libdeps_dir = Path(env.subst("$PROJECT_LIBDEPS_DIR")) / env.subst("$PIOENV")
    base = libdeps_dir / "ESP32-BLE-CompositeHID"
    if not base.exists():
        return

    patched = False
    patched |= _patch_file(
        base / "BaseCompositeDevice.cpp",
        [
            (
                "_autoReport(true),\n    _reportId(reportId),\n    _autoDefer(false)",
                "_autoReport(true),\n    _autoDefer(false),\n    _reportId(reportId)",
            )
        ],
    )
    patched |= _patch_file(
        base / "GamepadConfiguration.cpp",
        [
            ("USAGE_PAGE(1); 0x05;", "USAGE_PAGE(1);"),
            ("HIDINPUT(1); 0x81;", "HIDINPUT(1);"),
        ],
    )
    patched |= _patch_file(
        base / "MouseConfiguration.cpp",
        [
            (
                "MouseConfiguration::MouseConfiguration() : \n"
                "    BaseCompositeDeviceConfiguration(MOUSE_REPORT_ID),\n"
                "    _mouseButtonCount(5),\n"
                "    _whichAxes{true, true, false, false, false, false, true, true}\n"
                "{               \n"
                "}",
                "MouseConfiguration::MouseConfiguration() : \n"
                "    BaseCompositeDeviceConfiguration(MOUSE_REPORT_ID),\n"
                "    _buttonCount(0),\n"
                "    _whichAxes{true, true, false, false, false, false, true, true},\n"
                "    _mouseButtonCount(5)\n"
                "{\n"
                "}",
            )
        ],
    )
    patched |= _patch_file(
        base / "XboxGamepadDevice.cpp",
        [
            (
                "XboxGamepadDevice::XboxGamepadDevice() :\n"
                "    _config(new XboxOneSControllerDeviceConfiguration()),\n"
                "    _extra_input(nullptr),\n"
                "    _callbacks(nullptr)\n"
                "{\n"
                "}",
                "XboxGamepadDevice::XboxGamepadDevice() :\n"
                "    _extra_input(nullptr),\n"
                "    _callbacks(nullptr),\n"
                "    _config(new XboxOneSControllerDeviceConfiguration())\n"
                "{\n"
                "}",
            ),
            (
                "XboxGamepadDevice::XboxGamepadDevice(XboxGamepadDeviceConfiguration* config) :\n"
                "    _config(config),\n"
                "    _extra_input(nullptr),\n"
                "    _callbacks(nullptr)\n"
                "{\n"
                "}",
                "XboxGamepadDevice::XboxGamepadDevice(XboxGamepadDeviceConfiguration* config) :\n"
                "    _extra_input(nullptr),\n"
                "    _callbacks(nullptr),\n"
                "    _config(config)\n"
                "{\n"
                "}",
            ),
            (
                "    memset(&_inputReport, 0, sizeof(XboxGamepadInputReportData));\n",
                "    _inputReport = XboxGamepadInputReportData{};\n",
            ),
        ],
    )
    if patched:
        print("[pio_env_wifi] Patched ESP32-BLE-CompositeHID for ESP-IDF -Werror compatibility")


_patch_composite_hid_for_idf_werror()
