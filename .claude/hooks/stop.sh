#!/usr/bin/env bash
# Stop — buffer turn (user prompt + assistant response)
set -euo pipefail
case "${CLAUDE_PROJECT_DIR:-}" in */.agent-knowledge/memory*) exit 0;; esac
akw session turn 2>/dev/null
exit 0
