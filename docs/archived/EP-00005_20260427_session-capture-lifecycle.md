# EP-00005 — Session Capture Lifecycle

## Scope statement (read first)

EP-00005 covers **agent-driven capture only**. The MCP's responsibility ends at producing `drafts/sessions/<group>-<segment>.md`. Synthesis of those summaries into `knowledge/` and compilation into `skills/` is **human work**, performed ad-hoc against the memory folder using whatever tools the curator chooses (typically Claude Code, Obsidian, manual edit). The MCP does not synthesize, propose, promote, or otherwise modify curated tiers.

This is a deliberate scope cut from earlier drafts of this EP that included LLM synthesis, similarity flagging, draft promotion, and a maturation pipeline. Those flows were removed because:

- The single curator already uses Claude Code daily in the memory folder. "Open the folder, ask Claude to find patterns and write knowledge" is a workflow that needs no MCP support.
- The hardest correctness questions (multi-source provenance, archive timing, transactional review, similarity scoring) all lived in the synthesis path. Cutting that path eliminates the bulk of the design surface.
- The capture pipeline (group lifecycle, marker turns, opt-in pending hint) is the genuinely valuable structural work and ships cleaner without the synthesis baggage.

See **Out of Scope** at the bottom for a complete list of what's explicitly excluded.

**Frontmatter conventions for `knowledge/` and `skills/` pages** live in `knowledge/knowledge-management.md` (bootstrapped as part of Phase 6). The curator points Claude (or any LLM) to that page when invoking synthesis or compilation, so frontmatter and house rules stay consistent across pages without the MCP enforcing them. The wiki documents itself — conventions are content, not configuration.

---

## Problem / Pain Points

Current state has gaps in lifecycle modeling and one integrity gap. Tracing `server.py` and `cli.py` against `SPECS.md`:

### Lifecycle
1. **`sessions` table is mostly denormalized labels** (agent, project_id, type) + lifecycle timestamps. The lifecycle can live on `turns` directly via marker rows, removing a table and its sync surface.
2. **Continuation isn't first-class.** No clean way to express "continue group abc123 next week" without a new session row plus implicit linkage.
3. **Orphans accumulate silently.** Sessions that exit without an end signal stay open forever (server.py has no idle-close path).

### Pending-review hot path
4. **`has_pending_review` triggers on same-day sessions** (server.py:234–239). Spec says same-day shouldn't trigger.
5. **Pending-review computation scans frontmatter.** No indexed counts — gets slower with corpus size.
6. **Catch-up runs unsolicited at session_start.** Burdens fresh sessions before the user has asked anything.

### Integrity
7. **`memory_create` accepts writes to `knowledge/` and `skills/`.** Agents can bypass curation by writing curated paths directly through the MCP. Current `memory_delete` already guards `drafts/` (per the v0.4 hotfix); the symmetric guard on curated tiers is missing.

### Capture loss
8. **Session drafts are deleted on retention purge.** Conversation summaries vanish before the curator may want to verify a curated knowledge page against its source. Archive-not-delete preserves traceback at trivial storage cost.

---

## Architectural Direction

### Decision A — Drop the `sessions` table; `turns` becomes primary

The `sessions` table mostly carries denormalized labels plus lifecycle timestamps. All of that can live on turns. **Conversation/session/task boundaries become explicit marker turns.**

`turns` schema:

```
turns(
  id,
  group_id,        -- stable handle: session_id (Claude), conversation_id or task_id (barebone)
  kind,            -- NOT NULL: 'start' | 'turn' | 'end' | 'idle_close'
  request,         -- nullable on start/end markers
  response,        -- nullable on start/end markers
  metadata,        -- JSON: agent, project_id, conversation_id|task_id, working_dir, ...
  created_at
)
```

- **Claude flow:** SessionStart hook writes `kind='start'` with `{agent: "claude", project_id, working_dir, conversation_id}`. SessionEnd hook writes `kind='end'`.
- **Barebone conversation flow:** start marker carries `{agent: "barebone-agent", conversation_id}`.
- **Barebone task flow:** start marker carries `{agent: "barebone-agent", task_id}`.

**Continuation reuses the same `group_id` but creates a new segment.** "Continue group abc123" → new `start` marker with `group_id=abc123`. A *segment* is one start→end pair. A group may have N segments over time. **Each segment produces its own draft** — past drafts are never overwritten.

Segment identity:
- A segment begins at a `kind='start'` marker and ends at the next `kind='end'` or `kind='idle_close'` marker for the same `group_id`.
- Segments are queryable from turns alone: pair consecutive start/end markers per `group_id`, ordered by `created_at`.
- Draft filename embeds segment start time: `drafts/sessions/<group_id_first_8>-<segment_start_compact_iso>.md` (e.g. `abc123ef-20260429-0930.md`). Collision-free even for multiple same-day segments.

**Idle-close mechanics (lazy at next-touch):** when `group_log` or `group_start` is called, storage checks whether the target group's latest turn is older than 30 minutes. If so:
- For `group_log` against an existing group: write `kind='idle_close'` for the stale segment first, then write `kind='start'` for a new segment of the **same `group_id`**, then write the requested `kind='turn'`. This is continuation-by-implicit-resumption — the group_id persists, the segment cycles. The agent need not explicitly reopen the group.
- For `group_start` with an existing `group_id`: same idle_close-then-restart, then proceed with the new segment.
- For `group_start` with a new `group_id`: no implicit close needed.

This keeps the stale segment honest (it gets a real end marker, becomes a candidate for summarization) without forcing the agent harness to manage idle timers. The 30-minute threshold is config-driven (`AKW_IDLE_CLOSE_MINUTES`, default 30).

**Summarization after idle-close.** The resuming agent does **not** have the stale segment's turns in its working context (they happened >30min ago, possibly in a prior process). The `group_end`-style hint that fires after `idle_close` instructs the resuming agent to: (1) call `get_segment_turns(group_id, segment_start_at)` to fetch the stale segment's raw turns, (2) summarize those turns, (3) write the summary draft. This is a real summary (not a stub) because an active agent is present to do the work. If the resuming agent declines or fails, the stale segment falls through to closed-no-draft state and is recoverable via `akw recover` (Decision F) as a stub.

### Decision B — Lifecycle contract: every group ends with an end marker

```
kind='start' → kind='turn' (n) → kind='end' → summarize current segment → drafts/sessions/<group>-<seg_iso>.md
```

**Every segment must end with an end marker before it's eligible for summarization. Summarization scope is always the current segment (last start → end), never the whole group.** All paths converge on writing the end marker:

| Path | Writer | Marker kind | Draft outcome |
|---|---|---|---|
| Happy path (Claude) | SessionEnd hook | `end` | Agent writes summary draft via `memory_create` |
| Happy path (barebone, explicit signal) | Agent harness | `end` | Agent writes summary draft via `memory_create` |
| Idle close (no end signal arrives) | Lazy at next-touch (>30min idle) | `idle_close` | Resuming agent fetches the stale segment's turns via `get_segment_turns` and summarizes into a draft via `memory_create` |
| Orphan recovery (open >24h, agent crashed) | `akw recover` (explicit CLI) | `idle_close` | Stub draft (curator decides next) |
| Closed-no-draft recovery (end marker exists, draft never written) | `akw recover` (explicit CLI) | (none — already closed) | Stub draft (curator decides next) |

**Writing the end marker is the trigger for summarization.** `group_end` returns a hint to the caller: target draft path, segment scope, summarization instruction. The agent then summarizes its own current segment's turns. After idle-close (Decision A), the resuming agent fetches and summarizes the stale segment's turns. Orphan recovery is different: there is no active agent to summarize, so `akw recover` writes a stub draft instead — see Decision F.

Lifecycle queries become simple and indexable:
- "Open groups": latest turn for `group_id` is not an end marker (`end` or `idle_close`).
- "Orphan groups" (subset of open): latest turn is not an end marker AND its `created_at` is older than 24h.
- "Closed-no-draft segments": a paired end marker exists for the segment, but no `draft_state` row at the expected `(group_id, segment_start_at)` exists.
- "Incomplete segments" (umbrella): orphan groups + closed-no-draft segments. Both are recovered by `akw recover` (Decision F).
- "Unarchived session drafts": `draft_state.archived_at IS NULL`.

### Decision C — Opt-in pending review with cheap counts

`group_start` (the renamed `session_start`) does **not** auto-trigger catch-up. It returns indexed counts that the agent surfaces as a heads-up:

```json
{
  "session": {...},
  "pending": {
    "unarchived_session_drafts": 12,
    "incomplete_segments": 3
  },
  "recommended_context": [...]
}
```

- `unarchived_session_drafts`: session drafts that exist in `drafts/sessions/` and are not archived. Excludes today's. The curator reviews them and (optionally) synthesizes into knowledge before archiving.
- `incomplete_segments`: orphan groups (open >24h, no end marker) + closed-no-draft segments (end marker but no `memory_create` followed). Recovered by `akw recover` — see Decision F.
- `recommended_context`: existing field, retained as-is. Returns relevant knowledge pages (by tag/path match against the new session's `metadata`) for the agent to load into its working set. Implementation unchanged from the current `session_start`.

If any field is non-zero, the agent surfaces it to the user in its first response: *"You have 12 session summaries from prior days waiting and 3 incomplete segments. Open the memory folder to review summaries; run `akw recover` to write stub drafts for the incomplete ones."* User decides — there is no `/review` slash command, no automated processing.

What "review" means under this scope: the human `cd`s to the memory folder, opens Claude Code (or any editor + LLM combo), reads through `drafts/sessions/`, finds patterns, and writes `knowledge/<topic>.md` directly. The MCP plays no role beyond providing the source material.

The `draft_state` table backs the counts and tracks archive state. Only one kind of draft is tracked: session drafts.

```sql
CREATE TABLE draft_state (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  draft_path TEXT NOT NULL UNIQUE,    -- mutated on archive; id is the stable handle
  group_id TEXT NOT NULL,
  segment_start_at TEXT NOT NULL,
  segment_end_at TEXT NOT NULL,        -- backs the exclude-today filter
  created_at TEXT NOT NULL,            -- when this draft_state row was inserted (stub or real)
  archived_at TEXT
);
CREATE INDEX idx_draft_state_pending ON draft_state(archived_at, segment_end_at);
CREATE INDEX idx_draft_state_group ON draft_state(group_id);
```

Hot-path query:
```sql
SELECT COUNT(*) FROM draft_state
WHERE archived_at IS NULL
  AND segment_end_at < ?;  -- exclude today
```

Sub-millisecond regardless of N.

The table is canonical; frontmatter on the draft file mirrors a few fields as a courtesy projection for human readability in Obsidian. Reads always go to the table. `akw reindex` rebuilds the table from frontmatter as a recovery path if drift is detected.

### Decision D — Archive session drafts; never delete

When a session draft is no longer active work, **move** it to `drafts/archived/sessions/` instead of deleting. Preserves source context for verification when curated knowledge needs traceback.

The trigger is **explicit and human-driven** — there is no automated review flow that fires archive:

- **Primary path (file-system move):** human moves the file in their editor / via `git mv`. `akw reindex` (run on demand or as part of `akw status`) detects the move and reconciles `draft_state` (set `archived_at`, update `draft_path`).
- **Convenience CLI:** `akw archive <draft_path>` does the move + `draft_state` UPDATE atomically in one command.

`maintain_purge` (365-day default) **deletes** archived drafts at the retention boundary — that's the long-tail cleanup point. Active `drafts/sessions/` and curated `knowledge/` are never auto-purged.

Archived drafts stay excluded from search (the DuckDB indexer already skips `drafts/`).

### Decision E — Tier write boundary

`memory_create` and `memory_update` reject writes to paths under `knowledge/`, `skills/`, and `drafts/archived/`. The MCP cannot modify curated tiers or archive paths regardless of caller, mirroring the existing `memory_delete` guard on `drafts/`. Archive paths are reachable only via the `akw archive` CLI flow (Decision D), which moves files into `drafts/archived/sessions/` as part of an explicit curator action.

Curation happens **outside the MCP**, via the human's editor on the file system (Claude Code's `Edit` tool, Obsidian, manual edit, `git mv`). That's a separate, broader trust boundary — file-system access in the wiki folder is unguarded by design, and that's correct: curation is a trusted human action.

This also means: **the MCP exposes no promotion tools.** No `promote_to_knowledge`, no `promote_to_skill`. If those tools are present in the current code, they are removed as part of this EP.

### Decision F — Incomplete-segment recovery via explicit CLI

Two failure modes leave a segment in an incomplete state:

- **Orphan group:** agent crashed (or was killed) before writing any end marker. Latest turn is `'turn'`, age >24h.
- **Closed-no-draft:** `group_end` was called (end marker exists) but `memory_create` for the session draft never ran — agent crashed in the gap, `memory_create` failed, etc.

Both are recovered by **`akw recover`**, an explicit curator-invoked CLI command. There is no automated recovery during `group_start` or any other tool call (would violate the opt-in principle of Decision C).

`akw recover` flow:
1. Scan for orphan groups → write `kind='idle_close'` marker for each (sets `segment_end_at` to last-turn time).
2. Scan for closed-no-draft segments (which now includes the freshly-closed orphans).
3. For each, write a **stub session draft** + `draft_state` row.
4. Print a summary: `"Wrote 2 idle_close markers; created 5 stub drafts."`

Supports `--dry-run` to preview without writing. Idempotent: re-running after a successful recover is a no-op.

**Stub draft template** (written by `akw recover`):

```markdown
---
group_id: abc12345-...
segment_start_at: 2026-04-20T10:30:00Z
segment_end_at: 2026-04-20T11:45:00Z
source_metadata: {agent: claude, project_id: X, working_dir: /path}
created_at: <when akw recover wrote this stub>
recovery_kind: idle_close      # or 'closed_no_draft'
turn_count: 47
---

# Session segment recovered without summary

This segment ended without a clean session-end summary. The agent likely crashed,
disconnected, or was killed before writing the draft. The raw turns are preserved
in the database.

**Inspect raw turns:** `akw group turns <group_id> --segment-start <segment_start_at>`

**Next steps (curator's choice):**
- Read the raw turns and replace this body with a real summary, then archive.
- If the segment isn't worth preserving, `akw archive <this-path>` and move on.
```

When the template is rendered, `<group_id>`, `<segment_start_at>`, and `<this-path>` are substituted with the actual values for the recovered segment so the curator can copy-paste the commands directly.

**Why a stub instead of "let the agent summarize from raw turns":** under the capture-only scope (see scope statement), the MCP doesn't summarize. The agent that owned the segment is gone. The curator is the right actor to decide whether the recovered turns are worth preserving — most won't be (crashed sessions are usually noise). The stub is honest about its state, links to the recovery actions, and counts toward `unarchived_session_drafts` so it's discoverable through normal pending counts.

The MCP **does not** expose a recover tool. Recovery is curator-only — same as `akw archive`.

### MCP tool surface after this EP

For unambiguous reference, the complete MCP tool list after EP-00005 lands:

**Group lifecycle (renamed from `session_*`):**
- `group_start` — start or continue a group; returns `pending` counts
- `group_end` — close current segment; returns summarization hint
- `group_log` — append turns; idle-close-on-stale check before write
- `group_status` — current group + segment metadata

**Memory operations (mostly unchanged):**
- `memory_create` — accepts `drafts/sessions/` paths; rejects `knowledge/`, `skills/`, and `drafts/archived/` (Decision E)
- `memory_update` — same path rules as create
- `memory_read`, `memory_search`, `memory_index`, `memory_history` — unchanged
- `memory_delete` — unchanged; existing `drafts/` guard preserved

**Maintenance:**
- `maintain_get_stats`, `maintain_purge`, `maintain_reindex` — unchanged

**Project:**
- `project_create`, `project_list` — unchanged

**Removed:**
- `session_start` / `session_end` / `session_log` / `session_status` — renamed (above)
- `promote_to_knowledge`, `promote_to_skill` — removed (Decision E)
- `review_get_pending` — removed from MCP. The agent never enumerates pending (only sees counts via `group_start`); enumeration happens CLI-side (`akw status`, `akw recover`) directly against storage helpers, no MCP round-trip needed

CLI-only (not exposed via MCP):
- `akw archive`, `akw recover`, `akw reindex`, `akw status`, `akw group turns`

---

## Implementation Phases

**Phase coupling.** Phases 1, 3, and 5 are tightly coupled and **ship together in a single release** — Phase 1 drops the `sessions` table and renames tools, which breaks any code Phase 5 deletes (`set_session_reviewed`, `_review_complete_internal`, the legacy `akw review` synthesis flow, `promote_to_*`). Splitting them across releases would leave the build broken. Phase 2 (`draft_state` + `pending`), Phase 4 (archive), and Phase 4.5 (`akw recover`) layer on after the foundational change and can be staged or deferred. Phase 6 (tests + docs) accompanies whichever phases are in flight.

**Pre-release means data wipe.** "No migration; pre-release" in Phase 1 means: there are no external users yet, the curator (sole user) is willing to drop the local SQLite DB and start fresh on the new schema. Existing `knowledge/` and `skills/` markdown files are content, not schema, and survive untouched. Existing `drafts/sessions/` markdown files: if any exist, the curator chooses to keep or discard before running migrations; `akw reindex` rebuilds `draft_state` from surviving frontmatter.

### Phase 1 — Schema (no migration; pre-release)
- [ ] Drop `sessions` table from schema/migrations
- [ ] `turns`: drop `session_id`, add `group_id` (TEXT NOT NULL), add `kind` (TEXT NOT NULL CHECK kind IN ('start','turn','end','idle_close'))
- [ ] Indexes: `(group_id, created_at)` and `(kind)`
- [ ] `memory_edits`: drop `session_id`, add `group_id` (TEXT NULL — manual edits still allowed without a group)
- [ ] Storage layer: `start_group`, `end_group`, `create_turns` (with idle-close-on-stale check, see Decision A), `get_group_turns`, `get_current_segment_turns`, `get_segment_turns(group_id, segment_start_at)` (specific past segment for `akw recover` and `akw group turns`), `get_group_segments`, `get_open_groups`, `get_orphaned_groups`, `get_closed_no_draft_segments`, `list_groups(filter_metadata)`
- [ ] MCP tools: `group_start` / `group_end` / `group_log` / `group_status` (rename from `session_*`). No backward-compat alias — pre-release, callers update directly.
- [ ] `memory_create` accepts `group_id: str | None` parameter (replaces `session_id`); auto-bound to active group from env var or active-group lookup if omitted
- [ ] CLI commands: `akw group start/end/log/status/list/context/prompt/turn/flush`
- [ ] Hook scripts updated; `AKW_SESSION_ID` env var → `AKW_GROUP_ID`. Curator updates `~/.claude/settings.json` hooks to match (no in-flight users to migrate, so no compatibility shim).
- [ ] `group_end` returns `{group_id, segment_start_at, segment_end_at, draft_path, summarization_hint}` scoped to current segment
- [ ] **Metadata queryability:** `turns.metadata` is JSON. `list_groups(filter_metadata={"agent": "claude"})` uses `json_extract(metadata, '$.agent') = ?`. Acceptable while group counts are small; revisit (promote `agent` and `project_id` to first-class indexed columns on the start-marker turn) if `list_groups` profiling shows slow paths.

### Phase 2 — `draft_state` table; opt-in pending hint
- [ ] New `draft_state` table + indexes (Decision C)
- [ ] `memory_create(path='drafts/sessions/...')` writes `draft_state` row + frontmatter atomically
- [ ] `group_start` returns `pending` counts via indexed SQL — no file scans
- [ ] `pending.incomplete_segments` count: orphans (open >24h, no end marker) + closed-no-draft segments. Indexed query — see Decision B for the SQL shape
- [ ] `exclude_today=True` consistently across queries
- [ ] `akw reindex` has two roles: **(a) drift recovery** — rebuilds `draft_state` from on-disk frontmatter when the table is missing or known-stale; **(b) reconciliation** — picks up file moves the curator made by hand (e.g. `git mv` into `drafts/archived/sessions/`) instead of via `akw archive`. Role (a) requires `--force` when the table is non-empty so a stale frontmatter file can't silently overwrite canonical state; role (b) is safe to run anytime (only updates rows whose `draft_path` no longer exists at the recorded location).
- [ ] Update `session_bootstrap` MCP prompt (renamed to `group_bootstrap`) — exposed via the MCP `prompts/list` and `prompts/get` machinery; the agent fetches it at session start. Body: surfaces `pending` counts but does NOT auto-process
- [ ] Update `session_wrapup` MCP prompt (renamed to `group_wrapup`) + server `instructions` block
- [ ] `akw status` shows the same `pending` lists for human inspection (replaces the legacy `akw review` LLM-synthesis command, which is deleted in Phase 5)

### Phase 3 — Tier write boundary
- [ ] `memory_create` rejects writes to `knowledge/`, `skills/`, and `drafts/archived/` (returns clear error; mirrors existing `memory_delete` drafts/ guard)
- [ ] `memory_update` rejects writes to `knowledge/`, `skills/`, and `drafts/archived/`
- [ ] Remove `promote_to_knowledge` / `promote_to_skill` MCP tools and matching CLI subcommands entirely (no deprecation shim — pre-release scope per Phase coupling note)
- [ ] Server `instructions` updated to document the boundary explicitly

### Phase 4 — Archive flow
- [ ] `akw archive <draft_path>` CLI command — moves file to `drafts/archived/sessions/<basename>`, UPDATEs `draft_state` (set `archived_at = now`, update `draft_path` to new location)
- [ ] `akw reindex` detects manual file moves into `drafts/archived/` and reconciles `draft_state`
- [ ] `init` ensures `drafts/archived/sessions/` exists
- [ ] `maintain_purge` deletes archived drafts at retention boundary (365-day default)
- [ ] Archived drafts stay excluded from search (DuckDB indexer already skips `drafts/`; verify it also skips `drafts/archived/`)

### Phase 4.5 — Incomplete-segment recovery (`akw recover`)
- [ ] `akw recover [--dry-run]` CLI command implements the two-pass flow from Decision F
- [ ] Pass 1: identifies orphan groups (open >24h, no end marker) and writes `kind='idle_close'` markers; `segment_end_at` set to last-turn time
- [ ] Pass 2: identifies closed-no-draft segments (now includes freshly-closed orphans) and writes stub drafts using the template from Decision F + `draft_state` rows
- [ ] Stub drafts include `recovery_kind` frontmatter (`idle_close` or `closed_no_draft`) and `turn_count`
- [ ] Idempotent: re-running after success is a no-op
- [ ] `--dry-run` prints what would happen without writing
- [ ] `akw group turns <group_id> --segment-start <iso>` CLI helper (referenced by stub-draft body) so the curator can inspect raw turns

### Phase 5 — Cleanup of removed flows (code only)
- [ ] Delete `_review_complete_internal` from `server.py` (currently dead code at server.py:530)
- [ ] Delete the legacy `akw review` LLM-synthesis CLI command (`cli.py:504+`) entirely; the read-only listing functionality lives under `akw status` (Phase 2)
- [ ] Remove `set_session_reviewed` storage helper and any code paths referencing `reviewed_at`
- [ ] Delete `promote_to_knowledge` / `promote_to_skill` implementations (Phase 3 removes the surface; Phase 5 removes the dead code)

### Phase 6 — Tests + Docs
- [ ] Update SPECS.md to reflect capture-only scope: explicitly state synthesis and promotion are out-of-MCP human activities
- [ ] Update README's review section: explain `pending` counts, manual archive flow, and where curation happens
- [ ] AGENTS.md: no change. It describes this repo's development workflow (for contributors building the MCP server). Runtime curation conventions live in the deployed memory folder (`knowledge/knowledge-management.md`), not in this repo
- [ ] Archive `docs/EP-00006_20260427_knowledge-to-skill-conversion.md` to `docs/archived/` (its scope is mooted — see Out of Scope)
- [ ] **Bootstrap `knowledge/knowledge-management.md`** as the first curated knowledge page. This is the contract Claude (or any LLM) reads before synthesizing session drafts into knowledge or compiling knowledge into skills. Suggested content (outer fence is four backticks to allow nested triple-backtick blocks below):

  ````markdown
  ---
  title: Knowledge Management Conventions
  tags: [meta, conventions, frontmatter]
  summary: Frontmatter shapes and curation conventions for knowledge and skill pages.
  created_at: <iso8601>
  updated_at: <iso8601>
  sources: []
  related: []
  ---

  # Knowledge Management Conventions

  This page is the contract for writing curated content in this wiki. Read it before
  synthesizing session drafts into knowledge, or compiling knowledge into skills.

  ## What lives where

  - `drafts/sessions/` — **MCP-owned.** Written by the agent at session end. Do not edit
    by hand (except for stub drafts produced by `akw recover` — see below). The curator
    archives entries to `drafts/archived/sessions/` (via `akw archive` or manual move
    + `akw reindex`) when no longer active.
  - `knowledge/` — **Human-curated.** Synthesized from session drafts. Authoritative
    domain content.
  - `skills/` — **Human-curated.** Compiled from accumulated knowledge pages.
    Action-oriented "how to do X" guides.
  - `drafts/archived/sessions/` — Source material preserved for traceback. Cleaned by
    `maintain_purge` at the retention boundary (default 365 days).

  ## Stub drafts (recovery_kind in frontmatter)

  When an agent crashes mid-session, `akw recover` writes a **stub draft** with
  `recovery_kind: idle_close` (or `closed_no_draft`) in the frontmatter. The body is
  a placeholder, not a real summary. Curator's options:

  - Inspect raw turns via `akw group turns <group_id> --segment-start <iso>`, write a
    real summary into the body, then archive.
  - If the segment isn't worth keeping, archive the stub as-is — the raw turns stay in
    the DB until `maintain_purge`.

  Filter stubs in your editor by grepping `recovery_kind:` in `drafts/sessions/`.

  ## Frontmatter — knowledge pages

  ```yaml
  ---
  title: <human-readable title>
  tags: [tag1, tag2]
  summary: <one-line summary; appears in memory_index>
  created_at: <iso8601>
  updated_at: <iso8601>
  sources:
    - drafts/archived/sessions/abc12345-20260420-1030.md
    - drafts/archived/sessions/def45678-20260424-1500.md
  related:
    - knowledge/foo.md
  ---
  ```

  - `sources` is load-bearing provenance. Every knowledge page must list the session
    drafts (or other knowledge pages) it was synthesized from. Git records who/what
    changed; `sources` records upstream content lineage that git can't infer.
  - `related` is optional cross-references to other knowledge pages.
  - `updated_at` is bumped on every meaningful edit.

  ## Frontmatter — skill pages

  ```yaml
  ---
  title: <skill name>
  tags: [skill, ...]
  summary: <one-line description>
  created_at: <iso8601>
  updated_at: <iso8601>
  sources:
    - knowledge/auth-patterns.md
    - knowledge/jwt-rotation.md
  trigger: <when this skill applies — short sentence>
  ---
  ```

  - `sources` for a skill points to the knowledge pages it was compiled from.
  - `trigger` describes when an agent should invoke the skill — keep it specific.

  ## Synthesis prompt pattern

  When invoking Claude to synthesize knowledge from session drafts:

  > Read `knowledge/knowledge-management.md`, then review the session drafts under
  > `drafts/sessions/`. Look for recurring patterns, decisions, or insights worth
  > preserving. Propose new knowledge pages or updates to existing ones, following the
  > frontmatter conventions. Do not edit `drafts/sessions/` files.

  ## House rules

  - Tag taxonomy is curator-defined; reuse existing tags before inventing new ones
    (`memory_index` shows current tags).
  - One topic per knowledge page. Split rather than over-broaden.
  - When a session draft has been synthesized, archive it (`akw archive <path>`).
  - No emojis in titles or bodies unless explicitly requested.
  ````

  Bumped `updated_at` and a tag-taxonomy section can be added once the wiki has
  enough pages to need them.

#### Tests
- [ ] Unit tests: lifecycle paths (start → turns → end → idle_close)
- [ ] Unit tests: idle-close-on-stale via `group_log` against a >30min-old group writes `idle_close` + new `start` for the same `group_id`, then the requested `turn`
- [ ] Unit tests: orphan detection and `idle_close` recovery via `akw recover`
- [ ] Unit tests: closed-no-draft detection and stub-draft creation via `akw recover`
- [ ] Unit tests: `akw recover` idempotency (re-run is no-op) and `--dry-run` correctness
- [ ] Unit tests: `pending.incomplete_segments` count covers both orphan and closed-no-draft cases
- [ ] Unit tests: continuation reuses `group_id` (multi-segment groups); each segment produces its own draft; old drafts not overwritten
- [ ] Unit tests: `get_group_segments` correctly pairs start/end markers including `idle_close` and orphan-recovery markers
- [ ] Unit tests: `draft_state` writes and reindex round-trip (frontmatter ↔ table lossless)
- [ ] Unit tests: tier write guard — drafts/sessions allowed; `knowledge/`, `skills/`, `drafts/archived/` rejected on both create AND update
- [ ] Unit tests: archive flow — `akw archive` CLI happy path, manual-move detection via reindex
- [ ] Integration test: full happy path — `group_start` → log turns → `group_end` → draft created → `akw archive`
- [ ] Perf assertion: pending-counts query under 5ms at 10k draft rows

---

## Out of Scope (explicit non-goals and deferred work)

- **LLM-driven synthesis of session drafts → knowledge.** Removed from MCP. Humans synthesize using their own tools (Claude Code in the memory folder, Obsidian, etc.).
- **`promote_to_knowledge` / `promote_to_skill` MCP tools.** Removed. Promotion is a file-system move performed by the curator.
- **`drafts/knowledge/` as a tier the MCP knows about.** Not introduced. If the human chooses to drop LLM scratch output there, the MCP has no opinion.
- **`page_provenance` table.** Not introduced. Promotion provenance lives in git history (which the curator already maintains).
- **Similarity flagging, propose-draft tool schemas, multi-source provenance plumbing.** All scoped out — they only mattered when the MCP did synthesis.
- **Auto-promotion.** Explicitly rejected.
- **Eager idle-close cron.** Lazy idle-close at next-touch is sufficient.
- **EP-00006 as previously planned (knowledge → skill compilation pipeline).** Mooted by the same scope cut. If a future EP-00006 lands, it will be about something else. **Action item:** archive `docs/EP-00006_20260427_knowledge-to-skill-conversion.md` (move to `docs/archived/`) as part of this EP's commit, since its scope is now out-of-MCP human work.

## Open Questions

1. **`memory_search` over drafts.** Today the DuckDB indexer skips `drafts/`. If the human relies on Claude Code in the memory folder for synthesis, they'll use Read/Grep at the file level — which works fine at this scale. Whether to add an opt-in `memory_search(include_drafts=True)` is **deferred until measured need**.

2. **Auto-archive after N days.** Should `maintain_purge` (or a new `maintain_archive`) auto-archive session drafts older than ~30 days to reduce clutter in `drafts/sessions/` without requiring manual archive on every draft? **Deferred to operational tuning** — start with manual + CLI helper; add automation if the manual path becomes annoying.

## Status: DONE

Implementation landed on `feat/ep-00008-memory-tier-layout-migration` (combined with EP-00008). Live-tested via the deployed MCP: group/segment lifecycle, marker turns, `draft_state` indexed pending counts, capture-only scope (curated tiers reject writes), `akw recover` for incomplete segments, archive-on-review. The original `feat/ep-00005-session-capture-lifecycle` branch has the original commit history but was superseded.
