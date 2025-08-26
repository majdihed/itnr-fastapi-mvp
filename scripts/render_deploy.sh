#!/usr/bin/env bash
set -euo pipefail
: "${RENDER_API_HOOK_URL:?missing}"
curl -fsS -X POST "$RENDER_API_HOOK_URL" && echo "API deploy triggered."
if [ -n "${RENDER_FRONT_HOOK_URL:-}" ]; then
  curl -fsS -X POST "$RENDER_FRONT_HOOK_URL" && echo "Front deploy triggered."
fi
