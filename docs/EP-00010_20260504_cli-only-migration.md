# EP-00010 — CLI-Only Migration (Deprecate MCP Server)

## Problem / Pain Points

After deploying and using the MCP server in real sessions, the MCP transport is no longer pulling its weight. The friction it adds — process lifecycle, per-harness configuration, schema drift — outweighs the in-loop ergonomics it was supposed to buy. Concrete pain points:

1. **Two codepaths, one core.** `server.py` (798 lines) and `cli.py` (817 lines) both wrap the same `agent_knowledge.core` modules (`storage`, `search`, `memory`, `paths`, `sanitizer`). Every new tool requires registration in both surfaces; bug fixes have to be mirrored. The MCP layer adds zero behavioural value over a thin CLI wrapper around the same functions.
2. **MCP server lifecycle is operational overhead.** A persistent stdio server has to be registered (`$HOME/.claude/.mcp.json`), restarted on schema changes, and debugged when it silently disconnects. None of this is required by a stateless CLI invoked per call.
3. **Reach is limited to MCP-aware harnesses.** The current MCP server runs in Claude Code only. Codex, Cursor, terminal scripts, cron, CI, and other agents can't use it without reimplementing the transport. The CLI runs everywhere a shell does.
4. **Tool registry has a context cost.** 20 MCP tools means 20 schemas exposed to every session that connects. Even with deferred loading, the registry occupies surface area at session start. The CLI is invoked on demand and costs nothing until used.
5. **Curator/agent boundary is a convention, not a wall.** EP-00005 declared MCP "capture-only" — synthesis and promotion are human work. Today this is enforced by *naming* (`memory_create` rejects writes outside `1_drafts/`). Pure CLI lets us split the boundary by *interface*: an `akw` subcommand group for agent-safe operations, a separate group for curator operations. Physical boundary, not a runtime check.
6. **Hooks already bypass the MCP server.** `.claude/hooks/session-start.sh` calls `akw group start` directly. `stop.sh` and `session-end.sh` call `akw group turn` / `akw group flush`. The session-capture loop — the original justification for MCP's "in-loop" pitch — has already migrated to the CLI. The MCP tools `group_start` / `group_log` / `group_end` are now duplicate entry points for an integration that hooks own end-to-end.
7. **The "instructions" injection is the only MCP-unique capability left.** `FastMCP(instructions=...)` injects a system reminder at session start telling the agent about wrap-up flow, draft staging, and pending counts. This is replaceable with a `CLAUDE.md` snippet or a `SessionStart` hook that prints the same block — both already exist in the project for other purposes.

## Suggested Solution

Deprecate the MCP server. Promote the 20 MCP tools to CLI subcommands grouped by audience (agent-safe vs. curator). Move instruction injection to the existing `SessionStart` hook. Keep `agent_knowledge.core` untouched — this is a transport refactor, not a rewrite.

| MCP tool | CLI replacement | Already exists? |
|---|---|---|
| `group_start` | `akw group start` | ✅ |
| `group_end` | `akw group end` | ✅ |
| `group_status` | `akw group status` | ✅ |
| `group_log` | `akw group turn` + `akw group flush` (hook-driven) | ✅ |
| `memory_search` | `akw search` | ✅ |
| `skill_search` | `akw skill search` | ✅ |
| `skill_get` | `akw skill show` | ✅ |
| `agent_search` | `akw agent search` | ✅ |
| `agent_get` | `akw agent show` | ✅ |
| `maintain_reindex` | `akw reindex` | ✅ |
| `memory_read` | `akw memory read` | ❌ add |
| `memory_create` | `akw memory create` | ❌ add |
| `memory_update` | `akw memory update` | ❌ add (curator) |
| `memory_delete` | `akw memory rm` | ❌ add (curator) |
| `memory_index` | `akw memory ls` | ❌ add |
| `memory_history` | `akw memory history` | ❌ add |
| `project_create` | `akw project new` | ❌ add |
| `project_list` | `akw project ls` | ❌ add |
| `maintain_get_stats` | `akw status` (extend) / `akw maintain stats` | partial |
| `maintain_purge` | `akw maintain purge` | ❌ add |

Net new CLI surface: ~9 subcommands, all thin wrappers around existing core functions.

## Decisions

### Decision A — Single-shot migration, not parallel-run

Because the CLI and MCP both call the same core, there is no behavioural divergence to reconcile. Ship the new CLI subcommands, flip the install script, remove the MCP entry point, in one release. No deprecation window, no dual-run period.

Rejected alternative: keep MCP as opt-in for one release. Adds maintenance for a transport we've decided not to invest in. If a future need for MCP arises (browser-based / sandboxed agents), wrap the CLI with a fresh, smaller MCP shell — strictly easier than maintaining two surfaces today.

### Decision B — Subcommand groups encode the curator/agent boundary

CLI surface partitions by audience:

```
akw group {start,end,status,log,turn,flush,prompt,context,list,turns}   # agent-safe
akw search <query>                                                       # agent-safe
akw skill {search,show}                                                  # agent-safe
akw agent {search,show}                                                  # agent-safe
akw memory {read,create}                                                 # agent-safe (drafts only)
akw memory {update,rm,history,ls}                                        # curator
akw project {new,ls}                                                     # admin
akw maintain {stats,reindex,purge}                                       # admin
akw recover                                                              # admin (existing)
akw init                                                                 # admin (existing)
akw status                                                               # admin (existing)
```

`akw memory create` rejects writes outside `1_drafts/` (existing `paths.reject_curated_write()` check). `akw memory update` / `akw memory rm` apply the same gate but are intended for curator use against drafts. Tier-2/3 promotion stays manual — the CLI deliberately does not expose a "promote draft" command.

### Decision C — Replace MCP `instructions` injection via the SessionStart hook

The MCP `instructions` block (`server.py` lines 35–91) describes group lifecycle, wrap-up flow, draft staging, and pending counts. Move it to:

1. A markdown file at `.claude/akw-instructions.md` shipped with the install script.
2. The existing `.claude/hooks/session-start.sh` extended to `cat` this file to stderr (already its mechanism for printing recent group context).

Result: the same system-reminder content reaches the agent without an MCP server. Agents that don't run hooks (cron, scripts) don't need the instructions — they're not human-driven sessions.

Rejected alternative: ship as a top-level `CLAUDE.md`. Pollutes user space and conflicts with project-specific CLAUDE.md content.

### Decision D — Drop the MCP entry point from `pyproject.toml`

`[project.scripts]` currently exposes:

```toml
akw = "agent_knowledge.cli:main"
agent-knowledge-server = "agent_knowledge.server:main"
```

Remove the second line. Users with the binary on PATH from a prior install will see `command not found` after upgrade — acceptable, since `install.sh` rewrites `.mcp.json` and the agent-knowledge MCP entry will simply be removed from the user's Claude Code config in the same step.

### Decision E — Keep `agent_knowledge.core` untouched

`storage.py`, `search.py`, `memory.py`, `paths.py`, `sanitizer.py` are already transport-agnostic. No refactor of the core lib. The only code that moves is the thin tool-wrapper layer in `server.py`, which gets ported into Click commands in `cli.py`.

### Decision F — Delete `server.py` rather than archive it

Once the CLI subcommands are in place and tests are green, delete `server.py` outright. Git history preserves it. Keeping a dead file in the repo invites confusion ("is this still wired up?") and drift.

### Decision G — JSON output mode for agent consumers

Add a global `--json` flag (or per-subcommand where the human-readable form is meaningfully different). Where the MCP returns structured `dict` (e.g. `group_start` returning `{group_id, segment_start_at, pending, recommended_context}`), `akw group start --json` prints the same payload. Agents parsing CLI output get parity with the MCP return contract.

## Implementation Phases

### Phase 1 — Fill in missing CLI subcommands

- [x] `cli.py`: `akw memory read <path> [--json]` — wraps `memory.read_page()`.
- [x] `cli.py`: `akw memory create --path --title (--content | --content-file) [--tags] [--summary] [--group-id]` — wraps `memory.create_page()` + draft-state upsert. Mirrors `memory_create` rejection of curated paths.
- [x] `cli.py`: `akw memory update <path> (--content | --content-file) [--summary]` — wraps `memory.update_page()`.
- [x] `cli.py`: `akw memory rm <path> [--reason]` — wraps the archive-redirect logic from `memory_delete`.
- [x] `cli.py`: `akw memory ls [--tier] [--json]` — wraps `search.get_index()`.
- [x] `cli.py`: `akw memory history [--page-path] [--limit] [--json]` — wraps `storage.get_memory_history()`.
- [x] `cli.py`: `akw project new --name --path [--tags]` — wraps `storage.create_project()`.
- [x] `cli.py`: `akw project ls [--json]` — wraps `storage.list_projects()`.
- [x] `cli.py`: `akw maintain stats [--stale-days] [--json]` — wraps the stats aggregator from `maintain_get_stats`.
- [x] `cli.py`: `akw maintain purge [--older-than-days]` — moved from top-level `akw purge`; wired to the EP-00008 archive layout (`1_drafts/_archived/sessions__*.md`).
- [x] Added `--json` flag to: `akw search`, `akw skill search`, `akw skill show`, `akw agent search`, `akw agent show`, `akw group start`, `akw group status`, `akw memory read`, `akw memory ls`, `akw memory history`, `akw project ls`, `akw maintain stats`.
- [x] `akw search` now filters intelligences tiers (`skill`, `agent`) out of the default-tier ranking — parity with the MCP `memory_search` contract.

### Phase 2 — Move instruction injection to hooks

- [x] Extracted the instructions block from former `server.py` into `.claude/akw-instructions.md` (CLI-flavoured rewrite — references `akw memory create`, `akw group end`, `--json` outputs, etc.).
- [x] Updated `.claude/hooks/session-start.sh` to `cat` `~/.agent-knowledge/akw-instructions.md` to stderr after the group-start call so Claude Code surfaces it as a system reminder.
- [x] Updated `install.sh` to copy `akw-instructions.md` into `~/.agent-knowledge/akw-instructions.md` during install.

### Phase 3 — Update install script

- [x] Removed the block that wrote `agent-knowledge` into `~/.claude/.mcp.json`.
- [x] Added an idempotent migration step that strips any prior `agent-knowledge` MCP entry on upgrade (using `python3` + `dict.pop`).
- [x] Kept the hook registration block — canonical integration path.
- [x] Added a one-line upgrade notice explaining the MCP server has been removed and the CLI is the only entry point.

### Phase 4 — Remove MCP server

- [x] Deleted `src/agent_knowledge/server.py`.
- [x] Removed `agent-knowledge-server` from `pyproject.toml` `[project.scripts]`.
- [x] Dropped `mcp` from runtime dependencies; bumped version to `0.2.0`; updated package description.
- [x] `grep -r "agent_knowledge.server"` over `src`, `tests`, `.claude`, `install.sh`, `pyproject.toml`, `README.md`, `AGENTS.md`, `docs/SPECS.md` returns no hits (only the migration docs intentionally mention the legacy entry).

### Phase 5 — Tests

- [x] Added 19 Click `CliRunner` smoke tests in `tests/test_cli.py` covering: memory read/create/update/rm/ls/history, project new/ls (incl. empty-list case), maintain stats/purge, group start/status `--json` payload shape, `akw search --json` intelligences exclusion, skill/agent show `--json`.
- [x] Verified `akw memory create` rejects curated paths (`2_knowledges/architecture/foo.md`).
- [x] Verified `akw group start --json` returns `{group_id, segment_start_at, pending: {unarchived_session_drafts, incomplete_segments}, recommended_context}`.
- [x] All 117 pre-existing core tests still pass unchanged. Total suite now: **136 passing**.

### Phase 6 — Docs

- [x] `README.md`: rewrote intro / architecture / tech stack / install / CLI command tables to reflect CLI-only; added migration callout linking to `docs/MCP_TO_CLI_MIGRATION.md`; updated stale `drafts/sessions/` paths to EP-00008 `1_drafts/sessions/` layout.
- [x] `AGENTS.md`: scanned — no `agent-knowledge-server` / MCP references found.
- [x] `docs/SPECS.md`: rewrote Overview, Scope, Architecture diagram, "Agent Discovery & Context Loading" (now describes hook injection, not MCP `instructions`), the entire MCP Tools catalog (replaced with a single CLI Commands catalog grouped by audience), Tech Stack (dropped MCP SDK row, added click), and Constraints decision #5–#9 (added a #9 noting EP-00010). Added a JSON output contract subsection and a single-transport architecture diagram.
- [x] `docs/MCP_TO_CLI_MIGRATION.md`: standalone migration guide mapping every MCP tool to its CLI equivalent, with worked examples, JSON output contract, and FAQ. Drafted alongside this EP so downstream consumers can port their integrations without reading the EP.

## Out of Scope

- **Re-architecting the core library.** `agent_knowledge.core` modules stay as-is. This EP is transport-only.
- **Removing or changing hooks.** Hooks remain the canonical integration with Claude Code. They already use the CLI.
- **Vector / embedding search, new capabilities.** No feature work in this EP.
- **Multi-user / hosted deployments.** Out of scope; this is a single-user local tool.
- **Re-adding MCP in the future.** If/when needed (browser-based agents, sandboxed harnesses), it ships as a separate, thin wrapper around the CLI in a future EP.
- **Touching `.agents/commands/`.** Slash commands remain unchanged; they don't depend on MCP transport.

## Resolved Decisions

_(none yet — to be filled in during review)_

## Status: IN REVIEW

Implementation landed on `feat/ep-00010-cli-only-migration`. Tests: **136 passing** (117 pre-existing core tests + 19 new CLI smoke tests in `tests/test_cli.py`). MCP server (`src/agent_knowledge/server.py`, 798 lines) deleted; `agent-knowledge-server` removed from `pyproject.toml`; `mcp` runtime dep dropped; package bumped to `0.2.0`. Install script strips legacy `~/.claude/.mcp.json` entries idempotently and copies `akw-instructions.md` into `~/.agent-knowledge/`. SessionStart hook now prints the instructions to stderr. README / SPECS rewritten to drop MCP-transport language; `docs/MCP_TO_CLI_MIGRATION.md` provides the per-tool mapping for downstream consumers.
