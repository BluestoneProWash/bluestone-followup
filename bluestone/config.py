"""Load and validate config.yml."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config.yml"


class Config(dict):
    """dict with attribute access for convenience: cfg.timezone, cfg['timezone']."""

    def __getattr__(self, name: str) -> Any:
        try:
            val = self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc
        if isinstance(val, dict):
            return Config(val)
        return val


def load_config(path: str | os.PathLike | None = None) -> Config:
    p = Path(path) if path else DEFAULT_PATH
    with open(p, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    cfg = Config(raw)
    _validate(cfg)
    return cfg


def _validate(cfg: Config) -> None:
    required = [
        "timezone",
        "initial_followup",
        "classification",
        "templates",
        "template_values",
        "quote_parsing",
        "window_cleaning_plans",
        "escalation",
        "sending",
        "guardrails",
        "database",
    ]
    missing = [k for k in required if k not in cfg]
    if missing:
        raise ValueError(f"config.yml missing sections: {', '.join(missing)}")

    basis = cfg["initial_followup"].get("completion_basis")
    if basis not in ("marked_complete_at", "scheduled_end_time"):
        raise ValueError(
            f"initial_followup.completion_basis must be 'marked_complete_at' or "
            f"'scheduled_end_time', got {basis!r}"
        )


def unfilled_placeholders(cfg: Config) -> list[str]:
    """Return human-readable paths of values still set to a PLACEHOLDER string."""
    found: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, str) and "PLACEHOLDER" in node:
            found.append(path)

    walk(cfg, "")
    return found
