import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "high"
DEFAULT_OUTPUT_DIR = "output"
DEFAULT_MAX_SCRIPTS = 20
DEFAULT_REQUEST_TIMEOUT = 15.0
DEFAULT_MAX_OUTPUT_TOKENS = 64000
DEFAULT_BATCH_CHAR_BUDGET = 120000
VALID_EFFORT_LEVELS = frozenset({"low", "medium", "high", "xhigh", "max"})


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str | None
    anthropic_model: str
    anthropic_effort: str | None
    output_dir: Path
    max_scripts: int
    request_timeout: float
    max_output_tokens: int
    batch_char_budget: int

    @property
    def has_api_key(self) -> bool:
        return bool(self.anthropic_api_key)


def _read_text(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None:
        return default
    stripped = value.strip()
    return stripped if stripped else default


def _read_positive_int(name: str, default: int) -> int:
    raw_value = _read_text(name)
    if raw_value is None:
        return default
    try:
        parsed = int(raw_value)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be an integer, got '{raw_value}'") from error
    if parsed <= 0:
        raise ConfigurationError(f"{name} must be greater than zero, got {parsed}")
    return parsed


def _read_positive_float(name: str, default: float) -> float:
    raw_value = _read_text(name)
    if raw_value is None:
        return default
    try:
        parsed = float(raw_value)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be a number, got '{raw_value}'") from error
    if parsed <= 0:
        raise ConfigurationError(f"{name} must be greater than zero, got {parsed}")
    return parsed


def _read_effort() -> str | None:
    raw_value = os.getenv("ANTHROPIC_EFFORT")
    if raw_value is None:
        return DEFAULT_EFFORT
    normalized = raw_value.strip().lower()
    if not normalized or normalized == "none":
        return None
    if normalized not in VALID_EFFORT_LEVELS:
        allowed = ", ".join(sorted(VALID_EFFORT_LEVELS))
        raise ConfigurationError(f"ANTHROPIC_EFFORT must be one of: {allowed}, or 'none'")
    return normalized


def load_settings(env_file: Path | None = None) -> Settings:
    load_dotenv(dotenv_path=env_file, override=False)
    return Settings(
        anthropic_api_key=_read_text("ANTHROPIC_API_KEY"),
        anthropic_model=_read_text("ANTHROPIC_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL,
        anthropic_effort=_read_effort(),
        output_dir=Path(_read_text("OUTPUT_DIR", DEFAULT_OUTPUT_DIR) or DEFAULT_OUTPUT_DIR),
        max_scripts=_read_positive_int("MAX_SCRIPTS", DEFAULT_MAX_SCRIPTS),
        request_timeout=_read_positive_float("REQUEST_TIMEOUT", DEFAULT_REQUEST_TIMEOUT),
        max_output_tokens=_read_positive_int("MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS),
        batch_char_budget=_read_positive_int("BATCH_CHAR_BUDGET", DEFAULT_BATCH_CHAR_BUDGET),
    )
