"""File operations for the /memory markdown folder."""

from __future__ import annotations

from pathlib import Path


def ensure_memory_dirs(memory_dir: Path) -> None:
    """Create the full /memory folder structure."""
    dirs = [
        memory_dir / "drafts" / "sessions",
        memory_dir / "drafts" / "knowledge",
        memory_dir / "drafts" / "reviews",
        memory_dir / "knowledge" / "entities",
        memory_dir / "knowledge" / "concepts",
        memory_dir / "knowledge" / "sources",
        memory_dir / "skills",
    ]
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
    """Determine the tier from a page path."""
    if path.startswith("drafts/"):
        return "draft"
    if path.startswith("knowledge/"):
        return "knowledge"
    if path.startswith("skills/"):
        return "skill"
    return None
