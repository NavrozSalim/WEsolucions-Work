#!/usr/bin/env sh
# Build release images and push. Usage:
#   ./scripts/docker-release-push.sh YOUR_DOCKERHUB_USERNAME [tag] [vite_api_url]
# Example:
#   ./scripts/docker-release-push.sh johndoe latest 'http://localhost:8000/api/v1'

set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAMESPACE="${1:?Docker Hub username or org (argument 1)}"
TAG="${2:-latest}"
VITE_API_URL="${3:-http://localhost:8000/api/v1}"

BACKEND_IMAGE="${NAMESPACE}/saas-store-sync-backend:${TAG}"
FRONTEND_IMAGE="${NAMESPACE}/saas-store-sync-frontend:${TAG}"

echo "Building backend: ${BACKEND_IMAGE}"
docker build -f "${REPO_ROOT}/backend/Dockerfile.release" -t "${BACKEND_IMAGE}" "${REPO_ROOT}/backend"

echo "Building frontend: ${FRONTEND_IMAGE} (VITE_API_URL=${VITE_API_URL})"
docker build -f "${REPO_ROOT}/frontend/Dockerfile.release" \
  --build-arg "VITE_API_URL=${VITE_API_URL}" \
  -t "${FRONTEND_IMAGE}" \
  "${REPO_ROOT}/frontend"

echo "Pushing ${BACKEND_IMAGE}"
docker push "${BACKEND_IMAGE}"

echo "Pushing ${FRONTEND_IMAGE}"
docker push "${FRONTEND_IMAGE}"

echo ""
echo "Done. On the other machine use docker-compose.registry.yml with:"
echo "  BACKEND_IMAGE=${BACKEND_IMAGE}"
echo "  FRONTEND_IMAGE=${FRONTEND_IMAGE}"
echo "Then: docker compose -f docker-compose.registry.yml pull && docker compose -f docker-compose.registry.yml up -d"
