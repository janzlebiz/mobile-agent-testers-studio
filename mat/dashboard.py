"""Self-contained interactive HTML dashboard generator for Mobile Agent Testers Studio."""
from __future__ import annotations

import base64
import html
import json
from pathlib import Path
from statistics import median
from typing import Any

from mat.findings import SEVERITY_ORDER, discover_persona_dirs, load_all_findings
from mat.telemetry import collect_run_telemetry, summarize_telemetry


def _load_personas_map(personas_path: str | Path = "personas.json") -> dict[str, dict[str, Any]]:
    p = Path(personas_path)
    if not p.exists():
        p = Path(__file__).resolve().parent.parent / "personas.json"
    if not p.exists():
        return {}
    try:
        return {item["id"]: item for item in json.loads(p.read_text(encoding="utf-8"))}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _img_src(img_path: Path, run_root: Path, embed_images: bool) -> str:
    if embed_images:
        b64 = base64.b64encode(img_path.read_bytes()).decode("ascii")
        return f"data:image/png;base64,{b64}"
    try:
        return img_path.relative_to(run_root).as_posix()
    except ValueError:
        return img_path.as_posix()


def build_run_payload(
    run_dir: str | Path,
    personas_path: str | Path = "personas.json",
    embed_images: bool = False,
) -> dict[str, Any]:
    """Assemble structured JSON payload for the interactive dashboard."""
    root = Path(run_dir)
    personas_meta = _load_personas_map(personas_path)
    findings = load_all_findings(root)
    tel_list = collect_run_telemetry(root)
    tel_map = {t.persona: t.to_dict() for t in tel_list}
    tel_summary = summarize_telemetry(tel_list)

    personas_rows: list[dict[str, Any]] = []
    all_issues: list[dict[str, Any]] = []
    scores: list[float] = []

    for persona, pdir in discover_persona_dirs(root):
        pmeta = personas_meta.get(persona, {})
        dev = pmeta.get("device") or {}
        device_name = dev.get("name") or (
            f"{dev['simulator']}, iOS {dev.get('ios', '?')}" if "simulator" in dev else persona
        )
        pdata = findings.get(persona, {})
        score = pdata.get("score")
        if isinstance(score, (int, float)):
            scores.append(float(score))

        screenshots = []
        for img in sorted(pdir.glob("[0-9][0-9].png")):
            screenshots.append({
                "num": img.stem,
                "src": _img_src(img, root, embed_images),
            })
        screen_map = {s["num"]: s["src"] for s in screenshots}

        report_md = ""
        rp = pdir / "report.md"
        if rp.exists():
            report_md = rp.read_text(encoding="utf-8", errors="replace")

        issues = pdata.get("issues") or []
        for idx, iss in enumerate(issues, 1):
            sev = iss.get("severity", "minor")
            refs = [screen_map[n] for n in iss.get("screens", []) if n in screen_map]
            all_issues.append({
                "id": f"{persona}-{idx}",
                "persona": persona,
                "platform": pmeta.get("platform", "?"),
                "locale": pmeta.get("locale", "?"),
                "device": device_name,
                "severity": sev,
                "title": iss.get("title", "Untitled"),
                "screen": iss.get("screen", ""),
                "screens": iss.get("screens", []),
                "images": refs,
                "steps": iss.get("steps", ""),
                "actual": iss.get("actual", ""),
                "expected": iss.get("expected", ""),
            })

        sev_counts = {s: 0 for s in SEVERITY_ORDER}
        for iss in issues:
            s = iss.get("severity", "minor")
            sev_counts[s] = sev_counts.get(s, 0) + 1

        personas_rows.append({
            "persona": persona,
            "platform": pmeta.get("platform", "?"),
            "device": device_name,
            "locale": pmeta.get("locale", "?"),
            "theme": dev.get("theme", "light"),
            "text_size": str(dev.get("font_scale") or dev.get("text_size") or "default"),
            "score": score,
            "goal_reached": pdata.get("goal_reached", ""),
            "actions": pdata.get("actions"),
            "issue_counts": sev_counts,
            "total_issues": len(issues),
            "screenshots": screenshots,
            "report_md": report_md,
            "telemetry": tel_map.get(persona, {}),
        })

    all_issues.sort(key=lambda x: (SEVERITY_ORDER.get(x["severity"], 9), x["persona"], x["title"].lower()))
    sev_totals = {s: sum(1 for i in all_issues if i["severity"] == s) for s in SEVERITY_ORDER}

    return {
        "run_name": root.name,
        "summary": {
            "personas_count": len(personas_rows),
            "total_issues": len(all_issues),
            "severity_totals": sev_totals,
            "median_score": round(median(scores), 1) if scores else None,
            "telemetry": tel_summary,
        },
        "personas": personas_rows,
        "issues": all_issues,
    }


def render_dashboard_html(payload: dict[str, Any]) -> str:
    """Render a zero-dependency, self-contained interactive HTML dashboard."""
    data_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    title = html.escape(f"MAT Studio — {payload.get('run_name', 'Run Dashboard')}")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{title}</title>
<style>
  :root {{
    --bg: #0b0f17; --surface: #131a26; --surface2: #1a2333; --border: #263248;
    --text: #e6edf7; --muted: #93a4bd; --accent: #3b82f6;
    --blocker: #ef4444; --major: #f97316; --minor: #eab308; --cosmetic: #64748b;
    --ok: #22c55e;
  }}
  [data-theme="light"] {{
    --bg: #f8fafc; --surface: #ffffff; --surface2: #f1f5f9; --border: #cbd5e1;
    --text: #0f172a; --muted: #475569; --accent: #2563eb;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--text); }}
  header {{ display: flex; justify-content: space-between; align-items: center; padding: 16px 24px; border-bottom: 1px solid var(--border); background: var(--surface); position: sticky; top: 0; z-index: 10; }}
  header h1 {{ margin: 0; font-size: 18px; font-weight: 600; }}
  .tabs {{ display: flex; gap: 8px; padding: 12px 24px; border-bottom: 1px solid var(--border); background: var(--surface); flex-wrap: wrap; }}
  .tab {{ padding: 8px 14px; border-radius: 8px; border: 1px solid var(--border); background: transparent; color: var(--muted); cursor: pointer; font-size: 13px; font-weight: 500; }}
  .tab.active {{ background: var(--accent); color: #fff; border-color: var(--accent); }}
  main {{ max-width: 1280px; margin: 0 auto; padding: 24px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; margin-bottom: 24px; }}
  .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 16px; }}
  .kpi-label {{ font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }}
  .kpi-val {{ font-size: 26px; font-weight: 700; margin-top: 6px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--border); }}
  th {{ color: var(--muted); font-weight: 600; background: var(--surface2); }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; text-transform: uppercase; color: #fff; }}
  .sev-blocker {{ background: var(--blocker); }}
  .sev-major {{ background: var(--major); }}
  .sev-minor {{ background: var(--minor); color: #111; }}
  .sev-cosmetic {{ background: var(--cosmetic); }}
  .filters {{ display: flex; gap: 10px; margin-bottom: 16px; flex-wrap: wrap; align-items: center; }}
  input, select, button.btn {{ padding: 8px 12px; border-radius: 8px; border: 1px solid var(--border); background: var(--surface); color: var(--text); font-size: 13px; }}
  button.btn {{ cursor: pointer; background: var(--surface2); }}
  button.btn:hover {{ border-color: var(--accent); }}
  .issue-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 16px; margin-bottom: 12px; }}
  .issue-head {{ display: flex; justify-content: space-between; gap: 12px; align-items: center; flex-wrap: wrap; }}
  .thumbs {{ display: flex; gap: 10px; margin-top: 12px; overflow-x: auto; padding-bottom: 4px; }}
  .thumbs img {{ height: 220px; border-radius: 8px; border: 1px solid var(--border); cursor: zoom-in; }}
  .filmstrip {{ display: flex; gap: 12px; overflow-x: auto; padding: 12px 0; }}
  .film-item {{ flex: 0 0 auto; text-align: center; }}
  .film-item img {{ height: 280px; border-radius: 8px; border: 1px solid var(--border); cursor: zoom-in; }}
  pre {{ background: var(--surface2); padding: 12px; border-radius: 8px; overflow-x: auto; font-size: 12px; white-space: pre-wrap; }}
  .hidden {{ display: none; }}
  #lightbox {{ position: fixed; inset: 0; background: rgba(0,0,0,0.85); display: none; align-items: center; justify-content: center; z-index: 100; padding: 24px; }}
  #lightbox img {{ max-height: 90vh; max-width: 90vw; border-radius: 10px; }}
</style>
</head>
<body>
<header>
  <h1>Mobile Agent Testers Studio — <span id="run-title"></span></h1>
  <button class="btn" id="theme-toggle">Toggle Theme</button>
</header>
<div class="tabs">
  <button class="tab active" data-tab="overview">Overview &amp; Matrix</button>
  <button class="tab" data-tab="issues">Bug Explorer</button>
  <button class="tab" data-tab="walkthroughs">Persona Walkthroughs</button>
  <button class="tab" data-tab="telemetry">Telemetry &amp; Cost</button>
</div>
<main>
  <section id="tab-overview">
    <div class="grid" id="kpis"></div>
    <div class="card">
      <h2 style="margin-top:0;font-size:15px;">Device × Locale Matrix</h2>
      <table>
        <thead><tr><th>Persona</th><th>Platform</th><th>Device</th><th>Locale</th><th>Theme</th><th>Text</th><th>Score</th><th>Blockers</th><th>Majors</th><th>Total</th></tr></thead>
        <tbody id="matrix-body"></tbody>
      </table>
    </div>
  </section>

  <section id="tab-issues" class="hidden">
    <div class="filters">
      <input id="q" type="search" placeholder="Search title, screen, persona..." style="min-width:260px;" />
      <select id="sev-filter">
        <option value="">All severities</option>
        <option value="blocker">Blocker</option>
        <option value="major">Major</option>
        <option value="minor">Minor</option>
        <option value="cosmetic">Cosmetic</option>
      </select>
      <select id="plat-filter">
        <option value="">All platforms</option>
        <option value="android">Android</option>
        <option value="ios">iOS</option>
      </select>
    </div>
    <div id="issues-list"></div>
  </section>

  <section id="tab-walkthroughs" class="hidden">
    <div class="filters">
      <select id="persona-select"></select>
    </div>
    <div class="card">
      <div id="walkthrough-meta"></div>
      <div class="filmstrip" id="walkthrough-film"></div>
      <h3>Report</h3>
      <pre id="walkthrough-report"></pre>
    </div>
  </section>

  <section id="tab-telemetry" class="hidden">
    <div class="card">
      <h2 style="margin-top:0;font-size:15px;">Per-Persona Cost &amp; Execution Telemetry</h2>
      <table>
        <thead><tr><th>Persona</th><th>Model</th><th>Turns</th><th>Duration</th><th>Cost (USD)</th><th>Input Tok</th><th>Output Tok</th><th>Status</th></tr></thead>
        <tbody id="tel-body"></tbody>
      </table>
    </div>
  </section>
</main>
<div id="lightbox" onclick="this.style.display='none'"><img id="lb-img" alt="Screenshot" /></div>
<script>
const DATA = {data_json};
document.getElementById("run-title").textContent = DATA.run_name;

document.getElementById("theme-toggle").onclick = () => {{
  const root = document.documentElement;
  root.setAttribute("data-theme", root.getAttribute("data-theme") === "light" ? "dark" : "light");
}};

document.querySelectorAll(".tab").forEach(btn => {{
  btn.onclick = () => {{
    document.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    ["overview", "issues", "walkthroughs", "telemetry"].forEach(id => {{
      document.getElementById("tab-" + id).classList.toggle("hidden", btn.dataset.tab !== id);
    }});
  }};
}});

function zoom(src) {{
  document.getElementById("lb-img").src = src;
  document.getElementById("lightbox").style.display = "flex";
}}

function esc(s) {{
  return String(s ?? "").replace(/[&<>"]/g, c => ({{ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }}[c]));
}}

const s = DATA.summary;
const tel = s.telemetry || {{}};
const kpis = [
  ["Personas Run", s.personas_count],
  ["Total Issues", s.total_issues],
  ["Blockers", (s.severity_totals || {{}}).blocker || 0],
  ["Median Score", s.median_score != null ? s.median_score + "/10" : "—"],
  ["Total Cost", "$" + (tel.total_cost_usd || 0).toFixed(2)],
  ["Max Duration", (tel.max_duration_s || 0) + "s"]
];
document.getElementById("kpis").innerHTML = kpis.map(([k, v]) =>
  `<div class="card"><div class="kpi-label">${{esc(k)}}</div><div class="kpi-val">${{esc(v)}}</div></div>`
).join("");

document.getElementById("matrix-body").innerHTML = DATA.personas.map(p => `
  <tr>
    <td><strong>${{esc(p.persona)}}</strong></td>
    <td>${{esc(p.platform)}}</td>
    <td>${{esc(p.device)}}</td>
    <td>${{esc(p.locale)}}</td>
    <td>${{esc(p.theme)}}</td>
    <td>${{esc(p.text_size)}}</td>
    <td>${{p.score != null ? p.score + "/10" : "—"}}</td>
    <td>${{p.issue_counts.blocker || 0}}</td>
    <td>${{p.issue_counts.major || 0}}</td>
    <td>${{p.total_issues}}</td>
  </tr>
`).join("");

function copyIssue(idx) {{
  const i = DATA.issues[idx];
  const body = `## [${{i.severity.toUpperCase()}}] ${{i.title}}\\n\\n- **Persona**: ${{i.persona}} (${{i.device}}, ${{i.locale}})\\n- **Screen**: ${{i.screen}}\\n- **Steps**: ${{i.steps || "See attached screenshots"}}\\n- **Actual**: ${{i.actual || "—"}}\\n- **Expected**: ${{i.expected || "—"}}`;
  navigator.clipboard?.writeText(body);
  alert("GitHub issue markdown copied to clipboard.");
}}

function renderIssues() {{
  const q = document.getElementById("q").value.toLowerCase();
  const sev = document.getElementById("sev-filter").value;
  const plat = document.getElementById("plat-filter").value;
  const rows = DATA.issues.map((i, idx) => ({{ i, idx }})).filter(({{ i }}) =>
    (!sev || i.severity === sev) &&
    (!plat || i.platform === plat) &&
    (!q || (i.title + " " + i.screen + " " + i.persona).toLowerCase().includes(q))
  );
  document.getElementById("issues-list").innerHTML = rows.length ? rows.map(({{ i, idx }}) => `
    <div class="issue-card">
      <div class="issue-head">
        <div>
          <span class="badge sev-${{esc(i.severity)}}">${{esc(i.severity)}}</span>
          <strong style="margin-left:8px;">${{esc(i.title)}}</strong>
        </div>
        <button class="btn" onclick="copyIssue(${{idx}})">Copy GitHub Issue</button>
      </div>
      <div style="color:var(--muted);font-size:12px;margin-top:6px;">
        Persona: <strong>${{esc(i.persona)}}</strong> · ${{esc(i.device)}} · ${{esc(i.locale)}} · Screen: ${{esc(i.screen || "—")}}
      </div>
      ${{i.steps ? `<div style="margin-top:8px;font-size:13px;"><strong>Steps:</strong> ${{esc(i.steps)}}</div>` : ""}}
      ${{i.images.length ? `<div class="thumbs">${{i.images.map(src => `<img src="${{esc(src)}}" onclick="zoom(this.src)" />`).join("")}}</div>` : ""}}
    </div>
  `).join("") : "<p style='color:var(--muted)'>No matching issues.</p>";
}}
["q", "sev-filter", "plat-filter"].forEach(id => document.getElementById(id).addEventListener("input", renderIssues));
renderIssues();

const pSel = document.getElementById("persona-select");
pSel.innerHTML = DATA.personas.map(p => `<option value="${{esc(p.persona)}}">${{esc(p.persona)}} — ${{esc(p.device)}} (${{esc(p.locale)}})</option>`).join("");
function renderWalkthrough() {{
  const p = DATA.personas.find(x => x.persona === pSel.value);
  if (!p) return;
  document.getElementById("walkthrough-meta").innerHTML = `<strong>${{esc(p.persona)}}</strong> · ${{esc(p.device)}} · ${{esc(p.locale)}} · Score: ${{p.score ?? "—"}}/10`;
  document.getElementById("walkthrough-film").innerHTML = p.screenshots.map(sc =>
    `<div class="film-item"><img src="${{esc(sc.src)}}" onclick="zoom(this.src)" /><div style="font-size:11px;color:var(--muted);margin-top:4px;">#${{esc(sc.num)}}</div></div>`
  ).join("") || "<span style='color:var(--muted)'>No screenshots captured.</span>";
  document.getElementById("walkthrough-report").textContent = p.report_md || "No report.md found.";
}}
pSel.onchange = renderWalkthrough;
renderWalkthrough();

document.getElementById("tel-body").innerHTML = DATA.personas.map(p => {{
  const t = p.telemetry || {{}};
  const status = t.rate_limited ? "Rate limited" : (t.is_error ? "Error" : "OK");
  return `<tr>
    <td>${{esc(p.persona)}}</td><td>${{esc(t.model || "—")}}</td><td>${{t.num_turns ?? "—"}}</td>
    <td>${{t.duration_s != null ? t.duration_s + "s" : "—"}}</td>
    <td>${{t.cost_usd != null ? "$" + Number(t.cost_usd).toFixed(4) : "—"}}</td>
    <td>${{t.input_tokens ?? "—"}}</td><td>${{t.output_tokens ?? "—"}}</td><td>${{esc(status)}}</td>
  </tr>`;
}}).join("");
</script>
</body>
</html>
"""


def write_dashboard(
    run_dir: str | Path,
    out_path: str | Path | None = None,
    personas_path: str | Path = "personas.json",
    embed_images: bool = False,
) -> Path:
    root = Path(run_dir)
    payload = build_run_payload(root, personas_path=personas_path, embed_images=embed_images)
    target = Path(out_path) if out_path else root / "dashboard.html"
    target.write_text(render_dashboard_html(payload), encoding="utf-8")
    return target
