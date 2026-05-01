#!/usr/bin/env bash
# SessionStart — start group, persist GROUP_ID, read project from .env
set -euo pipefail
case "${CLAUDE_PROJECT_DIR:-}" in */.agent-knowledge/memory*) exit 0;; esac

PROJECT_FLAG=""
if [ -n "${CLAUDE_PROJECT_DIR:-}" ] && [ -f "$CLAUDE_PROJECT_DIR/.env" ]; then
    AKW_PROJECT=$(grep -E '^AKW_PROJECT=' "$CLAUDE_PROJECT_DIR/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")
    [ -n "${AKW_PROJECT:-}" ] && PROJECT_FLAG="--project $AKW_PROJECT"
fi

WD_FLAG=""
[ -n "${CLAUDE_PROJECT_DIR:-}" ] && WD_FLAG="--working-dir $CLAUDE_PROJECT_DIR"

GROUP_ID=$(akw group start --agent claude $PROJECT_FLAG $WD_FLAG 2>/dev/null) || exit 0
[ -n "${CLAUDE_ENV_FILE:-}" ] && [ -n "$GROUP_ID" ] && echo "export AKW_GROUP_ID=$GROUP_ID" >> "$CLAUDE_ENV_FILE"
exit 0
