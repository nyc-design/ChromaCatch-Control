import os
from SCons.Script import Import

Import("env")


def c_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


ssid = os.getenv("CC_WIFI_SSID") or os.getenv("WIFI_SSID")
password = os.getenv("CC_WIFI_PASSWORD") or os.getenv("WIFI_PASSWORD")

flags = []
if ssid:
    flags.append(f'-DCC_WIFI_SSID="{c_literal(ssid)}"')
if password:
    flags.append(f'-DCC_WIFI_PASSWORD="{c_literal(password)}"')

if flags:
    env.Append(BUILD_FLAGS=flags)
