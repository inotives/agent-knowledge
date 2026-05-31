#!/usr/bin/env bash
# SessionStart — start session, persist session id, inject akw instructions and recent summaries
set -euo pipefail
case "${CLAUDE_PROJECT_DIR:-}" in */.agent-knowledge/memory*) exit 0;; esac

PROJECT_FLAG=""
if [ -n "${CLAUDE_PROJECT_DIR:-}" ] && [ -f "$CLAUDE_PROJECT_DIR/.env" ]; then
    AKW_PROJECT=$(grep -E '^AKW_PROJECT=' "$CLAUDE_PROJECT_DIR/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")
    [ -n "${AKW_PROJECT:-}" ] && PROJECT_FLAG="--project $AKW_PROJECT"
fi

WD_FLAG=""
[ -n "${CLAUDE_PROJECT_DIR:-}" ] && WD_FLAG="--working-dir $CLAUDE_PROJECT_DIR"

if ! START_JSON=$(akw session start --agent claude $PROJECT_FLAG $WD_FLAG --json 2>&1); then
    printf '%s\n' "$START_JSON" >&2
    exit 1
fi
SESSION_ID=$(printf '%s' "$START_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null) || SESSION_ID=""
if [ -n "${CLAUDE_ENV_FILE:-}" ] && [ -n "$SESSION_ID" ]; then
    echo "export AKW_SESSION_ID=$SESSION_ID" >> "$CLAUDE_ENV_FILE"
    echo "export AKW_GROUP_ID=$SESSION_ID" >> "$CLAUDE_ENV_FILE"
fi

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

if [ -n "$START_JSON" ]; then
    printf '\n# Recent Agent Knowledge Session Summaries\n\n' >&2
    printf '%s\n' "$START_JSON" >&2
fi

exit 0
