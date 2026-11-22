"""Findings validation, JSON repair, artifact discovery, and CI quality gate evaluation."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SEVERITY_ORDER = {"blocker": 0, "major": 1, "minor": 2, "cosmetic": 3}


def repair_json_text(raw: str) -> Any:
    """Parse JSON with fallback repair for markdown fences and trailing commas."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except ValueError:
        cleaned = re.sub(r",\s*([}\]])", r"\1", text)
        m = re.search(r"\{.*\}", cleaned, re.S)
        if m:
            return json.loads(m.group(0))
        raise


def normalize_findings(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize findings.json fields so downstream dashboard/summary never crash."""
    out: dict[str, Any] = dict(data)
    score = out.get("score")
    if isinstance(score, (int, float)) and not isinstance(score, bool):
        out["score"] = max(0, min(10, score))
    else:
        out["score"] = None

    issues = []
    for raw in out.get("issues") or []:
        if not isinstance(raw, dict):
            continue
        sev = str(raw.get("severity") or "minor").strip().lower()
        if sev not in SEVERITY_ORDER:
            sev = "minor"
        screens = raw.get("screens")
        if not isinstance(screens, list):
            screens = [str(screens)] if screens else []
        issues.append({
            "title": str(raw.get("title") or "Untitled issue").strip(),
            "severity": sev,
            "screen": str(raw.get("screen") or "").strip(),
            "screens": [str(s).strip() for s in screens if str(s).strip()],
            "steps": str(raw.get("steps") or "").strip(),
            "actual": str(raw.get("actual") or "").strip(),
            "expected": str(raw.get("expected") or "").strip(),
        })
    out["issues"] = issues
    return out


def discover_persona_dirs(run_dir: str | Path) -> list[tuple[str, Path]]:
    """Find (persona_id, persona_results_dir) for collected or local run directories."""
    root = Path(run_dir)
    found: dict[str, Path] = {}
    for f in sorted(root.glob("*/results/*/findings.json")):
        found[f.parent.name] = f.parent
    for f in sorted(root.glob("*/results/*/report.md")):
        found.setdefault(f.parent.name, f.parent)
    for f in sorted(root.glob("*/findings.json")):
        found.setdefault(f.parent.name, f.parent)
    return sorted(found.items(), key=lambda x: x[0])


def load_all_findings(run_dir: str | Path) -> dict[str, dict[str, Any]]:
    """Load and normalize findings.json for every persona in a run directory."""
    result: dict[str, dict[str, Any]] = {}
    for persona, pdir in discover_persona_dirs(run_dir):
        fj = pdir / "findings.json"
        if not fj.exists():
            continue
        try:
            raw = repair_json_text(fj.read_text(encoding="utf-8", errors="replace"))
            if isinstance(raw, dict):
                result[persona] = normalize_findings(raw)
        except ValueError:
            continue
    return result


def evaluate_quality_gate(
    findings_by_persona: dict[str, dict[str, Any]],
    fail_on_severity: str = "blocker",
) -> tuple[bool, list[dict[str, str]]]:
    """Return (passed, violations) based on severity threshold."""
    if fail_on_severity == "none":
        return True, []
    threshold = SEVERITY_ORDER.get(fail_on_severity, 0)
    violations: list[dict[str, str]] = []
    for persona, data in sorted(findings_by_persona.items()):
        for issue in data.get("issues", []):
            sev = issue.get("severity", "minor")
            if SEVERITY_ORDER.get(sev, 9) <= threshold:
                violations.append({
                    "persona": persona,
                    "severity": sev,
                    "title": issue.get("title", ""),
                    "screen": issue.get("screen", ""),
                })
    return len(violations) == 0, violations
