#!/bin/sh
set -eu

usage() {
  cat <<'EOF'
Usage: ./scripts/flush.sh [--yes] [--images]

Permanently reset this PRISMUX Docker Compose installation.

Removes:
  - Compose containers, including PostgreSQL, Auth, proxy, and migration
  - Compose networks
  - Compose named volumes, including all PostgreSQL and Auth data
  - Locally built Compose images when --images is supplied

Preserves:
  - Source files and .env
  - Unrelated Docker projects, containers, networks, volumes, and images
  - Docker build cache

Options:
  --yes      Skip the interactive confirmation (for automation).
  --images   Also remove locally built images for this Compose project.
  -h, --help Show this help.

After flushing, run `docker compose up --build` to create a clean installation.
EOF
}

assume_yes=false
remove_images=false

while [ "$#" -gt 0 ]; do
  case "$1" in
    --yes)
      assume_yes=true
      ;;
    --images)
      remove_images=true
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown option: %s\n\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)
cd "$project_dir"

if ! command -v docker >/dev/null 2>&1; then
  printf 'Error: docker is not installed or not available on PATH.\n' >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  printf 'Error: the Docker Compose plugin is not available.\n' >&2
  exit 1
fi

docker compose config --quiet

cat <<'EOF'
WARNING: THIS OPERATION CANNOT BE UNDONE.

This will permanently delete this installation's PostgreSQL volume, including:
  - dashboard users and roles
  - machine API keys
  - providers and encrypted credentials
  - routes, pricing, settings, and discovered models
  - request history, statistics, attempts, and audit events

The project source and .env file will be preserved.
EOF

if [ "$remove_images" = true ]; then
  printf 'Locally built Compose images will also be removed.\n'
fi

if [ "$assume_yes" != true ]; then
  printf '\nType FLUSH PRISMUX to continue: '
  IFS= read -r confirmation
  if [ "$confirmation" != 'FLUSH PRISMUX' ]; then
    printf 'Flush cancelled. Nothing was removed.\n'
    exit 1
  fi
fi

printf '\nStopping services and deleting project runtime data...\n'
if [ "$remove_images" = true ]; then
  docker compose down --volumes --remove-orphans --rmi local
else
  docker compose down --volumes --remove-orphans
fi

printf '\nFlush complete. Run `docker compose up --build` for a clean installation.\n'
