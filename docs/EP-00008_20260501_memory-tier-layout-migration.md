# EP-00008 — Memory Tier Layout Migration

## Problem / Pain Points

The deployed memory folder (`~/.agent-knowledge/memory/`) has been restructured into a numbered three-tier wiki, but the MCP code still hardcodes the old flat paths. Concretely:

| Old path | New path |
|---|---|
| `drafts/sessions/` | `1_drafts/sessions/` |
| `drafts/archived/sessions/` | `1_drafts/_archived/sessions/` |
| `knowledge/` | `2_knowledges/` |
| `skills/` | `3_intelligences/skills/` |
| (no equivalent — new) | `3_intelligences/agents/` |
| (no equivalent — new) | `0_configs/` (templates + rules, curator-only) |

Numeric prefixes are load-bearing — they encode the promotion order (1 → 2 → 3). `0_configs/` is the wiki contract (templates and rules), not a tier.

The mismatch breaks three things:

1. **Session capture writes to the wrong place.** `cli.py:33`, `server.py:81`, `storage.py:513` produce `drafts/sessions/<...>.md`. After this lands, drafts go nowhere the curator can find them via the documented layout.
2. **Search index walks empty directories.** `core/search.py:48` iterates `memory_dir/knowledge` and `memory_dir/skills`. With the new layout those dirs don't exist, so `memory_search` returns nothing.
3. **Write boundary protects the wrong paths.** `server.py:189` and `core/memory.py:84-88` block writes to `knowledge/`, `skills/`, `drafts/archived/`. The curator-only tiers are now `2_knowledges/`, `3_intelligences/`, `0_configs/`, `1_drafts/_archived/`.

The agent-facing MCP server description (`server.py:49-63,226-235,460-467`) also documents the old paths to the LLM, which means agents are being told to write session drafts to the wrong place.

## Suggested Solution

Centralize the tier paths in one module, then update call sites. Capture-only scope from EP-00005 stays — this EP changes *where* the MCP writes, not *what* it writes.

The session-draft path change ships first as a small "re-install" step so any new session captures land in the correct place while the rest of the migration is in flight.

## Implementation Phases

### Phase 1 — Centralize tier paths
- [ ] Add `src/agent_knowledge/core/paths.py` with constants:
  - `SESSIONS_DIR = "1_drafts/sessions"`
  - `ARCHIVED_SESSIONS_DIR = "1_drafts/_archived/sessions"`
  - `DRAFTS_PREFIX = "1_drafts/"`
  - `WRITE_BLOCKED_PREFIXES = ("2_knowledges/", "3_intelligences/", "0_configs/", "1_drafts/_archived/")`
  - `INDEXED_TIERS = (("knowledge", "2_knowledges"), ("skill", "3_intelligences/skills"), ("agent", "3_intelligences/agents"), ("session_draft", "1_drafts/sessions"))`
  - `ARCHIVED_SESSION_GLOB = "1_drafts/_archived/sessions__*.md"` (flat-file glob, tier label `session_archived`)
- [ ] Helper `session_draft_path(group_id, segment_start_at) -> str` to replace the duplicated builder in `cli.py:33`, `server.py:81`, `storage.py:513`.

### Phase 2 — Session draft path (ship + reinstall)
- [ ] Replace `drafts/sessions/...` literals in `cli.py:33`, `server.py:81`, `storage.py:513,669,679`.
- [ ] Replace `drafts/archived/sessions/` in `cli.py:518-533`, `storage.py:793`.
- [ ] Update `cli.py:518-533` archive guard to check `1_drafts/sessions/` prefix and target `1_drafts/_archived/sessions/`.
- [ ] `pip install -e .` so MCP picks up the new paths before opening a new working session for this EP.

### Phase 3 — Write boundary
- [ ] `server.py:189-196` and `core/memory.py:84-88`: switch to `WRITE_BLOCKED_PREFIXES` from `paths.py`.
- [ ] `server.py:551-554` delete guard: keep blocking all `1_drafts/` deletes (no semantic change, just path rename).

### Phase 4 — Search index walker
- [ ] `core/search.py:48` `sync_from_files`: iterate over `INDEXED_TIERS`. New tier `agent` covers `3_intelligences/agents/` (single-file `<domain>/<slug>.md`).
- [ ] Index session drafts as well. Two locations, both flat-file:
  - `1_drafts/sessions/*.md` — live drafts, tier label `session_draft`
  - `1_drafts/_archived/sessions__*.md` — archived drafts (filename pattern, not a subfolder), tier label `session_archived`
  Rationale: the curator searches "what did we discuss about X" across both live and archived sessions; archived drafts are the long-term provenance trail for any curated knowledge page.
- [ ] Walking the archive: glob `1_drafts/_archived/sessions__*.md` (filename prefix is the marker, since archive is flat-file). Add a constant `ARCHIVED_SESSION_GLOB = "1_drafts/_archived/sessions__*.md"` in `paths.py`.
- [ ] Update `tier` filter in `search()` to accept the new tier names (`knowledge`, `skill`, `agent`, `session_draft`, `session_archived`).

### Phase 5 — Drift recovery walker
- [ ] `core/storage.py:762-793` reindex helper: walk `1_drafts/sessions/` and `1_drafts/_archived/sessions/`.

### Phase 6 — MCP server instructions
- [ ] Rewrite the LLM-facing description string in `server.py:49-63` to reference the new tier names. The agent reads this on every connect; getting it right is what teaches future sessions to write to the right place.
- [ ] Update tool docstrings (`server.py:226-235,460-467`).
- [ ] Verify against `~/.agent-knowledge/memory/AGENTS.md` so the wiki doc and the MCP description stay aligned.

### Phase 7 — Tests
- [ ] Update `tests/test_storage.py` and `tests/test_recover.py` for new path literals.
- [ ] Add a test that asserts `memory_create` rejects each of `2_knowledges/`, `3_intelligences/`, `0_configs/`, `1_drafts/_archived/`.
- [ ] Add a test that `sync_from_files` indexes pages from all five sources: `2_knowledges/`, `3_intelligences/skills/`, `3_intelligences/agents/`, `1_drafts/sessions/`, and `1_drafts/_archived/sessions__*.md`.

## Out of Scope

- Migration of existing draft files inside `~/.agent-knowledge/memory/`. The user has already restructured the folder; the MCP just needs to point at the new locations.
- Any synthesis / promotion logic. EP-00005's capture-only scope stands.
- Backwards compatibility with the old paths. There are no production users; the MCP just changes its target paths.
- Tier 3 agent persona authoring or tooling — the search index just needs to find them.

## Status: PROPOSED
