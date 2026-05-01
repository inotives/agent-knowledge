# EP-00009 — Skill and Agent Discovery Tools

## Problem / Pain Points

EP-00008 deliberately excluded `3_intelligences/skills/` and `3_intelligences/agents/` from the general `memory_search` index. Rationale: skills are invoked when an agent **equips** a capability ("how do I X?"); agent personas are invoked when **assigning a role** ("you are X"). Both are narrow, deliberate lookups — exploratory text search across them would dilute the curated `memory_search` results with hundreds of capability blurbs.

Today there's no path between an agent and these tiers:

1. **Discovery is impossible.** Agents have no way to ask "what skills exist for working with PostgreSQL?" or "is there an agent persona for code review?". `memory_search` deliberately doesn't surface them. `memory_index --tier=skill` lists everything (216 entries deployed) — too noisy to be useful for ranked lookup.
2. **Bundle structure isn't first-class.** Skills are bundles: `3_intelligences/skills/<domain>/<slug>/SKILL.md` plus optional `scripts/`, `resources/`, `tests/`. `memory_read` returns one file; an agent equipping a skill has no view of the bundle's companions without manual `memory_index` poking.
3. **Agent personas need a clean fetch primitive.** Personas are single-file (`3_intelligences/agents/<domain>/<slug>.md`), and while `memory_read` works, the lookup ergonomics (full path required) don't fit a "load this persona" gesture.

## Suggested Solution

Two pairs of MCP tools, each on a dedicated index walker so skills/agents stay out of the general `memory_search` results:

| Tool | Purpose |
|---|---|
| `skill_search(query, domain=None)` | BM25 search across `SKILL.md` files in `3_intelligences/skills/**`. Returns ranked bundle paths + summary. |
| `skill_get(skill_path)` | Return `SKILL.md` content + bundle manifest (paths to `scripts/`, `resources/`, `tests/` companions). Agent reads what it needs via `memory_read`. |
| `agent_search(query, domain=None)` | BM25 search across `3_intelligences/agents/**/*.md` files. Returns ranked persona paths + summary. |
| `agent_get(agent_path)` | Return the persona file content + parsed frontmatter. |

The DuckDB `memory_pages` table is reused — skills and agents get their own tiers (`skill`, `agent`) populated by a separate walker that runs alongside `sync_from_files`. `memory_search` continues to filter them out by tier set; `skill_search` / `agent_search` filter *to* them.

## Decisions

### Decision A — Single DuckDB table, separate tier set

Index skills and agents into the existing `memory_pages` table with `tier='skill'` and `tier='agent'`. `memory_search` already accepts tier filters, so:
- `memory_search` excludes `('skill', 'agent')` from default results.
- `skill_search` calls `search.search(query, tier='skill', domain_filter=...)`.
- `agent_search` calls `search.search(query, tier='agent', domain_filter=...)`.

Rejected alternative: separate DuckDB tables. Adds infrastructure with no behavioural win — tier filters already do the partitioning.

### Decision B — Skills index `SKILL.md` only; resources/scripts are not searchable

A skill bundle is one capability. Searching across `SKILL.md` + `resources/*.md` would surface multiple hits per skill, polluting ranked results. The agent equips a skill (one match) and reads resources on demand via `memory_read`. **Rule:** the indexer walks `3_intelligences/skills/**/SKILL.md` exactly — nothing else.

Agents (`3_intelligences/agents/**/*.md`) are single-file by design, so the same rule yields one row per persona.

### Decision C — `skill_get` returns content + manifest, not bundled inline

For a `SKILL.md` at `3_intelligences/skills/engineering/python-coding/SKILL.md`, `skill_get` returns:

```json
{
  "path": "3_intelligences/skills/engineering/python-coding/SKILL.md",
  "domain": "engineering",
  "slug": "python-coding",
  "title": "Python Coding",
  "frontmatter": {...},
  "content": "<full SKILL.md body>",
  "resources": ["3_intelligences/skills/engineering/python-coding/resources/style-guide.md", ...],
  "scripts":   ["3_intelligences/skills/engineering/python-coding/scripts/lint.sh", ...],
  "tests":     ["3_intelligences/skills/engineering/python-coding/tests/test_lint.py", ...]
}
```

Resource and script *paths* are listed; their contents are not inlined. Reasoning:
- A skill bundle's resources can be large (e.g. a 30k-token reference doc). Inlining everything would bloat tool responses and waste context for skills the agent doesn't end up using.
- The agent decides per resource whether to pull it via `memory_read`. This matches Anthropic's own [skills](https://docs.claude.com/en/docs/claude-code/skills) loading pattern: equip the skill, lazily resolve resources.

For `agent_get`, the file content is small and self-contained, so the full body is inlined directly — no manifest needed.

### Decision D — Domain filter as an optional parameter

Both `skill_search` and `agent_search` accept `domain` (e.g. `"engineering"`, `"design"`, `"product"`) — the first segment after the tier root. Implemented as a path-prefix filter on the indexed `path` column, no schema change needed.

Pre-filter rather than post-filter so BM25 ranks within the scoped subset (more useful when a query word is generic across domains).

### Decision E — Paths in `paths.py`, walker in `search.py`

`SKILLS_DIR` and `AGENTS_DIR` constants already exist in `paths.py` from EP-00008. Add:
- `SKILL_ENTRY_FILENAME = "SKILL.md"` — the filename the skills walker looks for.
- A helper to derive `(domain, slug)` from a skill or agent path.

`search.py` gets a separate `_sync_intelligences()` pass invoked after `sync_from_files`, walking the dedicated tiers. Both indices live in `memory_pages` and are blown away + rebuilt together on every connect.

## Implementation Phases

### Phase 1 — Path helpers
- [x] `paths.py`: add `SKILL_ENTRY_FILENAME = "SKILL.md"`.
- [x] `paths.py`: helpers `parse_skill_path(path) -> (domain, slug)` and `parse_agent_path(path) -> (domain, slug)`. Return `None` if the path doesn't fit the expected shape.
- [x] `paths.py`: helper `skill_bundle_dir(skill_path) -> str` (the directory containing `SKILL.md`).
- [x] Tests in `test_paths.py` cover happy paths and malformed inputs.

### Phase 2 — Index walker
- [x] `search.py`: add `sync_intelligences(conn, memory_dir)` that walks `3_intelligences/skills/**/SKILL.md` (tier `skill`) and `3_intelligences/agents/**/*.md` (tier `agent`), skipping any `_archived/` subfolder.
- [x] Wire `sync_intelligences` into `sync_from_files` so a single connect rebuilds everything.
- [x] `search.py`: add `domain_filter` kwarg to `search()` — applies an extra `path LIKE ?` predicate prefixed by the tier root + domain.
- [x] `paths.py`: surface `INTELLIGENCES_TIERS = (("skill", SKILLS_DIR, SKILL_ENTRY_FILENAME), ("agent", AGENTS_DIR, "*.md"))` so the walker has one source of truth.

### Phase 3 — MCP tools
- [x] `server.py`: `@mcp.tool() skill_search(query, domain=None) -> list[dict]`.
- [x] `server.py`: `@mcp.tool() skill_get(skill_path) -> dict` — reads the file, builds the manifest from sibling directories.
- [x] `server.py`: `@mcp.tool() agent_search(query, domain=None) -> list[dict]`.
- [x] `server.py`: `@mcp.tool() agent_get(agent_path) -> dict`.
- [x] Tool docstrings clearly distinguish from `memory_search` ("use when equipping a capability / assigning a role"). Update the server `instructions` block to mention the new tools and when to use them.

### Phase 4 — CLI helpers (optional, for parity with `akw search`)
- [x] `akw skill search <query> [--domain D]` — print ranked bundle paths.
- [x] `akw skill show <skill_path>` — print SKILL.md + manifest summary.
- [x] `akw agent search <query> [--domain D]` — print ranked persona paths.
- [x] `akw agent show <agent_path>` — print persona content.

### Phase 5 — Tests
- [x] `test_paths.py`: `parse_skill_path`, `parse_agent_path`, `skill_bundle_dir` round-trips.
- [x] `test_search.py`: skill walker indexes `SKILL.md` only (does NOT pick up `resources/*.md`).
- [x] `test_search.py`: agent walker picks up `*.md` under `agents/<domain>/`.
- [x] `test_search.py`: `_archived/` subfolders skipped for both walkers.
- [x] `test_search.py`: `domain_filter` scopes results to a single domain.
- [x] New `test_intelligences.py` (or add to `test_search.py`): `skill_get` returns manifest with correct `resources` / `scripts` / `tests` keys; missing companion dirs return empty arrays without erroring.
- [x] Live sanity check on the deployed memory: 216 skills + 59 agents indexed, search returns sensible top-K for known terms.

## Out of Scope

- **Skill execution / scripts.** This EP is read-only discovery + fetch. Agents already invoke skills via their own runtime (Claude Code's skill system); the MCP just makes them findable.
- **Auto-equip on `group_start`.** Returning a curated set of relevant skills based on group metadata is interesting but a separate problem (it depends on project tags, recent groups, and history). Punt to a follow-up EP.
- **Vector / embedding search.** BM25 is sufficient at this corpus size (~275 entries). Revisit if recall becomes a bottleneck.
- **Cross-tier reranking.** `memory_search` and the new `*_search` tools stay independent. No combined "best result across knowledge + skills" tool — different intents, different ranking.
- **Agent persona inheritance / composition.** If an agent persona references other personas, that's a content concern, not an indexer concern.

## Resolved Decisions

1. **`*_get` argument:** accept either the full relative path *or* `<domain>/<slug>`. The tool inspects the input and resolves accordingly. Both `skill_get("3_intelligences/skills/engineering/python-coding/SKILL.md")` and `skill_get("engineering/python-coding")` work.
2. **CLI parity (Phase 4):** ship as thin wrappers around the existing `akw search` function. `akw skill search` delegates to `search_cmd(query, tier="skill", domain=...)`, `akw agent search` to the same with `tier="agent"`. `akw skill show` / `akw agent show` reuse `memory_read` + manifest building. Reuses existing infrastructure rather than duplicating it.
3. **Manifest depth:** `skill_get` lists `resources/`, `scripts/`, `tests/` recursively via `rglob` so nested subdirs surface.

## Status: DONE

Implementation landed on `feat/ep-00009-skill-and-agent-discovery`. 117 tests passing (including new `test_intelligences.py` for the `list_bundle_companions` helper + CLI smoke). Live-tested via deployed memory: `akw skill search "incident response"` returns ranked bundles across `workflow/` domain; `akw agent search "code review" --domain engineering` returns 5 engineering agents; `akw skill show workflow/incident_commander` prints SKILL.md and lists the bundle's `scripts/`. MCP tools awaiting `/mcp` reconnect for live verification.
