#!/usr/bin/env bash
# Generate the admin-auth secrets into .env (gitignored), once.
# Idempotent: existing values are never overwritten — delete the line to
# regenerate.  Mirrors scripts/setup_influxdb.sh.
set -euo pipefail

cd "$(dirname "$0")/.."

gen() { python3 -c "import secrets; print(secrets.token_urlsafe(48))"; }

append_if_missing() {
    local key="$1" value="$2"
    if grep -q "^${key}=" .env 2>/dev/null; then
        echo "${key} already set — leaving it as is"
    else
        echo "${key}=${value}" >> .env
        echo "${key} generated"
    fi
}

touch .env
append_if_missing JWT_SECRET "$(gen)"
append_if_missing PASSWORD_RESET_SECRET "$(gen)"

if grep -q '^JWT_EXPIRE_DAYS=' .env 2>/dev/null; then
    echo "JWT_EXPIRE_DAYS already set — leaving it as is"
else
    echo "JWT_EXPIRE_DAYS=7" >> .env
    echo "JWT_EXPIRE_DAYS=7 added"
fi
