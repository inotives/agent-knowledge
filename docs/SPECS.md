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

## Scope: capture only

EP-00005 establishes the boundary: **the MCP captures conversation activity into session drafts; humans synthesize drafts into knowledge.** The server has no LLM, no synthesis tools, no promotion tools. Synthesis happens outside the MCP — typically by running Claude Code (or any editor + LLM) inside `~/.agent-knowledge/memory/` against the session drafts. The contract for that work lives in `0_configs/rules/knowledge-management.md`.

This is a deliberate scope cut: earlier drafts included LLM synthesis and a promotion pipeline. Those are removed because the curator can already invoke an LLM directly against the file system; the genuinely valuable structural work is the capture lifecycle.

## Core Workflow

```
┌──────────────────────────────────────────────────────────────┐
│  Group Start                                                 │
│  Agent calls group_start → server returns group_id, segment  │
│  start time, pending counts (unarchived drafts, incomplete   │
│  segments), and recommended context (matching skills/recent  │
│  knowledge). Agent surfaces non-zero pending counts to the   │
│  user as a heads-up; user opts in to review.                 │
├──────────────────────────────────────────────────────────────┤
│  During Segment                                              │
│  Agent works (code, discuss, debug) → logs turns via         │
│  group_log → reads/writes drafts and curated pages.          │
│  group_log applies idle-close-on-stale: if the group has     │
│  not been touched for >30min, the prior segment is closed    │
│  with an idle_close marker and a new segment is opened       │
│  under the same group_id (continuation-by-resumption).       │
├──────────────────────────────────────────────────────────────┤
│  Group End (segment wrap-up — happy path)                    │
│  Agent calls group_end → server returns segment scope and    │
│  draft path → agent summarizes the current segment's turns   │
│  and writes a session draft via memory_create to             │
│  1_drafts/sessions/<group_first_8>-<segment_iso>.md. Each    │
│  segment produces its own draft; past drafts are never       │
│  overwritten.                                                │
├──────────────────────────────────────────────────────────────┤
│  Recovery (curator-driven)                                   │
│  If an agent crashed before writing an end marker, or wrote  │
│  the marker but no draft, the segment is incomplete.         │
│  `akw recover` writes idle_close markers for orphans (>24h   │
│  open) and stub drafts for closed-no-draft segments. There   │
│  is no automated recovery during group_start.                │
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
                      MCP captures + agents     Curator authors —        agents/engineering/sre.md
                      stage drafts.             no MCP promotion.
                      No MCP synthesis.
```

### Tier 1: Drafts (`1_drafts/`)

Inside `1_drafts/`, the nested numeric prefix on each subfolder signals the *promotion target* — where a draft will land once curated. Sessions are MCP-written; the rest are agent-staged drafts during research/note/preference work.

```
1_drafts/
├── sessions/         # MCP-written session summaries (one per segment)
├── 2_knowledges/     # Drafts targeting Tier 2 knowledge pages
├── 2_notes/          # Ad-hoc notes (will promote to 2_knowledges/notes/)
├── 2_researches/     # Research outputs (will promote to 2_knowledges/researches/)
├── 3_skills/         # Drafts targeting Tier 3 skills
├── reviews/          # Curator review notes (excluded from search)
└── _archived/        # Flat-file archive: sessions__<basename>.md
```

**Session drafts (`1_drafts/sessions/`)** — auto-generated at segment end.
1. Agent summarizes its own current-segment turns — what was asked, decided, learned.
2. Writes a session draft to `1_drafts/sessions/<group_first_8>-<segment_iso>.md` (e.g. `abc12345-20260429-0930.md`). The frontmatter records `group_id` and `segment_start_at` so the draft is linkable back to its raw turns.
3. Each segment produces its own draft; multi-segment groups produce multiple drafts.

**Stub drafts (also under `1_drafts/sessions/`)** — written by `akw recover` for incomplete segments. Frontmatter carries `recovery_kind: idle_close` (or `closed_no_draft`) and `turn_count`. Body is a placeholder pointing to recovery actions; the curator either fills in a real summary from raw turns or archives as-is.

**Archived drafts (`1_drafts/_archived/sessions__*.md`)** — flat-file with `sessions__` filename prefix (no subfolder). Drafts move here via `akw archive` once they're no longer active work. Indexed under the `session_archived` tier as a long-term provenance trail; deleted by `maintain_purge` at the retention boundary.

The MCP server provides the data and file operations. The agent summarizes its own turns; the curator synthesizes across drafts. There is no MCP-driven synthesis path.

### Tier 2: Knowledge (`2_knowledges/`)

Curated, categorized pages — the **memory palace**. Organized by topic into `concepts/`, `entities/`, `notes/`, `preferences/`, `researches/`, `sources/`. The curator authors knowledge pages by reading session drafts and writing/editing files directly (typically using Claude Code or Obsidian inside the memory folder). Frontmatter conventions and house rules live in `0_configs/rules/knowledge-management.md` — point any synthesis LLM at that page first.

The MCP **does not write** to `2_knowledges/` by default: `memory_create` and `memory_update` reject paths under `2_knowledges/`, `3_intelligences/`, `0_configs/`, and `1_drafts/_archived/`. Curation is a trusted human action with file-system access.

**Carve-out — `2_knowledges/preferences/`.** User-preference pages are agent-writable. Agents can call `memory_create` / `memory_update` directly under this prefix. `memory_delete` on a carve-out path *archives* (moves to `2_knowledges/_archived/preferences/<name>.md`) instead of unlinking, preserving the audit trail. The carve-out list lives in `paths.WRITE_ALLOWED_OVERRIDES`; new carve-outs follow the same delete-redirects-to-archive contract.

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

A skill answers *how do I do X?*. An agent persona answers *who should I be while doing X?*. Both are curator-authored; the MCP rejects writes to `3_intelligences/` entirely.

Skills and agents are intentionally **excluded from `memory_search`** — they're invoked in narrow cases (skill equip, agent role assignment), not exploratory search. Dedicated discovery tools (Phase B — `skill_search`, `agent_search`) handle them.

### `0_configs/` (wiki contract, not a tier)

Templates and rules. Read this first when authoring new content.

```
0_configs/
├── templates/        # Scaffolds for new knowledge / skills / agents
└── rules/            # Conventions: knowledge-management, archival, session-review, external-extraction
```

Curator-only. The MCP rejects writes here.

---

## Architecture

```
┌──────────────────────┐  ┌────────────────────────┐
│  MCP Server          │  │  CLI (akw)             │
│  (agent-facing)      │  │  (user-facing)         │
└──────────┬───────────┘  └──────────┬─────────────┘
           │                         │
           ▼                         ▼
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
│      memory_search:  knowledge + drafts                  │
│      skill_search:   3_intelligences/skills (SKILL.md)   │
│      agent_search:   3_intelligences/agents              │
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
    │   ├── sessions/                    #   MCP session summaries (one per segment) + recover stubs
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

The `sessions` table is gone (EP-00005). Group/segment lifecycle lives entirely on `turns` via marker rows. State queries pair start/end markers per `group_id`, ordered by `created_at`.

**projects** — Scopes groups to a project context.
- `id`, `name`, `path` (working directory), `tags[]` (domain tags for matching skills), `created_at`, `metadata` (JSON)

**turns** — Marker rows + turn-level summaries within a group. A `turn` represents one logical exchange: user asks something → agent works (possibly multiple LLM calls, tool uses, iterations) → outcome. The agent summarizes its own turn before logging — we store the distilled content, not raw API exchanges.
- `id`, `group_id` (stable handle: session_id from Claude / conversation_id or task_id from barebone), `kind` (`'start' | 'turn' | 'end' | 'idle_close'`), `request` (nullable on markers), `response` (nullable on markers), `metadata` (JSON: agent, project_id, conversation_id|task_id, working_dir, …), `created_at`

  - `start` and `end`/`idle_close` markers carry agent + project metadata on the start row; downstream queries `json_extract` from there. Continuation reuses the same `group_id` and writes a new `start` (a new segment in the same group).

**memory_edits** — Audit log of all changes to pages across all tiers (single source of truth for change history).
- `id`, `group_id` (nullable for manual edits), `page_path` (relative to `/memory`), `tier` (`'draft' | 'knowledge' | 'skill' | 'agent' | 'config'`), `action` (`'create' | 'update' | 'delete' | 'archive'`), `summary`, `created_at`
- The `archive` action is recorded when `memory_delete` redirects a carve-out path (e.g. `2_knowledges/preferences/`) to `<tier>/_archived/<rel>` instead of unlinking.

**draft_state** — Indexed pending counts and archive state for session drafts.
- `id`, `draft_path` (UNIQUE; mutated on archive — `id` is the stable handle), `group_id`, `segment_start_at`, `segment_end_at` (backs the exclude-today filter), `created_at` (when the row was inserted), `archived_at` (nullable)
- Indexed query for pending: `WHERE archived_at IS NULL AND segment_end_at < <today>`. Sub-millisecond regardless of N.
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
- `group_bootstrap` — instructs the agent to start (or continue) a group, surface pending counts to the user as a heads-up, and load relevant knowledge/skills for the current project
- `group_wrapup` — instructs the agent to summarize the current segment's turns, write a session draft via `memory_create`, and call `group_end`

These prompts give agents a playbook for how to interact with the memory system.

### 3. `group_start` Context Response
When `group_start` is called, the server returns:
- `group_id` and `segment_start_at` for the new (or continued) segment
- `pending` counts: `unarchived_session_drafts` (excludes today) and `incomplete_segments` (orphans + closed-no-draft). The agent surfaces non-zero values to the user as a heads-up — there is no auto-processing.
- `recommended_context` — matching skill pages based on project tags and recent knowledge pages, returned inline so the agent can bootstrap without needing to know what to search for.

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

### Group Lifecycle

| Tool | Description |
|---|---|
| `group_start` | Begin (or continue) a group. Returns: `group_id`, `segment_start_at`, `pending` counts (unarchived drafts + incomplete segments), and `recommended_context` (matching skill pages and recent knowledge inline). If `group_id` is passed and the group's latest turn is stale (>30min idle), an `idle_close` is written for the stale segment before the new `start` (continuation-by-resumption). Params (all optional): `group_id`, `agent` ("claude", "codex", "barebone-agent"), `metadata` (dict — `project_id`, `working_dir`, `conversation_id`/`task_id`). |
| `group_end` | End the active segment. Returns `{group_id, segment_start_at, segment_end_at, draft_path, summarization_hint}` so the agent knows where to write its draft. Params (optional): `group_id`. |
| `group_log` | Append turns to the active group. Applies idle-close-on-stale before write: if the latest turn is older than 30min, writes `idle_close` for the stale segment and `start` for a new segment under the same `group_id`, then writes the requested turn. Agents should log incrementally during the segment to ensure turns survive crashes. |
| `group_status` | Current group + segment metadata for inspection. |

### Memory — Read

| Tool | Description |
|---|---|
| `memory_search` | Search drafts and curated knowledge by BM25. Tiers: `knowledge`, `session_draft`, `session_archived`, `knowledge_draft`, `note_draft`, `research_draft`, `skill_draft`. Skills and agent personas have dedicated tools — passing `tier='skill'`/`'agent'` returns a redirect hint. Params: `query`, `tier` (optional). |
| `memory_read` | Read a specific page. Params: `path` (relative to `/memory`). |
| `memory_index` | Return a catalog of indexed pages, queried from DuckDB. Params: `tier` (optional). Returns page paths, titles, summaries. |
| `memory_history` | Return recent edit history from the audit log. Params: `limit` (int, default 20), `page_path` (optional, filter by page). |

### Intelligences — Discovery (EP-00009)

Use these when looking up a *capability* to equip or a *role* to assign. They run on the same DuckDB table but stay partitioned from `memory_search` by tier label.

| Tool | Description |
|---|---|
| `skill_search` | Search SKILL.md files by query (BM25). One row per bundle. Resources/scripts within a bundle are NOT searchable on their own. Params: `query`, `domain` (optional, e.g. `engineering` — pre-filters before BM25). |
| `skill_get` | Return SKILL.md content + manifest of `resources/`, `scripts/`, `tests/` files (paths only, recursive). Resources are read on demand via `memory_read`. Param: `skill_path` — full path or `<domain>/<slug>` shorthand. |
| `agent_search` | Search agent persona files by query (BM25). Params: `query`, `domain` (optional). |
| `agent_get` | Return persona file content + parsed metadata. Param: `agent_path` — full path or `<domain>/<slug>` shorthand. |

### Memory — Write

| Tool | Description |
|---|---|
| `memory_create` | Create a new page. **Rejects** writes to `2_knowledges/`, `3_intelligences/`, `0_configs/`, and `1_drafts/_archived/` (curator-only). Allows the carve-out `2_knowledges/preferences/`. When the path is `1_drafts/sessions/...`, atomically writes a `draft_state` row alongside the file. Params: `path`, `title`, `content`, `tags[]`, `summary`, `group_id` (optional; auto-bound to active group). |
| `memory_update` | Update an existing page. Same path rules + carve-outs as `memory_create`. Params: `path`, `content`, `summary` (what changed). |
| `memory_delete` | Delete a page. **Drafts** (`1_drafts/` paths) cannot be deleted by agents. **Carve-out paths** (e.g. `2_knowledges/preferences/`) are *archived* (moved to `<tier>/_archived/<orig-rel>`), not unlinked — recorded as an `archive` edit. Other paths are deleted as before. Params: `path`, `reason`. |

> **Tier write boundary.** By default, the MCP cannot modify `2_knowledges/`, `3_intelligences/`, `0_configs/`, or `1_drafts/_archived/`. Curation is a trusted human action performed via the file system (Claude Code's `Edit`, Obsidian, manual edit, `git mv`). The MCP exposes no `promote_to_*` tools — promotion is a file-system move, not a tool call.
>
> **Carve-outs.** Narrow exceptions inside curated tiers, listed in `paths.WRITE_ALLOWED_OVERRIDES`:
> - `2_knowledges/preferences/` — agent-writable user preference pages.
>
> Carve-outs follow the **archive-on-delete** contract: `memory_delete` moves the file to `<tier>/_archived/<original-rel>` and records an `archive` edit, instead of unlinking.

### Maintenance

| Tool | Description |
|---|---|
| `maintain_get_stats` | Return structural stats for the memory system: orphaned pages (no inbound links), pages with no updates in N days, missing cross-references, index drift. The calling agent interprets and acts on the report. Params: `stale_days` (int, default 90). |
| `maintain_reindex` | Rebuild the DuckDB search index from indexed tiers (drafts + `2_knowledges/`). Also reconciles `draft_state` with on-disk drafts. |
| `maintain_purge` | Delete archived session drafts older than the retention boundary. Active drafts and curated tiers are never auto-purged. Params: `older_than_days` (int, default 365). |

---

## CLI (`akw`)

A companion CLI that complements the MCP server. Both the CLI and MCP server share the same **core library** (storage, search, file operations) — the CLI does not go through MCP protocol. The MCP server wraps the core as MCP tools for agents; the CLI wraps it as commands for users. The CLI works independently and does not require the MCP server to be running. The CLI calls no LLM — synthesis is curator work, performed in the memory folder using whatever editor + LLM the curator prefers.

```
akw <command> [options]
```

### Setup & Operations

| Command | Description |
|---|---|
| `akw init` | Initialize data directory (`~/.agent-knowledge/`), scaffold the numbered three-tier folder structure (including `1_drafts/sessions/`, `1_drafts/_archived/`, all draft staging dirs, `2_knowledges/`, `3_intelligences/skills/`, `3_intelligences/agents/`, `0_configs/`), and run migrations. First-time setup. |
| `akw status` | Show system stats: registered projects, group counts, open + orphan groups, unarchived session drafts (today vs prior days), incomplete segments, pages per tier, index health. |

### Group lifecycle (used by hooks)

| Command | Description |
|---|---|
| `akw group start` | Start (or continue) a group. Used by `SessionStart` hook. Persists `AKW_GROUP_ID` to env so subsequent hooks see the active group. |
| `akw group end` | End the active segment. Used by `SessionEnd` hook. |
| `akw group status` | Print active group + segment metadata. |
| `akw group list [--recent]` | List groups for continuation lookup. |
| `akw group context` | Print recent group/segment summary. |
| `akw group prompt` | Buffer user prompt from `UserPromptSubmit` hook (stdin JSON). |
| `akw group turn [--batch-size N]` | Buffer turn from `Stop` hook (stdin JSON); flushes every N turns (default 10). |
| `akw group flush` | Flush buffered turns to the database. |
| `akw group turns <group_id> [--segment-start ISO]` | Print raw turns for a group's segment. Used by `akw recover` follow-ups so the curator can inspect stub-draft sources. |

### Curation & recovery

| Command | Description |
|---|---|
| `akw archive <draft_path>` | Move a session draft into `1_drafts/_archived/sessions__<basename>.md` (flat-file, prefix-marked) and update `draft_state` (`archived_at`, new `draft_path`) atomically. |
| `akw recover [--dry-run]` | Two-pass: (1) write `idle_close` markers for orphan groups (open >24h, no end marker); (2) write stub drafts for closed-no-draft segments (now including the freshly-closed orphans). Stub drafts carry `recovery_kind: idle_close \| closed_no_draft` and `turn_count` in frontmatter. Idempotent — re-running is a no-op. |

### Maintenance

| Command | Description |
|---|---|
| `akw purge [--older-than 365]` | Delete archived session drafts older than retention period (default 365 days). Active drafts and curated tiers are never auto-purged. |
| `akw reindex [--force]` | Two roles: (a) drift-recover `draft_state` from frontmatter when the table is missing or known-stale (requires `--force` if non-empty); (b) reconcile manual file moves (e.g. `git mv` into `1_drafts/_archived/`). Also rebuilds the DuckDB search index from indexed tiers (drafts + `2_knowledges/`). |

### Inspection

| Command | Description |
|---|---|
| `akw groups [--project X]` | List recent groups with summaries. For quick inspection without opening an agent. |
| `akw search "query" [--tier T]` | Search memory from the terminal. Returns ranked results. |
| `akw skill search "query" [--domain D]` | Search skill bundles. Thin wrapper around `akw search --tier=skill` with a domain pre-filter. |
| `akw skill show <path or domain/slug>` | Print SKILL.md + a list of `resources/`, `scripts/`, `tests/` companions. |
| `akw agent search "query" [--domain D]` | Search agent personas. |
| `akw agent show <path or domain/slug>` | Print persona file content. |

### Design Principle

**CLI for admin and automation, MCP for agents.** The MCP server is the core — agents use it during sessions. The CLI wraps the core for curator-facing operations (archive, recover, status, inspection) that don't belong inside an agent session.

```
┌─────────────────────┐     ┌─────────────────────┐
│  Agents (Claude,    │     │  CLI (akw)           │
│  Codex, OpenCode)   │     │  - archive / recover │
│  - group lifecycle  │     │  - status / inspect  │
│  - memory read/write│     │  - purge / reindex   │
└────────┬────────────┘     └────────┬──────────────┘
         │ (MCP protocol)            │ (direct)
         ▼                           ▼
┌─────────────────────────────────────────────────┐
│  Core Library                                   │
│  Storage + Search + File operations             │
├─────────────────────────────────────────────────┤
│  ▲ wrapped as MCP tools    ▲ wrapped as CLI     │
│  MCP Server (no LLM)       CLI commands (no LLM)│
└─────────────────────────────────────────────────┘
```

Synthesis (drafts → knowledge) and skill compilation (knowledge → skills) happen **outside** this picture, in the curator's editor against the file system. Neither the MCP nor the CLI calls an LLM.

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
- `AKW_GROUP_ID` — current active group (set by hooks; consumed by subsequent hooks and `akw group *`).

The idle-close threshold for `group_log` / `group_start` is currently a code constant (`DEFAULT_IDLE_CLOSE_MINUTES = 30` in `core/storage.py`). Promotion to a config knob is deferred — it has not been needed in practice.

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
5. **No LLM calls inside the MCP server or CLI** — the server stores and retrieves; the CLI manages files and lifecycle. All reasoning (summarization, pattern detection, synthesis) happens in the calling agent or in the curator's editor + LLM, against the file system.
6. **Capture-only scope with narrow carve-outs** — the MCP captures session activity into drafts. Synthesis (drafts → knowledge) and compilation (knowledge → skills/agents) are human activities outside the MCP. The MCP exposes no `promote_to_*` tools; `memory_create` / `memory_update` reject writes to `2_knowledges/`, `3_intelligences/`, `0_configs/`, and `1_drafts/_archived/`. Carve-outs (e.g. `2_knowledges/preferences/`) are narrow exceptions where the agent's write is itself the curated state; deletes on carve-outs archive instead of unlinking.
7. **365-day retention for archived drafts** — `maintain_purge` deletes archived session drafts (`1_drafts/_archived/sessions__*.md`) older than the retention boundary. Active drafts and curated tiers are never auto-purged. Raw turns persist in SQLite alongside their group; aging-out raw turns is a future operational concern.
8. **No secrets in knowledge** — the server must sanitize content before storing. Agents may inadvertently log API keys, tokens, passwords, or credentials in turn summaries or draft pages. Safeguards:
   - **Write-time scanning** — `group_log`, `memory_create`, and `memory_update` scan content for common secret patterns (API keys, tokens, connection strings, private keys) and redact or reject them.
   - **Tool descriptions** — instruct agents to never include secrets, credentials, or sensitive tokens in turn summaries or knowledge pages.
   - **Curation layer** — the curator reviews drafts (in Claude Code, Obsidian, or any editor) before synthesizing into knowledge, providing a human check for leaked secrets.

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
