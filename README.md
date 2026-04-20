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
| DB migrations | Built-in (Python, auto-applied) |
| Testing | `pytest` |
| Type checking | `pyright` |

## Getting Started

**Prerequisites:** Python 3.12+ and [uv](https://docs.astral.sh/uv/getting-started/installation/)

### One-liner Install

Installs CLI + MCP server globally, configures Claude Code hooks for auto-session management:

```bash
git clone git@github.com:inotives/agent-knowledge-wikia.git ~/.agent-knowledge/src && ~/.agent-knowledge/src/install.sh
```

Restart Claude Code. Done.

### Development Setup

For contributing or local development:

```bash
git clone git@github.com:inotives/agent-knowledge-wikia.git
cd agent-knowledge-wikia
uv sync
uv run akw init
```

Add MCP server to project-level `.mcp.json`:
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

**Updating:**
```bash
cd ~/.agent-knowledge/src && git pull && uv tool install --reinstall --from . agent-knowledge
```

## CLI Commands

| Command | Description |
|---|---|
| `akw init` | Initialize data directory and run migrations |
| `akw status` | Show system stats |
| `akw sessions` | List recent sessions |
| `akw session start` | Start a new session (used by hooks) |
| `akw session end` | End the active session (used by hooks) |
| `akw session status` | Show active session info |
| `akw session list` | List sessions for continuation lookup |
| `akw session prompt` | Buffer user prompt from hook (stdin JSON) |
| `akw session turn` | Buffer turn from Stop hook (stdin JSON), flushes every N turns |
| `akw session flush` | Flush buffered turns to database |
| `akw session context` | Print recent session summary |
| `akw search "query"` | Search knowledge from terminal |
| `akw review` | Run LLM-powered daily review (requires `ANTHROPIC_API_KEY`) |
| `akw reindex` | Rebuild search index |
| `akw purge` | Delete old reviewed sessions |

## Auto-Session Management

Sessions are fully automated via four Claude Code hooks:

| Hook | What it does |
|---|---|
| `SessionStart` | Creates a new session, persists `AKW_SESSION_ID` to env |
| `UserPromptSubmit` | Captures user prompt to temp file |
| `Stop` | Pairs prompt + response, buffers turn (flushes every 10 turns) |
| `SessionEnd` | Flushes remaining turns, ends session |

**For Claude Code:** The install script configures hooks globally in `~/.claude/settings.json`. Hooks skip the wiki folder (`~/.agent-knowledge/memory`) to avoid meta-sessions during review.

**For other MCP clients:** The MCP server auto-creates a session on first tool use. No hooks or configuration needed.

**Check session status** (inside a Claude session):
```
! akw session status
```

**Session continuation:** To resume a previous session in a new conversation:
```
akw session list --recent    # find the session ID
# Then tell your agent: "continue session <id>"
```

## Knowledge Review & Promotion

Knowledge matures through three tiers: **session drafts → knowledge drafts → curated knowledge**. Agents propose, humans approve.

### How sessions become knowledge

1. **Session drafts** are auto-generated when a session ends (the agent summarizes before exiting). Missed sessions are caught by the next session's startup review.
2. **Knowledge drafts** are synthesized from session drafts — either by the agent during catch-up review, or via `akw review`.
3. **Curated knowledge** is promoted by the user after review.

### Review options

**Option A — Review with Claude in the wiki folder:**
```bash
cd ~/.agent-knowledge/memory
claude
```
Then ask Claude to review and promote:
> "Review the session drafts and promote anything worth keeping to knowledge"

Claude has MCP tools to read drafts, synthesize patterns, write knowledge pages, and promote — you just approve or steer.

**Option B — Review in Obsidian:**

Point Obsidian at `~/.agent-knowledge/memory/`. Browse `drafts/sessions/`, edit what's useful, then ask an agent to promote via `promote_to_knowledge`.

**Option C — Automated batch review:**
```bash
ANTHROPIC_API_KEY=... akw review
```
Processes all pending session drafts via LLM, generates knowledge drafts, and writes a review report. Can be scheduled as a cron job.

### Promotion flow

```
drafts/sessions/       →  drafts/knowledge/     →  knowledge/
(auto, per session)       (review output)           (curated, searchable)
```

Only curated knowledge in `knowledge/` and `skills/` is indexed for search. Drafts are proposals — they don't pollute search results.

## Documentation

- [Project Specification](docs/SPECS.md) — full design, data model, tools, and workflows

## License

[Apache License 2.0](LICENSE)
