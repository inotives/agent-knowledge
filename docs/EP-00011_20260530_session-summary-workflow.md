# EP-00011 — Session Summary Workflow

Status: IN PROGRESS

## Problem / Pain Points

The current capture model stores lifecycle markers and turn-level prompt/response pairs in SQLite. It was designed for a high-fidelity audit trail, but that fidelity is not carrying enough value for the day-to-day memory workflow.

Concrete pain points:

1. **Turn logging is noisy.** `UserPromptSubmit` and `Stop` hooks collect every prompt/assistant response pair, buffer them, and periodically flush rows into `turns`. This creates raw material, but the durable artifact the user actually reads is the session draft.
2. **Session recovery adds complexity.** Because turns can exist without a final draft, the system needs orphan detection, idle-close markers, closed-no-draft recovery, and stub drafts.
3. **Start-of-session context is indirect.** `akw session start --json` currently returns pending counts and recommended context, while `akw group context` prints recent group summaries from prior turn logs/drafts. The user now wants a simpler boot path: retrieve the latest five session summaries for the current project.
4. **Hook surface is larger than needed.** `install.sh` wires four hooks: `SessionStart`, `UserPromptSubmit`, `Stop`, and `SessionEnd`. The desired model only needs session start and session close.
5. **The durable memory unit should be the session summary.** The proposed summary sections map directly to how the user reviews past work: requests, fulfillment, discoveries, completed changes, follow-ups, and metadata.

## Current Implementation References

- `.claude/hooks/session-start.sh` starts a group with `akw session start`, persists `AKW_SESSION_ID`, and prints `akw-instructions.md`.
- `.claude/hooks/user-prompt.sh` calls `akw group prompt` to stage user prompts.
- `.claude/hooks/stop.sh` calls `akw group turn` to pair the staged prompt with the latest assistant response.
- `.claude/hooks/session-end.sh` calls `akw group flush` and `akw group end`.
- `install.sh` registers all four hooks globally for Claude Code.
- `src/agent_knowledge/cli.py` exposes `akw session start/end/status/context/prompt/turn/flush/turns`.
- `src/agent_knowledge/core/storage.py` stores lifecycle markers and turns in the `turns` table with `kind IN ('start', 'turn', 'end', 'idle_close')`.
- `src/agent_knowledge/core/storage.py` uses `draft_state` to count pending session drafts and reconcile archived drafts.
- `src/agent_knowledge/core/search.py` indexes `1_drafts/sessions/*.md` as `session_draft`, so session summaries are already searchable once written.

## Evaluation

The proposed workflow is directionally sound. It keeps the valuable part of the system, the session summary, and removes the highest-friction part, raw turn capture.

Benefits:

- **Lower storage volume and less sensitive data.** Prompts and assistant responses are no longer stored automatically at turn granularity.
- **Simpler hooks.** Only `SessionStart` and `SessionEnd` remain required.
- **Clearer retrieval semantics.** New sessions start from the latest summaries for the current project instead of pending counts and raw group history.
- **Better human review artifacts.** The summary template makes follow-up work easier to scan than raw turns.
- **Less recovery machinery.** Without turn logs, there is less value in orphan segment recovery and stub drafts.

Tradeoffs:

- **Loss of raw forensic detail.** If a session summary is poor or missing, the system cannot reconstruct it from stored turns.
- **Session-end reliability matters more.** The close hook becomes the only automatic capture point. If the agent crashes or the hook fails, the session may be lost.
- **The CLI cannot summarize by itself unless the agent supplies the summary.** This project intentionally avoids an in-process LLM. The hook should request or enforce that the agent writes a summary, but the summarization work still happens in the agent/harness.
- **Project matching must be deterministic.** "project id" needs one canonical identifier. Use registered project ID when available; otherwise derive from repo/project name and persist it in metadata.

Recommendation:

Adopt the workflow, but treat `SessionEnd` as a summary-writing protocol rather than an automatic LLM summarizer inside `akw`. The CLI should store and retrieve summaries; the agent should produce the summary text.

## Suggested Solution

Replace turn-level capture with session-level summaries:

1. At session start, identify the current project, verify `1_drafts/sessions/<project-slug>/` exists, and return the latest five session summaries for that project from both `1_drafts/sessions/<project-slug>/` and `2_knowledges/entities/projects/<project_id>/sessions/`.
2. During the session, do not log prompts or turns.
3. At session close, write one session summary draft under `1_drafts/sessions/`.
4. The summary body must contain:
   - Summary of requests and prompts
   - What was done to fulfill the requests
   - Key discoveries and insights
   - What was completed or changed
   - Suggested follow-up and next steps
   - Additional context or metadata
5. Keep the session summary indexed as `session_draft` so `akw search` continues to find it.

## Decisions

### Decision A — Session summary is the durable unit

Store one durable markdown page per session close. Do not store individual prompt/response turns by default.

Rejected alternative: keep turn logging as an opt-in flag for now. That preserves complexity in storage, hooks, recovery, docs, and tests. If raw capture is needed later, reintroduce it as an explicit debug mode with a separate EP.

### Decision B — Summary generation stays outside the core CLI

The CLI should not call an LLM. It should accept a completed summary via stdin or `--content-file`, validate/sanitize it, write the draft, and record metadata.

The agent/harness is responsible for producing the final summary at session close.

### Decision C — Project identity is canonicalized at session start

Session start resolves project identity in this order:

1. `AKW_PROJECT` from project `.env`, if present.
2. Explicit `--project`.
3. Registered project whose path matches the working directory.
4. Derived project name from the repository root basename.

The resolved project should be stored in session metadata as both:

- `project_id`: registered ID when available, otherwise a stable slug.
- `project_name`: human-readable project/repo name.

### Decision D — Latest summaries replace recent group context

`akw session start --json` should return:

```json
{
  "session_id": "...",
  "started_at": "...",
  "project": {
    "id": "...",
    "name": "...",
    "path": "..."
  },
  "latest_summaries": [
    {
      "path": "1_drafts/sessions/...",
      "title": "...",
      "summary": "...",
      "created_at": "...",
      "metadata": {}
    }
  ]
}
```

`latest_summaries` is limited to five by default and scoped to the resolved project.

### Decision E — Keep session drafts under `1_drafts/sessions/`

Do not introduce a new top-level folder. The existing tier layout already treats session drafts as the first stage of knowledge maturation.

### Decision F — Deprecate recovery for raw turns

`akw recover`, `akw group turns`, `akw group prompt`, `akw group turn`, and `akw group flush` become obsolete in the new workflow.

Implementation may remove them in one change because the repo is still pre-release/user-owned. If backward compatibility is desired, keep commands as no-op/deprecated wrappers for one release, but do not keep hooks wired to them.

## Target CLI Surface

### Agent-safe

```bash
akw session start [--project PROJECT] [--agent AGENT] [--working-dir DIR] [--create-project-folder] [--json]
akw session close [--session-id ID] (--content TEXT | --content-file FILE) [--json]
akw session status [--json]
akw session recent [--project PROJECT] [--limit 5] [--json]
akw search QUERY [-t TIER] [--json]
akw memory read PATH [--json]
akw memory create --path PATH --title TITLE (--content TEXT | --content-file FILE) [...]
```

### Deprecated / removed

```bash
akw group prompt
akw group turn
akw group flush
akw group turns
akw recover
```

`akw group end` can either be renamed to `akw session close` or kept as an alias. Prefer `close` because it now writes the durable session summary, not just an end marker.

## Session Summary Template

Every session summary draft should use this body shape:

```markdown
# Session Summary

## Requests And Prompts

## Work Performed

## Discoveries And Insights

## Completed Changes

## Follow-Up And Next Steps

## Additional Context
```

Frontmatter should include:

```yaml
---
summary: <one-line summary>
tags: [session]
project_id: <resolved-project-id>
project_name: <resolved-project-name>
agent: <agent-name>
session_id: <session-id>
started_at: <iso>
ended_at: <iso>
working_dir: <path>
created_at: <iso>
---
```

## Implementation Phases

### Phase 0 — Documentation Discovery

- Read and preserve the tier layout contract in `docs/SPECS.md`.
- Read the command catalog in `README.md` and `src/agent_knowledge/akw_instructions.md`.
- Read current hook behavior in `.claude/hooks/*.sh` and `install.sh`.
- Read current storage functions in `src/agent_knowledge/core/storage.py`.

Verification:

- Confirm every command and hook being removed or changed is listed in the EP.
- Confirm the new workflow still writes summaries under an indexed tier.

Anti-pattern guards:

- Do not add an LLM dependency to `agent_knowledge.core`.
- Do not create a second session-summary folder outside the numbered wiki tiers.
- Do not keep turn hooks installed after removing turn storage.

### Phase 1 — Storage model for session summaries

Implement a dedicated session-summary model in `storage.py`.

Suggested schema:

```sql
CREATE TABLE session_summaries (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    project_name TEXT NOT NULL,
    agent TEXT NOT NULL,
    working_dir TEXT,
    draft_path TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
```

Add indexes:

- `(project_id, ended_at DESC)`
- `(project_name, ended_at DESC)`
- `draft_path`

Add storage functions:

- `start_session(conn, project_id, project_name, agent, working_dir, metadata) -> dict`
- `close_session(conn, session_id, draft_path, title, summary, ended_at, metadata) -> dict`
- `get_open_session(conn, session_id | latest=True) -> dict | None`
- `list_recent_session_summaries(conn, project_id | project_name, limit=5) -> list[dict]`

Verification:

- Unit tests for project-scoped recent summary ordering.
- Unit tests for open-session status and idempotent close behavior.
- Migration test that existing databases upgrade cleanly.

Anti-pattern guards:

- Do not store raw prompt/response turns in the new table.
- Do not overload `memory_edits` as the only summary index; it is audit history, not the session catalog.

### Phase 2 — Project resolution

Implement one helper for resolving the active project from CLI inputs and working directory.

Expected behavior:

- Match existing registered projects by exact ID or name.
- Match by path containment when `--working-dir` is inside a registered project path.
- If no project exists, create one using the provided project name or repository basename.
- Check for `1_drafts/sessions/<project-slug>/`; fail with a create-folder prompt if missing.
- Return both `project_id` and `project_name`.

Verification:

- Tests for explicit project ID.
- Tests for explicit project name.
- Tests for working directory matching a registered project path.
- Tests for fallback repo basename.

Anti-pattern guards:

- Do not create duplicate projects when the path already belongs to a registered project.
- Do not require `.env`; it is only one input source.

### Phase 3 — CLI start and recent summaries

Update `akw session start --json` to return latest summaries for the resolved project.

Add:

```bash
akw session recent [--project PROJECT] [--limit 5] [--json]
```

The non-JSON `SessionStart` output should be readable by an agent as startup context, not just machine data.

Verification:

- CLI tests for `session start --json` including `latest_summaries`.
- CLI tests for `session recent --json`.
- Ensure `latest_summaries` is capped at five by default.

Anti-pattern guards:

- Do not return full raw session bodies by default if summaries are enough for startup. Use title, summary, path, timestamps, and a short excerpt unless the startup instructions need full bodies.
- Do not mix summaries across projects.

### Phase 4 — CLI close writes the summary draft

Add:

```bash
akw session close [--session-id ID] (--content TEXT | --content-file FILE) [--json]
```

Behavior:

- Resolve the open session.
- Sanitize content.
- Write a markdown draft to `1_drafts/sessions/<project-or-session>-<YYYYMMDD-HHMM>.md`.
- Include the summary frontmatter.
- Record a `memory_edits` row for the created draft.
- Record or update the `session_summaries` row.
- Reuse existing `memory.create_page()` and `storage.create_memory_edit()` patterns.

Verification:

- CLI test that close writes a valid draft file.
- CLI test that summary metadata is persisted and queryable.
- CLI test that duplicate close is idempotent or returns a clear error.
- Search test that the written summary is indexed under `session_draft`.

Anti-pattern guards:

- Do not write to `2_knowledges/` from the close command.
- Do not require turn logs to produce a summary.

### Phase 5 — Hook simplification

Update hooks:

- Keep `.claude/hooks/session-start.sh`.
- Keep `.claude/hooks/session-end.sh`, but change it to call the new close protocol or print clear close instructions if the harness cannot provide a summary body automatically.
- Remove `.claude/hooks/user-prompt.sh`.
- Remove `.claude/hooks/stop.sh`.
- Update `install.sh` to register only `SessionStart` and `SessionEnd`.

Session-end design note:

Claude Code `SessionEnd` may not provide a ready-made full-session transcript to the shell hook. If the hook cannot reliably provide the summary body, it should not invent one. Instead, `akw-instructions.md` must instruct the agent to call `akw session close --content-file <tmpfile>` during wrap-up, and `SessionEnd` should only finalize/check for missing summaries.

Verification:

- Install-script test or manual dry run confirms only two hooks are registered.
- Shellcheck-style smoke test for both remaining hooks.
- Confirm no hook references `group prompt`, `group turn`, or `group flush`.

Anti-pattern guards:

- Do not silently write empty summaries.
- Do not pretend shell hooks can summarize without transcript access.

### Phase 6 — Remove turn logging and recovery surface

Remove or deprecate:

- `akw group prompt`
- `akw group turn`
- `akw group flush`
- `akw group turns`
- `akw recover`
- turn buffering files: `pending_prompt.txt`, `turn_buffer.jsonl`
- storage functions used only by turn logging/recovery

Decide whether to drop the `turns` table immediately or leave it as a legacy table. Preferred path for pre-release: add a migration that leaves old data in place but stops writing it, then remove code paths. A later cleanup EP can drop the legacy table after one release.

Verification:

- `rg "group prompt|group turn|group flush|UserPromptSubmit|Stop|turn_buffer|pending_prompt"` returns only archived docs or explicit migration notes.
- Tests pass after removing obsolete CLI tests.

Anti-pattern guards:

- Do not leave installed hooks pointing at removed commands.
- Do not delete existing user data as part of this EP.

### Phase 7 — Documentation and instructions

Update:

- `README.md`
- `docs/SPECS.md`
- `src/agent_knowledge/akw_instructions.md`
- `docs/MCP_TO_CLI_MIGRATION.md`, only if it still claims four-hook capture as current behavior.
- This EP checklist as work progresses.

Documentation must describe:

- Two-hook workflow.
- Startup latest-five summaries.
- Required session-close summary sections.
- New `akw session close` / `akw session recent` commands.
- Removed/deprecated turn logging commands.

Verification:

- `rg "UserPromptSubmit|Stop|group prompt|group turn|group flush|raw turns|recover"` has no stale current-doc references.
- `akw guide` prints the new workflow after package install.

Anti-pattern guards:

- Do not leave docs saying session drafts are generated from raw turns.
- Do not document pending counts as the primary startup context.

### Phase 8 — Final verification

Run:

```bash
uv run pytest tests/ -v
```

Manual smoke flow:

```bash
akw init
akw project new --name agent-knowledge --path /home/inotives/workspaces/agent-knowledge
akw session start --project agent-knowledge --working-dir /home/inotives/workspaces/agent-knowledge --create-project-folder --json
akw session close --content-file /tmp/session-summary.md --json
akw session recent --project agent-knowledge --limit 5 --json
akw search "session summary" --tier session_draft --json
```

Final checks:

- No turn hooks installed by `install.sh`.
- New summary draft appears under `1_drafts/sessions/`.
- New summary appears in latest-five startup payload.
- New summary is searchable.
- Existing memory search and skill/agent search still work.

## Open Questions

Resolved from user response on 2026-05-30:

1. `SessionEnd` should warn the user to summarize and save with `akw`, then fail the exit/new-session.
2. Recent session summaries should include the full summary contents.
3. Recent session summaries should exclude the current open session.
4. Project fallback should create a new project when it does not already exist under project knowledge.
5. `akw group end` should not silently end a session without a summary; it should fail with a `session close` instruction.
