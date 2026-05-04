#!/usr/bin/env bash
# One-liner installer for agent-knowledge
# Usage: curl -fsSL <url>/install.sh | bash
#   or:  ./install.sh (from repo root)
set -euo pipefail

REPO_URL="https://github.com/inotives/agent-knowledge-wikia.git"
INSTALL_DIR="${AKW_INSTALL_DIR:-$HOME/.agent-knowledge/src}"
HOOKS_DIR="$HOME/.agent-knowledge/hooks"
INSTRUCTIONS_TARGET="$HOME/.agent-knowledge/akw-instructions.md"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
CLAUDE_MCP="$HOME/.claude/.mcp.json"

echo "==> Installing agent-knowledge..."

# Bootstrap apt deps on Debian/Ubuntu/Mint if missing
if [ -f /etc/debian_version ] && command -v apt-get &>/dev/null; then
    missing=()
    command -v git &>/dev/null  || missing+=(git)
    command -v curl &>/dev/null || missing+=(curl)
    if [ "${#missing[@]}" -gt 0 ]; then
        echo "==> Installing system packages: ${missing[*]} (sudo required)"
        sudo apt-get update -qq
        sudo apt-get install -y "${missing[@]}"
    fi
fi

# Bootstrap uv if missing
if ! command -v uv &>/dev/null; then
    echo "==> Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # uv installs to ~/.local/bin; ensure it's on PATH for this script
    export PATH="$HOME/.local/bin:$PATH"
    if ! command -v uv &>/dev/null; then
        echo "Error: uv installation completed but binary not found on PATH."
        echo "       Add ~/.local/bin to your shell PATH and re-run this script."
        exit 1
    fi
fi

# Clone or update source
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "==> Updating existing source..."
    git -C "$INSTALL_DIR" pull --quiet
else
    echo "==> Cloning repository..."
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone --quiet "$REPO_URL" "$INSTALL_DIR"
fi

# Install as global tool (CLI only as of EP-00010 — MCP server removed)
echo "==> Installing CLI..."
uv tool install --reinstall --from "$INSTALL_DIR" agent-knowledge 2>&1 | grep -E "^Installed"

# Initialize data directory and database
echo "==> Initializing..."
akw init

# Install hook scripts
echo "==> Installing hooks..."
mkdir -p "$HOOKS_DIR"
cp "$INSTALL_DIR/.claude/hooks/"*.sh "$HOOKS_DIR/"
chmod +x "$HOOKS_DIR/"*.sh
echo "  Installed $(ls "$HOOKS_DIR/"*.sh | wc -l | tr -d ' ') hooks to $HOOKS_DIR"

# Install agent-knowledge session instructions (replaces the MCP `instructions` field).
# Sourced from `akw guide` so the package is the single source of truth.
echo "==> Installing session instructions..."
akw guide > "$INSTRUCTIONS_TARGET"
echo "  Installed $INSTRUCTIONS_TARGET"

# Configure Claude Code only if it's already installed (~/.claude exists).
# For other clients, the user wires up their own integration.
if [ -d "$HOME/.claude" ]; then
    # EP-00010: strip any prior `agent-knowledge` MCP entry — server is gone.
    if [ -f "$CLAUDE_MCP" ]; then
        echo "==> Removing legacy MCP server entry (if present)..."
        python3 -c "
import json
with open('$CLAUDE_MCP') as f: data = json.load(f)
removed = data.get('mcpServers', {}).pop('agent-knowledge', None)
if removed is not None:
    with open('$CLAUDE_MCP', 'w') as f: json.dump(data, f, indent=2)
    print('  Removed agent-knowledge from $CLAUDE_MCP')
else:
    print('  No legacy MCP entry to remove.')
"
    fi

    echo "==> Configuring session hooks..."
    python3 -c "
import json, os

settings_path = '$CLAUDE_SETTINGS'
hooks_dir = '~/.agent-knowledge/hooks'

# Per-event akw hook entries. Each event gets its own group so existing
# user hooks (e.g. for other tools) on the same event are preserved.
akw_entries = {
    'SessionStart':     {'hooks': [{'type': 'command', 'command': f'{hooks_dir}/session-start.sh'}]},
    'UserPromptSubmit': {'hooks': [{'type': 'command', 'command': f'{hooks_dir}/user-prompt.sh'}]},
    'Stop':             {'hooks': [{'type': 'command', 'command': f'{hooks_dir}/stop.sh'}]},
    'SessionEnd':       {'hooks': [{'type': 'command', 'command': f'{hooks_dir}/session-end.sh'}]},
}

if os.path.exists(settings_path):
    with open(settings_path) as f: data = json.load(f)
    action = 'Updated'
else:
    data = {}
    action = 'Created'

hooks_root = data.setdefault('hooks', {})
for event, akw_entry in akw_entries.items():
    existing = hooks_root.setdefault(event, [])
    akw_cmd = akw_entry['hooks'][0]['command']
    # Drop any prior akw entry for this event so re-running install upgrades
    # in place rather than duplicating.
    pruned = []
    for entry in existing:
        cmds = [h.get('command', '') for h in entry.get('hooks', [])]
        if any('agent-knowledge/hooks/' in c for c in cmds):
            continue
        pruned.append(entry)
    pruned.append(akw_entry)
    hooks_root[event] = pruned

with open(settings_path, 'w') as f: json.dump(data, f, indent=2)
print(f'  {action} {settings_path}')
"
else
    echo "==> Claude Code not detected (~/.claude not found) — skipping client config."
    echo "    The CLI works in any shell; configure your harness to invoke 'akw' directly."
fi

echo ""
echo "Done! agent-knowledge is installed."
echo "  - CLI:           akw status"
echo "  - Hooks:         ~/.agent-knowledge/hooks/ (4 scripts)"
echo "  - Instructions:  ~/.agent-knowledge/akw-instructions.md"
echo "  - Project:       add AKW_PROJECT=name to your repo's .env"
echo ""
echo "If you upgraded from a release with the MCP server, restart Claude Code so"
echo "the legacy 'agent-knowledge' MCP entry is fully dropped."
