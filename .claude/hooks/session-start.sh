#!/usr/bin/env bash
# SessionStart — create session, persist ID, read project from .env
set -euo pipefail
case "${CLAUDE_PROJECT_DIR:-}" in */.agent-knowledge/memory*) exit 0;; esac

PROJECT_FLAG=""
if [ -n "${CLAUDE_PROJECT_DIR:-}" ] && [ -f "$CLAUDE_PROJECT_DIR/.env" ]; then
    AKW_PROJECT=$(grep -E '^AKW_PROJECT=' "$CLAUDE_PROJECT_DIR/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")
    [ -n "${AKW_PROJECT:-}" ] && PROJECT_FLAG="--project $AKW_PROJECT"
fi

SESSION_ID=$(akw session start --agent claude $PROJECT_FLAG 2>/dev/null) || exit 0
[ -n "${CLAUDE_ENV_FILE:-}" ] && [ -n "$SESSION_ID" ] && echo "export AKW_SESSION_ID=$SESSION_ID" >> "$CLAUDE_ENV_FILE"
exit 0
