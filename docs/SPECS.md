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

A CLI (`akw`) plus session hooks that provide agent-agnostic persistent knowledge management. Inspired by [Andrej Karpathy's LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).

Any agent that can shell out (Claude Code, Codex, OpenCode, terminal scripts, cron, CI) can drive the CLI to store conversations, build a curated knowledge wiki, and search across it. Knowledge is shared across agent tools — insights captured in a Claude session are available to Codex, OpenCode, or any other harness. Switching from one tool to another no longer means losing accumulated knowledge and insights, making migration between agents smooth and hassle-free.

Beyond individual agents, this project serves as a **shared memory module** for agent harness projects (e.g. OpenClaw, Hermes agents). Different harnesses can plug into the same CLI as their knowledge resource layer, bridging knowledge across independent agent systems. One harness learns something, all harnesses benefit.

> The project shipped as an MCP server in v0.1.x. EP-00010 deprecated that transport in favour of a pure CLI. See [MCP_TO_CLI_MIGRATION.md](MCP_TO_CLI_MIGRATION.md) for the tool-by-tool mapping.

---

## Scope: capture only

EP-00005 establishes the boundary: **the agent surface captures conversation activity into session drafts; humans synthesize drafts into knowledge.** There is no LLM, no synthesis path, no promotion command. Synthesis happens outside the agent loop — typically by running Claude Code (or any editor + LLM) inside `~/.agent-knowledge/memory/` against the session drafts. The contract for that work lives in `0_configs/rules/knowledge-management.md`.

This is a deliberate scope cut: earlier drafts included LLM synthesis and a promotion pipeline. Those are removed because the curator can already invoke an LLM directly against the file system; the genuinely valuable structural work is the capture lifecycle.

## Core Workflow

```
┌──────────────────────────────────────────────────────────────┐
│  Session Start                                               │
│  Agent calls group_start → CLI opens a session, resolves or  │
│  creates the project, and returns the latest five saved      │
│  summaries for that project with full content.               │
├──────────────────────────────────────────────────────────────┤
│  During Session                                              │
│  Agent works (code, discuss, debug). Raw prompt/response     │
│  turns are not logged by default.                            │
├──────────────────────────────────────────────────────────────┤
│  Session Close                                               │
│  Agent summarizes the full session and calls group_close.    │
│  CLI writes one draft under 1_drafts/sessions/ and records   │
│  session_summaries, draft_state, and memory_edits rows.      │
├──────────────────────────────────────────────────────────────┤
│  Recovery (curator-driven)                                   │
│  SessionEnd Guard                                            │
│  If the user exits or starts /new while a session is open,   │
│  the hook warns and fails until `akw session close` saves the  │
│  summary.                                                    │
├──────────────────────────────────────────────────────────────┤
│  Curation (human, in the memory folder)                      │
│  Curator runs Claude Code (or any editor + LLM) inside       │
│  ~/.agent-knowledge/memory/, reads session drafts, writes    │
│  2_knowledges/ pages following the conventions in            │
│  0_configs/rules/knowledge-management.md, archives consumed  │
│  drafts via `akw archive` (or `git mv` + `akw reindex`).     │
└──────────────────────────────────────────────────────────────┘
```

**Lifecycle markers (`turns.kind`):**
- `start` — opens a segment (one row per `group_start`, also written when idle-close-on-stale rolls a stale group into a new segment).
- `turn` — a logical exchange within the segment.
- `end` — clean close, written by `group_end`.
- `idle_close` — close written lazily when a stale group is touched, or by `akw recover` for orphans.

A segment is one `start` → `end`/`idle_close` pair for a `group_id`. A group may have N segments over time.

**Recovery / archive triggers:**
- **Segment summary** — automatic at `group_end`. The agent summarizes its current segment's turns and writes a draft.
- **Idle-close** — lazy at next-touch (`group_log` / `group_start`) when latest turn is older than 30min. Threshold is config-driven (`AKW_IDLE_CLOSE_MINUTES`, default 30).
- **`akw recover`** — explicit CLI for orphan groups (>24h open, no end marker) and closed-no-draft segments. Writes stub drafts; never auto-runs.
- **`akw archive`** — explicit CLI to move a draft into `1_drafts/_archived/sessions__<basename>.md` (flat-file, prefix-marked). Never deletes.
- **`maintain_purge`** — deletes archived drafts at the retention boundary (default 365 days). Active drafts and curated pages are never auto-purged.

The `/memory` folder is the **compounding artifact** — each segment feeds into it, the curator distills it, and skills & workflows make it actionable.

---

## Knowledge Pipeline: Raw → Draft → Knowledge → Intelligences

Knowledge matures through three numbered tiers. Numeric prefixes on the tier folders are load-bearing — they encode the **promotion order** (1 → 2 → 3). `0_configs/` is the wiki contract (templates and rules); it is *not* a tier and sits outside the promotion flow.

```
Raw Data (SQLite)     Tier 1: 1_drafts/        Tier 2: 2_knowledges/    Tier 3: 3_intelligences/
─────────────────     ───────────────────       ──────────────────       ─────────────────────────
Groups                Auto + agent-staged       Curated, durable         Skills (`skills/`)
Turns             →   session drafts        →   knowledge pages,    →   + agent personas
(marker turns)        + draft staging dirs      memory palace            (`agents/`)
Memory edits          for promotion targets     organized by topic
                                                                         e.g. skills/python-coding/,
                      Agents capture + stage    Curator authors —        agents/engineering/sre.md
                      drafts via the CLI.       no programmatic
                      No automated synthesis.   promotion path.
```

### Tier 1: Drafts (`1_drafts/`)

Inside `1_drafts/`, the nested numeric prefix on each subfolder signals the *promotion target* — where a draft will land once curated. Sessions are agent-written via `akw memory create`; the rest are agent-staged drafts during research/note/preference work.

```
1_drafts/
├── sessions/         # Agent-written session summaries (one per segment)
├── 2_knowledges/     # Drafts targeting Tier 2 knowledge pages
├── 2_notes/          # Ad-hoc notes (will promote to 2_knowledges/notes/)
├── 2_researches/     # Research outputs (will promote to 2_knowledges/researches/)
├── 3_skills/         # Drafts targeting Tier 3 skills
├── reviews/          # Curator review notes (excluded from search)
└── _archived/        # Flat-file archive: sessions__<basename>.md
```

**Session drafts (`1_drafts/sessions/`)** — generated by `akw session close`.
1. Agent summarizes the full session — what was asked, done, decided, learned, changed, and what remains.
2. `akw session close` writes the draft, redacts detected secrets, records audit/session indexes, and closes the session.
3. `SessionEnd` fails exit or `/new` if the current session has not been closed with a summary.

**Stub drafts (also under `1_drafts/sessions/`)** — written by `akw recover` for incomplete segments. Frontmatter carries `recovery_kind: idle_close` (or `closed_no_draft`) and `turn_count`. Body is a placeholder pointing to recovery actions; the curator either fills in a real summary from raw turns or archives as-is.

**Archived drafts (`1_drafts/_archived/sessions__*.md`)** — flat-file with `sessions__` filename prefix (no subfolder). Drafts move here via `akw archive` once they're no longer active work. Indexed under the `session_archived` tier as a long-term provenance trail; deleted by `akw maintain purge` at the retention boundary.

The CLI provides the data and file operations. The agent summarizes its own turns; the curator synthesizes across drafts. There is no programmatic synthesis path.

### Tier 2: Knowledge (`2_knowledges/`)

Curated, categorized pages — the **memory palace**. Organized by topic into `concepts/`, `entities/`, `notes/`, `preferences/`, `researches/`, `sources/`. The curator authors knowledge pages by reading session drafts and writing/editing files directly (typically using Claude Code or Obsidian inside the memory folder). Frontmatter conventions and house rules live in `0_configs/rules/knowledge-management.md` — point any synthesis LLM at that page first.

The CLI **does not write** to `2_knowledges/` by default: `akw memory create` and `akw memory update` reject paths under `2_knowledges/`, `3_intelligences/`, `0_configs/`, and `1_drafts/_archived/`. Curation is a trusted human action with file-system access.

**Carve-out — `2_knowledges/preferences/`.** User-preference pages are agent-writable. Agents can call `akw memory create` / `akw memory update` directly under this prefix. `akw memory rm` on a carve-out path *archives* (moves to `2_knowledges/_archived/preferences/<name>.md`) instead of unlinking, preserving the audit trail. The carve-out list lives in `paths.WRITE_ALLOWED_OVERRIDES`; new carve-outs follow the same delete-redirects-to-archive contract.

### Tier 3: Intelligences (`3_intelligences/`)

Active capabilities the agent invokes. Two subtrees:

```
3_intelligences/
├── skills/<domain>/<slug>/    # Bundle dirs ("how to do X")
│   ├── SKILL.md               # Required entry point
│   ├── scripts/               # Optional executable resources
│   └── resources/             # Optional reference material
├── agents/<domain>/<slug>.md  # Single-file persona definitions ("you are X")
└── _archived/                 # Deprecated skills + agents
```

A skill answers *how do I do X?*. An agent persona answers *who should I be while doing X?*. Both are curator-authored; the CLI rejects writes to `3_intelligences/` entirely.

Skills and agents are intentionally **excluded from `akw search`** — they're invoked in narrow cases (skill equip, agent role assignment), not exploratory search. Dedicated discovery commands (`akw skill search` / `akw agent search`, EP-00009) handle them.

### `0_configs/` (wiki contract, not a tier)

Templates and rules. Read this first when authoring new content.

```
0_configs/
├── templates/        # Scaffolds for new knowledge / skills / agents
└── rules/            # Conventions: knowledge-management, archival, session-review, external-extraction
```

Curator-only. The CLI rejects writes here.

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│  CLI (akw) + Session Hooks                               │
│  (single entry point — agents shell out, hooks drive     │
│   session lifecycle; subcommand groups encode the          │
│   agent-safe vs curator/admin boundary.)                 │
└──────────────────────────┬───────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────┐
│  Core Library                                            │
├──────────────────────────────────────────────────────────┤
│  Memory (/memory) — Numbered three-tier wiki             │
│  - 0_configs/        templates + rules (curator-only)    │
│  - 1_drafts/         agent-writable drafts (Tier 1)      │
│  - 2_knowledges/     curated memory palace (Tier 2)      │
│  - 3_intelligences/  skills + agent personas (Tier 3)    │
├──────────────────────────────────────────────────────────┤
│  Storage & Search                                        │
│  - SQLite: groups, turns, memory_edits, draft_state      │
│  - DuckDB: full-text search                              │
│      akw search:        knowledge + drafts               │
│      akw skill search:  3_intelligences/skills (SKILL.md)│
│      akw agent search:  3_intelligences/agents           │
└──────────────────────────────────────────────────────────┘
```

---

## Data Layout

```
~/.agent-knowledge/                      # Default data root (configurable)
├── db/
│   ├── sessions.db                      # SQLite — turns (with markers), memory_edits, draft_state
│   └── search.db                        # (DuckDB is in-memory; rebuilt from files on each connect)
└── memory/                              # All markdown tiers (open as Obsidian vault)
    ├── 0_configs/                       # Wiki contract (curator-only, not a tier)
    │   ├── templates/                   #   Scaffolds for knowledge / skills / agents
    │   └── rules/                       #   Conventions (knowledge-management, archival, …)
    │
    ├── 1_drafts/                        # Tier 1: agent-writable drafts
    │   ├── sessions/                    #   Agent-written session summaries (one per segment) + recover stubs
    │   ├── 2_knowledges/                #   Drafts targeting Tier 2 knowledge
    │   ├── 2_notes/                     #   Ad-hoc notes (promote to 2_knowledges/notes/)
    │   ├── 2_researches/                #   Research outputs (promote to 2_knowledges/researches/)
    │   ├── 3_skills/                    #   Drafts targeting Tier 3 skills
    │   ├── reviews/                     #   Curator review notes (excluded from search)
    │   └── _archived/                   #   Flat-file archive: sessions__<basename>.md
    │
    ├── 2_knowledges/                    # Tier 2: curated knowledge (memory palace)
    │   ├── concepts/                    #   Concept pages (ideas, patterns, domains)
    │   ├── entities/                    #   Entity pages (people, projects, tools)
    │   ├── notes/                       #   Promoted notes
    │   ├── preferences/                 #   User preferences (CARVE-OUT: agent-writable)
    │   ├── researches/                  #   Promoted research
    │   ├── sources/                     #   Summaries of ingested material
    │   └── _archived/                   #   Subfolder archive: <orig-rel-path>.md
    │
    └── 3_intelligences/                 # Tier 3: actionable capabilities (curator-only)
        ├── skills/<domain>/<slug>/      #   Bundle dirs with SKILL.md + scripts/, resources/
        ├── agents/<domain>/<slug>.md    #   Single-file persona definitions
        └── _archived/                   #   Deprecated skills and agents
```

---

## Data Model

Schemas are managed by an in-code migration list in `src/agent_knowledge/core/storage.py` (`_MIGRATIONS`), versioned via SQLite `PRAGMA user_version` and applied on every `storage.connect`. This section describes the conceptual model.

### SQLite (`sessions.db`) — Lifecycle & Provenance

The durable session unit is `session_summaries` (EP-00011). The legacy `turns` table remains for old data and recovery commands, but new hook flows no longer write raw prompt/response turns.

**projects** — Scopes sessions to a project context.
- `id`, `name`, `path` (working directory), `tags[]` (domain tags for matching skills), `created_at`, `metadata` (JSON)

**session_summaries** — One row per open or closed agent session.
- `id`, `project_id`, `project_name`, `agent`, `working_dir`, `draft_path`, `title`, `summary`, `metadata`, `started_at`, `ended_at`, `created_at`
- Open sessions have `ended_at IS NULL`. `akw session close` writes the markdown draft, records memory provenance, and sets `ended_at`.
- Recent summary queries are scoped by project and exclude the current open session.

**turns** — Legacy marker rows + turn-level summaries from the pre-EP-00011 capture model. Kept for recovery and migration only.

**memory_edits** — Audit log of all changes to pages across all tiers (single source of truth for change history).
- `id`, `group_id` (nullable for manual edits), `page_path` (relative to `/memory`), `tier` (`'draft' | 'knowledge' | 'skill' | 'agent' | 'config'`), `action` (`'create' | 'update' | 'delete' | 'archive'`), `summary`, `created_at`
- The `archive` action is recorded when `memory_delete` redirects a carve-out path (e.g. `2_knowledges/preferences/`) to `<tier>/_archived/<rel>` instead of unlinking.

**draft_state** — Indexed archive state for session drafts.
- `id`, `draft_path` (UNIQUE; mutated on archive — `id` is the stable handle), `group_id`, `segment_start_at`, `segment_end_at` (backs the exclude-today filter), `created_at` (when the row was inserted), `archived_at` (nullable)
- Frontmatter on each draft mirrors a few fields as a courtesy projection for human readers; reads always go to the table. `akw reindex` rebuilds the table from frontmatter as a recovery path.

### DuckDB — Search Index (in-memory)

DuckDB runs as an in-memory connection (no file lock contention across concurrent sessions); the index is rebuilt from `/memory` files on each connect via `sync_from_files`.

**memory_pages** — Indexed copy of markdown pages from drafts + curated knowledge.
- `path`, `title`, `content`, `summary`, `tags[]`, `tier`, `updated_at`, `metadata` (JSON)
- BM25 full-text index on `title` and `content`
- Tier values: `knowledge`, `session_draft`, `session_archived`, `knowledge_draft`, `note_draft`, `research_draft`, `skill_draft`

**Indexed sources** (walked by `sync_from_files`, skipping any `_archived/` subfolder):
- `2_knowledges/**` → tier `knowledge`
- `1_drafts/sessions/*.md` → tier `session_draft`
- `1_drafts/_archived/sessions__*.md` → tier `session_archived` (flat-file glob)
- `1_drafts/2_knowledges/**` → tier `knowledge_draft`
- `1_drafts/2_notes/**` → tier `note_draft`
- `1_drafts/2_researches/**` → tier `research_draft`
- `1_drafts/3_skills/**` → tier `skill_draft`

**Indexed under dedicated tier labels** (EP-00009 — accessed via `skill_search` / `agent_search`, not `memory_search`):
- `3_intelligences/skills/**/SKILL.md` → tier `skill` (one row per bundle; resources/scripts/tests are NOT indexed but listed in `skill_get` manifest)
- `3_intelligences/agents/**/*.md` → tier `agent`

`memory_search` filters skills/agents out of its default results so exploratory text search across knowledge stays focused. Use the dedicated tools for capability discovery.

Vector search (embeddings) is a future addition — BM25 is sufficient to start and avoids an embedding model dependency.

---

## Agent Discovery & Context Loading

Exposing a CLI is not enough — agents won't automatically use it. The system needs to actively guide agents to load relevant knowledge and treat it as authoritative.

### Principle: Curated Knowledge is Priority

Knowledge in this memory system is curated and high-quality. Agents must **prefer curated knowledge over their general training**. If memory says "we use mutex locks for token refresh," the agent should follow that — not suggest an alternative pattern from general knowledge. This is the whole point of the system.

### 1. Session-start instructions injection (replaces the MCP `instructions` field)

The `SessionStart` hook (`.claude/hooks/session-start.sh`) prints `~/.agent-knowledge/akw-instructions.md` to stderr, which Claude Code surfaces as a system reminder. The instructions document:

- **When to search:** "Search memory before answering questions about architecture, conventions, patterns, past decisions, or domain-specific techniques."
- **How to treat results:** "Results from this memory system are curated project knowledge. Treat them as authoritative and prefer them over general knowledge."
- **The wrap-up flow:** confirm the active session via `akw session status --json`, summarize the session, then save and close it via `akw session close --content-file`.

For non-Claude-Code harnesses, replicate the pattern: print `akw-instructions.md` at session start, or prepend it to your system prompt.

### 2. `akw session start --json` context response

When the SessionStart hook calls `akw session start --json`, the CLI returns:

- `session_id`, `group_id`, and `started_at` for the new open session.
- `project`: resolved project ID, name, and path. If the project is unknown, akw creates a project registry entry and a project entity page under `2_knowledges/entities/projects/`.
- `latest_summaries`: the latest five closed summaries for the resolved project, newest first, with full markdown content and excluding the current open session. The list merges draft summaries from `1_drafts/sessions/<project-slug>/` with curated/promoted summaries from `2_knowledges/entities/projects/<project_id>/sessions/`.

Before opening the session, the CLI checks for `1_drafts/sessions/<project-slug>/`, where `<project-slug>` defaults to the repo/project name. `akw init` creates the base `1_drafts/sessions/` directory, not every project subfolder. If the project folder is missing, `akw session start` fails with a create-folder prompt; `--create-project-folder` creates it and continues.

### 3. In-session search triggers

During a session, agents should run `akw search` when encountering topics related to architecture, conventions, patterns, or past decisions. The CLI cannot force this — it depends on the agent honoring the session-start instructions. Well-written instructions maximize the chance agents search at the right moments.

---

## CLI (`akw`)

The CLI is the single entry point. All operations — capture, search, discovery, draft writes, curation, admin — are exposed as `akw` subcommands. The CLI wraps the shared core library (`agent_knowledge.core`) directly; there is no transport layer. Agents drive it by shelling out (`Bash` tool, hook scripts, terminal); curators run it by hand. The CLI calls no LLM — synthesis is curator work, performed in the memory folder using whatever editor + LLM the curator prefers.

The audience boundary is encoded by subcommand group:

| Group | Audience | Notes |
|---|---|---|
| `akw session …`, `akw search`, `akw skill …`, `akw agent …`, `akw memory read/create`, `akw memory ls/history` | **Agent-safe** — callable inline from a session via Bash. | `akw session close` is the session-summary writer; `akw memory create` rejects writes outside `1_drafts/` and the carve-outs. |
| `akw memory update/rm`, `akw maintain …`, `akw project …`, `akw archive`, `akw recover`, `akw reindex`, `akw init`, `akw status`, `akw groups` | **Curator / admin** — humans only, by convention. | Not surfaced in `akw-instructions.md`. |

```
akw <subcommand> [options]
```

### Capture & lifecycle (agent-safe; hook-driven by default)

| Command | Description |
|---|---|
| `akw session start [--group-id] [--project] [--agent] [--working-dir] [--create-project-folder] [--json]` | Start a new session. Used by the `SessionStart` hook; persists `AKW_SESSION_ID` to env. With `--json`, returns `{session_id, group_id, started_at, project, latest_summaries}`. Requires `1_drafts/sessions/<project-slug>/`; `--create-project-folder` creates it. |
| `akw session close [--session-id] (--content TEXT \| --content-file FILE) [--summary] [--json]` | Write the full session summary under `1_drafts/sessions/`, record memory/session indexes, and close the session. |
| `akw session status [--json]` | Active session metadata. With `--json`, returns `{session_id, group_id, segment_start_at, segment_turn_count, agent, project_id, project_name, latest_at}`. |
| `akw session recent [--project PROJECT] [--working-dir DIR] [--limit 5] [--json]` | Return recent closed summaries for the resolved project, with full markdown content and excluding the current open session. Merges `1_drafts/sessions/<project-slug>/` and `2_knowledges/entities/projects/<project_id>/sessions/`. |
| `akw group start/status/close` | Deprecated aliases for the matching `akw session ...` commands. |
| `akw group end [--group-id]` | Deprecated guard. Fails with a reminder to save the summary through `akw session close`. |
| `akw group turns <group_id> [--segment-start ISO]` | Legacy inspection of raw turns for recovery follow-ups. |

### Discovery (agent-safe)

| Command | Description |
|---|---|
| `akw search "<query>" [--tier T] [--json]` | Search drafts + curated knowledge by BM25. Tiers: `knowledge`, `session_draft`, `session_archived`, `knowledge_draft`, `note_draft`, `research_draft`, `skill_draft`. Skills and agent personas are excluded from default results — pass `--tier=skill`/`--tier=agent` explicitly, or use `akw skill search` / `akw agent search`. |
| `akw memory read <path> [--json]` | Read a specific page. With `--json`, returns `{path, content}`. |
| `akw memory ls [--tier T] [--json]` | Catalog of indexed pages (replaces MCP `memory_index`). |
| `akw memory history [--page-path P] [--limit N] [--json]` | Recent edit history from the audit log. |
| `akw skill search "<query>" [--domain D] [--json]` | Search SKILL.md files by query (BM25). One row per bundle. Resources/scripts within a bundle are NOT searchable on their own. `--domain` (e.g. `engineering`) pre-filters before BM25. |
| `akw skill show <path-or-domain/slug> [--json]` | SKILL.md content + manifest of `resources/`, `scripts/`, `tests/` files (paths only, recursive). Resources are read on demand via `akw memory read`. With `--json`, returns the same shape MCP `skill_get` did. |
| `akw agent search "<query>" [--domain D] [--json]` | Search agent persona files by query (BM25). |
| `akw agent show <path-or-domain/slug> [--json]` | Persona file content + parsed metadata. With `--json`, returns the same shape MCP `agent_get` did. |

### Draft writes (agent-safe)

| Command | Description |
|---|---|
| `akw memory create --path P --title T (--content C \| --content-file F) [--tags …] [--summary …] [--group-id …]` | Create a new page. **Rejects** writes to `2_knowledges/`, `3_intelligences/`, `0_configs/`, and `1_drafts/_archived/`. Allows the carve-out `2_knowledges/preferences/`. When the path is `1_drafts/sessions/...`, atomically writes a `draft_state` row alongside the file. |

### Curation & admin (humans only)

| Command | Description |
|---|---|
| `akw init` | Initialize data directory (`~/.agent-knowledge/`), scaffold the numbered three-tier folder structure (including `1_drafts/sessions/`, `1_drafts/_archived/`, all draft staging dirs, `2_knowledges/`, `3_intelligences/skills/`, `3_intelligences/agents/`, `0_configs/`), and run migrations. First-time setup. |
| `akw status` | System stats: registered projects, group counts, open + orphan groups, unarchived session drafts (today vs prior days), incomplete segments, pages per tier, index health. Human-readable companion to `akw maintain stats --json`. |
| `akw groups [--project X]` | List recent groups with summaries. For quick inspection. |
| `akw memory update <path> --content … [--summary …]` | Update an existing page. Same path rules + carve-outs as `akw memory create`. |
| `akw memory rm <path> [--reason …]` | Delete a page. **Drafts** (`1_drafts/` paths) cannot be deleted via this command. **Carve-out paths** are *archived* (moved to `<tier>/_archived/<orig-rel>`), not unlinked — recorded as an `archive` edit. |
| `akw project new --name … --path … [--tags …]` | Register a project. Tags are domain labels (e.g. `python,web` — used to match relevant skills at session start). |
| `akw project ls [--json]` | List registered projects. |
| `akw archive <draft_path>` | Move a session draft into `1_drafts/_archived/sessions__<basename>.md` (flat-file, prefix-marked) and update `draft_state` (`archived_at`, new `draft_path`) atomically. |
| `akw recover [--dry-run]` | Two-pass: (1) write `idle_close` markers for orphan groups (open >24h, no end marker); (2) write stub drafts for closed-no-draft segments (now including the freshly-closed orphans). Stub drafts carry `recovery_kind: idle_close \| closed_no_draft` and `turn_count` in frontmatter. Idempotent. |
| `akw reindex [--force]` | Two roles: (a) drift-recover `draft_state` from frontmatter when the table is missing or known-stale (requires `--force` if non-empty); (b) reconcile manual file moves (e.g. `git mv` into `1_drafts/_archived/`). Also rebuilds the DuckDB search index from indexed tiers (drafts + `2_knowledges/`). |
| `akw maintain stats [--stale-days N] [--json]` | Structural stats for the memory system: page counts per tier, group stats, stale pages older than `stale_days`. Replaces MCP `maintain_get_stats`. |
| `akw maintain purge [--older-than-days N]` | Delete archived session drafts older than retention period (default 365 days). Active drafts and curated tiers are never auto-purged. |

> **Tier write boundary.** By default, the CLI cannot modify `2_knowledges/`, `3_intelligences/`, `0_configs/`, or `1_drafts/_archived/`. Curation is a trusted human action performed via the file system (Claude Code's `Edit`, Obsidian, manual edit, `git mv`). The CLI exposes no `promote_to_*` subcommand — promotion is a file-system move, not a command.
>
> **Carve-outs.** Narrow exceptions inside curated tiers, listed in `paths.WRITE_ALLOWED_OVERRIDES`:
> - `2_knowledges/preferences/` — agent-writable user preference pages.
>
> Carve-outs follow the **archive-on-delete** contract: `akw memory rm` moves the file to `<tier>/_archived/<original-rel>` and records an `archive` edit, instead of unlinking.

### JSON output contract

Every subcommand whose result is a structured payload accepts `--json`. Stable shapes:

| Command | Payload |
|---|---|
| `akw session start --json` | `{session_id, group_id, started_at, project, latest_summaries}` |
| `akw session status --json` | `{session_id, group_id, segment_start_at, segment_turn_count, agent, project_id, project_name, latest_at}` |
| `akw session recent --json` | `[{session_id, path, title, summary, content, started_at, ended_at, metadata}, ...]` |
| `akw memory read --json` | `{path, content}` |
| `akw memory ls --json` | `[{path, tier, title, ...}, ...]` |
| `akw memory history --json` | `[{page_path, kind/edit_kind, summary, created_at, ...}, ...]` |
| `akw skill show --json` | `{path, domain, slug, title, content, resources, scripts, tests}` |
| `akw agent show --json` | `{path, domain, slug, title, content}` |
| `akw project ls --json` | `[{id, name, path, tags, ...}, ...]` |
| `akw maintain stats --json` | `{pages: {…}, stale_pages: [...], groups: {…}}` |
| `akw search --json` | `[{path, tier, title, summary, ...}, ...]` |

### Design principle

**One transport, two audiences, one core library.**

```
┌──────────────────────────────────────────────────┐
│  Agents (via Bash) + Hooks      Curators (shell) │
│       │                                  │       │
│       ▼                                  ▼       │
│  akw <agent-safe subcommand>    akw <admin cmd>  │
└──────────────────────┬───────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────┐
│  Core Library                                    │
│  Storage + Search + File operations              │
└──────────────────────────────────────────────────┘
```

Synthesis (drafts → knowledge) and skill compilation (knowledge → skills) happen **outside** this picture, in the curator's editor against the file system. The CLI does not call an LLM.

---

## Configuration

All config lives in `pyproject.toml` under `[tool.agent-knowledge]`:

```toml
[tool.agent-knowledge]
data_dir = "~/.agent-knowledge"
search_engine = "bm25"             # "bm25" now, "hybrid" later (bm25 + vector)
```

Environment overrides:
- `AKW_DATA_DIR` — overrides `data_dir`.
- `AKW_SESSION_ID` — current active session (set by hooks; consumed by subsequent hooks and `akw session *`).

The legacy idle-close threshold is currently a code constant (`DEFAULT_IDLE_CLOSE_MINUTES = 30` in `core/storage.py`). It applies only to pre-EP-00011 turn-capture recovery paths.

---

## Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3.12+ |
| CLI framework | `click` |
| SQLite | `sqlite3` (stdlib) |
| DuckDB | `duckdb` |
| Package manager | `uv` |
| Virtual env | `.venv` in project root |
| DB migrations | In-code (`_MIGRATIONS` in `core/storage.py`, gated by `PRAGMA user_version`) |
| Testing | `pytest` |
| Type checking | `pyright` |

### Development Setup

- All commands run from `.venv` virtual env in the project folder (`uv venv && source .venv/bin/activate`)
- `uv` manages dependencies and virtual environment
- SQLite migrations apply automatically on `storage.connect`. Append a new entry to `_MIGRATIONS` and bump the implicit version number; existing installs upgrade in place. DuckDB is in-memory, rebuilt from files on every connect — no migrations.

---

## Constraints & Decisions

1. **Memory pages are plain markdown** — human-readable, git-trackable, editable outside the system.
2. **DuckDB is a search index, not source of truth** — indexes drafts + curated knowledge (`1_drafts/sessions/`, `1_drafts/_archived/sessions__*.md`, `1_drafts/2_knowledges/`, `1_drafts/2_notes/`, `1_drafts/2_researches/`, `1_drafts/3_skills/`, `2_knowledges/`). Skills and agent personas are indexed separately by Phase B discovery tools. Always rebuildable from files; runs in-memory to avoid file-lock contention.
3. **SQLite is for provenance** — knowing which session/agent created or modified a memory page.
4. **BM25 first, vectors later** — avoids embedding model dependency at start. DuckDB supports both when ready.
5. **No LLM calls inside the CLI** — the CLI stores, retrieves, and manages files and lifecycle. All reasoning (summarization, pattern detection, synthesis) happens in the calling agent or in the curator's editor + LLM, against the file system.
6. **Capture-only scope with narrow carve-outs** — agents capture session activity into drafts via the CLI. Synthesis (drafts → knowledge) and compilation (knowledge → skills/agents) are human activities outside the agent loop. The CLI exposes no `promote_to_*` subcommand; `akw memory create` / `akw memory update` reject writes to `2_knowledges/`, `3_intelligences/`, `0_configs/`, and `1_drafts/_archived/`. Carve-outs (e.g. `2_knowledges/preferences/`) are narrow exceptions where the agent's write is itself the curated state; deletes on carve-outs archive instead of unlinking.
7. **365-day retention for archived drafts** — `akw maintain purge` deletes archived session drafts (`1_drafts/_archived/sessions__*.md`) older than the retention boundary. Active drafts and curated tiers are never auto-purged. Legacy raw turns may exist in SQLite from older capture flows.
8. **No secrets in knowledge** — the CLI must sanitize content before storing. Agents may inadvertently log API keys, tokens, passwords, or credentials in turn summaries or draft pages. Safeguards:
   - **Write-time scanning** — `akw session close`, `akw memory create`, and `akw memory update` scan content for common secret patterns (API keys, tokens, connection strings, private keys) and redact or reject them.
   - **Session-start instructions** — `akw-instructions.md` instructs agents to never include secrets, credentials, or sensitive tokens in session summaries or knowledge pages.
   - **Curation layer** — the curator reviews drafts (in Claude Code, Obsidian, or any editor) before synthesizing into knowledge, providing a human check for leaked secrets.
9. **CLI-only transport (EP-00010)** — v0.1.x shipped an MCP server alongside the CLI. The MCP transport was removed in v0.2.0; the CLI is now the sole entry point. See `MCP_TO_CLI_MIGRATION.md` for the rationale and per-tool mapping. If a future use case requires MCP (browser-based agents, sandboxed harnesses without shell access), it ships as a thin wrapper around the CLI rather than a parallel surface.

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
