# EP-00006 — Knowledge to Skill Conversion (Bundle Architecture)

## Problem / Pain Points

EP-00005 establishes the curated-tier write boundary and the knowledge maturation pipeline (sessions → drafts/sessions → drafts/knowledge → knowledge). It intentionally defers the **knowledge → skill** half of the pipeline. This EP closes that gap.

Specific issues to address:

1. **No compilation pipeline.** Today `promote_to_skill(knowledge/X.md → skills/X.md)` is a 1:1 file move. There's no synthesis step, no template, no actionability check. This breaks the "agents propose, humans approve" pattern that every other tier transition follows.

2. **"Skills as plain markdown" is a known anti-pattern.** Modern agent frameworks (Anthropic Skills, Microsoft Semantic Kernel plugins, OpenAI Assistants) have all converged on **bundle-based skills** — directories containing instructions plus executable resources. Plain text skills suffer from:
   - **Hallucination on rigid operations** — agents asked to "log to the right section" mess up regex, locking, formatting. Scripts handle this deterministically.
   - **Context bloat** — full skill content loaded at session start; a few hundred skills exceed token budgets.
   - **No pre-LLM distillation** — token-heavy work (summarizing 50 turns) sends raw text to the LLM when a script could pre-process to compact features first.

3. **No drift detection.** When a source knowledge page is updated, downstream skills compiled from it stay frozen. There's no signal that they may now be inconsistent with their sources.

4. **Weak skill activation.** `_get_recommended_context` (server.py:733) matches skills to projects only by project tags. No trigger context within a session that activates a skill mid-conversation.

5. **No way to track which skills are compiled vs uncompiled.** Skills get neglected because nothing prompts the user to make them.

---

## Architectural Direction

### Decision A — Skills are bundle directories, not single markdown files

A skill is a **directory** under `skills/<domain>/<skill>/`, not a `.md` file. The directory contains:

```
skills/<domain>/<skill>/
├── SKILL.md                    # manifest + actionable instructions (required)
├── scripts/                    # optional: deterministic helpers
│   ├── extract.py
│   └── format.py
├── resources/                  # optional: templates, data files
│   ├── template.md
│   └── examples.json
└── tests/                      # optional: validation tests
    └── test_skill.py
```

`SKILL.md` is the entry point — a manifest header (frontmatter) plus the actionable instructions body. Scripts/resources/tests are optional. Most early skills will be manifest-only; complex skills accrete scripts as needed.

**This is the central architectural change from a file-per-skill design.** It enables:
- **Deterministic post-processing** — rigid operations live in scripts; reasoning lives in the LLM.
- **Pre-LLM distillation** — preprocessing scripts compact data before the LLM sees it.
- **Progressive disclosure** — manifests are tiny; full bundle loads on demand (Decision D).

### Decision B — Atomic Skill Format: structured contract, not just instructions

The SKILL.md is a **structural contract** the LLM can parse to understand *when*, *how*, and *why* to invoke a capability — not just narrative instructions. It follows the **Manifest–Action–Constraint** pattern used in modern agent skill frameworks, with each section serving a specific role in the agent's retrieval and invocation reasoning.

**Why this structure (not just narrative text):**
- **Standardized `### [Skill Name]` headers** let the LLM index available skills quickly during retrieval.
- **Explicit typed Interface (`Input` / `Output` with types)** reduces formatting errors at invocation. The LLM knows it needs `(String)` not `(JSON)` for a given parameter.
- **Distinct Trigger Logic section** (separate from Description) prevents wrong-tool selection — the LLM sees explicit cues for *when* to use this skill versus a similar one.
- **Explicit Constraints (Do/Don't)** create boundary conditions the LLM can check against before acting.

A SKILL.md produced by `compile_to_skill_draft` MUST conform to this template. Human-edited skills MAY deviate where justified.

**SKILL.md template:**

```markdown
---
# Manifest frontmatter (machine-parseable)
name: <skill-slug>                   # filename-friendly; matches dirname
domain: <primary-tag>                # determines folder: skills/<domain>/<name>/
title: <human-readable title>
trigger_tags: [auth, jwt, security]  # tags that activate this skill at session_start
sources: [knowledge/auth-patterns.md, knowledge/jwt-validation.md]
scripts:                              # optional; populated only if scripts/ has files
  - path: scripts/extract.py
    description: Pre-process turns into a compact pattern list
    invoke: bash scripts/extract.py --session-id <id> --output <path>
resources:                            # optional
  - path: resources/keywords.txt
    description: Auth-related keywords for matching
created_at: <iso8601>
last_compiled_from: <draft path>
---

### <Skill Name>

**Description:** <Concise one-sentence summary of the skill's utility.>

**Trigger Logic:** <Contextual cues or conditions where this skill is optimal. Should answer: "when should I prefer this over similar skills?">

#### Interface
* **Input:** `<param_name>` (`<Type>`) — <description of what the agent provides>
* **Input:** `<param_name>` (`<Type>`) — <description>
* **Output:** `<return_name>` (`<Type>`) — <what the agent should expect back>

(Types are explicit and parseable: `String`, `Integer`, `Boolean`, `JSON`, `JSON[]`, `Path`, `MarkdownText`, etc. For pure-instruction skills with no scripts, Interface still applies — describes conceptual inputs/outputs.)

#### Usage Guidelines
* **Do:** <mandatory positive constraint>
* **Do:** <mandatory positive constraint>
* **Don't:** <mandatory negative constraint, with brief why>
* **Don't:** <mandatory negative constraint>

#### Examples

\`\`\`<language>
<few-shot prompt or code snippet showing the skill in action>
\`\`\`

#### Sources
- [auth-patterns](../../knowledge/auth-patterns.md) — <one-line note>
- [jwt-validation](../../knowledge/jwt-validation.md) — <one-line note>
```

**Concrete example** (illustrates the format):

```markdown
---
name: extract-auth-patterns
domain: python
title: Extract Auth Patterns
trigger_tags: [auth, jwt, security, session-summary]
sources: [knowledge/auth-patterns.md, knowledge/jwt-validation.md]
scripts:
  - path: scripts/extract.py
    description: Pre-process turns into a compact pattern list
    invoke: bash scripts/extract.py --session-id <id> --output <path>
created_at: 2026-04-27
---

### Extract Auth Patterns

**Description:** Distill auth-related patterns from a session's turns into a structured pattern list.

**Trigger Logic:** Use when summarizing a session that involved authentication code, JWT validation, or session/token management. Prefer this over generic session summarization for any auth-tagged session — the script's pattern-frequency analysis is more reliable than asking the LLM to scan raw turns.

#### Interface
* **Input:** `session_id` (`String`) — UUID of the session group to analyze.
* **Input:** `output_path` (`Path`) — where the script writes the JSON result.
* **Output:** `patterns` (`JSON[]`) — list of `{pattern_name, location, frequency}` objects.

#### Usage Guidelines
* **Do:** Run `bash scripts/extract.py --session-id <id> --output patterns.json` first; pass the JSON to the LLM for final synthesis.
* **Do:** Validate `output_path` is writable before invoking.
* **Don't:** Send raw session turns directly to the LLM for auth analysis — defeats the pre-distillation benefit.
* **Don't:** Modify `scripts/extract.py` at runtime; if regex tuning is needed, propose an update via `drafts/skills/_updates/`.

#### Examples

\`\`\`python
# Agent invokes the skill:
import subprocess, json
result = subprocess.run(
    ["bash", "scripts/extract.py", "--session-id", "abc123", "--output", "/tmp/patterns.json"],
    capture_output=True, check=True,
)
with open("/tmp/patterns.json") as f:
    patterns = json.load(f)
# Then LLM synthesizes summary from compact pattern list (~10 items vs 50 turns).
\`\`\`

#### Sources
- [auth-patterns](../../knowledge/auth-patterns.md) — JWT validation patterns
- [jwt-validation](../../knowledge/jwt-validation.md) — Token expiry handling
```

The frontmatter `trigger_tags` drives session-start activation matching (Decision D). The body's `Description` and `Trigger Logic` are returned in the manifest summary at activation time so the agent can reason about whether to load the full skill.

### Decision C — Script execution: lazy, via the host's existing tooling

Scripts in skill bundles execute **lazily** — the agent reads `SKILL.md`, decides whether to invoke a script, then runs it via its host's existing tooling.

- **Claude Code:** scripts invoked via the `Bash` tool. No new infrastructure needed. The `invoke:` field in the manifest tells the agent the canonical command.
- **Other MCP clients (Codex, OpenCode, etc.):** if they have shell execution, same path. If not, they read SKILL.md as instructions and skip script invocation. Bundles still degrade gracefully.

We do NOT add a generic `skill_invoke(skill_path, script_name, args)` MCP tool that would run subprocesses server-side. Reasons:
- Adds attack surface (server-side arbitrary execution).
- Duplicates the host's existing execution capability.
- Mostly benefits non-Bash clients, which are not the primary users today.

If a non-Bash client becomes a primary user, revisit. **Out of scope for now.**

### Decision D — Progressive disclosure for skill activation

`_get_recommended_context` returns **manifests only** at session start, not full skill bodies. Each manifest includes the structured contract fields the agent needs to decide invocation:

```json
{
  "skills_available": [
    {
      "path": "skills/python/extract-auth-patterns/",
      "name": "extract-auth-patterns",
      "domain": "python",
      "title": "Extract Auth Patterns",
      "description": "Distill auth-related patterns from session turns into a structured pattern list.",
      "trigger_logic": "Use when summarizing a session that involved authentication code, JWT validation, or session/token management.",
      "trigger_tags": ["auth", "jwt", "security", "session-summary"],
      "interface_inputs": [
        {"name": "session_id", "type": "String"},
        {"name": "output_path", "type": "Path"}
      ],
      "interface_outputs": [
        {"name": "patterns", "type": "JSON[]"}
      ],
      "has_scripts": true
    },
    ...
  ]
}
```

~80–120 tokens per skill instead of 500–1000. The agent sees enough structure to decide:
- **Description** — what the skill does (one-line).
- **Trigger Logic** — when to prefer this over similar skills.
- **Interface inputs/outputs** — what data it needs to gather and what it'll get back.
- **`has_scripts`** — whether invocation requires running a script.

When the agent decides to use a skill, it calls `memory_read("skills/python/extract-auth-patterns/SKILL.md")` to load the full body (Constraints, Examples, Sources). From there it can read additional files in the bundle directory if needed (resources, scripts, tests).

Matching is by `trigger_tags` overlap against:
- Project tags (current behavior)
- Current group's metadata tags
- Recent turn metadata tags (if any)

Ordered by tag overlap count (descending), tie-break by skill `created_at`.

### Decision E — Trust boundary: promotion gate runs optional linting and tests

Scripts in `skills/` execute as the user. The trust transition is the **promotion gate** — `promote_to_skill(draft_path, target_path)` runs pre-promotion checks:

- **Manifest validation** — required frontmatter fields populated; `invoke:` strings parse safely; `name` matches dirname.
- **Atomic structure validation** — body has `### <Name>` heading, `**Description:**` single-line, `**Trigger Logic:**` paragraph, `#### Interface` with at least one `Input:` or `Output:` line using a recognized type, `#### Usage Guidelines` with at least one `Do:` and one `Don't:`, `#### Examples` with at least one fenced code block, `#### Sources` listing the manifest's `sources` paths.
- **Pyright on Python scripts** (if `scripts/*.py` present). Promotion blocked on type errors.
- **Test execution** (if `tests/` present). Promotion blocked on failing tests.

The curator can override checks via `--force` flag (escape hatch for known-good cases). The default path is "checks pass or no promotion."

This makes the human review step substantive: the curator reviews the manifest, the scripts, the resources, the tests. They see exactly what will execute.

### Decision F — Drift detection via provenance, opt-in recompile

(Carried from previous EP-00006 draft, unchanged.)

When a knowledge page is updated, every skill that lists it in `sources` (manifest frontmatter) gets a `drift_detected_at` timestamp written to `page_provenance`. Cheap to detect.

`pending.skill_drift_count` is the count of skills with drift. User runs `/compile --recompile-drifted` to address them — produces update drafts targeting the existing skill bundle.

No automatic recompilation. Drift detection is informational; the curator decides what to do.

---

## Pipeline

The full maturation pipeline now closes:

```
sessions/turns
   ↓ (group_end → summarize)
drafts/sessions/<group>-<seg>.md
   ↓ (akw review / agent synthesis — similarity-flagged creates)
drafts/knowledge/<topic>.md
   ↓ (promote_to_knowledge — curator decides merge/split using similarity hints)
knowledge/<topic>.md
   ↓ (akw compile / agent compilation via compile_to_skill_draft — produces a bundle)
drafts/skills/<skill>/
├── SKILL.md
├── scripts/
└── resources/
   ↓ (promote_to_skill — runs linting + tests; curator approves)
skills/<domain>/<skill>/
├── SKILL.md
├── scripts/
└── resources/
```

---

## Suggested Solution

### Phase 1 — Skill draft directory + bundle scaffolding

- New directories ensured by `init`: `drafts/skills/`, `drafts/skills/_updates/`, `drafts/archived/skills/`.
- Storage / indexing handles both shapes:
  - File-form: `skills/<domain>/<skill>.md` — legacy / human-authored simple skills.
  - Bundle-form: `skills/<domain>/<skill>/SKILL.md` — agent-compiled, multi-file bundles.
- DuckDB indexer (`search.sync_from_files`) recursively indexes `**/SKILL.md` and `**/<skill>.md` under `skills/`. Path normalization: bundle skills indexed by their directory path (`skills/python/extract-auth-patterns/`); file skills by their file path.
- Knowledge draft frontmatter (from EP-00005) gains a backward link: when synthesized into a skill bundle, the `synthesized_into` array may include the bundle directory path.

### Phase 2 — `compile_to_skill_draft` MCP tool (bundle-aware)

```python
def compile_to_skill_draft(
    source_paths: list[str],          # all in knowledge/
    target_path: str,                  # drafts/skills/<skill>/  (directory, not file)
    manifest: dict,                    # frontmatter content (Decision B template)
    instructions: str,                 # SKILL.md body (markdown)
    scripts: list[dict] | None = None, # [{path, content, description}]
    resources: list[dict] | None = None, # [{path, content}]
    tests: list[dict] | None = None,   # [{path, content}]
) -> dict:
    """Write a skill bundle draft. Validates manifest schema. Populates page_provenance."""
```

- Validates manifest required fields (`name`, `domain`, `title`, `when_to_use`, `trigger_tags`, `sources`).
- `source_paths` must all start with `knowledge/`.
- `target_path` must start with `drafts/skills/` and end with `/`.
- Writes the bundle directory: `SKILL.md` always; `scripts/`, `resources/`, `tests/` only if respective arrays non-empty.
- Records `page_provenance` rows (one per source).
- Re-rendering of frontmatter from `manifest` dict.

For drift recompiles:

```python
def compile_to_skill_update_draft(
    source_paths: list[str],
    target_skill_path: str,            # skills/<domain>/<skill>/  (existing bundle)
    proposed_changes: dict,            # {manifest_delta?, instructions_delta?, scripts_added?, scripts_changed?, scripts_removed?, ...}
    summary: str,
) -> dict:
    """Write a skill update draft to drafts/skills/_updates/<skill>/."""
```

### Phase 3 — `akw compile` CLI (bundle-aware)

LLM-powered, mirrors `akw review`. Uses Anthropic tool-use with a `propose_skill` schema:

```json
{
  "name": "propose_skill",
  "input_schema": {
    "type": "object",
    "required": [
      "name", "domain", "title",
      "description", "trigger_logic", "trigger_tags",
      "interface", "constraints", "examples",
      "source_paths"
    ],
    "properties": {
      "name": {"type": "string", "description": "filename-friendly skill slug; matches dirname"},
      "domain": {"type": "string", "description": "primary tag; determines skills/<domain>/ folder"},
      "title": {"type": "string", "description": "human-readable title"},
      "description": {
        "type": "string",
        "description": "Concise ONE-SENTENCE summary. Must be a single sentence, no line breaks."
      },
      "trigger_logic": {
        "type": "string",
        "description": "When to prefer this skill over similar ones — explicit conditional cues."
      },
      "trigger_tags": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Tags that activate this skill at session_start."
      },
      "interface": {
        "type": "object",
        "required": ["inputs", "outputs"],
        "properties": {
          "inputs": {
            "type": "array",
            "items": {
              "type": "object",
              "required": ["name", "type", "description"],
              "properties": {
                "name": {"type": "string"},
                "type": {
                  "type": "string",
                  "enum": ["String", "Integer", "Boolean", "Float", "Path", "JSON", "JSON[]", "MarkdownText", "URL"]
                },
                "description": {"type": "string"}
              }
            }
          },
          "outputs": {
            "type": "array",
            "items": {
              "type": "object",
              "required": ["name", "type", "description"],
              "properties": {
                "name": {"type": "string"},
                "type": {"type": "string"},
                "description": {"type": "string"}
              }
            }
          }
        }
      },
      "constraints": {
        "type": "object",
        "required": ["dos", "donts"],
        "properties": {
          "dos": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "description": "Mandatory positive constraints."
          },
          "donts": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "description": "Mandatory negative constraints, each with brief why."
          }
        }
      },
      "examples": {
        "type": "array",
        "minItems": 1,
        "items": {
          "type": "object",
          "required": ["language", "code"],
          "properties": {
            "language": {"type": "string"},
            "code": {"type": "string"}
          }
        }
      },
      "source_paths": {
        "type": "array",
        "items": {"type": "string"},
        "minItems": 1
      },
      "scripts": {
        "type": "array",
        "items": {
          "type": "object",
          "required": ["path", "content", "description", "invoke"],
          "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
            "description": {"type": "string"},
            "invoke": {"type": "string"}
          }
        }
      },
      "resources": {
        "type": "array",
        "items": {
          "type": "object",
          "required": ["path", "content"],
          "properties": {"path": {"type": "string"}, "content": {"type": "string"}}
        }
      },
      "tests": {
        "type": "array",
        "items": {
          "type": "object",
          "required": ["path", "content"],
          "properties": {"path": {"type": "string"}, "content": {"type": "string"}}
        }
      },
      "rationale": {"type": "string"}
    }
  }
}
```

The schema enforces the atomic structure at LLM-generation time: `description` is required and one-sentence, `trigger_logic` is required and distinct, `interface.inputs[].type` is from a closed enum (no free-form types), `constraints.dos` and `constraints.donts` are both required non-empty.

`akw compile` flags:
- `--candidate <tag>` — limit to one candidate set.
- `--recompile-drifted` — process drift-detected skills instead of new candidates.
- `--limit N` — cap proposals per run.
- `--no-scripts` — produce manifest-only bundles (text skills); useful for early simple skills before script patterns emerge.

Per-candidate transactional write; rollback on validation failure, loop continues.

### Phase 4 — Refine `promote_to_skill` (bundle-aware, with optional gate checks)

```python
def promote_to_skill(
    draft_path: str,                   # drafts/skills/<skill>/
    target_path: str,                  # skills/<domain>/<skill>/
    skip_checks: bool = False,         # --force escape hatch
) -> dict:
```

- `draft_path` MUST start with `drafts/skills/`. Legacy 1:1 `knowledge/ → skills/` move is removed.
- `target_path` MUST start with `skills/<domain>/` where `<domain>` matches the manifest's `domain` field.
- **Pre-promotion gate** (skipped if `skip_checks=True`):
  - Manifest validation (required fields, schema).
  - Pyright on `scripts/*.py` (if any). Block on type errors.
  - Run `pytest tests/` (if `tests/` exists). Block on failures.
- Moves the entire directory (`memory.move_directory`) to target.
- Records final `page_provenance` rows.

Direct-edit fallback: if Pyright/pytest aren't available, log a warning but allow promotion (dev environments without dev tools shouldn't be blocked entirely).

### Phase 5 — Drift detection

(Carried from previous EP-00006 draft.)

- `page_provenance` schema: add `drift_detected_at TEXT NULL`.
- On any update to a `knowledge/X.md` page (via promotion of an `_updates/` draft, or human file-system edit detected at reindex time via mtime), set `drift_detected_at = now()` on every `page_provenance` row where `source_page_path = 'knowledge/X.md'` AND target is a skill.
- `pending.skill_drift_count` SQL: `SELECT COUNT(DISTINCT page_path) FROM page_provenance WHERE drift_detected_at IS NOT NULL AND page_path LIKE 'skills/%'`.
- `akw compile --recompile-drifted` — for each drifted skill, loads the bundle's current manifest + sources, asks LLM to propose deltas via `compile_to_skill_update_draft`.

### Phase 6 — Skill activation refinements (progressive disclosure)

- `_get_recommended_context` returns `skills_available` array of **manifests only** (Decision D shape).
  - For bundle-form skills: parse `SKILL.md` frontmatter, return manifest fields.
  - For file-form skills (legacy / simple): synthesize a manifest from frontmatter + first paragraph.
- Order by `trigger_tags` overlap count (descending), tie-break by `created_at`.
- Default top 5; configurable via `[tool.agent-knowledge.activation]`.
- New helper: `storage.get_active_session_tags()` — aggregates tags from open group's metadata + last N turn metadata for activation matching.
- Agents read full bundle on demand via existing `memory_read` (works on `SKILL.md`) and additional reads for resources/scripts content if they need to inspect.

No new MCP tool needed for "load full skill" — `memory_read` already handles it. Keep the tool surface minimal.

### Phase 7 — Compile candidate heuristic

(Same as previous draft, unchanged in logic — operational tuning only.)

```sql
WITH knowledge_tags AS (
  SELECT path, tag FROM memory_pages, json_each(tags)
  WHERE tier = 'knowledge'
),
already_compiled AS (
  SELECT DISTINCT source_page_path FROM page_provenance
  WHERE page_path LIKE 'skills/%' OR page_path LIKE 'drafts/skills/%'
),
candidates AS (
  SELECT tag, COUNT(*) AS page_count, json_group_array(path) AS page_paths
  FROM knowledge_tags
  WHERE path NOT IN (SELECT source_page_path FROM already_compiled)
  GROUP BY tag
  HAVING COUNT(*) >= 3
)
SELECT tag, page_count, page_paths FROM candidates;
```

`pending.compile_candidates = SELECT COUNT(*) FROM candidates`.

### Phase 8 — Tests + Docs

- Unit + integration test coverage as before, plus bundle-specific.
- SPECS.md fully revised: bundle architecture, SKILL.md template, scripts/resources/tests directories, progressive disclosure.
- README adds `/compile` workflow and explains bundle structure.

---

## Implementation Phases

### Phase 1 — Skill bundle scaffolding
- [ ] `init` ensures `drafts/skills/`, `drafts/skills/_updates/`, `drafts/archived/skills/`
- [ ] Indexer (`search.sync_from_files`) handles both `<skill>.md` and `<skill>/SKILL.md`; bundle skills indexed by directory path
- [ ] `memory.read_page` works on `SKILL.md` paths inside bundle directories
- [ ] New helper `memory.write_bundle(path, files)` — writes a directory with manifest + optional subdirs atomically
- [ ] New helper `memory.move_directory(src, dst)` for bundle moves

### Phase 2 — `compile_to_skill_draft` MCP tool (bundle-aware)
- [ ] New MCP tool with bundle-aware signature
- [ ] Manifest schema validation (required fields, `name` matches dirname, `source_paths` exist)
- [ ] Atomic bundle write via `memory.write_bundle`
- [ ] Populates `page_provenance` (one row per source)
- [ ] Companion tool `compile_to_skill_update_draft` for drift recompiles

### Phase 3 — `akw compile` CLI
- [ ] LLM-powered command using Anthropic tool-use with `propose_skill` schema
- [ ] Compile candidate enumeration via Phase 7 SQL
- [ ] `--candidate <tag>`, `--recompile-drifted`, `--limit N`, `--no-scripts` flags
- [ ] Per-candidate-set transactional write; rollback on validation failure
- [ ] `/compile` slash command (`.agents/commands/compile.md`)
- [ ] Pre-LLM context: candidate's source knowledge contents + existing skills list (avoid re-compiling)

### Phase 4 — Refine `promote_to_skill` with gate checks
- [ ] `promote_to_skill` requires `drafts/skills/<skill>/` source (directory)
- [ ] Drop legacy `knowledge/ → skills/` 1:1 path
- [ ] Validate `target_path` matches manifest `domain`
- [ ] Pre-promotion gate: manifest validation, **atomic structure validation** (Description / Trigger Logic / Interface / Usage Guidelines / Examples / Sources sections all present and well-formed), pyright on scripts, pytest on tests
- [ ] `--force` flag to skip gate
- [ ] `memory.move_directory` for bundle promotion
- [ ] Confirm `page_provenance` rows on promotion

### Phase 5 — Drift detection
- [ ] `page_provenance` schema: add `drift_detected_at`
- [ ] Detect on `memory_update` of curated knowledge or reindex mtime change
- [ ] `pending.skill_drift_count` query
- [ ] `--recompile-drifted` flow proposes update drafts via `compile_to_skill_update_draft`

### Phase 6 — Activation (progressive disclosure)
- [ ] `_get_recommended_context` returns manifest summaries (`skills_available`), not full bodies
- [ ] Manifest summary parser extracts: `description`, `trigger_logic`, `interface_inputs`, `interface_outputs`, `has_scripts` from SKILL.md body sections
- [ ] Bundle SKILL.md frontmatter parsed for the rest (`name`, `domain`, `trigger_tags`, etc.)
- [ ] File-form skill manifests synthesized from frontmatter + first paragraph (legacy compat; no atomic-format extraction)
- [ ] Order by trigger-tag overlap count; configurable top-K
- [ ] `storage.get_active_session_tags()` helper
- [ ] Agents read full bundle on demand via existing `memory_read`

### Phase 7 — Compile candidate heuristic
- [ ] SQL implementation backing `pending.compile_candidates`
- [ ] Config-driven threshold (`compile_min_pages_per_tag`, default 3)
- [ ] `akw status` shows candidates by tag

### Phase 8 — Tests + Docs
- [ ] Unit tests: bundle write/read (atomic, handles missing optional dirs)
- [ ] Unit tests: SKILL.md manifest schema validation (frontmatter)
- [ ] Unit tests: SKILL.md atomic-body validation (Description single-line; Trigger Logic present; Interface inputs/outputs typed; Constraints have ≥1 Do and ≥1 Don't; Examples have ≥1 fenced code block)
- [ ] Unit tests: `compile_to_skill_draft` writes correct bundle structure (atomic body rendered from `propose_skill` output), populates provenance
- [ ] Unit tests: `propose_skill` tool-use schema rejects malformed (multi-sentence description, missing trigger_logic, free-form Interface types, empty constraints)
- [ ] Unit tests: manifest summary parser extracts `description`, `trigger_logic`, `interface_inputs/outputs` correctly from atomic body
- [ ] Unit tests: drift detection — knowledge update flags dependent skills
- [ ] Unit tests: candidate heuristic — already-compiled pages excluded
- [ ] Unit tests: `_get_recommended_context` returns manifests, not full bodies
- [ ] Unit tests: pre-promotion gate — pyright/pytest blocking, `--force` override
- [ ] Unit tests: bundle indexer (both file-form and directory-form skills found)
- [ ] Integration test: knowledge → compile → review → promote (with passing tests in bundle) → activate
- [ ] Integration test: drift recompile flow end-to-end
- [ ] SPECS.md: full pipeline diagram, bundle architecture, SKILL.md template, progressive disclosure rules
- [ ] README: `/compile` workflow, bundle structure explained

---

## Out of Scope (future)

- **Server-side script execution (`skill_invoke` MCP tool).** Adds attack surface; duplicates host execution. Revisit if non-Bash MCP clients become primary users.
- **Eager preprocessing** (auto-run skill scripts at session start). Lazy invocation preserves agent agency.
- **Skill usage tracking / actionability metrics.** Counting how often a skill is loaded vs ignored. Useful future signal; premature without baseline observations.
- **ML-based skill clustering / topic modeling.** Tag-based heuristic stays.
- **Multi-domain skill placement.** Pick primary tag for folder; `trigger_tags` handles cross-domain discovery.
- **Auto-recompile on knowledge change.** Drift detection + opt-in recompile is the contract; no automatic overwrites.
- **Skill versioning beyond git/`memory_edits`.** A skill that evolves through multiple recompilations doesn't track its history beyond the audit log.
- **Cross-skill composition / dependency graph.** A skill that requires another skill's scripts. Useful eventually; not needed initially.
- **Sandboxed script execution.** Scripts run as the user. The promotion gate (curator review + linting/tests) is the trust boundary. If a stronger sandbox is needed later, revisit.

---

## Open Questions

1. **Bundle vs file form for human-authored simple skills.** Should curators always write bundles (even manifest-only), or is a single `skills/<domain>/<skill>.md` an acceptable shorthand? The indexer supports both. Lean toward "bundles for compile-generated; file-form OK for human-authored quick-and-simple skills."

2. **What's in `tests/` for compile-generated bundles?** The LLM may not produce meaningful tests for early skills. Options:
   - Allow `tests/` to be empty / absent. Promotion gate skips test step.
   - Require at least a smoke test (`tests/test_imports.py` that imports each script). Catches obvious script breakage.
   - Mandate per-script test (`scripts/extract.py` → `tests/test_extract.py`). Ambitious but high quality.
   
   Lean toward option 1 initially; tighten after observing quality.

3. **Atomic format enforcement on human edits.** If a curator edits a promoted skill's `SKILL.md` and removes a required section (e.g., omits the `Don't:` constraint, or makes Description multi-sentence), should `maintain_get_stats` flag it as malformed, or accept human deviation? Lean toward **flag-but-allow**: structural drift reported as a warning by `maintain_get_stats`, not blocked. Curators who deliberately deviate (e.g., a skill that genuinely has no anti-patterns) can accept the warning.

4. **Activation context scope.** Should `_get_recommended_context` match against all of (project tags, group metadata tags, recent turn tags), or some subset? Lean toward project + group metadata at session start (cheap), recent turn tags only on explicit request (expensive).

5. **Compile candidate heuristic threshold.** Suggest: ≥3 knowledge pages sharing a tag, none yet compiled. Make config-driven; tune from observation.

6. **`scripts/` languages beyond Python.** Pyright covers Python. Should we also support shell scripts (`scripts/*.sh`) with shellcheck? Lean toward "Python first; shell scripts allowed but un-linted; revisit if shell becomes common."

## Status: PLANNED (blocked on EP-00005 completion)
