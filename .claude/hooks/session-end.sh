#!/usr/bin/env bash
# SessionEnd — flush buffered turns, close session
set -euo pipefail
case "${CLAUDE_PROJECT_DIR:-}" in */.agent-knowledge/memory*) exit 0;; esac
akw session flush 2>/dev/null
akw session end 2>/dev/null
exit 0
