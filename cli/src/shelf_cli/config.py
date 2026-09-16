"""Where the CLI gets its instance URL and token.

Three layers, most-specific first — the same shape deputy uses:

    CLI flag / env var   ->   shelf.toml   ->   built-in default

so the static bits (which instance, which space things default into) live
in a file next to your profiles, and only what changes per run is typed.

The token is deliberately *not* read from `shelf.toml`: config files get
committed by accident and tokens shouldn't be. It comes from the
environment or `--token`, and the error says so when it's missing.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG_FILE = "shelf.toml"

ENV_BASE_URL = "SHELF_API_BASE_URL"
ENV_TOKEN = "SHELF_API_TOKEN"
ENV_CONFIG = "SHELF_CLI_TOML"


class ConfigError(RuntimeError):
    """Something the user has to fix before anything can run."""


@dataclass(frozen=True)
class Config:
    base_url: str
    token: str
    # Default space for commands that don't name one. None means "let the
    # server pick", which it does by taking the caller's oldest writable space.
    space: str | None = None


def load_file(path: str | None) -> dict:
    """Read shelf.toml, or return {} when there isn't one.

    An explicitly-named file that doesn't exist is an error; the default
    one simply not being there is not.
    """
    explicit = path or os.environ.get(ENV_CONFIG)
    target = Path(explicit or DEFAULT_CONFIG_FILE)
    if not target.exists():
        if explicit:
            raise ConfigError(f"No such config file: {target}")
        return {}
    with target.open("rb") as fh:
        return tomllib.load(fh)


def resolve(
    *,
    base_url: str | None = None,
    token: str | None = None,
    space: str | None = None,
    config_path: str | None = None,
) -> Config:
    """Collapse the three layers into one resolved config."""
    cfg = load_file(config_path)
    instance = cfg.get("instance", {}) if isinstance(cfg.get("instance"), dict) else {}

    resolved_base = base_url or os.environ.get(ENV_BASE_URL) or instance.get("base_url")
    if not resolved_base:
        raise ConfigError(
            f"No instance URL. Pass --api-base, set {ENV_BASE_URL}, or put\n"
            f'  [instance]\n  base_url = "https://shelf.example.com"\n'
            f"in {DEFAULT_CONFIG_FILE}."
        )

    resolved_token = token or os.environ.get(ENV_TOKEN)
    if not resolved_token:
        raise ConfigError(
            f"No API token. Pass --token or set {ENV_TOKEN}. Mint one under\n"
            f"Settings -> API tokens; it is not read from {DEFAULT_CONFIG_FILE} "
            f"on purpose, so a committed config can't leak it."
        )

    return Config(
        base_url=str(resolved_base).rstrip("/"),
        token=str(resolved_token),
        space=space or instance.get("space"),
    )
