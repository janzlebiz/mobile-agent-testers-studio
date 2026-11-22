"""Unit tests for Mobile Agent Testers Studio (mat package + SoM annotation)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import annotate  # noqa: E402
from mat.cli import main as cli_main  # noqa: E402
from mat.config import MatConfig, load_config, save_config, validate_config, validate_personas  # noqa: E402
from mat.dashboard import build_run_payload, write_dashboard  # noqa: E402
from mat.findings import evaluate_quality_gate, load_all_findings, normalize_findings, repair_json_text  # noqa: E402
from mat.maestro_export import export_maestro_suite  # noqa: E402
from mat.telemetry import collect_run_telemetry, parse_agent_output, summarize_telemetry  # noqa: E402


def _make_sample_run(root: Path) -> None:
    pdir = root / "results-anna" / "results" / "anna"
    pdir.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (200, 400), color=(30, 40, 50))
    img.save(pdir / "01.png")
    (pdir / "01.meta.json").write_text(json.dumps({
        "screen": "01",
        "platform": "android",
        "width": 400,
        "height": 800,
        "last_action": {"type": "tap", "label": "Continue"},
        "elements": [{"kind": "button", "label": "Continue", "centre": [200, 600], "box": [100, 560, 300, 640]}],
    }), encoding="utf-8")
    (pdir / "findings.json").write_text(json.dumps({
        "score": 6,
        "goal_reached": "partly",
        "actions": 18,
        "issues": [
            {
                "title": "Submit button clipped at 1.3x font",
                "severity": "blocker",
                "screen": "Sign-up",
                "screens": ["01"],
                "steps": "1. Open Sign-up\n2. Scroll down",
                "actual": "Button cut off",
                "expected": "Button visible",
            },
            {
                "title": "Missing label on help icon",
                "severity": "minor",
                "screen": "Home",
                "screens": ["01"],
            },
        ],
    }), encoding="utf-8")
    (pdir / "report.md").write_text("# Anna Report\nBlocker found on Sign-up.", encoding="utf-8")
    (root / "results-anna" / "results" / "agent-output.json").write_text(json.dumps({
        "total_cost_usd": 0.24,
        "duration_ms": 42500,
        "num_turns": 18,
        "is_error": False,
        "usage": {"input_tokens": 12000, "output_tokens": 1800, "cache_read_input_tokens": 5000},
        "modelUsage": {"claude-sonnet-4-5": {"costUSD": 0.24}},
    }), encoding="utf-8")


class StudioTests(unittest.TestCase):
    def test_config_roundtrip_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = MatConfig(app_id="com.acme.app", fail_on_severity="major")
            p = save_config(cfg, Path(tmp) / "mat.config.json")
            loaded = load_config(p)
            self.assertEqual(loaded.app_id, "com.acme.app")
            self.assertEqual(loaded.fail_on_severity, "major")
            self.assertEqual(validate_config(loaded), [])

            bad = MatConfig(app_id="", fail_on_severity="critical")
            errs = validate_config(bad)
            self.assertEqual(len(errs), 2)

    def test_personas_schema_valid(self):
        personas, errs = validate_personas(ROOT / "personas.json")
        self.assertEqual(errs, [])
        self.assertGreaterEqual(len(personas), 20)

    def test_findings_repair_and_gate(self):
        repaired = repair_json_text("```json\n{\"score\": 15, \"issues\": [{\"title\": \"Crash\", \"severity\": \"BLOCKER\",},]}\n```")
        norm = normalize_findings(repaired)
        self.assertEqual(norm["score"], 10)
        self.assertEqual(norm["issues"][0]["severity"], "blocker")

        passed, violations = evaluate_quality_gate({"anna": norm}, fail_on_severity="blocker")
        self.assertFalse(passed)
        self.assertEqual(len(violations), 1)

        passed_none, _ = evaluate_quality_gate({"anna": norm}, fail_on_severity="none")
        self.assertTrue(passed_none)

    def test_telemetry_parsing(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run-1"
            _make_sample_run(run_dir)
            items = collect_run_telemetry(run_dir)
            self.assertEqual(len(items), 1)
            self.assertAlmostEqual(items[0].cost_usd, 0.24)
            self.assertEqual(items[0].model, "claude-sonnet-4-5")
            summary = summarize_telemetry(items)
            self.assertEqual(summary["total_turns"], 18)
            self.assertEqual(summary["rate_limited"], 0)

            tel = parse_agent_output("x", run_dir / "missing.json")
            self.assertEqual(tel.cost_usd, 0.0)

    def test_dashboard_and_maestro_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run-1"
            _make_sample_run(run_dir)
            findings = load_all_findings(run_dir)
            self.assertIn("anna", findings)

            payload = build_run_payload(run_dir, personas_path=ROOT / "personas.json", embed_images=True)
            self.assertEqual(payload["summary"]["personas_count"], 1)
            self.assertEqual(payload["summary"]["total_issues"], 2)
            self.assertTrue(payload["issues"][0]["images"][0].startswith("data:image/png;base64,"))

            dash_path = write_dashboard(run_dir, personas_path=ROOT / "personas.json")
            self.assertTrue(dash_path.exists())
            self.assertIn("Submit button clipped", dash_path.read_text(encoding="utf-8"))

            maestro_files = export_maestro_suite(run_dir, run_dir / "maestro", app_id="com.acme.app")
            self.assertEqual(len(maestro_files), 2)
            walk_yaml = (run_dir / "maestro" / "walkthrough-anna.yaml").read_text(encoding="utf-8")
            self.assertIn("appId: com.acme.app", walk_yaml)
            self.assertIn('- tapOn: "Continue"', walk_yaml)

    def test_som_annotate(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            img = Image.new("RGB", (200, 400), color=(255, 255, 255))
            img_path = p / "01.png"
            img.save(img_path)
            els = [{"kind": "button", "label": "OK", "centre": [100, 200], "box": [40, 180, 160, 220]}]
            meta = annotate.write_screen_metadata(p, "01", els, 400, 800, "android", None)
            som = annotate.draw_som_overlay(img_path, els, scale_x=0.5, scale_y=0.5)
            self.assertTrue(meta.exists())
            self.assertTrue(som.exists())

    def test_cli_validate_and_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run-1"
            _make_sample_run(run_dir)
            rc_val = cli_main(["validate", "--personas", str(ROOT / "personas.json")])
            self.assertEqual(rc_val, 0)
            rc_gate = cli_main(["gate", str(run_dir), "--fail-on-severity", "blocker"])
            self.assertEqual(rc_gate, 2)


if __name__ == "__main__":
    unittest.main()
