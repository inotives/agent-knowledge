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

- **Tier 1: Drafts** — Auto-generated session summaries (`drafts/sessions/`)
- **Tier 2: Knowledge** — Curated pages organized as a memory palace (entities, concepts, patterns)
- **Tier 3: Skills & Workflows** — Domain-specific actionable instructions agents can directly use

The MCP is **capture-only**: it produces session drafts and exposes them to the curator. Synthesis of drafts into `knowledge/` and compilation into `skills/` is a **human activity** performed in the memory folder using whatever editor + LLM the curator prefers (typically Claude Code in `~/.agent-knowledge/memory`, or Obsidian + manual edit). Agents propose; humans curate.

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

- **MCP Server** — agents connect via MCP protocol to log groups (sessions), search knowledge, and write session drafts
- **CLI (`akw`)** — admin, inspection, archive, and recovery
- **Core Library** — shared storage, search, and file operations

## Key Features

- **Agent-agnostic** — works with any MCP-compatible agent
- **Cross-agent knowledge sharing** — insights from Claude are available to Codex and vice versa
- **Automatic session capture** — turns logged incrementally, session drafts generated at segment end
- **Group/segment lifecycle** — a group is one logical unit of work; continuation reuses the same `group_id` and starts a new segment, so each segment gets its own draft
- **Indexed pending counts** — `group_start` returns counts of unarchived drafts and incomplete segments so the curator can opt in to review
- **Three-tier knowledge maturation** — drafts → knowledge → skills & workflows (synthesis is a human activity, not an MCP tool)
- **Obsidian-native** — all knowledge is plain markdown, browsable as an Obsidian vault
- **Crash-resilient** — turns buffered to disk and persisted incrementally; incomplete segments are recovered on demand via `akw recover`

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
| `akw status` | Show system stats and pending counts (unarchived drafts, incomplete segments) |
| `akw groups` | List recent groups |
| `akw group start` | Start (or continue) a group — used by hooks |
| `akw group end` | End the active segment — used by hooks |
| `akw group status` | Show active group + segment metadata |
| `akw group list` | List groups for continuation lookup |
| `akw group context` | Print recent group/segment summary |
| `akw group prompt` | Buffer user prompt from hook (stdin JSON) |
| `akw group turn` | Buffer turn from Stop hook (stdin JSON); flushes every N turns |
| `akw group flush` | Flush buffered turns to database |
| `akw group turns <id> [--segment-start ISO]` | Print raw turns for a group's segment (used by recovery follow-ups) |
| `akw search "query"` | Search knowledge from terminal |
| `akw archive <draft_path>` | Archive a session draft into `drafts/archived/sessions/` |
| `akw recover [--dry-run]` | Write `idle_close` markers and stub drafts for incomplete segments |
| `akw reindex [--force]` | Rebuild search index and reconcile `draft_state` with on-disk drafts |
| `akw purge [--older-than N]` | Delete archived drafts older than N days (default 365) |

## Auto-Session Management

Groups (sessions) are fully automated via four Claude Code hooks:

| Hook | What it does |
|---|---|
| `SessionStart` | Starts (or continues) a group, persists `AKW_GROUP_ID` to env |
| `UserPromptSubmit` | Captures user prompt to a temp file |
| `Stop` | Pairs prompt + response, buffers turn (flushes every 10 turns) |
| `SessionEnd` | Flushes remaining turns, ends the active segment |

A *group* is one logical unit of work. Continuation reuses the same `group_id` and starts a new *segment* — each segment is one start→end pair on the `turns` table and produces its own draft.

**For Claude Code:** The install script configures hooks globally in `~/.claude/settings.json`. Hooks skip the wiki folder (`~/.agent-knowledge/memory`) to avoid meta-sessions during curation.

**For other MCP clients:** The MCP server auto-creates a group on first tool use. No hooks or configuration needed.

**Check group status** (inside a Claude session):
```
! akw group status
```

**Group continuation:** To resume a previous group in a new conversation:
```
akw group list --recent    # find the group_id
# Then tell your agent: "continue group <id>"
```

## Curation Workflow

Knowledge matures through three tiers: **session drafts → curated knowledge → skills**. The MCP captures; the curator synthesizes.

### How sessions become knowledge

1. **Session drafts** are auto-written by the agent at segment end (the agent summarizes its own turns into `drafts/sessions/<group>-<segment_iso>.md`). Incomplete segments are recovered on demand via `akw recover`, which writes a stub draft the curator can fill in or archive.
2. **Curated knowledge** is **human work**, performed in the memory folder against `drafts/sessions/`. The MCP exposes no `promote_to_knowledge` / `promote_to_skill` tools — promotion is a file-system action, not a tool call.
3. **Skills & workflows** are likewise compiled by the curator from accumulated knowledge pages.

The contract for frontmatter shapes, source provenance, and house rules lives in `knowledge/knowledge-management.md` inside the deployed memory folder. Point Claude (or any LLM) at that page when synthesizing.

### Pending counts (opt-in review)

`group_start` returns indexed counts on every new segment:

```json
{
  "pending": {
    "unarchived_session_drafts": 12,
    "incomplete_segments": 3
  }
}
```

If non-zero, the agent surfaces these in its first reply. The curator decides whether to act — there is no automated synthesis flow.

### Curating with Claude in the wiki folder

```bash
cd ~/.agent-knowledge/memory
claude
```

Then ask Claude:
> "Read knowledge-management.md, then review session drafts. Propose new knowledge pages or updates following the frontmatter conventions."

Claude reads/edits files directly; the MCP layer does not gate or summarize this work.

### Archive flow

Once a session draft is no longer active work, **move** it (don't delete):

```bash
akw archive drafts/sessions/<group>-<segment>.md
# or move it manually with `git mv` and run `akw reindex`
```

Archived drafts live under `drafts/archived/sessions/`, are excluded from search, and are deleted by `akw purge` at the retention boundary (365-day default).

### Recovery

If an agent crashes before writing an end marker, or writes the marker but no draft, the segment is *incomplete*. Run:

```bash
akw recover --dry-run   # preview
akw recover             # write idle_close markers + stub drafts
```

Stub drafts carry `recovery_kind: idle_close` (or `closed_no_draft`) in frontmatter. The curator fills them in from raw turns (`akw group turns <id> --segment-start <iso>`) or archives them as-is.

### Search

Only curated `knowledge/` and `skills/` are indexed for search. Drafts (active and archived) are excluded — they are source material, not authoritative content.

## Documentation

- [Project Specification](docs/SPECS.md) — full design, data model, tools, and workflows

## License

[Apache License 2.0](LICENSE)
