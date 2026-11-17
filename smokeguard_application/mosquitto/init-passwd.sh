#!/bin/sh
# One-shot init: turn MQTT_USERNAME / MQTT_PASSWORD into a Mosquitto password
# file on the shared volume. The broker container runs as uid 1883 and only
# needs to read it.
set -eu

: "${MQTT_USERNAME:?MQTT_USERNAME is required}"
: "${MQTT_PASSWORD:?MQTT_PASSWORD is required}"

# mosquitto_passwd -c refuses to overwrite an existing file, and this init
# service is recreated whenever `docker compose up` runs.
rm -f /mosquitto/auth/passwd
mosquitto_passwd -b -c /mosquitto/auth/passwd "$MQTT_USERNAME" "$MQTT_PASSWORD"
chown 1883:1883 /mosquitto/auth/passwd
chmod 640 /mosquitto/auth/passwd
