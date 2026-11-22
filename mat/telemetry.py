"""Telemetry parser for Claude Code agent-output.json and run diagnostics."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class PersonaTelemetry:
    persona: str
    cost_usd: float = 0.0
    duration_ms: int = 0
    num_turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    model: str = ""
    is_error: bool = False
    rate_limited: bool = False
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "persona": self.persona,
            "cost_usd": round(self.cost_usd, 6),
            "duration_ms": self.duration_ms,
            "duration_s": round(self.duration_ms / 1000.0, 1),
            "num_turns": self.num_turns,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "model": self.model,
            "is_error": self.is_error,
            "rate_limited": self.rate_limited,
            "error_message": self.error_message,
        }


def parse_agent_output(persona: str, path: Path) -> PersonaTelemetry:
    """Extract cost, duration, turns, tokens, and error state from agent-output.json."""
    tel = PersonaTelemetry(persona=persona)
    if not path.exists():
        return tel
    raw = path.read_text(encoding="utf-8", errors="replace")
    if "hit your" in raw.lower() or "rate limit" in raw.lower():
        tel.rate_limited = True
    try:
        data = json.loads(raw)
    except ValueError:
        tel.is_error = True
        tel.error_message = "Malformed agent-output.json"
        return tel

    if isinstance(data, list) and data:
        data = data[-1]
    if not isinstance(data, dict):
        return tel

    tel.cost_usd = float(data.get("total_cost_usd") or data.get("cost_usd") or 0.0)
    tel.duration_ms = int(data.get("duration_ms") or 0)
    tel.num_turns = int(data.get("num_turns") or 0)
    tel.is_error = bool(data.get("is_error"))
    if tel.is_error:
        tel.error_message = str(data.get("result") or "Agent exited with error")[:200]

    usage = data.get("usage") or {}
    if isinstance(usage, dict):
        tel.input_tokens = int(usage.get("input_tokens") or 0)
        tel.output_tokens = int(usage.get("output_tokens") or 0)
        tel.cache_read_tokens = int(usage.get("cache_read_input_tokens") or 0)

    model_usage = data.get("modelUsage") or {}
    if isinstance(model_usage, dict) and model_usage:
        tel.model = next(iter(model_usage.keys()))
    return tel


def collect_run_telemetry(run_dir: str | Path) -> list[PersonaTelemetry]:
    """Collect telemetry across all personas in a run directory."""
    root = Path(run_dir)
    items: list[PersonaTelemetry] = []
    # Support both collected layout (<run>/<artifact>/results/agent-output.json)
    # and flat local layout (<run>/<persona>/agent-output.json)
    seen: set[str] = set()
    for p in sorted(root.glob("*/results/agent-output.json")):
        artifact_dir = p.parent.parent.name
        persona = artifact_dir.removeprefix("results-")
        seen.add(persona)
        items.append(parse_agent_output(persona, p))
    for p in sorted(root.glob("*/agent-output.json")):
        persona = p.parent.name
        if persona not in seen:
            items.append(parse_agent_output(persona, p))
    return items


def summarize_telemetry(items: list[PersonaTelemetry]) -> dict[str, Any]:
    return {
        "personas": len(items),
        "total_cost_usd": round(sum(t.cost_usd for t in items), 4),
        "total_duration_s": round(sum(t.duration_ms for t in items) / 1000.0, 1),
        "max_duration_s": round(max((t.duration_ms for t in items), default=0) / 1000.0, 1),
        "total_turns": sum(t.num_turns for t in items),
        "total_input_tokens": sum(t.input_tokens for t in items),
        "total_output_tokens": sum(t.output_tokens for t in items),
        "errors": sum(1 for t in items if t.is_error),
        "rate_limited": sum(1 for t in items if t.rate_limited),
    }
