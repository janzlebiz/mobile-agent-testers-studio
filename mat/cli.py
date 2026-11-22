"""Unified CLI for Mobile Agent Testers Studio (`mat`)."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from mat import __version__
from mat.config import MatConfig, load_config, save_config, validate_config, validate_personas
from mat.dashboard import write_dashboard
from mat.findings import evaluate_quality_gate, load_all_findings
from mat.maestro_export import export_maestro_suite
from mat.telemetry import collect_run_telemetry, summarize_telemetry


def _cmd_init(args: argparse.Namespace) -> int:
    cfg = MatConfig(
        app_id=args.app_id,
        app_dir=args.app_dir,
        source_repo=args.source_repo,
        framework=args.framework,
        model=args.model,
        fail_on_severity=args.fail_on_severity,
    )
    out = save_config(cfg, args.config)
    print(f"Created {out}")
    if not Path("results_public.pem").exists():
        print("Next: run ./setup.sh (or .\\setup.ps1 on Windows) to generate encryption keys and GitHub secrets.")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    errs = validate_config(cfg)
    personas, perrs = validate_personas(args.personas)
    all_errs = errs + perrs
    if all_errs:
        for e in all_errs:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"Valid config and {len(personas)} personas.")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    android = args.android or ",".join(cfg.android_personas) or "none"
    ios = args.ios or ",".join(cfg.ios_personas) or "none"
    cmd = [
        "gh", "workflow", "run", "agent-testers.yml",
        "-f", f"android_personas={android}",
        "-f", f"ios_personas={ios}",
        "-f", f"app_dir={args.app_dir or cfg.app_dir}",
        "-f", f"app_id={args.app_id or cfg.app_id}",
        "-f", f"framework={cfg.framework}",
        "-f", f"brief={args.brief or cfg.brief}",
        "-f", f"model={args.model or cfg.model}",
    ]
    if cfg.source_repo:
        cmd += ["-f", f"source_repo={cfg.source_repo}"]
    if cfg.ref:
        cmd += ["-f", f"ref={cfg.ref}"]
    if cfg.llm_base_url:
        cmd += ["-f", f"llm_base_url={cfg.llm_base_url}"]
    if cfg.record_video or args.record_video:
        cmd += ["-f", "record_video=true"]
    if args.bug_report:
        cmd += ["-f", f"bug_report={args.bug_report}"]
    if args.dry_run:
        print(" ".join(cmd))
        return 0
    return subprocess.run(cmd).returncode


def _cmd_collect(args: argparse.Namespace) -> int:
    script = Path(__file__).resolve().parent.parent / "scripts" / "collect.sh"
    cmd = ["bash", str(script), str(args.run_id)]
    if args.key:
        cmd.append(args.key)
    if args.out:
        cmd.append(args.out)
    rc = subprocess.run(cmd).returncode
    if rc != 0:
        return rc
    run_dir = Path(args.out or f"runs/{args.run_id}")
    dash = write_dashboard(run_dir, embed_images=args.embed_images)
    print(f"Dashboard: {dash}")
    cfg = load_config(args.config)
    maestro_files = export_maestro_suite(run_dir, run_dir / "maestro", app_id=cfg.app_id)
    if maestro_files:
        print(f"Maestro flows: {len(maestro_files)} files in {run_dir / 'maestro'}")
    return 0


def _cmd_dashboard(args: argparse.Namespace) -> int:
    out = write_dashboard(args.run_dir, out_path=args.out, personas_path=args.personas, embed_images=args.embed_images)
    print(f"Dashboard written to {out}")
    return 0


def _cmd_export_maestro(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    app_id = args.app_id or cfg.app_id
    out_dir = Path(args.out or Path(args.run_dir) / "maestro")
    files = export_maestro_suite(args.run_dir, out_dir, app_id=app_id)
    print(f"Exported {len(files)} Maestro flow(s) to {out_dir}")
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    script = Path(__file__).resolve().parent.parent / "scripts" / "compare.py"
    cmd = [sys.executable, str(script), args.run_a, args.run_b]
    if args.name_a and args.name_b:
        cmd += [args.name_a, args.name_b]
    return subprocess.run(cmd).returncode


def _cmd_personas(args: argparse.Namespace) -> int:
    personas, errs = validate_personas(args.personas)
    if errs:
        for e in errs:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(personas, indent=2))
        return 0
    for p in personas:
        if args.platform and p.get("platform") != args.platform:
            continue
        dev = p.get("device") or {}
        dname = dev.get("name") or dev.get("simulator") or "?"
        print(f"{p['id']:10s} {p['platform']:7s} {p['locale']:6s} {dname}")
    return 0


def _cmd_gate(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    threshold = args.fail_on_severity or cfg.fail_on_severity
    findings = load_all_findings(args.run_dir)
    passed, violations = evaluate_quality_gate(findings, fail_on_severity=threshold)
    tel = summarize_telemetry(collect_run_telemetry(args.run_dir))
    print(f"Quality gate (fail_on_severity={threshold}): {'PASS' if passed else 'FAIL'} | "
          f"Personas={tel['personas']} | Cost=${tel['total_cost_usd']:.2f} | Violations={len(violations)}")
    for v in violations:
        print(f"  - [{v['severity'].upper()}] ({v['persona']}) {v['title']} [{v['screen']}]")
    return 0 if passed else 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mat", description="Mobile Agent Testers Studio CLI")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--config", default=None, help="Path to mat.config.json or mat.config.yml")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Create mat.config.json in current repo")
    p_init.add_argument("--app-id", default="com.example.tinytodo")
    p_init.add_argument("--app-dir", default="demo-app")
    p_init.add_argument("--source-repo", default="")
    p_init.add_argument("--framework", default="auto")
    p_init.add_argument("--model", default="sonnet")
    p_init.add_argument("--fail-on-severity", default="blocker")
    p_init.set_defaults(func=_cmd_init)

    p_val = sub.add_parser("validate", help="Validate config and personas.json")
    p_val.add_argument("--personas", default="personas.json")
    p_val.set_defaults(func=_cmd_validate)

    p_run = sub.add_parser("run", help="Trigger GitHub Actions agent-testers workflow")
    p_run.add_argument("--android", default="", help="Comma-separated Android personas, 'all', or 'none'")
    p_run.add_argument("--ios", default="", help="Comma-separated iOS personas, 'all', or 'none'")
    p_run.add_argument("--app-dir", default="")
    p_run.add_argument("--app-id", default="")
    p_run.add_argument("--brief", default="")
    p_run.add_argument("--model", default="")
    p_run.add_argument("--bug-report", default="")
    p_run.add_argument("--record-video", action="store_true")
    p_run.add_argument("--dry-run", action="store_true")
    p_run.set_defaults(func=_cmd_run)

    p_col = sub.add_parser("collect", help="Download, decrypt, summarize, and build dashboard for a run")
    p_col.add_argument("run_id")
    p_col.add_argument("--key", default="")
    p_col.add_argument("--out", default="")
    p_col.add_argument("--embed-images", action="store_true")
    p_col.set_defaults(func=_cmd_collect)

    p_dash = sub.add_parser("dashboard", help="Generate interactive dashboard.html from a collected run")
    p_dash.add_argument("run_dir")
    p_dash.add_argument("--out", default="")
    p_dash.add_argument("--personas", default="personas.json")
    p_dash.add_argument("--embed-images", action="store_true")
    p_dash.set_defaults(func=_cmd_dashboard)

    p_mae = sub.add_parser("export-maestro", help="Export Maestro regression YAML flows from a run")
    p_mae.add_argument("run_dir")
    p_mae.add_argument("--out", default="")
    p_mae.add_argument("--app-id", default="")
    p_mae.set_defaults(func=_cmd_export_maestro)

    p_cmp = sub.add_parser("compare", help="Compare two collected runs")
    p_cmp.add_argument("run_a")
    p_cmp.add_argument("run_b")
    p_cmp.add_argument("--name-a", default="")
    p_cmp.add_argument("--name-b", default="")
    p_cmp.set_defaults(func=_cmd_compare)

    p_per = sub.add_parser("personas", help="List and validate configured personas")
    p_per.add_argument("--personas", default="personas.json")
    p_per.add_argument("--platform", choices=["android", "ios"], default=None)
    p_per.add_argument("--json", action="store_true")
    p_per.set_defaults(func=_cmd_personas)

    p_gate = sub.add_parser("gate", help="Evaluate CI quality gate on a collected run")
    p_gate.add_argument("run_dir")
    p_gate.add_argument("--fail-on-severity", default="")
    p_gate.set_defaults(func=_cmd_gate)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
