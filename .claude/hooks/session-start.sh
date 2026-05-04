#!/usr/bin/env bash
# SessionStart — start group, persist GROUP_ID, read project from .env, inject akw instructions
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

# EP-00010: print agent-knowledge usage instructions to stderr so Claude Code
# surfaces them as a system reminder. Replaces the MCP server's `instructions`
# field. Prefer the deployed copy; fall back to `akw guide` so this works even
# before install.sh has run.
INSTRUCTIONS_FILE="$HOME/.agent-knowledge/akw-instructions.md"
if [ -f "$INSTRUCTIONS_FILE" ]; then
    cat "$INSTRUCTIONS_FILE" >&2
else
    akw guide >&2 2>/dev/null || true
fi

exit 0
