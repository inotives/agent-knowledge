#!/usr/bin/env bash
# SessionEnd — flush buffered turns, end group's current segment
set -euo pipefail
case "${CLAUDE_PROJECT_DIR:-}" in */.agent-knowledge/memory*) exit 0;; esac
akw group flush 2>/dev/null
akw group end 2>/dev/null
exit 0
