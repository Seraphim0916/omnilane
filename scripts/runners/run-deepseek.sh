#!/usr/bin/env bash
set -euo pipefail
# omnilane runner: DeepSeek (OpenAI-compatible direct API). Thin wrapper over
# run-openai-compat.sh; endpoint and default model live in vendor_api_spec.
# Override the endpoint with DEEPSEEK_BASE_URL; needs DEEPSEEK_API_KEY.
export OMNILANE_OAI_VENDOR=deepseek
exec "$(dirname "${BASH_SOURCE[0]}")/run-openai-compat.sh" "$@"
