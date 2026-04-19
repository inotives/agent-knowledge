# Agent Knowledge — Project Specification

## Problem

Agent sessions are ephemeral. The typical workflow is:

```
Project → Start agent session → Message, code, discuss → End session → Agent forgets everything
```

Two problems:

1. **Sessions are lost.** Every new session starts from zero. The agent has no memory of past decisions, conversations, context, or accumulated knowledge. Users must re-explain context, re-establish conventions, and re-discover solutions that were already found in previous sessions.

2. **Conversations never become knowledge.** Valuable insights — architectural decisions, debugging breakthroughs, domain explanations, convention rationale — are buried in ephemeral chat logs. They are never distilled into reusable knowledge that can inform future development. The same lessons are rediscovered, the same explanations are repeated, the same mistakes are made.

The core reason for this project: **conversations with agents should compound into a persistent, curated knowledge base that makes every future session smarter.**

---

## Overview

An MCP (Model Context Protocol) server that provides agent-agnostic persistent knowledge management. Inspired by [Andrej Karpathy's LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).

Any MCP-compatible agent (Claude, Codex, OpenCode, etc.) can connect to this server to store conversations, build a curated knowledge wiki, and search across it. Because it's an MCP server, knowledge is shared across agent tools — insights captured in a Claude session are available to Codex, OpenCode, or any other connected agent. Switching from one tool to another no longer means losing accumulated knowledge and insights, making migration between agents smooth and hassle-free.

Beyond individual agents, this project serves as a **shared memory module** for agent harness projects (e.g. OpenClaw, Hermes agents). Different harnesses can plug into the same MCP server as their knowledge resource layer, bridging knowledge across independent agent systems. One harness learns something, all harnesses benefit.

---

## Core Workflow

```
┌──────────────────────────────────────────────────────────────┐
│  Session Start                                               │
│  Agent calls session_start → server returns session ID +     │
│  has_pending_review flag + recommended context (matching     │
│  skills, recent knowledge) → if pending review, agent        │
│  runs daily review first → agent reads recommended           │
│  context to bootstrap with curated knowledge                 │
├──────────────────────────────────────────────────────────────┤
│  Daily Review (auto-triggered, only if pending drafts)       │
│  Agent reads pending session drafts → detects cross-session  │
│  patterns → generates knowledge drafts → suggests updates    │
│  to existing knowledge pages                                 │
├──────────────────────────────────────────────────────────────┤
│  During Session                                              │
│  Agent works (code, discuss, debug) → logs turns →           │
│  creates/updates pages with learnings                        │
├──────────────────────────────────────────────────────────────┤
│  Session End (session review — happy path)                    │
│  Agent summarizes its own turns → writes a session draft     │
│  to /memory/drafts/sessions/ → calls session_end             │
│  If agent crashes/closes: turns already saved in SQLite.     │
│  Next session_start auto-closes orphans older than 24hrs,    │
│  agent generates session drafts from their raw turns.        │
├──────────────────────────────────────────────────────────────┤
│  Curation (user in Obsidian, whenever)                       │
│  Review drafts → promote to knowledge → compile into         │
│  skills & workflows                                          │
└──────────────────────────────────────────────────────────────┘
```

**Review triggers:**
- **Session review** — automatic at every session end. The agent summarizes its own turns into a session draft.
- **Orphan recovery** — at session start, server auto-closes sessions older than 24 hours that have no `ended_at` (sets `ended_at` to last turn timestamp). These show up in `review_get_pending` so the agent can generate session drafts from their raw turns.
- **Daily review** — auto-triggered at next session start if there are unreviewed session drafts from previous days. Same-day sessions don't trigger it (still accumulating). Multi-day gaps are covered — all unreviewed days get processed.
- **Manual review** — `akw review` (CLI with API key) as fallback for automation/cron.

The `/memory` folder is the **compounding artifact** — each session feeds into it, daily reviews distill it, and skills & workflows make it actionable. Knowledge accumulates across sessions and across agents.

---

## Knowledge Pipeline: Raw → Draft → Knowledge → Skills & Workflows

Knowledge matures through three tiers:

```
Raw Data (SQLite)     Tier 1: Drafts        Tier 2: Knowledge       Tier 3: Skills & Workflows
─────────────────     ──────────────────     ──────────────────      ──────────────────────────
Sessions              Session review →       Categorized pages       Domain-specific actionable
Turns             →   session drafts     →   organized like a    →   instructions, skills, and
Decisions             Daily review →         memory palace           workflows compiled from
Context               knowledge drafts       (entities, concepts,    accumulated knowledge
                                             patterns)
                      Agent proposes,        User curates in         e.g. python-coding/,
                      user curates           Obsidian                writing-novel/,
                                                                     creating-ai-animation/
```

### Tier 1: Drafts (`/memory/drafts`)

Two types of drafts, generated at different stages:

**Session drafts (`/memory/drafts/sessions/`)** — auto-generated at session end.
1. Agent summarizes its own turns — what was asked, decided, learned
2. Writes a session draft (e.g. `/memory/drafts/sessions/2026-04-19-auth-fix.md`) with metadata recording the originating `session_id` — this links the draft file back to the session record in SQLite
3. This happens every session, no user action needed

**Knowledge drafts (`/memory/drafts/knowledge/`)** — generated during daily review.
1. Agent reads pending session drafts from previous days
2. Detects cross-session patterns — recurring topics, repeated explanations, emerging conventions
3. Synthesizes knowledge drafts (e.g. `/memory/drafts/knowledge/concurrency-patterns.md`)
4. Suggests updates to existing knowledge pages if new info contradicts or extends them
5. Writes a review report to `/memory/drafts/reviews/YYYY-MM-DD.md`

The MCP server provides the data and file operations. The agent does all the thinking.

### Tier 2: Knowledge (`/memory/knowledge`)

Curated, categorized pages — the **memory palace**. Organized by topic into entities, concepts, patterns, and sources. The user promotes drafts into knowledge by reviewing in Obsidian and using `promote_to_knowledge` (or moving files manually). This keeps quality high — agents propose, humans approve.

### Tier 3: Skills & Workflows (`/memory/skills`)

The actionable output. Knowledge is compiled into domain-specific skills, instructions, and workflows that agents can directly use. Each domain has its own set:

```
/memory/skills
├── python-coding/
│   ├── skills.md              # Coding patterns, conventions, techniques
│   └── workflows.md           # Development workflows, testing strategies
├── writing-novel/
│   ├── skills.md              # Writing techniques, style guides
│   └── workflows.md           # Outlining, drafting, revision processes
├── creating-ai-animation/
│   ├── skills.md              # Tools, prompting techniques, pipelines
│   └── workflows.md           # Production workflows
└── ...
```

Knowledge that hasn't been converted into skills and workflows is just reference material. **Tier 3 is where knowledge becomes useful** — it gives agents concrete instructions for how to act in a specific domain.

---

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
│  Memory (/memory) — Three-tier markdown system      │
│  - Tier 1: /drafts     — session + knowledge drafts  │
│  - Tier 2: /knowledge  — curated memory palace      │
│  - Tier 3: /skills     — actionable domain workflows │
├─────────────────────────────────────────────────────┤
│  Storage & Search                                   │
│  - SQLite: sessions, turns, provenance              │
│  - DuckDB: full-text search (knowledge + skills only)│
└─────────────────────────────────────────────────────┘
```

---

## Data Layout

```
~/.agent-knowledge/          # Default data root (configurable)
├── db/
│   ├── sessions.db                # SQLite — sessions, turns, edits
│   └── search.db                  # DuckDB — search index (synced on startup)
└── memory/                        # All markdown tiers (open as Obsidian vault)
    ├── drafts/                    # Tier 1: draft pages pending curation
    │   ├── sessions/              # Session drafts (auto, per session end)
    │   ├── knowledge/             # Knowledge drafts (daily review output)
    │   └── reviews/               # Daily review reports
    ├── knowledge/                 # Tier 2: curated knowledge (memory palace)
    │   ├── entities/              # Entity pages (people, projects, tools)
    │   ├── concepts/              # Concept pages (ideas, patterns, domains)
    │   └── sources/               # Summaries of ingested material
    └── skills/                    # Tier 3: actionable skills & workflows
        ├── python-coding/         # Domain-specific
        ├── writing-novel/
        └── .../
```

---

## Data Model

Actual schemas are managed by dbmate migrations in `db/migrations/`. This section describes the conceptual model.

### SQLite (`sessions.db`) — Sessions & Provenance

**projects** — Scopes sessions to a project context.
- `id`, `name`, `path` (working directory), `tags[]` (domain tags for matching skills), `created_at`, `metadata` (JSON)

**sessions** — One per agent conversation.
- `id`, `project_id` (FK), `agent` (e.g. "claude", "codex"), `type` (e.g. "coding", "research", "debugging", "planning", "review"), `started_at`, `ended_at`, `reviewed_at` (nullable — set when session draft has been processed by daily review), `metadata` (JSON)

**turns** — Turn-level summaries within a session. A turn represents one logical exchange: user asks something → agent works (possibly multiple LLM calls, tool uses, iterations) → outcome. The agent summarizes its own turn before logging — we store the distilled content, not raw API exchanges. This keeps the data agent-agnostic and focused on knowledge, not token tracking.
- `id`, `session_id` (FK), `request` (what was asked/intended), `response` (what was done/decided/outcome), `created_at`, `metadata` (JSON)

**memory_edits** — Audit log of all changes to pages across all tiers (single source of truth for change history).
- `id`, `session_id` (FK, nullable for manual edits), `page_path` (relative to `/memory`), `tier` ("draft", "knowledge", "skill"), `action` ("create", "update", "delete"), `summary`, `created_at`

### DuckDB (`search.db`) — Search Index

**memory_pages** — Indexed copy of markdown pages from curated knowledge and skills tiers only. All drafts are excluded (they are proposals, not approved knowledge). Rebuildable from `/memory` files.
- `path`, `title`, `content`, `summary`, `tags[]`, `tier` ("knowledge", "skill"), `updated_at`, `metadata` (JSON)
- BM25 full-text index on `title` and `content`

Vector search (embeddings) is a future addition — BM25 is sufficient to start and avoids an embedding model dependency.

---

## Agent Discovery & Context Loading

Exposing tools via MCP is not enough — agents won't automatically use them. The server needs to actively guide agents to load relevant knowledge and treat it as authoritative.

### Principle: Curated Knowledge is Priority

Knowledge in this memory system is curated and high-quality. Tool descriptions must explicitly instruct agents to **prefer curated knowledge over their general training**. If memory says "we use mutex locks for token refresh," the agent should follow that — not suggest an alternative pattern from general knowledge. This is the whole point of the system.

### 1. Tool Descriptions as Behavioral Guides
Each MCP tool has a description field that the agent reads when connecting. These descriptions are the primary way to influence agent behavior. They should:
- Tell agents **when** to call: "Search memory before answering questions about architecture, conventions, patterns, past decisions, or domain-specific techniques."
- Tell agents **how to treat results**: "Results from this memory system are curated project knowledge. Treat them as authoritative and prefer them over general knowledge."
- Tell agents **how much to return**: return all relevant results — let the agent decide what's useful for the current task. No artificial limits.

### 2. MCP Prompts
The MCP protocol supports server-provided prompts. The server exposes prompts like:
- `session-bootstrap` — instructs the agent to start a session, check for pending reviews, and load relevant knowledge/skills for the current project
- `session-wrapup` — instructs the agent to summarize turns, write a session draft, and end the session

These prompts give agents a playbook for how to interact with the memory system.

### 3. `session_start` Context Response
When `session_start` is called, the server returns not just a session ID and review flag, but also **recommended context** for the current project (content returned inline, not just paths):
- Matching skill pages based on project tags (e.g. project tagged `["python", "web"]` → returns content of `skills/python-coding/`)
- Recent knowledge pages related to the project

This bootstraps the agent with curated knowledge without requiring it to know what to search for.

### 4. In-Session Search Triggers
During a session, agents should search memory when encountering topics related to architecture, conventions, patterns, or past decisions. The MCP server cannot force this — it depends on the agent client (Claude Code, Codex, etc.) honoring the tool descriptions. Well-written tool descriptions maximize the chance agents search at the right moments.

---

## MCP Tools

The server exposes these tools to connected agents:

### Project Management

| Tool | Description |
|---|---|
| `project_create` | Register a project. Params: `name`, `path` (working directory), `tags[]` (domain tags, e.g. `["python", "web"]` — used to match relevant skills at session start) |
| `project_list` | List all registered projects. |

### Session Management

| Tool | Description |
|---|---|
| `session_start` | Begin a new session. Returns: (1) session ID, (2) `has_pending_review` flag, (3) recommended context — matching skill pages and recent knowledge for the project (content inline). Auto-closes orphaned sessions older than 24 hours. Agent should call `review_get_pending` if flag is true. Params: `project_id`, `agent` (string), `type` (string: "coding", "research", "debugging", "planning", "review") |
| `session_end` | End current session. Params: `session_id` |
| `session_log` | Log turn summaries to a session. Params: `session_id`, `turns[]` (each: `request`, `response`). Agents should log incrementally during the session (not batch at end) to ensure turns survive crashes. Supports single or multiple turns per call. |

### Memory — Read

| Tool | Description |
|---|---|
| `memory_search` | Search across knowledge and skills tiers by query. Returns all relevant ranked results — drafts are excluded (they are transient, not curated). Params: `query` (string), `tier` (optional filter: "knowledge", "skill") |
| `memory_read` | Read a specific page. Params: `path` (relative to `/memory`) |
| `memory_index` | Return a catalog of pages in a tier, queried from DuckDB. Params: `tier` (optional: "knowledge", "skill" — defaults to both). Returns page paths, titles, and summaries. |
| `memory_history` | Return recent edit history from the audit log. Params: `limit` (int, default 20), `page_path` (optional, filter by page) |

### Memory — Write

| Tool | Description |
|---|---|
| `memory_create` | Create a new page in any tier. Params: `path`, `title`, `content`, `tags[]`, `summary` |
| `memory_update` | Update an existing page. Params: `path`, `content`, `summary` (what changed) |
| `memory_delete` | Delete a page. Params: `path`, `reason` |

### Review

| Tool | Description |
|---|---|
| `review_get_pending` | Get all items needing review: (1) orphaned sessions — sessions with turns but no session draft (agent can generate drafts from raw turns), (2) unreviewed session drafts from previous days (for daily review synthesis). Returns both types with their data. Params: `project_id` (optional) |
| `review_complete` | Mark daily review as done. Sets `reviewed_at` on processed sessions, deletes their session draft files from `/memory/drafts/sessions/` (the knowledge has been synthesized into knowledge drafts). Params: `session_ids[]` |

### Promotion

| Tool | Description |
|---|---|
| `promote_to_knowledge` | Move a knowledge draft into curated knowledge. Operates on files in `/memory/drafts/knowledge/` (not session drafts — those are deleted after daily review). Params: `draft_path`, `target_path` (destination in `/knowledge`) |
| `promote_to_skill` | Move a knowledge page into skills. This is a user/human-driven action — the user decides what knowledge is ready to become a skill or workflow. Params: `source_path`, `target_path` (destination in `/skills`) |

### Maintenance

| Tool | Description |
|---|---|
| `maintain_get_stats` | Return structural stats for the memory system: orphaned pages (no inbound links), pages with no updates in N days, missing cross-references, index drift. The calling agent interprets and acts on the report. Params: `stale_days` (int, default 90) |
| `maintain_reindex` | Rebuild the DuckDB search index from `/memory/knowledge` and `/memory/skills` files. |
| `maintain_purge` | Delete sessions, turns, and any remaining associated session draft files older than retention period. Only purges sessions that have been reviewed (`reviewed_at` is set). Params: `older_than_days` (int, default 365) |

---

## CLI (`akw`)

A companion CLI that complements the MCP server. Both the CLI and MCP server share the same **core library** (storage, search, file operations) — the CLI does not go through MCP protocol. The MCP server wraps the core as MCP tools for agents; the CLI wraps it as commands for users. The CLI works independently and does not require the MCP server to be running. For operations that require reasoning (e.g. daily review), the CLI calls an LLM directly.

```
akw <command> [options]
```

### Setup & Operations

| Command | Description |
|---|---|
| `akw init` | Initialize data directory (`~/.agent-knowledge/`), create folder structure, run dbmate migrations. First-time setup. |
| `akw migrate` | Run pending dbmate migrations. Used after pulling project updates with new migration files. |
| `akw status` | Show system stats: registered projects, session counts, pages per tier, index health, last review date. |

### Daily Review

| Command | Description |
|---|---|
| `akw review` | Run the daily review for all pending unreviewed sessions (covers multi-day gaps). Reads pending session drafts, calls a configured LLM to detect patterns, writes knowledge drafts to `/memory/drafts/knowledge/` and a review report to `/memory/drafts/reviews/YYYY-MM-DD.md`. Requires API key. Can be scheduled as a cron job. |

`akw review` is a **fallback for automation**. The primary path is auto-triggered: when an agent starts a new session and there are pending session drafts from previous days, the agent runs the daily review itself. `akw review` exists for users who want cron-based automation or don't use agents daily.

### Maintenance

| Command | Description |
|---|---|
| `akw purge [--older-than 365]` | Delete sessions and turns older than retention period (default: 365 days). Only purges sessions that have been reviewed (`reviewed_at` is set). |
| `akw reindex` | Rebuild DuckDB search index from `/memory/knowledge` and `/memory/skills` files. |

### Inspection

| Command | Description |
|---|---|
| `akw sessions [--project X] [--date Y]` | List recent sessions with summaries. For quick inspection without opening an agent. |
| `akw search "query" [--tier T]` | Search memory from the terminal. Returns ranked results. |

### Design Principle

**CLI for admin and automation, MCP for agents.** The MCP server is the core — agents use it during sessions. The CLI wraps the MCP server for user-facing operations that don't belong inside an agent session.

```
┌─────────────────────┐     ┌─────────────────────┐
│  Agents (Claude,    │     │  CLI (akw)           │
│  Codex, OpenCode)   │     │  - review (+ LLM)    │
│  - session mgmt     │     │  - admin/maintenance  │
│  - memory read/write│     │  - inspection         │
└────────┬────────────┘     └────────┬──────────────┘
         │ (MCP protocol)            │ (direct)
         ▼                           ▼
┌─────────────────────────────────────────────────┐
│  Core Library                                   │
│  Storage + Search + File operations             │
├─────────────────────────────────────────────────┤
│  ▲ wrapped as MCP tools    ▲ wrapped as CLI     │
│  MCP Server (no LLM)       CLI commands         │
└─────────────────────────────────────────────────┘
```

---

## Configuration

All config lives in `pyproject.toml` under `[tool.agent-knowledge]`:

```toml
[tool.agent-knowledge]
data_dir = "~/.agent-knowledge"
search_engine = "bm25"             # "bm25" now, "hybrid" later (bm25 + vector)

[tool.agent-knowledge.llm]
provider = "anthropic"             # LLM provider for CLI review (anthropic, openai, etc.)
model = "claude-sonnet-4-6"        # Model used by `akw review`
# API key read from environment: ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.
```

MCP transport and server name are defined by the MCP client configuration (e.g. `claude_desktop_config.json`), not by the server itself.

---

## Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3.12+ |
| MCP SDK | `mcp` (official Python MCP SDK) |
| SQLite | `sqlite3` (stdlib) |
| DuckDB | `duckdb` |
| Package manager | `uv` |
| Virtual env | `.venv` in project root |
| DB migrations | `dbmate-bin` (>=2.31, Python-packaged binary) |
| Testing | `pytest` |
| Type checking | `pyright` |

### Development Setup

- All commands run from `.venv` virtual env in the project folder (`uv venv && source .venv/bin/activate`)
- `uv` manages dependencies and virtual environment
- `dbmate` handles schema migrations for both SQLite and DuckDB. Migration files live in the project repo (`db/migrations/`), not in the runtime data directory

---

## Constraints & Decisions

1. **Memory pages are plain markdown** — human-readable, git-trackable, editable outside the system.
2. **DuckDB is a search index, not source of truth** — indexes only curated knowledge and skills. Can always be rebuilt from `/memory/knowledge` and `/memory/skills` files. All drafts are excluded.
3. **SQLite is for provenance** — knowing which session/agent created or modified a memory page.
4. **BM25 first, vectors later** — avoids embedding model dependency at start. DuckDB supports both when ready.
5. **No LLM calls inside the MCP server** — the server stores and retrieves. The calling agent does all reasoning (summarization, pattern detection, draft generation). For example, `review_get_pending` returns raw data; the agent decides what to write as drafts.
6. **Stateless tools** — each tool call is self-contained. Session tracking is explicit, not implicit.
7. **365-day retention for conversations** — sessions and turns are kept for 1 year then purged. Daily reviews distill the valuable parts into knowledge, so raw conversations don't need to live forever.
8. **No secrets in knowledge** — the server must sanitize content before storing. Agents may inadvertently log API keys, tokens, passwords, or credentials in turn summaries or draft pages. Safeguards:
   - **Write-time scanning** — `session_log`, `memory_create`, and `memory_update` scan content for common secret patterns (API keys, tokens, connection strings, private keys) and redact or reject them.
   - **Tool descriptions** — instruct agents to never include secrets, credentials, or sensitive tokens in turn summaries or knowledge pages.
   - **Curation layer** — user reviews drafts in Obsidian before promotion, providing a human check for leaked secrets.

---

## Usage Context

- **User role:** The user is primarily a **curator and guide**. Agents do the heavy lifting (capturing sessions, generating drafts, writing wiki pages). The user reviews, curates, steers quality, and occasionally authors pages directly through Obsidian.
- **Human interface:** Users read and curate the knowledge wiki primarily through **Obsidian** as a markdown editor/reader. This reinforces the decision to keep wiki pages as plain markdown with standard linking conventions.

---

## Non-Goals (for now)

- Multi-user / auth — single-user local server
- Web UI — agents are the interface (Obsidian for human curation)
- Real-time sync — file-based, eventual consistency
- Embedding model integration — BM25 is sufficient to start
