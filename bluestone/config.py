"""Load and validate config.yml.

Any string value of the form ${ENV_VAR} (or ${ENV_VAR:-default}) is replaced
with the environment variable at load time - this is how secrets (Anderson's
cell / email) stay out of the public repo.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config.yml"
_ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


class Config(dict):
    """dict with attribute access: cfg.timezone == cfg['timezone']."""

    def __getattr__(self, name: str) -> Any:
        try:
            val = self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc
        return Config(val) if isinstance(val, dict) else val


def _expand(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _expand(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_expand(v) for v in node]
    if isinstance(node, str):
        def sub(m: re.Match) -> str:
            return os.environ.get(m.group(1), m.group(2) if m.group(2) is not None else m.group(0))
        return _ENV_RE.sub(sub, node)
    return node


def load_config(path: str | os.PathLike | None = None) -> Config:
    p = Path(path) if path else DEFAULT_PATH
    with open(p, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    cfg = Config(_expand(raw))
    _validate(cfg)
    return cfg


def _validate(cfg: Config) -> None:
    required = ["timezone", "initial_followup", "classification", "templates",
               "template_values", "quote_parsing", "window_cleaning_plans",
               "escalation", "sending", "guardrails"]
    missing = [k for k in required if k not in cfg]
    if missing:
        raise ValueError(f"config.yml missing sections: {', '.join(missing)}")

    basis = cfg["initial_followup"].get("completion_basis")
    if basis not in ("marked_complete_at", "scheduled_end_time"):
        raise ValueError(
            f"initial_followup.completion_basis must be 'scheduled_end_time' or "
            f"'marked_complete_at', got {basis!r}")


def unfilled_placeholders(cfg: Config) -> list[str]:
    """Paths of values still set to a PLACEHOLDER string or an unresolved ${VAR}."""
    found: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, str) and ("PLACEHOLDER" in node or _ENV_RE.search(node)):
            found.append(path)

    walk(cfg, "")
    return found
