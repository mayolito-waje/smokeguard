#!/usr/bin/env python3
"""Turn the project's .env file into main/mqtt_config.h.

Run automatically by main/CMakeLists.txt during the CMake configure step, so
`idf.py build` always compiles against the current .env. Credentials therefore
live in exactly one place, and that one place is gitignored.

Exits non-zero with an actionable message when a required value is missing or
malformed, which surfaces as a CMake error rather than a device that silently
fails to connect.
"""

from __future__ import annotations

import argparse
import re
import sys

# key, kind, default (None => must be present in .env), allow_empty, help text
SPEC = [
    ("WIFI_SSID",          "string", None,        False, "name of the Wi-Fi network / hotspot"),
    ("WIFI_PASSWORD",      "string", None,        True,  "Wi-Fi password (may be empty for an open network)"),
    ("MQTT_BROKER_HOST",   "string", None,        False, "broker hostname, e.g. my-laptop.local"),
    ("MQTT_BROKER_PORT",   "int",    1883,        False, "broker TCP port"),
    ("MQTT_USERNAME",      "string", "",          True,  "broker username (empty = anonymous)"),
    ("MQTT_PASSWORD",      "string", "",          True,  "broker password"),
    ("MQTT_TOPIC_DATA",    "topic",  "/home/csi/data",   False, "topic for CSI packets"),
    ("MQTT_TOPIC_STATUS",  "topic",  "/home/csi/status", False, "topic for status/heartbeat"),
    ("MQTT_CLIENT_ID",     "string", "",          True,  "empty derives one from the chip MAC"),
    ("MQTT_KEEPALIVE_S",   "int",    60,          False, "MQTT keepalive, seconds"),
    ("NTP_SERVER",         "string", "pool.ntp.org", False, "NTP server for wall-clock sync"),
    ("MDNS_TIMEOUT_MS",    "int",    3000,        False, "mDNS lookup timeout, milliseconds"),
    ("MDNS_HOSTNAME",      "string", "csi-recv",  False, "hostname advertised over mDNS"),
    ("CSI_SENDER_MAC",     "mac",    None,        False, "ESP-NOW sender MAC, aa:bb:cc:dd:ee:ff"),
    ("CSI_CHANNEL",        "int",    0,           False, "channel a fixed-channel sender transmits on (0 = do not check)"),
    ("CSI_QUEUE_DEPTH",    "int",    32,          False, "packets buffered while the broker is slow"),
    ("STATUS_INTERVAL_S",  "int",    30,          False, "heartbeat period, seconds (0 disables)"),
    ("LOG_LEVEL",          "string", "INFO",      False, "NONE/ERROR/WARN/INFO/DEBUG/VERBOSE"),
]

LOG_LEVELS = {"NONE", "ERROR", "WARN", "INFO", "DEBUG", "VERBOSE"}

MAC_RE = re.compile(r"^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")

# Inclusive bounds so an absurd value fails at build time, not at 3am.
RANGES = {
    "MQTT_BROKER_PORT":  (1, 65535),
    "MQTT_KEEPALIVE_S":  (5, 65535),
    "MDNS_TIMEOUT_MS":   (100, 60000),
    "CSI_CHANNEL":       (0, 14),
    "CSI_QUEUE_DEPTH":   (1, 1024),
    "STATUS_INTERVAL_S": (0, 86400),
}


class ConfigError(Exception):
    pass


def parse_env(path: str) -> dict[str, str]:
    """Parse a minimal .env file.

    Only a "#" at the start of a line is a comment, so passwords containing
    "#" survive. Surrounding quotes are stripped. A trailing "\\r" from a
    Windows editor is tolerated.
    """
    values: dict[str, str] = {}
    with open(path, "r", encoding="utf-8-sig") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.rstrip("\r\n")
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                raise ConfigError(
                    f"{path}:{lineno}: expected KEY=value, got: {stripped!r}"
                )
            key, _, value = stripped.partition("=")
            key = key.strip()
            # Strip one layer of matching quotes; otherwise only trim spaces.
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            else:
                value = value.strip()
            if not key:
                raise ConfigError(f"{path}:{lineno}: empty key")
            values[key] = value
    return values


def c_escape(value: str) -> str:
    """Escape for a C string literal."""
    out = []
    for ch in value:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\x{ord(ch):02x}")
        else:
            out.append(ch)
    return "".join(out)


def resolve(env: dict[str, str]) -> dict[str, object]:
    """Apply defaults, coerce types, validate. Raises ConfigError."""
    known = {key for key, _, _, _, _ in SPEC}
    out: dict[str, object] = {}
    problems: list[str] = []

    for key, kind, default, allow_empty, help_text in SPEC:
        if key in env:
            raw = env[key]
        elif default is not None:
            raw = str(default)
        else:
            problems.append(f"  {key} is not set -- {help_text}")
            continue

        if raw == "" and not allow_empty:
            problems.append(f"  {key} must not be empty -- {help_text}")
            continue

        if kind == "int":
            try:
                number = int(raw, 10)
            except ValueError:
                problems.append(f"  {key} must be a whole number, got {raw!r}")
                continue
            low, high = RANGES.get(key, (None, None))
            if low is not None and not (low <= number <= high):
                problems.append(
                    f"  {key} must be between {low} and {high}, got {number}"
                )
                continue
            out[key] = number

        elif kind == "mac":
            if not MAC_RE.match(raw):
                problems.append(
                    f"  {key} must look like aa:bb:cc:dd:ee:ff, got {raw!r}"
                )
                continue
            out[key] = ":".join(part.upper() for part in raw.split(":"))

        elif kind == "topic":
            # "+" and "#" are wildcards and are invalid in a topic you publish to.
            bad = [c for c in "+#" if c in raw]
            if bad:
                problems.append(
                    f"  {key} must not contain the wildcard(s) {', '.join(bad)} -- "
                    f"that is only valid when subscribing, not publishing"
                )
                continue
            out[key] = raw

        else:  # string
            if key == "LOG_LEVEL" and raw.upper() not in LOG_LEVELS:
                problems.append(
                    f"  {key} must be one of {', '.join(sorted(LOG_LEVELS))}, got {raw!r}"
                )
                continue
            out[key] = raw.upper() if key == "LOG_LEVEL" else raw

    for extra in sorted(set(env) - known):
        print(f"warning: ignoring unknown key {extra!r} in .env", file=sys.stderr)

    if problems:
        raise ConfigError(
            "the following values need fixing in .env:\n" + "\n".join(problems)
        )
    return out


def render(values: dict[str, object]) -> str:
    """Render the header.

    Deterministic on purpose: no timestamp and no generation path, so an
    unchanged .env produces a byte-identical header and nothing recompiles.
    """
    lines = [
        "/*",
        " * Generated from .env by tools/gen_config.py -- DO NOT EDIT.",
        " * Edit .env instead, then rebuild.",
        " */",
        "",
        "#pragma once",
        "",
    ]
    for key, kind, _default, _allow_empty, _help in SPEC:
        value = values[key]
        literal = str(value) if kind == "int" else f'"{c_escape(str(value))}"'
        lines.append(f"#define CFG_{key:<18} {literal}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("env_file", help="path to the .env file")
    parser.add_argument("out_header", help="path of the header to write")
    args = parser.parse_args()

    try:
        values = resolve(parse_env(args.env_file))
    except FileNotFoundError:
        print(
            f"error: {args.env_file} does not exist.\n"
            f"       Create it first:  cp .env.example .env",
            file=sys.stderr,
        )
        return 1
    except ConfigError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1

    with open(args.out_header, "w", encoding="utf-8") as fh:
        fh.write(render(values))
    return 0


if __name__ == "__main__":
    sys.exit(main())
