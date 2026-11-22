"""Project configuration and persona schema validation for Mobile Agent Testers Studio."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_BLOCK_HOSTS = [
    "app.posthog.com",
    "eu.i.posthog.com",
    "us.i.posthog.com",
    "graph.facebook.com",
    "api.mixpanel.com",
    "api2.amplitude.com",
]

VALID_PLATFORMS = ("android", "ios")
VALID_NETWORKS = ("normal", "offline", "slow")
VALID_SEVERITIES = ("blocker", "major", "minor", "cosmetic", "none")


@dataclass
class MatConfig:
    """Typed configuration loaded from mat.config.json or mat.config.yml."""

    app_id: str = "com.example.tinytodo"
    app_dir: str = "demo-app"
    source_repo: str = ""
    ref: str = ""
    framework: str = "auto"
    android_personas: list[str] = field(default_factory=lambda: ["anna", "malik"])
    ios_personas: list[str] = field(default_factory=lambda: ["clara", "jonas"])
    brief: str = "AGENT_BRIEF.md"
    model: str = "sonnet"
    llm_base_url: str = ""
    block_hosts: list[str] = field(default_factory=lambda: list(DEFAULT_BLOCK_HOSTS))
    record_video: bool = False
    som_annotate: bool = True
    fail_on_severity: str = "blocker"
    deep_link: str = ""
    android_build: str = ""
    apk_path: str = ""
    ios_build: str = ""
    ios_app_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "app_id": self.app_id,
            "app_dir": self.app_dir,
            "source_repo": self.source_repo,
            "ref": self.ref,
            "framework": self.framework,
            "android_personas": self.android_personas,
            "ios_personas": self.ios_personas,
            "brief": self.brief,
            "model": self.model,
            "llm_base_url": self.llm_base_url,
            "block_hosts": self.block_hosts,
            "record_video": self.record_video,
            "som_annotate": self.som_annotate,
            "fail_on_severity": self.fail_on_severity,
            "deep_link": self.deep_link,
            "android_build": self.android_build,
            "apk_path": self.apk_path,
            "ios_build": self.ios_build,
            "ios_app_path": self.ios_app_path,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MatConfig":
        cfg = cls()
        for k, v in data.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        return cfg


def load_config(path: str | Path | None = None) -> MatConfig:
    """Load mat.config.json (or .yml/.yaml if PyYAML is installed)."""
    candidates = [Path(path)] if path else [
        Path("mat.config.json"),
        Path("mat.config.yml"),
        Path("mat.config.yaml"),
    ]
    for p in candidates:
        if not p.exists():
            continue
        raw = p.read_text(encoding="utf-8")
        if p.suffix == ".json":
            return MatConfig.from_dict(json.loads(raw))
        try:
            import yaml  # type: ignore
            return MatConfig.from_dict(yaml.safe_load(raw) or {})
        except ImportError as exc:
            raise RuntimeError(
                f"{p} requires PyYAML (`pip install pyyaml`), or use mat.config.json."
            ) from exc
    return MatConfig()


def save_config(cfg: MatConfig, path: str | Path = "mat.config.json") -> Path:
    p = Path(path)
    p.write_text(json.dumps(cfg.to_dict(), indent=2) + "\n", encoding="utf-8")
    return p


def validate_config(cfg: MatConfig) -> list[str]:
    errors: list[str] = []
    if not cfg.app_id.strip():
        errors.append("app_id must not be empty")
    if cfg.fail_on_severity not in VALID_SEVERITIES:
        errors.append(f"fail_on_severity '{cfg.fail_on_severity}' must be one of {VALID_SEVERITIES}")
    if not isinstance(cfg.android_personas, list) or not isinstance(cfg.ios_personas, list):
        errors.append("android_personas and ios_personas must be lists")
    return errors


def validate_personas(path: str | Path = "personas.json") -> tuple[list[dict[str, Any]], list[str]]:
    """Validate personas.json schema; returns (personas, errors)."""
    p = Path(path)
    if not p.exists():
        return [], [f"{p} not found"]
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as exc:
        return [], [f"Invalid JSON in {p}: {exc}"]
    if not isinstance(data, list):
        return [], [f"{p} must be a JSON array"]

    errors: list[str] = []
    seen_ids: set[str] = set()
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            errors.append(f"Persona #{idx} must be an object")
            continue
        pid = str(item.get("id") or "").strip()
        if not pid:
            errors.append(f"Persona #{idx} missing 'id'")
        elif pid in seen_ids:
            errors.append(f"Duplicate persona id '{pid}'")
        seen_ids.add(pid)

        platform = item.get("platform")
        if platform not in VALID_PLATFORMS:
            errors.append(f"Persona '{pid or idx}' invalid platform '{platform}' (expected android|ios)")

        for req in ("profile", "goal", "locale", "device"):
            if not item.get(req):
                errors.append(f"Persona '{pid or idx}' missing '{req}'")

        net = item.get("network", "normal")
        if net not in VALID_NETWORKS:
            errors.append(f"Persona '{pid or idx}' invalid network '{net}' (expected {VALID_NETWORKS})")

        dev = item.get("device")
        if isinstance(dev, dict):
            if platform == "ios" and "simulator" not in dev:
                errors.append(f"iOS persona '{pid}' device missing 'simulator'")
            if platform == "android" and "profile" not in dev:
                errors.append(f"Android persona '{pid}' device missing 'profile'")
    return data, errors
