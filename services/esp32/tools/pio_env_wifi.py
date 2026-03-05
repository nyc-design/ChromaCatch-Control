import os
from SCons.Script import Import

Import("env")


def c_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


ssid = os.getenv("CC_WIFI_SSID") or os.getenv("WIFI_SSID")
password = os.getenv("CC_WIFI_PASSWORD") or os.getenv("WIFI_PASSWORD")

cpp_defines = []
if ssid:
    cpp_defines.append(("CC_WIFI_SSID", f'\\"{c_literal(ssid)}\\"'))
if password:
    cpp_defines.append(("CC_WIFI_PASSWORD", f'\\"{c_literal(password)}\\"'))

if cpp_defines:
    env.Append(CPPDEFINES=cpp_defines)
