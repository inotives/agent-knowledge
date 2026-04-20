#!/usr/bin/env bash
# UserPromptSubmit — capture user prompt for pairing with response
set -euo pipefail
case "${CLAUDE_PROJECT_DIR:-}" in */.agent-knowledge/memory*) exit 0;; esac
akw session prompt 2>/dev/null
exit 0
