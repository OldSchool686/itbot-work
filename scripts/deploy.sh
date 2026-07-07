#!/bin/bash
# scripts/deploy.sh — one-click deployment for IT Support Bot
set -euo pipefail

echo "=== IT Support Bot Deployment ==="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# Check .env exists
if [ ! -f .env ]; then
    echo "ERROR: .env not found. Copy .env.example to .env and configure."
    exit 1
fi

echo "[1/5] Starting infrastructure services..."
docker compose up -d postgres redis chromadb ollama

echo "[2/5] Waiting for PostgreSQL..."
for i in $(seq 1 30); do
    if docker exec "$(docker compose ps -q postgres | head -1)" pg_isready > /dev/null 2>&1; then
        echo "PostgreSQL is ready."
        break
    fi
    sleep 2
done

echo "[3/5] Starting backend (initial admin created on first start)..."
docker compose up -d backend

echo "[4/5] Pulling AI models..."
OLLAMA_CONTAINER=$(docker compose ps -q ollama | head -1)
if [ -n "$OLLAMA_CONTAINER" ]; then
    docker exec "$OLLAMA_CONTAINER" ollama pull qwen2.5:3b || true
    docker exec "$OLLAMA_CONTAINER" ollama pull nomic-embed-text || true
fi

echo "[5/5] Starting bot service..."
docker compose up -d bot

echo ""
echo "=== Deployment complete ==="
echo "Admin panel: http://localhost:8000/admin"
echo "Health check: curl http://localhost:8000/api/v1/health"
echo "Logs: docker compose logs -f [backend|bot]"
