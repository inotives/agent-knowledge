# Agent Knowledge

A persistent-memory system for AI agents. Conversations compound into a curated knowledge base that makes every future session smarter.

Inspired by [Andrej Karpathy's LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).

> **Migrating from the MCP server?** As of v0.2.0 (EP-00010), `agent-knowledge` ships as a CLI + session hooks; the MCP server has been removed. See [docs/MCP_TO_CLI_MIGRATION.md](docs/MCP_TO_CLI_MIGRATION.md) for a tool-by-tool mapping and uninstall steps.

## The Problem

Agent sessions are ephemeral. When a session ends, the agent forgets everything — decisions, context, insights. Users re-explain the same things, agents rediscover the same solutions, and valuable knowledge stays buried in chat logs.

## How It Works

Agent Knowledge is a CLI (`akw`) plus a small set of session hooks that any shell-capable agent can drive — Claude Code, Codex, OpenCode, terminal scripts, cron, CI. Knowledge is shared across agents and persists across sessions.

**Knowledge matures through three tiers:**

```
Sessions & Turns → Drafts → Knowledge → Skills & Workflows
     (raw)        (proposed)  (curated)     (actionable)
```

- **Tier 1: Drafts** — Auto-generated session summaries (`1_drafts/sessions/`)
- **Tier 2: Knowledge** — Curated pages organized as a memory palace (entities, concepts, patterns)
- **Tier 3: Intelligences** — Skills and agent personas (`3_intelligences/skills/`, `3_intelligences/agents/`)

The CLI is **capture-only** for the agent surface: it produces session drafts and exposes them to the curator. Synthesis of drafts into `2_knowledges/` and compilation into `3_intelligences/skills/` is a **human activity** performed in the memory folder using whatever editor + LLM the curator prefers (typically Claude Code in `~/.agent-knowledge/memory`, or Obsidian + manual edit). Agents propose; humans curate.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  CLI (akw) + Session Hooks                          │
├─────────────────────────────────────────────────────┤
│  Memory — three-tier markdown system                │
│  Storage — SQLite (sessions) + DuckDB (search)      │
└─────────────────────────────────────────────────────┘
```

- **CLI (`akw`)** — capture, search, discovery, admin, and recovery; the only entry point
- **Session hooks** — drive session lifecycle automatically inside Claude Code (and any harness with shell hooks)
- **Core Library** — shared storage, search, and file operations

The audience boundary is encoded by subcommand group:

| Group | Audience | Examples |
|---|---|---|
| `akw group …`, `akw session …`, `akw search`, `akw skill …`, `akw agent …`, `akw memory read/create`, `akw memory ls/history` | Agent-safe (callable in a session) | session summaries, discovery, draft writes |
| `akw memory update/rm`, `akw maintain …`, `akw project …`, `akw archive`, `akw recover`, `akw reindex`, `akw init` | Curator / admin (humans only) | curation, retention, setup |

## Key Features

- **Agent-agnostic** — works with any agent or harness that can shell out (Claude Code, Codex, OpenCode, terminal, cron, CI)
- **Cross-agent knowledge sharing** — insights captured by one agent are available to all
- **Session summary capture** — each session closes with one durable markdown summary written by `akw session close`
- **Two-hook lifecycle** — `SessionStart` opens a session and returns recent project summaries; `SessionEnd` blocks exit or `/new` until the summary is saved
- **Project-scoped startup context** — `akw session start --json` returns the latest five saved summaries for the resolved project, with full content and excluding the current open session
- **Three-tier knowledge maturation** — drafts → knowledge → intelligences (synthesis is a human activity, not a tool call)
- **Obsidian-native** — all knowledge is plain markdown, browsable as an Obsidian vault
- **Project auto-registration** — unknown working directories are registered as projects and get a project entity page under `2_knowledges/entities/projects/`
- **Structured output** — every CLI command whose result is a structured payload accepts `--json` for programmatic consumers

## Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3.12+ |
| CLI framework | `click` |
| Databases | SQLite (sessions), DuckDB (search) |
| Package manager | `uv` |
| DB migrations | Built-in (Python, auto-applied) |
| Testing | `pytest` |
| Type checking | `pyright` |

## Getting Started

**Prerequisites:** Python 3.12+ and [uv](https://docs.astral.sh/uv/getting-started/installation/)

### One-liner Install

Installs the CLI globally, copies hooks and session-instructions into `~/.agent-knowledge/`, and (if Claude Code is detected) wires up the hooks in `~/.claude/settings.json`:

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

**Updating:**
```bash
cd ~/.agent-knowledge/src && git pull && uv tool install --reinstall --from . agent-knowledge
```

If you previously had the `agent-knowledge` MCP server registered, the install script removes the entry from `~/.claude/.mcp.json` automatically. See [docs/MCP_TO_CLI_MIGRATION.md](docs/MCP_TO_CLI_MIGRATION.md) for full migration details.

## CLI Commands

Full surface of the `akw` CLI. Run `akw <command> --help` for the canonical signature; the tables below are a reference.

**Globals:** `akw -V` / `akw --version` prints the installed version. Set `AKW_DATA_DIR=/path` to override the data directory (default `~/.agent-knowledge`).

### Top-level

| Command | Description |
|---|---|
| `akw init` | Create `~/.agent-knowledge/{db,memory/{0_configs,1_drafts,2_knowledges,3_intelligences}}` and run migrations. Idempotent. |
| `akw status` | Print data dir, project / group / draft / page counts, search-index size, and recovery hints if there are incomplete legacy segments. |
| `akw search QUERY [-t TIER] [--json]` | BM25 search over drafts + curated knowledge. Skills / agents are excluded by default — use `akw skill search` / `akw agent search` for those. `--tier` accepts: `knowledge`, `skill`, `agent`, `session_draft`, `session_archived`. |
| `akw groups [-p PROJECT]` | List groups with start metadata and latest activity. |
| `akw archive DRAFT_PATH` | Move a session draft from `1_drafts/sessions/` to `1_drafts/_archived/sessions__*.md` and record the move in audit history. |
| `akw recover [--dry-run]` | Write `idle_close` markers for orphan segments + stub drafts for closed-no-draft segments. |
| `akw reindex [--force]` | Rebuild the DuckDB search index and reconcile `draft_state` with on-disk drafts. `--force` drift-recovers `draft_state` from frontmatter even when the table is non-empty. |

### `akw session …` — session lifecycle (agent-safe; mostly hook-driven)

| Command | Description |
|---|---|
| `akw session start [-g ID] [-p PROJ] [-a AGENT] [--working-dir DIR] [--create-project-folder] [--json]` | Start a new session. Prints `session_id` to stdout. `--json` returns `{session_id, group_id, started_at, project, latest_summaries}`; `latest_summaries` includes full saved summary content and excludes the current open session. Unknown projects are auto-created. Requires `1_drafts/sessions/<project-slug>/`; pass `--create-project-folder` to create it. |
| `akw session close [--session-id ID] (--content C \| --content-file F) [--summary S] [--json]` | Save the full session summary to `1_drafts/sessions/`, update audit/draft/session indexes, and close the active session. |
| `akw group end [-g ID]` | Deprecated guard. Fails with a reminder to use `akw session close` so sessions cannot end without a summary. |
| `akw session status [--json]` | Show the most recent open session. `--json` returns `{session_id, group_id, segment_start_at, segment_turn_count, agent, project_id, project_name, latest_at}`. |
| `akw session recent [-p PROJECT] [--working-dir DIR] [--limit 5] [--json]` | Return recent closed summaries for the resolved project, newest first, with full markdown content. Includes draft summaries from `1_drafts/sessions/<project>/` and curated summaries from `2_knowledges/entities/projects/<project_id>/sessions/`. Excludes the current open session. |
| `akw group start/status/close` | Deprecated aliases for the matching `akw session ...` commands. |
| `akw group end` | Deprecated guard. Fails with a reminder to use `akw session close`. |
| `akw group turns GROUP_ID [--segment-start ISO]` | Legacy recovery-only inspection of raw turns. |

### `akw memory …` — page operations

| Command | Description |
|---|---|
| `akw memory read PATH [--json]` | Read a page by repo-relative path (e.g. `2_knowledges/architecture/foo.md`). `--json` returns `{path, content}` (raw body, frontmatter included). |
| `akw memory create --path P --title T (--content C \| --content-file F) [--tags TAGS] [--summary S] [--group-id G]` | **Agent-safe.** Create a new draft. `--tags` is a comma-separated list (`"foo,bar"`). Rejects any path under `0_configs/`, `2_knowledges/`, `3_intelligences/`, or `1_drafts/_archived/` with `Cannot write to '<prefix>' — curator-only tier.` |
| `akw memory update PATH (--content C \| --content-file F) [--summary S]` | **Curator.** Replaces the file body wholesale — no merge with existing frontmatter. `--summary` records an edit summary in audit history; it is not written to the page. |
| `akw memory rm PATH [--reason R]` | **Curator.** Hard-deletes curated pages, or moves to the archive-redirect target for archive-aware tiers. **Drafts are rejected** — use `akw archive` instead. |
| `akw memory ls [-t TIER] [--json]` | List indexed pages. `--json` returns `[{path, title, summary, tier}, ...]`. |
| `akw memory history [--page-path P] [--limit N] [--json]` | Recent edit history. `--json` returns `[{id, group_id, page_path, tier, action, summary, created_at}, ...]`. |

### `akw skill …` / `akw agent …` — intelligences discovery

| Command | Description |
|---|---|
| `akw skill search QUERY [-d DOMAIN] [--json]` | Search skill bundles. `-d` filters to one domain (e.g. `engineering`). |
| `akw skill show <domain>/<slug>` (or full path) `[--json]` | Print SKILL.md + bundle manifest. `--json` returns `{path, domain, slug, title, content, resources, scripts, tests}`. |
| `akw agent search QUERY [-d DOMAIN] [--json]` | Search agent personas. |
| `akw agent show <domain>/<slug>` (or full path) `[--json]` | Print agent persona. `--json` returns `{path, domain, slug, title, content}`. |

### `akw project …` — project registry

| Command | Description |
|---|---|
| `akw project new --name N --path P [--tags T1,T2]` | Register a project. Prints the new project ID. |
| `akw project ls [--json]` | List registered projects. `--json` returns `[{id, name, path, tags, created_at, metadata}, ...]`. |

### `akw maintain …` — maintenance

| Command | Description |
|---|---|
| `akw maintain stats [--stale-days N] [--json]` | Page counts per tier, group health, stale-page report. `--json` returns `{pages: {knowledge, skills, agents, drafts}, stale_pages, groups: {total, open, orphaned, closed_no_draft_segments}}`. |
| `akw maintain purge [--older-than-days N]` | Delete archived session drafts older than N days (default 365). |

### Common usage patterns

```bash
# Verify install
akw --version
akw status

# Inspect the active session
akw session status --json | jq

# Search the wiki
akw search "auth middleware" --json
akw search "auth middleware" -t knowledge

# Read / list / inspect history
akw memory read 2_knowledges/architecture/event-bus.md
akw memory ls -t 1_drafts --json
akw memory history --page-path 2_knowledges/architecture/event-bus.md --limit 10

# Close a summarized session (agent-safe)
akw session close --content-file /tmp/session-summary.md --json

# Discover skills / agents
akw skill search "incident response" --json
akw skill show workflow/incident_commander
akw agent show engineering/code-reviewer --json

# Curator: archive a draft, then purge old archives
akw archive 1_drafts/sessions/abc12345-20260504T1530.md
akw maintain purge --older-than-days 90

# Recovery after a crash
akw recover --dry-run
akw recover

# Start and close a summarized session
akw session start --project agent-knowledge --create-project-folder --json
akw session close --content-file /tmp/session-summary.md --json
```

## Auto-Session Management

Sessions are automated via two Claude Code hooks:

| Hook | What it does |
|---|---|
| `SessionStart` | Starts a session, persists `AKW_SESSION_ID` to env, prints `akw-instructions.md`, and surfaces the latest five project summaries |
| `SessionEnd` | Fails exit or `/new` if the open session has not been saved with `akw session close` |

A session is one logical unit of work. The durable memory unit is the session summary saved by `akw session close`.

**For Claude Code:** The install script configures hooks globally in `~/.claude/settings.json`. Hooks skip the wiki folder (`~/.agent-knowledge/memory`) to avoid meta-sessions during curation.

**For other harnesses:** Call `akw session start --json` at session start and require `akw session close --content-file <summary.md>` before ending or opening a new session. The CLI works in any shell.

**Check session status** (inside a Claude session):
```
! akw session status
```

## Curation Workflow

Knowledge matures through three tiers: **session drafts → curated knowledge → intelligences (skills + agents)**. The CLI captures; the curator synthesizes.

### How sessions become knowledge

1. **Session drafts** are written by `akw session close` when the agent summarizes the full session into `1_drafts/sessions/`. The `SessionEnd` hook blocks exit or `/new` until this happens.
2. **Curated knowledge** is **human work**, performed in the memory folder against `1_drafts/sessions/`. There are no `promote_to_knowledge` / `promote_to_skill` commands — promotion is a file-system action.
3. **Skills & agent personas** are likewise compiled by the curator from accumulated knowledge pages.

The contract for frontmatter shapes, source provenance, and house rules lives in `0_configs/rules/knowledge-management.md` inside the deployed memory folder. Point Claude (or any LLM) at that page when synthesizing.

### Startup summaries

`akw session start --json` returns the latest saved summaries for the resolved project:

```json
{
  "latest_summaries": [
    {
      "path": "1_drafts/sessions/demo-abc12345-20260530-1015.md",
      "title": "Session Summary",
      "summary": "Implemented session close workflow",
      "content": "# Session Summary\n..."
    }
  ]
}
```

The list is project-scoped, capped at five by default, includes full markdown content, and excludes the current open session. It merges draft summaries from `1_drafts/sessions/<project-slug>/` with curated/promoted summaries from `2_knowledges/entities/projects/<project_id>/sessions/`.

`akw init` creates the base memory vault, including `1_drafts/sessions/`. The first start for a project checks for `1_drafts/sessions/<project-slug>/`, where the slug defaults to the repo/project name. If missing, the CLI asks you to create it or rerun with `--create-project-folder`.

### Curating with Claude in the wiki folder

```bash
cd ~/.agent-knowledge/memory
claude
```

Then ask Claude:
> "Read knowledge-management.md, then review session drafts. Propose new knowledge pages or updates following the frontmatter conventions."

Claude reads/edits files directly; the CLI does not gate or summarize this work.

### Archive flow

Once a session draft is no longer active work, **move** it (don't delete):

```bash
akw archive 1_drafts/sessions/<group>-<segment>.md
# or move it manually with `git mv` and run `akw reindex`
```

Archived drafts live flat-file under `1_drafts/_archived/sessions__*.md`, are excluded from search, and are deleted by `akw maintain purge` at the retention boundary (365-day default).

### Legacy recovery

Older databases may contain turn-capture segments without a draft. Run:

```bash
akw recover --dry-run   # preview
akw recover             # write idle_close markers + stub drafts
```

Stub drafts carry `recovery_kind: idle_close` (or `closed_no_draft`) in frontmatter. The curator can inspect legacy raw turns with `akw group turns <id> --segment-start <iso>` or archive stubs as-is.

### Search

Only curated `2_knowledges/` and drafts under `1_drafts/` are indexed by the default `akw search` (skills and agent personas have dedicated `akw skill search` / `akw agent search` commands). Archived drafts are excluded from search — they are source material, not authoritative content.

## Documentation

- [Project Specification](docs/SPECS.md) — full design, data model, commands, and workflows
- [MCP → CLI Migration Guide](docs/MCP_TO_CLI_MIGRATION.md) — for users upgrading from the v0.1.x MCP server

## License

[Apache License 2.0](LICENSE)
