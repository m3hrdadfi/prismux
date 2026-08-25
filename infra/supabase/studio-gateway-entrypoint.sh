#!/bin/sh
set -eu

: "${DASHBOARD_USERNAME:?DASHBOARD_USERNAME is required}"
: "${DASHBOARD_PASSWORD:?DASHBOARD_PASSWORD is required}"

case "$DASHBOARD_PASSWORD" in
  *[A-Za-z]*) ;;
  *)
    echo "DASHBOARD_PASSWORD must contain at least one letter" >&2
    exit 1
    ;;
esac

mkdir -p /run/prismux-studio-auth
htpasswd -Bbc /run/prismux-studio-auth/.htpasswd "$DASHBOARD_USERNAME" "$DASHBOARD_PASSWORD" >/dev/null
chown nginx:nginx /run/prismux-studio-auth/.htpasswd
chmod 600 /run/prismux-studio-auth/.htpasswd

exec "$@"
