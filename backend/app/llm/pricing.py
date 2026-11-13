"""Cost estimation from a user-editable ``pricing.json``.

Format: ``{"<provider>/<model>": {"input": <usd per 1M input tokens>, "output": ...}}``.
Keys are matched exactly first, then by longest prefix (so ``openai/gpt-4o``
also covers ``openai/gpt-4o-2026-01-01``). Unknown models cost 0 and are
reported as such.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from app.config import get_settings

DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "anthropic/claude-fable-5-1": {"input": 10.0, "output": 50.0},
    "anthropic/claude-opus-5-5": {"input": 4.0, "output": 20.0},
    "anthropic/claude-opus-5": {"input": 5.0, "output": 25.0},
    "anthropic/claude-sonnet-5-5": {"input": 2.0, "output": 10.0},
    "anthropic/claude-sonnet-5": {"input": 2.0, "output": 10.0},
    "anthropic/claude-haiku-5-5": {"input": 0.1, "output": 0.5},
    "openai/gpt-4o": {"input": 2.5, "output": 10.0},
    "openai/gpt-4o-mini": {"input": 0.15, "output": 0.6},
    "openai/gpt-4.1": {"input": 2.0, "output": 8.0},
    "openai/o3": {"input": 2.0, "output": 8.0},
    "ollama/": {"input": 0.0, "output": 0.0},
}

_lock = threading.Lock()
_cache: tuple[float, dict[str, dict[str, float]]] | None = None


def pricing_path() -> Path:
    return Path(get_settings().pricing_file)


def load_pricing() -> dict[str, dict[str, float]]:
    """Current table; creates the file with defaults when missing."""
    global _cache
    path = pricing_path()
    with _lock:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(DEFAULT_PRICING, indent=2) + "\n")
        mtime = os.stat(path).st_mtime
        if _cache and _cache[0] == mtime:
            return _cache[1]
        try:
            data = json.loads(path.read_text())
        except (ValueError, OSError):
            data = dict(DEFAULT_PRICING)
        table = _normalise(data)
        _cache = (mtime, table)
        return table


def save_pricing(data: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Validate and persist a new table."""
    global _cache
    table = _normalise(data)
    path = pricing_path()
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(table, indent=2) + "\n")
        _cache = None
    return table


def _normalise(data: dict[str, Any]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for key, val in data.items():
        if not isinstance(key, str) or not isinstance(val, dict):
            raise ValueError(f"invalid pricing entry {key!r}")  # noqa: TRY004 - API reports 422
        try:
            out[key] = {"input": float(val.get("input", 0)), "output": float(val.get("output", 0))}
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid numbers for {key!r}") from exc
        if out[key]["input"] < 0 or out[key]["output"] < 0:
            raise ValueError(f"negative price for {key!r}")
    return out


def lookup(label: str, table: dict[str, dict[str, float]] | None = None) -> dict[str, float] | None:
    """Price entry for ``provider/model`` (exact, then longest prefix)."""
    table = table if table is not None else load_pricing()
    if label in table:
        return table[label]
    best = None
    for key in table:
        if label.startswith(key) and (best is None or len(key) > len(best)):
            best = key
    return table[best] if best else None


def estimate_cost(label: str, input_tokens: int, output_tokens: int) -> float | None:
    """USD cost, or ``None`` when the model is not in the table."""
    entry = lookup(label)
    if entry is None:
        return None
    return round((input_tokens * entry["input"] + output_tokens * entry["output"]) / 1_000_000, 6)
