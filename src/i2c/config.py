"""Configuration: secrets/provider from .env, UI labels/timeouts/mappings from config/settings.yaml."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from i2c.errors import ConfigError


def find_root(start: Path | None = None) -> Path:
    """Project root = first directory upwards containing config/settings.yaml."""
    env_root = os.environ.get("I2C_ROOT")
    if env_root:
        return Path(env_root).resolve()
    for base in (start or Path.cwd(), Path(__file__).resolve()):
        for d in (base, *base.parents):
            if (d / "config" / "settings.yaml").is_file():
                return d
    raise ConfigError("config/settings.yaml not found; run from the project directory or set I2C_ROOT")


@dataclass(frozen=True)
class Settings:
    root: Path
    llm_provider: str = "anthropic"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-haiku-5-5"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-flash-latest"
    extraction_max_attempts: int = 2
    window_title: str = ".*Fakturama.*"
    yaml: dict[str, Any] = field(default_factory=dict)

    @property
    def cache_dir(self) -> Path:
        return self.root / ".cache" / "extractions"

    @property
    def artifacts_dir(self) -> Path:
        return self.root / "artifacts"

    @property
    def model(self) -> str:
        return self.anthropic_model if self.llm_provider == "anthropic" else self.gemini_model

    def get(self, dotted: str, default: Any = None) -> Any:
        """settings.get('timeouts.default') -> value from settings.yaml."""
        node: Any = self.yaml
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def load_settings(root: Path | None = None) -> Settings:
    root = root or find_root()
    load_dotenv(root / ".env", override=False)
    with open(root / "config" / "settings.yaml", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    provider = (_env("LLM_PROVIDER", "anthropic") or "anthropic").lower()
    if provider not in ("anthropic", "gemini"):
        raise ConfigError(f"LLM_PROVIDER must be 'anthropic' or 'gemini', got {provider!r}")
    try:
        attempts = int(_env("EXTRACTION_MAX_ATTEMPTS", "2") or "2")
    except ValueError as exc:
        raise ConfigError("EXTRACTION_MAX_ATTEMPTS must be an integer") from exc

    return Settings(
        root=root,
        llm_provider=provider,
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        anthropic_model=_env("ANTHROPIC_MODEL", "claude-haiku-5-5") or "claude-haiku-5-5",
        gemini_api_key=_env("GEMINI_API_KEY"),
        gemini_model=_env("GEMINI_MODEL", "gemini-flash-latest") or "gemini-flash-latest",
        extraction_max_attempts=max(1, attempts),
        window_title=_env("FAKTURAMA_WINDOW_TITLE", data.get("app", {}).get("window_title_regex"))
        or ".*Fakturama.*",
        yaml=data,
    )
