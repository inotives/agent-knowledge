# EP-00007 — Agent Profiles

## Problem / Pain Points

EP-00006 makes individual skills first-class actionable artifacts. But agents working on real tasks don't operate one skill at a time — they operate in *roles*. A frontend engineer reaches for a specific cluster of skills, primes a specific knowledge subset, follows specific conventions. A backend engineer reaches for different ones. A mobile app developer differs again from a web developer. Today nothing in the system captures that role-shape.

Specific issues this EP addresses:

1. **No role-shaped configuration.** Skills are atomic capabilities. Knowledge is descriptive reference. Neither captures "what does it mean to be a frontend engineer working on this project?" — the bundle of skills + primed knowledge + behavioral conventions + anti-patterns that defines a role.

2. **Activation is too coarse.** `_get_recommended_context` returns top-K skills by tag overlap. For a project tagged `[python, web]`, that surfaces skills generically — no role-shaped curation. A frontend-focused session and a backend-focused session in the same project see the same top-K.

3. **No mechanism to equip context for a task.** Users today either re-explain role context every session or hope tag matching surfaces the right skills. There's no "equip the frontend toolkit" action.

4. **Convention drift across roles.** Knowledge pages capture project conventions, but those conventions often differ by role (frontend uses TypeScript types proactively; backend follows different patterns). One knowledge page can't gracefully serve all roles. Profiles let convention guidance be role-shaped.

5. **No way to capture role-specific anti-patterns.** A skill's anti-patterns are about that skill specifically. A profile's anti-patterns are about the role — "don't suggest backend solutions for frontend problems" applies regardless of which specific skill is being invoked.

---

## Architectural Direction

### Decision A — Profiles are bundle directories, parallel to skills

A profile is a **directory** under `profiles/<role>/`, not a `.md` file. Same bundle pattern as skills (EP-00006 Decision A):

```
profiles/<role>/
├── PROFILE.md              # manifest + atomic structured body (required)
├── conventions/            # optional: role-specific convention docs
│   ├── coding-style.md
│   └── tool-preferences.md
├── examples/               # optional: role-shaped few-shot examples
│   └── component-patterns.md
└── resources/              # optional: data, templates
```

`PROFILE.md` is the entry point — manifest frontmatter + atomic-format body. Subdirectories are optional. Most profiles will be PROFILE.md-only initially.

### Decision B — PROFILE.md atomic format (parallel to SKILL.md)

A PROFILE.md follows the same Manifest–Action–Constraint atomic structure as SKILL.md (EP-00006 Decision B), with content adapted to *configuration* instead of *action*. The five body sections are: **Description**, **Trigger Context**, **Conventions**, **Activated Skills**, **Primed Knowledge**, **Examples**.

A PROFILE.md produced by `compile_to_profile_draft` MUST conform to this template. Human-edited profiles MAY deviate where justified.

**PROFILE.md template:**

```markdown
---
# Manifest frontmatter
name: <role-slug>                       # e.g. frontend-engineer
title: <human-readable role name>
trigger_context: [tags, ...]            # tags that suggest equipping this profile
activated_skills:
  - skills/<domain>/<skill>/
  - skills/<domain>/<skill>/
primed_knowledge:
  - knowledge/<topic>.md
  - knowledge/<topic>.md
extends:                                # optional profile inheritance
  - profiles/<base-role>/
created_at: <iso8601>
last_compiled_from: <draft path>
---

### <Role Name>

**Description:** <Concise one-sentence summary of the role's scope.>

**Trigger Context:** <When to equip this profile. Should answer: "when should I prefer this over similar profiles?">

#### Conventions
* **Do:** <mandatory positive convention>
* **Do:** <mandatory positive convention>
* **Don't:** <mandatory negative convention, with brief why>
* **Don't:** <mandatory negative convention, with brief why>

#### Activated Skills
- [<skill-name>](../../skills/<domain>/<skill>/) — <one-line note on why this skill is in the toolkit>
- [<skill-name>](../../skills/<domain>/<skill>/) — <one-line note>

#### Primed Knowledge
- [<topic>](../../knowledge/<topic>.md) — <one-line note on why this knowledge is primed>
- [<topic>](../../knowledge/<topic>.md) — <one-line note>

#### Examples

\`\`\`<language>
<few-shot showing how the role's reasoning shapes a typical decision>
\`\`\`
```

**Concrete example:**

```markdown
---
name: frontend-engineer
title: Frontend Engineer
trigger_context: [react, vue, css, accessibility, ui-ux, frontend]
activated_skills:
  - skills/javascript/lint-react/
  - skills/javascript/extract-component-patterns/
  - skills/general/accessibility-audit/
primed_knowledge:
  - knowledge/react-patterns.md
  - knowledge/css-architecture.md
  - knowledge/web-accessibility.md
extends:
  - profiles/general/web-developer/
created_at: 2026-04-27
---

### Frontend Engineer

**Description:** Role-shaped configuration for UI/UX work — React/Vue components, CSS architecture, web accessibility, browser compatibility.

**Trigger Context:** Equip when the project is web-frontend, the working directory contains UI code (`components/`, `pages/`, `*.tsx`, `*.vue`), or the user is asking about styling, accessibility, or interactive behavior. Prefer this over `fullstack-engineer` when the immediate task is frontend-only.

#### Conventions
* **Do:** Prefer functional components over class components; suggest TypeScript types proactively.
* **Do:** Flag accessibility issues unprompted (alt text, ARIA roles, keyboard navigation) — they're cheaper to fix early.
* **Don't:** Suggest backend solutions for frontend problems (state management is not API design; keep concerns separated).
* **Don't:** Recommend CSS frameworks without checking the project's existing tooling — convention drift hurts more than missing features.

#### Activated Skills
- [lint-react](../../skills/javascript/lint-react/) — Run ESLint with React rules; flag common pitfalls
- [extract-component-patterns](../../skills/javascript/extract-component-patterns/) — Distill component patterns from session turns
- [accessibility-audit](../../skills/general/accessibility-audit/) — Run axe-core or pa11y against rendered HTML

#### Primed Knowledge
- [react-patterns](../../knowledge/react-patterns.md) — Hooks, composition, render patterns
- [css-architecture](../../knowledge/css-architecture.md) — BEM, utility-first, CSS-in-JS tradeoffs
- [web-accessibility](../../knowledge/web-accessibility.md) — WCAG, ARIA, keyboard navigation

#### Examples

\`\`\`typescript
// When the user asks "how should I handle this state?", the profile primes:
// - prefer hooks (useState, useReducer, context) over external libs unless complexity demands
// - suggest TypeScript discriminated unions for state shapes
// - flag a11y implications (focus management, announcements)

// Wrong (backend-shaped reasoning):
"Use Redux with a reducer pattern and middleware for side effects"

// Right (frontend-shaped reasoning):
"Start with useReducer + context. Reach for Redux only if you need devtools-level
inspection or middleware for cross-cutting concerns. For your form state, useState
plus react-hook-form is the lighter path."
\`\`\`
```

### Decision C — Compilation from skills + knowledge (hybrid creation)

Profile compilation is hybrid:

- **Curator declares the role** — names the profile, picks a primary tag (`frontend-engineer`, domain `frontend`).
- **LLM populates** — given the candidate skill set (skills tagged with overlapping `trigger_tags`) and the candidate knowledge set (knowledge pages tagged with overlapping tags), the LLM proposes:
  - `activated_skills` — which skills belong to this role
  - `primed_knowledge` — which knowledge to prime in context
  - Conventions and anti-patterns synthesized from observed knowledge
  - Examples illustrating role-shaped reasoning

The LLM doesn't invent skills or knowledge from nothing — only selects from existing curated content. This keeps profiles grounded in the project's actual accumulated knowledge.

### Decision D — Single equipped profile + inheritance via `extends:`

A group has at most ONE equipped profile at a time (`group_metadata.equipped_profile`). Stacking/blending is rejected — equipping is a deliberate context-narrowing action.

Inheritance via `extends:` lets profiles compose without runtime stacking:

- `frontend-engineer` extends `web-developer`
- `web-developer` extends `software-engineer`
- At equip time, the chain is resolved: base profile's conventions/skills/knowledge are merged with the leaf profile's. Leaf overrides base on conflicts (e.g., a Don't in `frontend-engineer` overrides a Do in `web-developer`).
- Cycle detection at promotion gate.

This gives the flexibility of layered configuration without the complexity of multi-equip.

### Decision E — Equipping mechanism: three layers

All three should work:

1. **At group start:** `group_start(profile="frontend-engineer")` — declarative; profile equipped for the entire group.
2. **Mid-session via MCP tool:** `profile_equip(profile_path)` — for switching tasks within a group. Updates `group_metadata.equipped_profile`. Returns the resolved manifest + activated skills/knowledge.
3. **Auto-suggest from project tags:** at `session_start`, if any profile's `trigger_context` overlaps with project tags, surface a suggestion: `pending.profile_suggestions: [{path, name, reason}]`. Agent surfaces to the user; user confirms via `profile_equip` or `/profile`. Never auto-apply.

`profile_unequip()` clears the equipped profile.

### Decision F — Promotion gate

Profiles don't have executable scripts (typically), so no pyright/pytest. The gate validates:

- **Manifest required fields** populated; `name` matches dirname.
- **Atomic body structure** — all six sections present and well-formed (Description single-line; Trigger Context paragraph; Conventions has ≥1 Do and ≥1 Don't; Activated Skills lists ≥1 skill; Primed Knowledge lists ≥1 knowledge page; Examples has ≥1 fenced code block).
- **Reference resolution** — every `activated_skills` path exists as a skill bundle; every `primed_knowledge` path exists as a curated knowledge page.
- **Inheritance acyclicity** — `extends:` chain doesn't cycle.

`--force` flag to skip gate (escape hatch).

### Decision G — Drift detection chains profile → skill → knowledge

`page_provenance` (introduced in EP-00005, extended in EP-00006) tracks dependencies. With profiles, the chain becomes:

```
profile  →  skill  →  knowledge
```

Two drift sources:
- **Direct skill drift** — a constituent skill of the profile got recompiled, deprecated, or renamed.
- **Cascading knowledge drift** — knowledge changed → skill flagged drifted → profile inherits drift.

`pending.profile_drift_count` is the count of profiles whose `activated_skills` have any drift. User runs `/compile --recompile-drifted-profiles` to address them — produces update drafts in `drafts/profiles/_updates/`.

---

## Pipeline

The full maturation pipeline now extends through profiles:

```
sessions/turns
   ↓
drafts/sessions/<group>-<seg>.md
   ↓
drafts/knowledge/<topic>.md
   ↓
knowledge/<topic>.md
   ↓
drafts/skills/<skill>/
   ↓
skills/<domain>/<skill>/
   ↓
drafts/profiles/<role>/
   ↓
profiles/<role>/
```

---

## Suggested Solution

### Phase 1 — Profile bundle scaffolding

- New directories ensured by `init`: `profiles/`, `drafts/profiles/`, `drafts/profiles/_updates/`, `drafts/archived/profiles/`.
- Storage / indexing:
  - Bundle-form: `profiles/<role>/PROFILE.md` — agent-compiled, multi-file bundles.
  - File-form `profiles/<role>.md` is NOT supported (profiles always have manifest + body structure; simpler to enforce bundle from day one).
- DuckDB indexer (`search.sync_from_files`) recursively indexes `profiles/**/PROFILE.md`. Path normalization: profiles indexed by directory path (`profiles/frontend-engineer/`).
- New `tier='profile'` distinction in DuckDB index for `memory_search` filtering.

### Phase 2 — `compile_to_profile_draft` MCP tool

```python
def compile_to_profile_draft(
    target_path: str,                       # drafts/profiles/<role>/  (directory)
    manifest: dict,                          # frontmatter content
    body_sections: dict,                     # description, trigger_context, conventions, activated_skills_notes, primed_knowledge_notes, examples
    activated_skills: list[str],             # skills/ paths
    primed_knowledge: list[str],             # knowledge/ paths
    extends: list[str] | None = None,        # parent profile paths
    resources: list[dict] | None = None,
) -> dict:
    """Write a profile bundle draft. Validates manifest + atomic structure + reference resolution. Populates page_provenance."""
```

- Validates manifest required fields.
- Validates `activated_skills` paths all exist as skill bundles.
- Validates `primed_knowledge` paths all exist as curated knowledge.
- Validates `extends` paths exist (if provided) and don't cycle.
- Writes the bundle directory: `PROFILE.md` always; subdirectories only if non-empty.
- Records `page_provenance` rows: one per activated skill, one per primed knowledge page.

For drift recompiles:

```python
def compile_to_profile_update_draft(
    target_profile_path: str,                # profiles/<role>/  (existing)
    proposed_changes: dict,
    summary: str,
) -> dict:
    """Write a profile update draft to drafts/profiles/_updates/<role>/."""
```

### Phase 3 — `akw compile-profile` CLI

LLM-powered, hybrid creation. Workflow:

1. User specifies the role: `akw compile-profile --name frontend-engineer --domain frontend`.
2. Tool gathers candidate skills (filter by tag overlap with `domain` and any `--include-tags`) and candidate knowledge (same filter).
3. Calls LLM with Anthropic tool-use using a `propose_profile` schema (parallel to `propose_skill`):

```json
{
  "name": "propose_profile",
  "input_schema": {
    "type": "object",
    "required": [
      "name", "title",
      "description", "trigger_context",
      "conventions", "activated_skills", "primed_knowledge",
      "examples"
    ],
    "properties": {
      "name": {"type": "string"},
      "title": {"type": "string"},
      "description": {"type": "string", "description": "Single sentence."},
      "trigger_context": {"type": "string"},
      "trigger_tags": {"type": "array", "items": {"type": "string"}},
      "conventions": {
        "type": "object",
        "required": ["dos", "donts"],
        "properties": {
          "dos": {"type": "array", "items": {"type": "string"}, "minItems": 1},
          "donts": {"type": "array", "items": {"type": "string"}, "minItems": 1}
        }
      },
      "activated_skills": {
        "type": "array",
        "minItems": 1,
        "items": {
          "type": "object",
          "required": ["path", "rationale"],
          "properties": {
            "path": {"type": "string"},
            "rationale": {"type": "string"}
          }
        }
      },
      "primed_knowledge": {
        "type": "array",
        "minItems": 1,
        "items": {
          "type": "object",
          "required": ["path", "rationale"],
          "properties": {
            "path": {"type": "string"},
            "rationale": {"type": "string"}
          }
        }
      },
      "extends": {"type": "array", "items": {"type": "string"}},
      "examples": {
        "type": "array",
        "minItems": 1,
        "items": {
          "type": "object",
          "required": ["language", "code"],
          "properties": {"language": {"type": "string"}, "code": {"type": "string"}}
        }
      },
      "rationale": {"type": "string"}
    }
  }
}
```

4. Validate output (paths exist; constraints non-empty; description single-sentence).
5. Write the bundle via `compile_to_profile_draft`.
6. Per-profile transactional write; rollback on validation failure.

`akw compile-profile` flags:
- `--name <slug>` — required; the role identifier.
- `--domain <tag>` — primary tag for folder placement.
- `--include-tags <tag,tag,...>` — additional tag filter for candidate gathering.
- `--extends <profile-path>` — declare base profile.
- `--recompile-drifted` — process drift-detected profiles.
- `--limit-skills N`, `--limit-knowledge N` — cap candidate set sizes per category.
- `--force` — skip post-compile validation (debug only).

### Phase 4 — Promotion: `promote_to_profile`

```python
def promote_to_profile(
    draft_path: str,                # drafts/profiles/<role>/
    target_path: str,                # profiles/<role>/
    skip_checks: bool = False,
) -> dict:
```

- `draft_path` MUST start with `drafts/profiles/`.
- `target_path` MUST start with `profiles/`.
- Pre-promotion gate (Decision F):
  - Manifest validation
  - Atomic body structure validation
  - Reference resolution: all `activated_skills` and `primed_knowledge` paths exist
  - Inheritance acyclicity
- Moves the entire directory.
- Records final `page_provenance` rows.

### Phase 5 — Equipping mechanisms

- `group_start(profile="<name-or-path>", ...)` — declarative; equips at group creation. Resolves name to path if not absolute. Persists in `group_metadata.equipped_profile`.
- New MCP tool `profile_equip(profile_path)` — sets `equipped_profile`, returns resolved manifest + activated skills/knowledge inline. Mid-session switching.
- New MCP tool `profile_unequip()` — clears `equipped_profile`.
- New MCP tool `profile_list(filter_tags=None)` — lists available profiles.
- Auto-suggest at `session_start`: if any profile's `trigger_context` overlaps project tags, return in `pending.profile_suggestions`. Agent surfaces; user opts in via `profile_equip` or `/profile`. Never auto-apply.

`pending` payload (extended from EP-00005/EP-00006) gains:

```json
{
  "pending": {
    "unsummarized_segments": ...,
    "unsynthesized_drafts": ...,
    "orphaned_groups": ...,
    "compile_candidates": ...,
    "skill_drift_count": ...,
    "profile_suggestions": [
      {
        "path": "profiles/frontend-engineer/",
        "name": "frontend-engineer",
        "reason": "Project tags include 'react', 'css' which match this profile's trigger_context"
      }
    ],
    "profile_drift_count": ...
  }
}
```

### Phase 6 — Activation: equipped profile shapes recommended_context

When a profile is equipped, `_get_recommended_context` reshapes its output:

- Returns the equipped profile's manifest (description, trigger_context, conventions) inline.
- Replaces the generic top-K skills with the profile's `activated_skills` manifests.
- Replaces the generic top-N knowledge with the profile's `primed_knowledge` summaries.
- Includes resolved inheritance: base profile's conventions/skills/knowledge merged in (leaf overrides on conflict).

When NO profile is equipped, behavior falls back to EP-00006 default (top-K skills by trigger-tag overlap).

This gives the agent a coherent role-shaped context payload at session start.

### Phase 7 — Drift detection extended through the chain

- `page_provenance` already tracks `page_path → source_page_path`. Extend semantics so profile pages can have skill source paths.
- On skill drift (set in EP-00006): cascade to profiles. Set `drift_detected_at` on `page_provenance` rows where `page_path LIKE 'profiles/%'` AND `source_page_path` matches the drifted skill.
- `pending.profile_drift_count` SQL: count distinct profile pages with drift.
- `akw compile-profile --recompile-drifted` proposes update drafts via `compile_to_profile_update_draft`.

### Phase 8 — Tests + Docs

- Unit + integration tests covering bundle, atomic-body validation, hybrid compilation, equipping mechanisms, inheritance resolution, drift cascade.
- SPECS.md fully revised: profile tier, atomic format parallel to skills, equipping protocol, inheritance semantics.
- README adds `/profile` workflow and explains profile vs skill distinction.

---

## Implementation Phases

### Phase 1 — Profile bundle scaffolding
- [ ] `init` ensures `profiles/`, `drafts/profiles/`, `drafts/profiles/_updates/`, `drafts/archived/profiles/`
- [ ] Indexer handles `profiles/<role>/PROFILE.md` (directory-form only — no file-form for profiles)
- [ ] `tier='profile'` distinction in DuckDB index
- [ ] `memory_search(tier='profile')` works
- [ ] `memory.read_page` works on `PROFILE.md` paths

### Phase 2 — `compile_to_profile_draft` MCP tool
- [ ] New MCP tool with hybrid-aware signature
- [ ] Manifest schema validation
- [ ] Atomic body structure validation
- [ ] Reference resolution (`activated_skills` + `primed_knowledge` paths exist)
- [ ] Inheritance acyclicity check
- [ ] Atomic bundle write via `memory.write_bundle`
- [ ] Populates `page_provenance` (rows per activated skill, per primed knowledge)
- [ ] Companion `compile_to_profile_update_draft` for drift recompiles

### Phase 3 — `akw compile-profile` CLI
- [ ] LLM-powered command using Anthropic tool-use with `propose_profile` schema
- [ ] Candidate gathering (skills + knowledge by tag filter)
- [ ] `--name`, `--domain`, `--include-tags`, `--extends`, `--recompile-drifted`, `--limit-skills`, `--limit-knowledge` flags
- [ ] Per-profile transactional write; rollback on validation failure
- [ ] `/compile-profile` slash command (`.agents/commands/compile-profile.md`)

### Phase 4 — `promote_to_profile`
- [ ] New MCP tool with gate checks
- [ ] Manifest + atomic structure validation
- [ ] Reference resolution at promotion time (catches drifted refs)
- [ ] Inheritance acyclicity
- [ ] `--force` flag
- [ ] `memory.move_directory` for bundle promotion

### Phase 5 — Equipping mechanisms
- [ ] `group_start(profile=...)` accepts profile param; persists in `group_metadata.equipped_profile`
- [ ] New MCP tool `profile_equip(profile_path)` — resolves name to path, returns manifest + activated content
- [ ] New MCP tool `profile_unequip()`
- [ ] New MCP tool `profile_list(filter_tags=None)`
- [ ] `session_start` returns `pending.profile_suggestions` when project tags match
- [ ] `/profile` slash command for user-facing equip/list/unequip

### Phase 6 — Activation: profile-shaped recommended_context
- [ ] When `equipped_profile` is set, `_get_recommended_context` returns profile-shaped payload
- [ ] Inheritance resolution: walk `extends:` chain, merge conventions/skills/knowledge (leaf overrides)
- [ ] Fallback to EP-00006 default when no profile equipped
- [ ] Agents read full PROFILE.md on demand via `memory_read`

### Phase 7 — Drift detection (cascade through chain)
- [ ] `page_provenance` already tracks; extend cascade logic from skill drift to profile drift
- [ ] `pending.profile_drift_count` query
- [ ] `--recompile-drifted-profiles` flag on `akw compile-profile`

### Phase 8 — Tests + Docs
- [ ] Unit tests: profile bundle write/read
- [ ] Unit tests: PROFILE.md manifest schema validation
- [ ] Unit tests: PROFILE.md atomic-body validation (Description single-line; Trigger Context; Conventions Dos/Don'ts; Activated Skills ≥1; Primed Knowledge ≥1; Examples ≥1)
- [ ] Unit tests: `compile_to_profile_draft` writes correct structure, populates provenance
- [ ] Unit tests: `propose_profile` schema rejects malformed
- [ ] Unit tests: reference resolution at promotion time (rejects missing skill/knowledge paths)
- [ ] Unit tests: inheritance resolution (merge order, leaf overrides base on conflict)
- [ ] Unit tests: inheritance cycle detection
- [ ] Unit tests: equipping persists in `group_metadata`
- [ ] Unit tests: `_get_recommended_context` switches shape when profile equipped
- [ ] Unit tests: drift cascade (knowledge → skill → profile)
- [ ] Integration test: skills + knowledge → compile-profile → review → promote → equip → activate
- [ ] Integration test: profile drift recompile flow end-to-end
- [ ] SPECS.md: profile tier, atomic format, equipping protocol, inheritance semantics
- [ ] README: `/profile` workflow, profile vs skill distinction

---

## Out of Scope (future)

- **Multi-profile stacking / blending.** Single equipped + inheritance is the model. Stacking complicates context generation and conflict resolution; revisit only if real use cases demand.
- **Auto-equip on project tag match.** Profiles are suggested, never auto-applied. Equipping is a deliberate context-narrowing action that should be explicit.
- **Profile usage analytics.** "Which profiles get equipped most often" / "which equipped profile correlated with successful task completion" — useful future signal; out of scope initially.
- **Profile templates from scratch (no candidate skills/knowledge).** A user spinning up a fresh project has no skills/knowledge yet; can they create profiles? Yes, but manually in Obsidian — `compile_to_profile_draft` requires a curated base. Bootstrapping helpers can come later.
- **Per-task profile switching automation.** "Detect from the user's prompt that they switched from frontend to backend work and auto-suggest re-equipping." Out of scope; the explicit `profile_equip` tool is enough.
- **Profile composition beyond linear inheritance.** Mixins, traits, multiple inheritance. Linear `extends:` chain is sufficient.
- **Skill-level overrides within profiles.** "When using `lint-react` from this profile, also apply this extra config." Out of scope; if needed, the profile can include a script in `resources/` or a wrapper skill.

---

## Open Questions

1. **Inheritance conflict resolution semantics.** When `frontend-engineer` extends `web-developer` and both have a `Don't:` constraint, do they merge (both apply) or does leaf override (only frontend's applies)? Lean toward **merge for Dos/Don'ts** (more constraints don't hurt; user sees both) and **override for activated_skills / primed_knowledge** (leaf curates the precise toolkit).

2. **Profile suggestions threshold.** How much tag overlap warrants surfacing a profile suggestion? Suggest: ≥2 trigger tags overlap with project tags. Make config-driven.

3. **Should `equipped_profile` survive group continuation?** When "continue group abc123" creates a new segment, does the prior segment's equipped profile carry over? Lean **yes** — continuation implies same context. Agent can `profile_unequip` if the new segment is genuinely different work.

4. **Profile in `_get_recommended_context` token budget.** A profile's full activated-skills + primed-knowledge inlined could be substantial (5 skills × 100 tokens manifest + 5 knowledge × 200 tokens summary = ~1500 tokens). Compare to no-profile default (~5 skills inlined). The profile load is roughly equivalent. Acceptable, but worth measuring.

5. **Hybrid creation friction.** The user has to know what role they want to create. If they don't, is there a bootstrapping flow ("survey existing skills, suggest profile names")? Defer to operational observation.

6. **PROFILE.md vs SKILL.md when both apply to the same domain.** A `frontend-engineer` profile lives at `profiles/frontend-engineer/`. Could a domain-named skill also exist at `skills/frontend/<some-skill>/`? Yes — different concepts. Profiles configure roles; skills are individual capabilities. Naming collision unlikely.

## Status: PLANNED (blocked on EP-00005 + EP-00006 completion)
