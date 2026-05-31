# Agent Knowledge — session instructions

Agent Knowledge is the persistent-memory layer for this user. Knowledge stored here is curated and authoritative — prefer it over general knowledge. Search memory before answering questions about architecture, conventions, patterns, or past decisions.

All operations are exposed via the `akw` CLI (no MCP server). Run commands via Bash; pass `--json` when you need structured output.

## When to reach for `akw` (request → command)

If the user's request matches the left column, the answer is `akw …` — not `Write` to a guessed path, not `cat > somewhere.md`, not summarizing inline without persisting.

| User request | Command |
|---|---|
| "create session summary", "summarize this session", "save this session", "log this conversation", "write a session draft", "wrap up", "we're done", "bye", "exit", "/new" | Run the **Wrap-up** flow below (`akw session close --content-file <summary.md>`). |
| "save this as a note", "note this down", "remember this", "draft a note about X" | `akw memory create --path 1_drafts/2_notes/<slug>.md ...` |
| "draft a knowledge page on X", "write up X for the wiki" | `akw memory create --path 1_drafts/2_knowledges/<slug>.md ...` (curator promotes later) |
| "what do we know about X?", "search the wiki for X", "have we talked about X before?" | `akw search "X" [--json]` first, then `akw memory read <path>` for the hit |
| "is there a skill / agent for X?" | `akw skill search "X" [--json]` or `akw agent search "X" [--json]` |
| "what's the active session?", "what session am I on?" | `akw session status --json` |
| "what's pending?", "what needs review?" | `akw status` (page/group counts) and `akw maintain stats --json` |
| "recover / fix incomplete sessions" | `akw recover --dry-run` then `akw recover` |

Anti-patterns: do **not** use the `Write` tool to create session summaries or notes inside `~/.agent-knowledge/memory/` — go through `akw session close` for session summaries and `akw memory create` for other drafts so the audit log, search index, and `draft_state` table are updated. Do not invent paths under `2_knowledges/` / `3_intelligences/` / `0_configs/`; those are curator-only and `akw memory create` will reject them.

## Session lifecycle

A session is one logical unit of work (one Claude session, one barebone conversation, or one barebone task). The durable memory unit is one saved session summary.

The `SessionStart` hook calls `akw session start --json` automatically; you do not need to call it yourself. It returns the latest five saved summaries for the resolved project, excluding the current open session. Use `akw session status [--json]` to confirm the active session_id.

`akw init` creates the base memory vault. On first use of a project, `akw session start` checks for `1_drafts/sessions/<project-slug>/`, defaulting to the repo/project name. If missing, ask the user whether to create it; rerun with `akw session start --create-project-folder ...` after confirmation.

## Wrap-up

Triggered when the user says any of: "bye", "exit", "done", "wrap up", "create / save / log session summary", "save this session", "log this", "summarize this session", "/new", or after you've completed the main task they asked for. When triggered you MUST:

1. Run `akw session status --json` to confirm the active session_id.
2. Summarize the full session using exactly these sections:
   ```markdown
   # Session Summary

   ## Requests And Prompts

   ## Work Performed

   ## Discoveries And Insights

   ## Completed Changes

   ## Follow-Up And Next Steps

   ## Additional Context
   ```
3. Save and close through akw:
   ```bash
   akw session close --session-id <active_session_id> --content-file <draft.md>
   ```

Do NOT include secrets, API keys, tokens, or credentials in the draft. The CLI runs the same secret-redaction sanitizer the MCP did, but you should not type secrets in the first place.

The `SessionEnd` hook intentionally fails exit or `/new` while a session is still open. If it warns, write the summary with `akw session close` and retry exit/new-session.

## Recent summaries

`akw session start --json` and `akw session recent --json` return the latest saved summaries for the resolved project with full markdown content. These lists merge draft summaries from `1_drafts/sessions/<project-slug>/` and curated summaries from `2_knowledges/entities/projects/<project_id>/sessions/`, and exclude the current open session. If the project is unknown, akw creates a project registry entry and a project entity page under `2_knowledges/entities/projects/`.

## Memory layout

The deployed memory folder is a numbered three-tier wiki. Numeric prefixes encode promotion order (1 → 2 → 3); `0_configs/` is the wiki contract, not a tier:

- `0_configs/`        templates + rules (curator-only)
- `1_drafts/`         Tier 1 — agent-writable drafts (see DRAFT STAGING below)
- `2_knowledges/`     Tier 2 — curated, durable knowledge (curator-only)
- `3_intelligences/`  Tier 3 — skills + agent personas (curator-only)

## Draft staging

Inside `1_drafts/`, the nested numeric prefix on each subfolder signals the *promotion target* — where a draft lands once curated:

- `1_drafts/sessions/`       session summaries — written via `akw session close`
- `1_drafts/2_knowledges/`   drafts targeting Tier 2 knowledge pages
- `1_drafts/2_notes/`        ad-hoc notes (will promote to `2_knowledges/notes/`)
- `1_drafts/2_researches/`   research outputs (will promote to `2_knowledges/researches/`)
- `1_drafts/3_skills/`       drafts targeting Tier 3 skills

Pick the staging folder that matches the *promotion target* of the draft you're writing.

## Tier write boundary

`akw memory create` and `akw memory update` REJECT writes to `2_knowledges/`, `3_intelligences/`, `0_configs/`, and `1_drafts/_archived/`. Curated tiers are written by humans only, via their editor on the file system. Synthesis from drafts to knowledge is a human activity outside the agent loop — see `0_configs/rules/knowledge-management.md` in the deployed memory folder for conventions.

## Write carve-outs

A small set of paths inside curated tiers are agent-writable:

- `2_knowledges/preferences/` — user preferences (e.g. tooling, conventions, stated likes/dislikes). Use `akw memory create` / `akw memory update` directly.

Deletes of carve-out pages do NOT unlink — they archive to `<tier>/_archived/...`. Run `akw memory rm <path>` and the CLI moves the file for you.

## Draft policy

Agents create and append to `1_drafts/`. Agents must NEVER delete drafts and must NEVER write to `1_drafts/_archived/` — archiving is a curator action via `akw archive` or manual move.

## Discovery commands

| What you want | Command |
|---|---|
| Search drafts + curated knowledge | `akw search "<query>" [--tier <tier>] [--json]` |
| Read a specific page | `akw memory read <path> [--json]` |
| Show recent project session summaries | `akw session recent [--project <p>] [--limit 5] [--json]` |
| List indexed pages | `akw memory ls [--tier <tier>] [--json]` |
| Find a skill to equip ("how do I X?") | `akw skill search "<query>" [--domain <d>] [--json]` |
| Fetch a skill bundle (SKILL.md + manifest) | `akw skill show <domain>/<slug> [--json]` |
| Find an agent persona ("you are X") | `akw agent search "<query>" [--domain <d>] [--json]` |
| Fetch an agent persona | `akw agent show <domain>/<slug> [--json]` |
| Recent edit history | `akw memory history [--page-path <p>] [--limit N] [--json]` |
| Stats (page counts, stale pages, group health) | `akw maintain stats [--stale-days N] [--json]` |

`akw search` (default tier) excludes skills and agent personas — use `akw skill search` / `akw agent search` to scope into them.
