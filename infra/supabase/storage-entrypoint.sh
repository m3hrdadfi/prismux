#!/bin/sh
set -eu

: "${ANON_KEY_FILE:?ANON_KEY_FILE is required}"
: "${SERVICE_KEY_FILE:?SERVICE_KEY_FILE is required}"

export ANON_KEY="$(cat "$ANON_KEY_FILE")"
export SERVICE_KEY="$(cat "$SERVICE_KEY_FILE")"

exec docker-entrypoint.sh node dist/start/server.js
