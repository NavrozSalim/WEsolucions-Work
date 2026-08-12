#!/usr/bin/env bash
# Shared helpers for deploy scripts. Sourced by server-audit / validate / fix.

# Resolve the public domain used for Host: health checks.
# Prefer DOMAIN_NAME from the environment or .env.prod; fall back to sellerpilothub.com.
resolve_domain_host() {
  local host="${DOMAIN_NAME:-}"
  if [ -z "$host" ] && [ -f .env.prod ]; then
    host=$(grep -E '^DOMAIN_NAME=' .env.prod 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
  fi
  host="${host:-sellerpilothub.com}"
  # Use first host if comma-separated
  host="${host%%,*}"
  host="${host#"${host%%[![:space:]]*}"}"
  host="${host%"${host##*[![:space:]]}"}"
  printf '%s' "$host"
}
