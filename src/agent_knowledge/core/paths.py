"""Tier path constants for the deployed memory folder layout (EP-00008).

The deployed memory folder uses a numbered three-tier wiki layout. The numeric
prefixes encode promotion order (1 → 2 → 3); `0_configs/` is the wiki contract
(templates + rules), not a tier.

Archived drafts are stored flat-file under `1_drafts/_archived/` with a
`sessions__` filename prefix, NOT in a subfolder. Flat-file form keeps every
tier's archive a single glob and makes the archive cheap to walk.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath


DRAFTS_PREFIX = "1_drafts/"
SESSIONS_DIR = "1_drafts/sessions"
ARCHIVED_DIR = "1_drafts/_archived"
ARCHIVED_SESSION_PREFIX = "sessions__"
ARCHIVED_SESSION_GLOB = "1_drafts/_archived/sessions__*.md"

DRAFT_STAGING_DIRS = (
    "1_drafts/sessions",
    "1_drafts/2_knowledges",
    "1_drafts/2_notes",
    "1_drafts/2_researches",
    "1_drafts/3_skills",
    "1_drafts/reviews",
)

SKILLS_DIR = "3_intelligences/skills"
AGENTS_DIR = "3_intelligences/agents"
SKILL_ENTRY_FILENAME = "SKILL.md"

# Tiers indexed for the dedicated discovery tools (EP-00009). Each entry:
#   (tier_label, root_dir, file_or_glob_to_walk)
# `SKILL.md` for skills (one row per bundle, `resources/`/`scripts/`/`tests/` are NOT
# searchable but surface in `skill_get` manifest); any `*.md` for agents (single-file
# personas).
INTELLIGENCES_TIERS = (
    ("skill", SKILLS_DIR, SKILL_ENTRY_FILENAME),
    ("agent", AGENTS_DIR, "*.md"),
)

WRITE_BLOCKED_PREFIXES = (
    "2_knowledges/",
    "3_intelligences/",
    "0_configs/",
    "1_drafts/_archived/",
)

# Carve-outs inside curated tiers that agents may create + update.
# Delete of these paths archives to `<tier>/_archived/<original-relative>.md`
# instead of unlinking — see `archived_knowledge_path` and the memory_delete flow.
WRITE_ALLOWED_OVERRIDES = (
    "2_knowledges/preferences/",
)

# Tiers indexed by `memory_search` (general). Skills + agents are intentionally
# excluded — they have dedicated discovery tools (Phase B) since they're invoked
# in narrow cases (skill equip, agent role assignment), not exploratory search.
INDEXED_TIERS = (
    ("knowledge", "2_knowledges"),
    ("session_draft", "1_drafts/sessions"),
    ("knowledge_draft", "1_drafts/2_knowledges"),
    ("note_draft", "1_drafts/2_notes"),
    ("research_draft", "1_drafts/2_researches"),
    ("skill_draft", "1_drafts/3_skills"),
)

ARCHIVED_SESSION_TIER = "session_archived"

VALID_TIERS = frozenset(
    [label for label, _ in INDEXED_TIERS] + [ARCHIVED_SESSION_TIER]
)


def compact_iso(iso: str) -> str:
    """ISO8601 → filename-safe compact form: `20260420-1030`."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})", iso)
    if m:
        return f"{m.group(1)}{m.group(2)}{m.group(3)}-{m.group(4)}{m.group(5)}"
    return iso.replace(":", "").replace("-", "").replace("T", "-")[:13]


def session_draft_path(group_id: str, segment_start_at: str) -> str:
    """Canonical session-draft path: `1_drafts/sessions/<gid8>-<YYYYMMDD-HHMM>.md`."""
    return f"{SESSIONS_DIR}/{group_id[:8]}-{compact_iso(segment_start_at)}.md"


def archived_session_path(draft_path: str) -> str:
    """Map a live draft path to its archived flat-file equivalent.

    `1_drafts/sessions/foo.md` → `1_drafts/_archived/sessions__foo.md`.
    """
    basename = PurePosixPath(draft_path).name
    return f"{ARCHIVED_DIR}/{ARCHIVED_SESSION_PREFIX}{basename}"


def is_archived_session_path(path: str) -> bool:
    """True for paths matching `1_drafts/_archived/sessions__*.md`."""
    return (
        path.startswith(f"{ARCHIVED_DIR}/{ARCHIVED_SESSION_PREFIX}")
        and path.endswith(".md")
    )


def reject_curated_write(path: str) -> str | None:
    """Return a rejection reason if `path` is in a curator-only tier, else None.

    The MCP write boundary: agents may not create or update under any of
    `WRITE_BLOCKED_PREFIXES` (curated tiers, the wiki contract, archived drafts).

    `WRITE_ALLOWED_OVERRIDES` carves out narrow agent-writable paths inside
    those blocked tiers (e.g. `2_knowledges/preferences/` for user preferences).
    """
    for override in WRITE_ALLOWED_OVERRIDES:
        if path.startswith(override):
            return None
    for prefix in WRITE_BLOCKED_PREFIXES:
        if path.startswith(prefix):
            return (
                f"MCP cannot write to `{prefix}` — curator-only. "
                f"Path rejected: {path}"
            )
    return None


def is_archive_redirected_path(path: str) -> bool:
    """True if `memory_delete` should archive (move) instead of unlink.

    Mirrors `WRITE_ALLOWED_OVERRIDES` — paths agents can create are paths
    they should be able to retire, but archival keeps the audit trail.
    """
    for override in WRITE_ALLOWED_OVERRIDES:
        if path.startswith(override):
            return True
    return False


def parse_skill_path(path: str) -> tuple[str, str] | None:
    """Extract `(domain, slug)` from a skill bundle path.

    Accepts the canonical SKILL.md path or the bundle dir:
      `3_intelligences/skills/engineering/python-coding/SKILL.md` → `("engineering", "python-coding")`
      `3_intelligences/skills/engineering/python-coding`         → `("engineering", "python-coding")`

    Returns `None` if the path isn't shaped like a skill bundle.
    """
    if not path.startswith(f"{SKILLS_DIR}/"):
        return None
    rest = path[len(SKILLS_DIR) + 1:].rstrip("/")
    if rest.endswith(f"/{SKILL_ENTRY_FILENAME}"):
        rest = rest[: -len(SKILL_ENTRY_FILENAME) - 1]
    parts = rest.split("/")
    if len(parts) < 2:
        return None
    domain = parts[0]
    slug = "/".join(parts[1:])
    if not domain or not slug:
        return None
    return domain, slug


def parse_agent_path(path: str) -> tuple[str, str] | None:
    """Extract `(domain, slug)` from an agent persona path.

    `3_intelligences/agents/engineering/sre.md` → `("engineering", "sre")`.

    Returns `None` for malformed paths.
    """
    if not path.startswith(f"{AGENTS_DIR}/"):
        return None
    rest = path[len(AGENTS_DIR) + 1:]
    if not rest.endswith(".md"):
        return None
    parts = rest[: -len(".md")].split("/")
    if len(parts) < 2:
        return None
    domain = parts[0]
    slug = "/".join(parts[1:])
    if not domain or not slug:
        return None
    return domain, slug


def skill_bundle_dir(skill_path: str) -> str:
    """Directory containing `SKILL.md` for a bundle.

    `3_intelligences/skills/engineering/python-coding/SKILL.md`
        → `3_intelligences/skills/engineering/python-coding`.

    Accepts the bundle directory itself unchanged.
    """
    if skill_path.endswith(f"/{SKILL_ENTRY_FILENAME}"):
        return skill_path[: -len(SKILL_ENTRY_FILENAME) - 1]
    return skill_path.rstrip("/")


def resolve_skill_path(arg: str) -> str:
    """Resolve a skill_get argument to a canonical SKILL.md path.

    Accepts either the full path (with or without `/SKILL.md` suffix) or
    `<domain>/<slug>` shorthand. Returns the canonical SKILL.md path.
    """
    if arg.startswith(f"{SKILLS_DIR}/"):
        bundle = skill_bundle_dir(arg)
        return f"{bundle}/{SKILL_ENTRY_FILENAME}"
    return f"{SKILLS_DIR}/{arg.rstrip('/')}/{SKILL_ENTRY_FILENAME}"


def resolve_agent_path(arg: str) -> str:
    """Resolve an agent_get argument to a canonical persona path.

    Accepts either the full path (with `.md`) or `<domain>/<slug>` shorthand.
    """
    if arg.startswith(f"{AGENTS_DIR}/"):
        return arg if arg.endswith(".md") else f"{arg}.md"
    return f"{AGENTS_DIR}/{arg.rstrip('/')}.md"


def archived_knowledge_path(path: str) -> str:
    """Compute the archive target for a curated-tier page.

    Inserts `_archived/` after the tier root, preserving the rest of the path:
    `2_knowledges/preferences/foo.md` → `2_knowledges/_archived/preferences/foo.md`.

    Raises ValueError if `path` isn't under a known curated tier root.
    """
    for tier_root in ("2_knowledges/", "3_intelligences/"):
        if path.startswith(tier_root) and not path.startswith(f"{tier_root}_archived/"):
            rest = path[len(tier_root):]
            return f"{tier_root}_archived/{rest}"
    raise ValueError(f"Not a curated-tier path eligible for archive: {path}")
