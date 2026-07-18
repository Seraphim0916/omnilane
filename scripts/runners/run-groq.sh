#!/usr/bin/env bash
set -euo pipefail
# omnilane runner: Groq (OpenAI-compatible direct API). Thin wrapper over
# run-openai-compat.sh; endpoint and default model live in vendor_api_spec.
# Override the endpoint with GROQ_BASE_URL; needs GROQ_API_KEY.
export OMNILANE_OAI_VENDOR=groq
exec "$(dirname "${BASH_SOURCE[0]}")/run-openai-compat.sh" "$@"
