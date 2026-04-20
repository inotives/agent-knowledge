# Agent Knowledge

An MCP server that gives AI agents persistent memory. Conversations compound into a curated knowledge base that makes every future session smarter.

Inspired by [Andrej Karpathy's LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).

## The Problem

Agent sessions are ephemeral. When a session ends, the agent forgets everything — decisions, context, insights. Users re-explain the same things, agents rediscover the same solutions, and valuable knowledge stays buried in chat logs.

## How It Works

Agent Knowledge is an MCP (Model Context Protocol) server that any agent can connect to — Claude, Codex, OpenCode, or any MCP-compatible tool. Knowledge is shared across all connected agents and persists across sessions.

**Knowledge matures through three tiers:**

```
Sessions & Turns → Drafts → Knowledge → Skills & Workflows
     (raw)        (proposed)  (curated)     (actionable)
```

- **Tier 1: Drafts** — Auto-generated session summaries and daily review outputs
- **Tier 2: Knowledge** — Curated pages organized as a memory palace (entities, concepts, patterns)
- **Tier 3: Skills & Workflows** — Domain-specific actionable instructions agents can directly use

Users curate knowledge in **Obsidian**. Agents propose, humans approve.

## Architecture

```
┌──────────────────────┐  ┌────────────────────────┐
│  MCP Server          │  │  CLI (akw)             │
│  (agent-facing)      │  │  (user-facing)         │
└──────────┬───────────┘  └──────────┬─────────────┘
           │                         │
           ▼                         ▼
┌─────────────────────────────────────────────────────┐
│  Core Library                                       │
├─────────────────────────────────────────────────────┤
│  Memory — three-tier markdown system                │
│  Storage — SQLite (sessions) + DuckDB (search)      │
└─────────────────────────────────────────────────────┘
```

- **MCP Server** — agents connect via MCP protocol to log sessions, search knowledge, and write pages
- **CLI (`akw`)** — admin, inspection, and automated daily reviews
- **Core Library** — shared storage, search, and file operations

## Key Features

- **Agent-agnostic** — works with any MCP-compatible agent
- **Cross-agent knowledge sharing** — insights from Claude are available to Codex and vice versa
- **Automatic session capture** — turns logged incrementally, session drafts generated at session end
- **Daily review pipeline** — auto-triggered synthesis of session data into knowledge drafts
- **Three-tier knowledge maturation** — drafts → knowledge → skills & workflows
- **Obsidian-native** — all knowledge is plain markdown, browsable as an Obsidian vault
- **Crash-resilient** — turns saved incrementally, orphaned sessions auto-recovered

## Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3.12+ |
| MCP SDK | `mcp` (official Python MCP SDK) |
| Databases | SQLite (sessions), DuckDB (search) |
| Package manager | `uv` |
| DB migrations | `dbmate-bin` |
| Testing | `pytest` |
| Type checking | `pyright` |

## Getting Started

**Prerequisites:** Python 3.12+ and [uv](https://docs.astral.sh/uv/getting-started/installation/)

```bash
git clone git@github.com:inotives/agent-knowledge.git
cd agent-knowledge
```

### Option A — Local Install (recommended)

Install as global commands. Works from anywhere, no path needed.

```bash
uv tool install --from . agent-knowledge
akw init
```

Add MCP server to Claude Code:
```bash
claude mcp add agent-knowledge agent-knowledge-server
```
Or manually add to `~/.claude/settings.json`:
```json
{
  "mcpServers": {
    "agent-knowledge": {
      "command": "agent-knowledge-server"
    }
  }
}
```

**Updating to latest:**
```bash
cd agent-knowledge
git pull
uv tool install --from . agent-knowledge --force
```

### Option B — Development Setup

Run from the project directory with `uv run`. Use this when developing or contributing.

```bash
uv sync
uv run akw init
```

Add MCP server to project-level `.mcp.json` in the repo root:
```json
{
  "mcpServers": {
    "agent-knowledge": {
      "command": "uv",
      "args": ["run", "agent-knowledge-server"]
    }
  }
}
```
This only activates when working in this project directory.

### Verify

Restart your agent. The 19 tools + 2 prompts will be available.

## CLI Commands

| Command | Description |
|---|---|
| `akw init` | Initialize data directory and run migrations |
| `akw status` | Show system stats |
| `akw sessions` | List recent sessions |
| `akw search "query"` | Search knowledge from terminal |
| `akw review` | Run LLM-powered daily review (requires `ANTHROPIC_API_KEY`) |
| `akw reindex` | Rebuild search index |
| `akw purge` | Delete old reviewed sessions |

## Documentation

- [Project Specification](docs/SPECS.md) — full design, data model, tools, and workflows

## License

[Apache License 2.0](LICENSE)
