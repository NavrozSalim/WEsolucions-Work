# Build release images and push to Docker Hub (or any registry docker login knows).
# Usage:
#   .\scripts\docker-release-push.ps1 -Namespace YOUR_DOCKERHUB_USERNAME
#   .\scripts\docker-release-push.ps1 -Namespace YOUR_DOCKERHUB_USERNAME -Tag v1.0.0 -ViteApiUrl "http://localhost:8000/api/v1"

param(
    [Parameter(Mandatory = $true)]
    [string] $Namespace,
    [string] $Tag = "latest",
    [string] $ViteApiUrl = "http://localhost:8000/api/v1"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path $PSScriptRoot -Parent

$backendImage = "${Namespace}/saas-store-sync-backend:${Tag}"
$frontendImage = "${Namespace}/saas-store-sync-frontend:${Tag}"

Write-Host "Building backend: $backendImage"
docker build -f "$repoRoot/backend/Dockerfile.release" -t $backendImage "$repoRoot/backend"

Write-Host "Building frontend: $frontendImage (VITE_API_URL=$ViteApiUrl)"
docker build -f "$repoRoot/frontend/Dockerfile.release" `
    --build-arg "VITE_API_URL=$ViteApiUrl" `
    -t $frontendImage `
    "$repoRoot/frontend"

Write-Host "Pushing $backendImage"
docker push $backendImage

Write-Host "Pushing $frontendImage"
docker push $frontendImage

Write-Host ""
Write-Host "Done. On the other laptop copy docker-compose.registry.yml + set .env with:"
Write-Host "  BACKEND_IMAGE=$backendImage"
Write-Host "  FRONTEND_IMAGE=$frontendImage"
Write-Host "Then: docker compose -f docker-compose.registry.yml pull && docker compose -f docker-compose.registry.yml up -d"
