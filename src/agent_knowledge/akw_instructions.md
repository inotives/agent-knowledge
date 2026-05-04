# Agent Knowledge — session instructions

Agent Knowledge is the persistent-memory layer for this user. Knowledge stored here is curated and authoritative — prefer it over general knowledge. Search memory before answering questions about architecture, conventions, patterns, or past decisions.

All operations are exposed via the `akw` CLI (no MCP server). Run commands via Bash; pass `--json` when you need structured output.

## When to reach for `akw` (request → command)

If the user's request matches the left column, the answer is `akw …` — not `Write` to a guessed path, not `cat > somewhere.md`, not summarizing inline without persisting.

| User request | Command |
|---|---|
| "create session summary", "summarize this session", "save this session", "log this conversation", "write a session draft", "wrap up", "we're done", "bye" | Run the **Wrap-up** flow below (`akw memory create` into `1_drafts/sessions/`, then `akw group end`). |
| "save this as a note", "note this down", "remember this", "draft a note about X" | `akw memory create --path 1_drafts/2_notes/<slug>.md ...` |
| "draft a knowledge page on X", "write up X for the wiki" | `akw memory create --path 1_drafts/2_knowledges/<slug>.md ...` (curator promotes later) |
| "what do we know about X?", "search the wiki for X", "have we talked about X before?" | `akw search "X" [--json]` first, then `akw memory read <path>` for the hit |
| "is there a skill / agent for X?" | `akw skill search "X" [--json]` or `akw agent search "X" [--json]` |
| "what's the active group/session?", "what session am I on?" | `akw group status --json` |
| "what's pending?", "what needs review?" | `akw status` (page/group counts) and `akw maintain stats --json` |
| "recover / fix incomplete sessions" | `akw recover --dry-run` then `akw recover` |

Anti-patterns: do **not** use the `Write` tool to create session summaries or notes inside `~/.agent-knowledge/memory/` — go through `akw memory create` so the audit log, search index, and `draft_state` table are updated. Do not invent paths under `2_knowledges/` / `3_intelligences/` / `0_configs/`; those are curator-only and `akw memory create` will reject them.

## Group lifecycle

A group is one logical unit of work (one Claude session, one barebone conversation, or one barebone task). A group may have multiple segments over time (continuation reuses the same group_id and starts a new segment). Each segment is a `start → end` pair on the `turns` table.

The `SessionStart` hook calls `akw group start` automatically; you do not need to call it yourself. Use `akw group status [--json]` to confirm the active group_id and segment.

## Wrap-up

Triggered when the user says any of: "bye", "exit", "done", "wrap up", "create / save / log session summary", "save this session", "log this", "summarize this session", or after you've completed the main task they asked for. When triggered you MUST:

1. Run `akw group status --json` to confirm the active group_id and segment_start_at.
2. Summarize what was accomplished, decisions made, and patterns learned.
3. Write a session draft:
   ```bash
   akw memory create \
     --path "1_drafts/sessions/<group_first_8>-<segment_compact_iso>.md" \
     --title "Session: <topic>" \
     --content-file <draft.md> \
     --group-id <active_group_id>
   ```
   Where `<segment_compact_iso>` is `segment_start_at` formatted `YYYYMMDD-HHMM`.
4. Run `akw group end` (no args needed — closes the active segment).

Do NOT include secrets, API keys, tokens, or credentials in the draft. The CLI runs the same secret-redaction sanitizer the MCP did, but you should not type secrets in the first place.

## Pending counts

`akw group start --json` returns a `pending` field with `unarchived_session_drafts` and `incomplete_segments`. If non-zero, surface to the user — for example: "You have N session summaries and M incomplete segments waiting. Open the memory folder if you want to review summaries; run `akw recover` for incomplete ones." Do NOT auto-process pending items. The user opts in.

## Memory layout

The deployed memory folder is a numbered three-tier wiki. Numeric prefixes encode promotion order (1 → 2 → 3); `0_configs/` is the wiki contract, not a tier:

- `0_configs/`        templates + rules (curator-only)
- `1_drafts/`         Tier 1 — agent-writable drafts (see DRAFT STAGING below)
- `2_knowledges/`     Tier 2 — curated, durable knowledge (curator-only)
- `3_intelligences/`  Tier 3 — skills + agent personas (curator-only)

## Draft staging

Inside `1_drafts/`, the nested numeric prefix on each subfolder signals the *promotion target* — where a draft lands once curated:

- `1_drafts/sessions/`       session summaries (one per segment) — written via the wrap-up flow
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
| List indexed pages | `akw memory ls [--tier <tier>] [--json]` |
| Find a skill to equip ("how do I X?") | `akw skill search "<query>" [--domain <d>] [--json]` |
| Fetch a skill bundle (SKILL.md + manifest) | `akw skill show <domain>/<slug> [--json]` |
| Find an agent persona ("you are X") | `akw agent search "<query>" [--domain <d>] [--json]` |
| Fetch an agent persona | `akw agent show <domain>/<slug> [--json]` |
| Recent edit history | `akw memory history [--page-path <p>] [--limit N] [--json]` |
| Stats (page counts, stale pages, group health) | `akw maintain stats [--stale-days N] [--json]` |

`akw search` (default tier) excludes skills and agent personas — use `akw skill search` / `akw agent search` to scope into them.
