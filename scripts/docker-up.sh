#!/usr/bin/env bash
# Start Postgres stack — requires Docker installed (see install-docker-and-up.sh).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${REPO_ROOT}/docker-compose.yml"

docker_wrap() {
  if docker info >/dev/null 2>&1; then
    docker "$@"
  elif sudo docker info >/dev/null 2>&1; then
    sudo docker "$@"
  else
    echo "Docker is not available. Run: bash ${REPO_ROOT}/scripts/install-docker-and-up.sh"
    exit 1
  fi
}

docker_wrap compose -f "${COMPOSE_FILE}" up -d
docker_wrap compose -f "${COMPOSE_FILE}" ps
