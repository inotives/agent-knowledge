"""File operations for the /memory markdown folder."""

from __future__ import annotations

from pathlib import Path


def ensure_memory_dirs(memory_dir: Path) -> None:
    """Create the deployed three-tier memory layout (EP-00008).

    Numbered prefixes encode promotion order (1 → 2 → 3); `0_configs/` is the
    wiki contract, not a tier. Archived drafts live flat-file under
    `1_drafts/_archived/` (no `sessions/` subfolder). Inside `1_drafts/`,
    nested numeric prefixes signal the *promotion target* of each draft:
    `2_knowledges/` / `2_notes/` / `2_researches/` promote to Tier 2;
    `3_skills/` promotes to Tier 3.
    """
    from agent_knowledge.core.paths import DRAFT_STAGING_DIRS

    dirs = [
        memory_dir / "0_configs" / "templates",
        memory_dir / "0_configs" / "rules",
        memory_dir / "1_drafts" / "_archived",
        memory_dir / "2_knowledges",
        memory_dir / "3_intelligences" / "skills",
        memory_dir / "3_intelligences" / "agents",
    ]
    dirs.extend(memory_dir / d for d in DRAFT_STAGING_DIRS)
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


def create_page(memory_dir: Path, path: str, content: str) -> Path:
    """Create a new markdown page. Raises if file already exists."""
    full_path = memory_dir / path
    if full_path.exists():
        raise FileExistsError(f"Page already exists: {path}")
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")
    return full_path


def read_page(memory_dir: Path, path: str) -> str:
    """Read a markdown page. Raises if not found."""
    full_path = memory_dir / path
    if not full_path.exists():
        raise FileNotFoundError(f"Page not found: {path}")
    return full_path.read_text(encoding="utf-8")


def update_page(memory_dir: Path, path: str, content: str) -> Path:
    """Update an existing markdown page. Raises if not found."""
    full_path = memory_dir / path
    if not full_path.exists():
        raise FileNotFoundError(f"Page not found: {path}")
    full_path.write_text(content, encoding="utf-8")
    return full_path


def delete_page(memory_dir: Path, path: str) -> None:
    """Delete a markdown page. Raises if not found."""
    full_path = memory_dir / path
    if not full_path.exists():
        raise FileNotFoundError(f"Page not found: {path}")
    full_path.unlink()


def move_page(memory_dir: Path, src_path: str, dst_path: str) -> Path:
    """Move a page from one location to another."""
    src = memory_dir / src_path
    dst = memory_dir / dst_path
    if not src.exists():
        raise FileNotFoundError(f"Source not found: {src_path}")
    if dst.exists():
        raise FileExistsError(f"Destination already exists: {dst_path}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    return dst


def list_pages(memory_dir: Path, subdir: str | None = None) -> list[str]:
    """List all .md files under a subdirectory, relative to memory_dir."""
    search_dir = memory_dir / subdir if subdir else memory_dir
    if not search_dir.exists():
        return []
    return sorted(
        str(p.relative_to(memory_dir))
        for p in search_dir.rglob("*.md")
    )


def get_tier(path: str) -> str | None:
    """Determine the tier label from a page path (EP-00008 layout)."""
    if path.startswith("1_drafts/"):
        return "draft"
    if path.startswith("2_knowledges/"):
        return "knowledge"
    if path.startswith("3_intelligences/skills/"):
        return "skill"
    if path.startswith("3_intelligences/agents/"):
        return "agent"
    if path.startswith("0_configs/"):
        return "config"
    return None
