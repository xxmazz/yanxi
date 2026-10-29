#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false
exec .venv/bin/python -m uvicorn backend.app:app \
  --host "${YANXI_HOST:-127.0.0.1}" --port "${YANXI_PORT:-18474}" \
  --proxy-headers --forwarded-allow-ips 127.0.0.1 \
  --limit-concurrency 16 --timeout-keep-alive 5
