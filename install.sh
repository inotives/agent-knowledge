#!/usr/bin/env bash
# One-liner installer for agent-knowledge
# Usage: curl -fsSL <url>/install.sh | bash
#   or:  ./install.sh (from repo root)
set -euo pipefail

REPO_URL="https://github.com/inotives/agent-knowledge-wikia.git"
INSTALL_DIR="${AKW_INSTALL_DIR:-$HOME/.agent-knowledge/src}"
HOOKS_DIR="$HOME/.agent-knowledge/hooks"
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

# Install as global tool
echo "==> Installing CLI and MCP server..."
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

# Configure Claude Code only if it's already installed (~/.claude exists).
# For other MCP clients (opencode, etc.), the user wires up MCP themselves.
if [ -d "$HOME/.claude" ]; then
    echo "==> Configuring Claude Code MCP server..."
    if [ -f "$CLAUDE_MCP" ]; then
        python3 -c "
import json
with open('$CLAUDE_MCP') as f: data = json.load(f)
data.setdefault('mcpServers', {})['agent-knowledge'] = {'command': 'agent-knowledge-server'}
with open('$CLAUDE_MCP', 'w') as f: json.dump(data, f, indent=2)
print('  Updated $CLAUDE_MCP')
"
    else
        echo '{"mcpServers":{"agent-knowledge":{"command":"agent-knowledge-server"}}}' | python3 -m json.tool > "$CLAUDE_MCP"
        echo "  Created $CLAUDE_MCP"
    fi

    echo "==> Configuring session hooks..."
    python3 -c "
import json, os

settings_path = '$CLAUDE_SETTINGS'
hooks_dir = '~/.agent-knowledge/hooks'

hooks = {
    'SessionStart': [{'hooks': [{'type': 'command', 'command': f'{hooks_dir}/session-start.sh'}]}],
    'UserPromptSubmit': [{'hooks': [{'type': 'command', 'command': f'{hooks_dir}/user-prompt.sh'}]}],
    'Stop': [{'hooks': [{'type': 'command', 'command': f'{hooks_dir}/stop.sh'}]}],
    'SessionEnd': [{'hooks': [{'type': 'command', 'command': f'{hooks_dir}/session-end.sh'}]}],
}

if os.path.exists(settings_path):
    with open(settings_path) as f: data = json.load(f)
    data['hooks'] = hooks
    action = 'Updated'
else:
    data = {'hooks': hooks}
    action = 'Created'

with open(settings_path, 'w') as f: json.dump(data, f, indent=2)
print(f'  {action} {settings_path}')
"
else
    echo "==> Claude Code not detected (~/.claude not found) — skipping client config."
    echo "    Configure your MCP client manually with: command = agent-knowledge-server"
fi

echo ""
echo "Done! agent-knowledge is installed."
echo "  - CLI:        akw status"
echo "  - MCP server: agent-knowledge-server"
echo "  - Hooks:      ~/.agent-knowledge/hooks/ (4 scripts)"
echo "  - Project:    add AKW_PROJECT=name to your repo's .env"
echo ""
echo "Restart Claude Code to activate."
