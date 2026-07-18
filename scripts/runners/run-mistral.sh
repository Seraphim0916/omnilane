#!/usr/bin/env bash
set -euo pipefail
# omnilane runner: Mistral (OpenAI-compatible direct API). Thin wrapper over
# run-openai-compat.sh; endpoint and default model live in vendor_api_spec.
# Override the endpoint with MISTRAL_BASE_URL; needs MISTRAL_API_KEY.
export OMNILANE_OAI_VENDOR=mistral
exec "$(dirname "${BASH_SOURCE[0]}")/run-openai-compat.sh" "$@"
