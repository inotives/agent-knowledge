# MCP → CLI Migration Guide

This guide maps every `agent-knowledge` MCP tool to its `akw` CLI replacement. Use it to port any tool, agent harness, script, or project that previously called the MCP server.

> **Status:** This guide accompanies [EP-00010](EP-00010_20260504_cli-only-migration.md). Once that EP ships, the MCP server (`agent-knowledge-server`) is removed and the CLI (`akw`) is the only supported entry point.

---

## TL;DR

1. **Replace MCP tool calls with `akw` subcommands.** All 20 MCP tools have direct CLI equivalents (table below).
2. **Use `--json` for programmatic output.** Any subcommand whose MCP form returned a `dict` accepts `--json` and prints the same payload to stdout.
3. **Stop relying on the `instructions` field.** Inject session-start guidance via a `SessionStart` hook (or an equivalent for your harness) that prints `.claude/akw-instructions.md` to stderr.
4. **Remove the MCP server registration.** Strip the `agent-knowledge` entry from your harness's MCP config (e.g. `~/.claude/.mcp.json`). The new `install.sh` does this automatically.
5. **Hooks need no changes.** The Claude Code hooks shipped in this repo already call `akw` — no migration required for hook-based capture.

After upgrade, verify the install:

```bash
akw --version    # akw, version 0.2.0  (or newer)
akw status       # data dir + page/group counts
```

---

## Quick reference

| Old MCP tool | New CLI command | Audience |
|---|---|---|
| `project_create(name, path, tags)` | `akw project new --name --path [--tags]` | admin |
| `project_list()` | `akw project ls [--json]` | admin |
| `group_start(group_id?, agent?, metadata?)` | `akw group start [--group-id] [--agent] [--working-dir] [--json]` | agent |
| `group_status()` | `akw group status [--json]` | agent |
| `group_end(group_id?)` | `akw group end [--group-id]` | agent |
| `group_log(group_id, turns)` | `akw group turn` + `akw group flush` (hook-driven; see below) | agent (hooks) |
| `memory_search(query, tier?)` | `akw search <query> [--tier]` | agent |
| `memory_read(path)` | `akw memory read <path> [--json]` | agent |
| `memory_create(path, title, content, tags?, summary?, group_id?)` | `akw memory create --path --title --content [--tags] [--summary] [--group-id]` | agent (drafts only) |
| `memory_update(path, content, summary?)` | `akw memory update <path> --content [--summary]` | curator |
| `memory_delete(path, reason?)` | `akw memory rm <path> [--reason]` | curator |
| `memory_index(tier?)` | `akw memory ls [--tier] [--json]` | agent |
| `memory_history(limit?, page_path?)` | `akw memory history [--page-path] [--limit] [--json]` | curator |
| `skill_search(query, domain?)` | `akw skill search <query> [--domain]` | agent |
| `skill_get(skill_path)` | `akw skill show <path-or-domain/slug> [--json]` | agent |
| `agent_search(query, domain?)` | `akw agent search <query> [--domain]` | agent |
| `agent_get(agent_path)` | `akw agent show <path-or-domain/slug> [--json]` | agent |
| `maintain_get_stats(stale_days?)` | `akw maintain stats [--stale-days] [--json]` | admin |
| `maintain_reindex()` | `akw reindex [--force]` | admin |
| `maintain_purge(older_than_days?)` | `akw maintain purge [--older-than-days]` | admin |

The audience column matches the subcommand-group split documented in EP-00010 Decision B. Operations marked **curator** or **admin** are not intended for in-session agent use.

---

## Per-tool migration

Each section shows the MCP signature, the CLI equivalent, and a worked example.

### Project lifecycle

#### `project_create` → `akw project new`

```python
# Before (MCP)
project_create(name="my-project", path="/abs/path", tags=["py", "cli"])
```

```bash
# After (CLI)
akw project new --name my-project --path /abs/path --tags py,cli
```

#### `project_list` → `akw project ls`

```python
# Before
project_list()  # returns list[dict]
```

```bash
# After
akw project ls            # human-readable
akw project ls --json     # same payload as MCP, for programmatic use
```

### Group lifecycle (session capture)

> **Most consumers will not migrate these manually.** The session-capture hooks in `.claude/hooks/` already call `akw group start/turn/flush/end`. If you only used MCP via Claude Code hooks, you have nothing to do here.

#### `group_start` → `akw group start`

```python
# Before
group_start(group_id="abc...", agent="claude", metadata={"project_id": "..."})
# returns {"group_id", "segment_start_at", "pending", "recommended_context"}
```

```bash
# After
akw group start --group-id abc... --agent claude --json
# stdout: same JSON payload
```

If you previously inlined the MCP return into a system reminder, capture the JSON and feed it into your prompt template:

```bash
GROUP_INFO=$(akw group start --json)
# parse with jq, or pass directly into your harness
```

#### `group_status` → `akw group status`

```bash
akw group status --json
```

#### `group_end` → `akw group end`

```bash
akw group end                  # ends most recent open segment
akw group end --group-id abc   # explicit
```

#### `group_log` → hook-driven

The MCP `group_log(group_id, turns)` accepted a batch of turns. The CLI splits this into:

- `akw group prompt` — buffer a user prompt (called by `UserPromptSubmit` hook)
- `akw group turn` — buffer a turn pair (called by `Stop` hook)
- `akw group flush` — flush the buffer to SQLite (called by `SessionEnd` hook)

If you have a non-hook integration that previously called `group_log` directly, the closest match is to write each turn through `akw group turn` and finish with `akw group flush`. The hooks in `.claude/hooks/` are the canonical reference.

### Memory (read & search)

#### `memory_search` → `akw search`

```bash
akw search "auth middleware"
akw search "auth middleware" --tier 2_knowledges
```

#### `memory_read` → `akw memory read`

```bash
akw memory read 2_knowledges/architecture/event-bus.md
akw memory read 2_knowledges/architecture/event-bus.md --json   # returns {path, content} (content is the raw file body, frontmatter included)
```

#### `memory_index` → `akw memory ls`

```bash
akw memory ls
akw memory ls --tier 1_drafts --json
```

### Memory (write)

> Capture-path writes (`akw memory create`) are agent-safe — they reject any path under `0_configs/`, `2_knowledges/`, or `3_intelligences/` via `paths.reject_curated_write()`, with the message: `` Cannot write to `<prefix>` — curator-only tier. Path rejected: <path> ``. Edit/delete operations are curator-only by intent; no runtime gate prevents an agent from calling them, but they're documented as out-of-scope for in-session use.

#### `memory_create` → `akw memory create`

```python
# Before
memory_create(
    path="1_drafts/sessions/abc12345-2026-05-04T15-30.md",
    title="Session: CLI migration discussion",
    content="...",
    tags=["session", "migration"],
    summary="Discussed deprecating MCP server in favor of pure CLI.",
    group_id="abc...",
)
```

```bash
# After
akw memory create \
  --path "1_drafts/sessions/abc12345-2026-05-04T15-30.md" \
  --title "Session: CLI migration discussion" \
  --content "$(cat draft.md)" \
  --tags session,migration \
  --summary "Discussed deprecating MCP server in favor of pure CLI." \
  --group-id abc...
```

For long content, pass via a heredoc, file substitution, or `--content-file <path>` if your shell makes inline content awkward.

#### `memory_update` → `akw memory update` (curator)

```bash
akw memory update 1_drafts/notes/foo.md --content "$(cat new.md)" --summary "Reworded intro"
```

`--content` / `--content-file` replaces the entire file body — there is no merge with existing frontmatter. To preserve `title` / `tags:` / `summary:` lines, include them in the new content. `--summary` records an edit summary in the audit history table; it is **not** written to the page itself.

#### `memory_delete` → `akw memory rm` / `akw archive` (curator)

```bash
# Curated tier (knowledge/skill/agent) — hard-deletes (or moves to the
# archive-redirect target if the path is in an archived-redirect-aware tier):
akw memory rm 2_knowledges/auth/legacy.md --reason "Superseded by 2_knowledges/auth/overview.md"

# Drafts cannot be removed via `memory rm` — use `akw archive` to move them
# under `1_drafts/_archived/sessions__*.md`:
akw archive 1_drafts/sessions/abc12345-2026-05-04T15-30.md
```

`akw memory rm` rejects any path under `1_drafts/` with: `Drafts cannot be deleted via this command. The curator removes drafts via the file system.` This mirrors the MCP boundary — agent-writable drafts are archived, never deleted, so audit history stays intact.

#### `memory_history` → `akw memory history`

```bash
akw memory history --limit 20
akw memory history --page-path 2_knowledges/auth/overview.md --json
```

### Skill & agent discovery (already CLI-mirrored)

These tools have shipped in the CLI since EP-00009. The MCP tools were thin wrappers around the same core. Migration is purely renaming.

| MCP | CLI |
|---|---|
| `skill_search("incident response")` | `akw skill search "incident response"` |
| `skill_search("incident response", domain="workflow")` | `akw skill search "incident response" --domain workflow` |
| `skill_get("workflow/incident_commander")` | `akw skill show workflow/incident_commander` |
| `skill_get("3_intelligences/skills/workflow/incident_commander/SKILL.md")` | `akw skill show 3_intelligences/skills/workflow/incident_commander/SKILL.md` |
| `agent_search("code review", domain="engineering")` | `akw agent search "code review" --domain engineering` |
| `agent_get("engineering/code-reviewer")` | `akw agent show engineering/code-reviewer` |

`akw skill show --json` returns `{path, domain, slug, title, content, resources, scripts, tests}`; `akw agent show --json` returns `{path, domain, slug, title, content}`. In both, `content` is the raw file body (frontmatter included).

### Maintenance

#### `maintain_get_stats` → `akw maintain stats`

```bash
akw maintain stats --stale-days 30
akw maintain stats --json
```

#### `maintain_reindex` → `akw reindex`

```bash
akw reindex          # standard rebuild
akw reindex --force  # also reconcile draft_state with on-disk drafts
```

#### `maintain_purge` → `akw maintain purge`

```bash
akw maintain purge --older-than-days 90
```

(Currently a no-op in both transports; the CLI form is preserved for future implementation.)

---

## Replacing the `instructions` injection

The MCP server set a `FastMCP(instructions=...)` block describing group lifecycle, wrap-up flow, draft staging, and pending counts. This block was injected into every session as a system reminder.

There is no equivalent CLI mechanism — the CLI is invoked per call and has no session lifecycle of its own. To preserve the same behaviour:

1. The repo ships the instructions text at `.claude/akw-instructions.md`.
2. The `SessionStart` hook (`.claude/hooks/session-start.sh`) `cat`s it to stderr after the existing group-context block. Claude Code surfaces stderr from `SessionStart` hooks as a system reminder.

If your harness is **not** Claude Code, replicate this pattern with whatever per-session-start mechanism it offers. If it has none, prepend the contents of `akw-instructions.md` to your system prompt manually.

For agents that don't run interactive sessions (cron jobs, CI tasks, one-shot scripts), the instructions are not needed — those flows don't trigger wrap-up or draft staging.

---

## Replacing the MCP server registration

If you have an existing install with the MCP server wired into Claude Code, remove it:

```bash
# 1. Remove the entry from ~/.claude/.mcp.json (the new install.sh does this for you)
jq 'del(.mcpServers."agent-knowledge")' ~/.claude/.mcp.json > ~/.claude/.mcp.json.tmp \
  && mv ~/.claude/.mcp.json.tmp ~/.claude/.mcp.json

# 2. The agent-knowledge-server binary is no longer in [project.scripts] after upgrade.
#    Stale shims on PATH can be left in place — they just won't be invoked.

# 3. Restart Claude Code so it drops the stale MCP entry.
```

For other harnesses, follow their MCP-deregistration procedure.

---

## JSON output contract

CLI subcommands whose MCP equivalents returned a `dict` accept `--json` and emit the same payload on stdout. Stable contract:

| Command | Payload |
|---|---|
| `akw group start --json` | `{group_id, segment_start_at, pending: {unarchived_session_drafts, incomplete_segments}, recommended_context}` |
| `akw group status --json` | `{group_id, segment_start_at, segment_turn_count, agent, project_id, latest_at}` |
| `akw memory read <path> --json` | `{path, content}` (raw file body, frontmatter included) |
| `akw memory ls --json` | `[{path, title, summary, tier}, ...]` |
| `akw memory history --json` | `[{id, group_id, page_path, tier, action, summary, created_at}, ...]` |
| `akw search <q> --json` | `[{path, title, summary, tier, score}, ...]` |
| `akw skill search <q> --json` | `[{path, title, summary, tier, score}, ...]` |
| `akw skill show <ref> --json` | `{path, domain, slug, title, content, resources, scripts, tests}` |
| `akw agent search <q> --json` | `[{path, title, summary, tier, score}, ...]` |
| `akw agent show <ref> --json` | `{path, domain, slug, title, content}` |
| `akw project ls --json` | `[{id, name, path, tags, created_at, metadata}, ...]` |
| `akw maintain stats --json` | `{pages: {knowledge, skills, agents, drafts}, stale_pages, groups: {total, open, orphaned, closed_no_draft_segments}}` |

Stdout is JSON only when `--json` is set; human-readable formatting otherwise. Errors always go to stderr; CLI exits non-zero on failure.

---

## FAQ

**Q: Does the migration change any behaviour or data layout?**
No. `agent_knowledge.core` (storage, search, memory, paths, sanitizer) is untouched. SQLite + DuckDB schemas, file layout under `0_configs / 1_drafts / 2_knowledges / 3_intelligences/`, draft frontmatter format, and the BM25 index are all unchanged.

**Q: Will my existing memory and groups still be readable?**
Yes. The CLI uses the same databases and file layout the MCP server used.

**Q: I had a custom integration that called `group_log` from a non-hook context. What now?**
Use `akw group turn` followed by `akw group flush`. See the [hook-driven section](#group_log--hook-driven). If your use case is recurring, file an issue and we'll consider exposing a single batch entry point.

**Q: Can I still run the MCP server from an older release?**
Yes — older versions of this package still expose `agent-knowledge-server`. The migration starts from the EP-00010 release. If you need to stay on MCP, pin the prior version.

**Q: Will MCP come back?**
Maybe — if a future use case (browser-based agents, sandboxed harnesses without shell access) needs it, MCP will be re-introduced as a thin wrapper around the CLI in a separate EP. Re-wrapping is strictly easier than maintaining both surfaces today.

**Q: How do I know which subcommands are agent-safe vs curator-only?**
The audience column in the [quick reference table](#quick-reference) is authoritative. By convention, agent-safe subcommands are documented in `.claude/akw-instructions.md`; curator/admin subcommands are documented in `AGENTS.md` and aren't surfaced to in-session agents.
