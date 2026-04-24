#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
ENV_FILE="$PROJECT_ROOT/.env"
IMAGE_NAME="esg-snapshot-pipeline"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[ERROR] Missing .env file at: $ENV_FILE"
  exit 1
fi

if ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
  echo "[INFO] Docker image '$IMAGE_NAME' not found. Building it now..."
  docker build -t "$IMAGE_NAME" "$PROJECT_ROOT"
fi

echo "[INFO] Running pipeline with project root mounted into /app"
echo "[INFO] Host project: $PROJECT_ROOT"

docker run --rm \
  --env-file "$ENV_FILE" \
  --network host \
  -v "$PROJECT_ROOT":/app \
  "$IMAGE_NAME"
