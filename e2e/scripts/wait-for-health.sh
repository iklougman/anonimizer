#!/usr/bin/env bash
# Polls the compose stack's health until every service is ready, or fails
# loudly after a bounded timeout. Shared by CI and local manual e2e runs
# (see e2e/README.md) so both fail the same clear way if the stack never
# comes up, instead of a vague Playwright timeout inside a test.
set -euo pipefail

TIMEOUT_SECONDS="${WAIT_FOR_HEALTH_TIMEOUT:-180}"
INTERVAL_SECONDS=3
elapsed=0

check() {
  # --max-time bounds each individual curl call -- without it, a connection
  # that's accepted but never responds (observed in practice: a stopped-then-
  # restarting container, or a loaded Docker host) can hang past this
  # script's own TIMEOUT_SECONDS entirely, since the elapsed-time check below
  # only runs *between* check() calls, never inside a hung one.
  curl -sf --max-time 5 http://localhost:8000/health > /dev/null 2>&1 \
    && curl -sf --max-time 5 http://localhost:3000 > /dev/null 2>&1 \
    && curl -sf --max-time 5 "http://localhost:8080/realms/chatgpt-proxy-dev" > /dev/null 2>&1
}

echo "Waiting for backend/frontend/keycloak to become healthy (timeout: ${TIMEOUT_SECONDS}s)..."
until check; do
  if [ "$elapsed" -ge "$TIMEOUT_SECONDS" ]; then
    echo "FAILED: stack did not become healthy within ${TIMEOUT_SECONDS}s" >&2
    docker compose ps >&2
    exit 1
  fi
  sleep "$INTERVAL_SECONDS"
  elapsed=$((elapsed + INTERVAL_SECONDS))
done
echo "Stack is healthy after ${elapsed}s."
