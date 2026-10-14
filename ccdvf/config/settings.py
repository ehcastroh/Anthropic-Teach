"""Runtime settings loaded from settings.toml (or defaults when no file is present)."""

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings for ccdvf notebooks and scripts.

    Attributes:
        default_model: Registry key into model_registry.MODELS for default model.
        max_turns: Default agent loop turn cap before stopping.
        max_budget_usd: Default per-session cost cap in USD.
        cache_ttl_seconds: Default prompt cache TTL (300 = 5-minute; 3600 = 1-hour).
        retry_max_attempts: Default retry count on transient API failures.
        retry_backoff_base_seconds: Initial backoff interval for exponential retry.
        log_level: Default logging level ("DEBUG" | "INFO" | "WARNING" | "ERROR").
    """

    default_model: str = "haiku_current"
    max_turns: int = 20
    max_budget_usd: float = 1.00
    cache_ttl_seconds: int = 300
    retry_max_attempts: int = 3
    retry_backoff_base_seconds: float = 1.0
    log_level: str = "INFO"


_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_VALID_TTL_SECONDS = {300, 3600}


def load(path: str | Path | None = None) -> Settings:
    """Load settings from a TOML file or return defaults when no path given.

    Reads [ccdvf] table from the TOML file and merges recognized keys over
    the defaults. Unknown keys in the file are silently ignored (forward-compat).

    Preconditions:
      - If path is not None, the file exists and contains valid TOML.

    Postconditions:
      - Returns a Settings instance; any missing keys use their defaults.
      - log_level is upper-cased before comparison.

    Raises:
      - FileNotFoundError: path is not None but the file does not exist.
      - tomllib.TOMLDecodeError: file exists but cannot be parsed as TOML.
    """
    if path is None:
        return Settings()

    resolved = Path(path)
    if not resolved.exists():
        raise FileNotFoundError(f"Settings file not found: {resolved}")

    with resolved.open("rb") as f:
        raw = tomllib.load(f)

    table = raw.get("ccdvf", {})

    kwargs: dict = {}
    for f_name in Settings.__dataclass_fields__:  # type: ignore[attr-defined]
        if f_name in table:
            kwargs[f_name] = table[f_name]

    return Settings(**kwargs)
