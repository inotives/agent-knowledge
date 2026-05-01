"""Configuration loader — reads [tool.agent-knowledge] from pyproject.toml.

Environment overrides (highest precedence):
- AKW_DATA_DIR — override data_dir path
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class LLMConfig:
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-6"


@dataclass
class Config:
    data_dir: Path = field(default_factory=lambda: Path.home() / ".agent-knowledge")
    search_engine: str = "bm25"
    llm: LLMConfig = field(default_factory=LLMConfig)

    @property
    def db_dir(self) -> Path:
        return self.data_dir / "db"

    @property
    def sessions_db(self) -> Path:
        return self.db_dir / "sessions.db"

    @property
    def search_db(self) -> Path:
        return self.db_dir / "search.db"

    @property
    def memory_dir(self) -> Path:
        return self.data_dir / "memory"


def load_config(pyproject_path: Path | None = None) -> Config:
    """Load config from pyproject.toml [tool.agent-knowledge] section.

    Precedence: AKW_DATA_DIR env var > pyproject.toml > defaults.
    """
    env_data_dir = os.environ.get("AKW_DATA_DIR")

    if pyproject_path is None:
        pyproject_path = _find_pyproject()

    if pyproject_path is None or not pyproject_path.exists():
        if env_data_dir:
            return Config(data_dir=Path(env_data_dir).expanduser())
        return Config()

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    section = data.get("tool", {}).get("agent-knowledge", {})

    llm_data = section.get("llm", {})
    llm_defaults = LLMConfig()
    llm = LLMConfig(
        provider=llm_data.get("provider", llm_defaults.provider),
        model=llm_data.get("model", llm_defaults.model),
    )

    defaults = Config()
    data_dir_str = env_data_dir or section.get("data_dir", str(defaults.data_dir))
    return Config(
        data_dir=Path(data_dir_str).expanduser(),
        search_engine=section.get("search_engine", defaults.search_engine),
        llm=llm,
    )


def _find_pyproject() -> Path | None:
    """Walk up from cwd to find pyproject.toml."""
    current = Path.cwd()
    for parent in [current, *current.parents]:
        candidate = parent / "pyproject.toml"
        if candidate.exists():
            return candidate
    return None
