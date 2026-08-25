#!/bin/sh
set -eu

: "${SUPABASE_ANON_KEY_FILE:?SUPABASE_ANON_KEY_FILE is required}"
: "${SUPABASE_SERVICE_KEY_FILE:?SUPABASE_SERVICE_KEY_FILE is required}"

export SUPABASE_ANON_KEY="$(cat "$SUPABASE_ANON_KEY_FILE")"
export SUPABASE_SERVICE_KEY="$(cat "$SUPABASE_SERVICE_KEY_FILE")"
export SUPABASE_SERVICE_ROLE_KEY="$SUPABASE_SERVICE_KEY"

exec docker-entrypoint.sh node apps/studio/server.js
