#!/usr/bin/env bash
# SessionEnd — block exit/new-session until the agent saves a session summary
set -euo pipefail
case "${CLAUDE_PROJECT_DIR:-}" in */.agent-knowledge/memory*) exit 0;; esac
STATUS_JSON=$(akw session status --json 2>/dev/null || true)
SESSION_ID=$(printf '%s' "$STATUS_JSON" | python3 -c "import json,sys; print((json.load(sys.stdin) or {}).get('session_id') or '')" 2>/dev/null || true)
if [ -n "$SESSION_ID" ]; then
    cat >&2 <<EOF
Agent Knowledge: session summary required before exit or /new.

Summarize the current session and save it with:
  akw session close --session-id "$SESSION_ID" --content-file <summary.md>

The summary must include:
  - Requests And Prompts
  - Work Performed
  - Discoveries And Insights
  - Completed Changes
  - Follow-Up And Next Steps
  - Additional Context

Exit/new-session is blocked until the summary is saved through akw.
EOF
    exit 1
fi
exit 0
