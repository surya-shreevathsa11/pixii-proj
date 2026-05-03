#!/usr/bin/env bash
# One-shot: install Docker + Compose on Ubuntu, start project Postgres, print env hints.
# Run:  bash scripts/install-docker-and-up.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${REPO_ROOT}/docker-compose.yml"

if ! command -v docker >/dev/null 2>&1; then
  echo "Installing docker.io and docker-compose-v2 (sudo password required) ..."
  sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io docker-compose-v2
fi

echo "Enabling Docker daemon ..."
sudo systemctl enable --now docker

if ! id -nG "${USER}" | grep -qw docker; then
  echo "Adding ${USER} to the docker group (use newgrp docker, or log out/in, to run docker without sudo)."
  sudo usermod -aG docker "${USER}" || true
fi

echo "Starting Postgres from ${REPO_ROOT} (sudo may prompt once) ..."
sudo docker compose -f "${COMPOSE_FILE}" up -d
sudo docker compose -f "${COMPOSE_FILE}" ps

echo ""
echo "Postgres: localhost:5432  user=pixii  password=pixii  database=amazon_analytics"
echo "In backend/.env set:"
echo "  DATABASE_URL=postgresql://pixii:pixii@localhost:5432/amazon_analytics"
echo ""
echo "Restart the API from backend/ without the SQLite override, for example:"
echo "  cd ${REPO_ROOT}/backend && source .venv/bin/activate && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"
