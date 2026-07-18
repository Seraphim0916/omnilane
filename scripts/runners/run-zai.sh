#!/usr/bin/env bash
set -euo pipefail
# omnilane runner: Z.ai GLM (OpenAI-compatible direct API). Thin wrapper over
# run-openai-compat.sh; endpoint and default model live in vendor_api_spec.
# Override the endpoint with ZAI_BASE_URL; needs ZAI_API_KEY.
export OMNILANE_OAI_VENDOR=zai
exec "$(dirname "${BASH_SOURCE[0]}")/run-openai-compat.sh" "$@"
