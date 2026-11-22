"""Zero-dependency Gemini multimodal mobile QA agent runner.

Replaces Claude Code CLI on GitHub Actions runners when GEMINI_API_KEY (or a
Google OAuth access token) is provided. Uses Gemini REST API + function calling
to drive Android (adb) or iOS (AXe/simctl) and produce:
  - results/<persona>/report.md
  - results/<persona>/findings.json
  - results/agent-output.json (compatible with mat/telemetry.py)
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

MODEL_PRICING_PER_M = {
    "gemini-2.5-flash": (0.15, 0.60),
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.0-flash": (0.10, 0.40),
}

TOOLS_DECL = [
    {
        "functionDeclarations": [
            {
                "name": "see_screen",
                "description": "Take a numbered screenshot and list interactive UI elements + accessibility warnings.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {"step": {"type": "STRING", "description": "Two-digit step number, e.g. '01', '02'."}},
                    "required": ["step"],
                },
            },
            {
                "name": "tap",
                "description": "Tap screen coordinates (Android: real pixels; iOS: points).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "x": {"type": "INTEGER"},
                        "y": {"type": "INTEGER"},
                    },
                    "required": ["x", "y"],
                },
            },
            {
                "name": "swipe",
                "description": "Swipe/scroll from (x1,y1) to (x2,y2).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "x1": {"type": "INTEGER"},
                        "y1": {"type": "INTEGER"},
                        "x2": {"type": "INTEGER"},
                        "y2": {"type": "INTEGER"},
                    },
                    "required": ["x1", "y1", "x2", "y2"],
                },
            },
            {
                "name": "type_text",
                "description": "Type text into the currently focused field.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {"text": {"type": "STRING"}},
                    "required": ["text"],
                },
            },
            {
                "name": "back",
                "description": "Navigate back / dismiss sheet.",
                "parameters": {"type": "OBJECT", "properties": {}},
            },
            {
                "name": "relaunch",
                "description": "Relaunch the app under test.",
                "parameters": {"type": "OBJECT", "properties": {}},
            },
            {
                "name": "finish_report",
                "description": "Save final report.md and findings.json and end the test session.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "report_md": {"type": "STRING", "description": "Full markdown QA report."},
                        "score": {"type": "NUMBER", "description": "Persona score out of 10."},
                        "goal_reached": {"type": "STRING", "description": "yes, partly, or no."},
                        "issues": {
                            "type": "ARRAY",
                            "items": {
                                "type": "OBJECT",
                                "properties": {
                                    "title": {"type": "STRING"},
                                    "severity": {"type": "STRING"},
                                    "screen": {"type": "STRING"},
                                    "screens": {"type": "ARRAY", "items": {"type": "STRING"}},
                                    "steps": {"type": "STRING"},
                                    "actual": {"type": "STRING"},
                                    "expected": {"type": "STRING"},
                                },
                                "required": ["title", "severity", "screen"],
                            },
                        },
                    },
                    "required": ["report_md", "score", "issues"],
                },
            },
        ]
    }
]


def _run(cmd: list[str], timeout: int = 60) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")


def _exec_tool(name: str, args: dict[str, Any], platform: str, persona: str, app_id: str) -> tuple[str, Path | None]:
    out_dir = Path("results") / persona
    out_dir.mkdir(parents=True, exist_ok=True)
    if name == "see_screen":
        nn = str(args.get("step", "01")).zfill(2)
        script = f"scripts/{platform}/screen.py"
        out = _run([sys.executable, script, str(out_dir), nn])
        som = out_dir / f"{nn}_som.png"
        png = som if som.exists() else (out_dir / f"{nn}.png")
        return out.strip(), png if png.exists() else None

    if name == "tap":
        x, y = int(args["x"]), int(args["y"])
        if platform == "android":
            return _run(["adb", "shell", "input", "tap", str(x), str(y)]), None
        return _run(["bash", "scripts/ios/gesture.sh", "tap", str(x), str(y)]), None

    if name == "swipe":
        x1, y1, x2, y2 = int(args["x1"]), int(args["y1"]), int(args["x2"]), int(args["y2"])
        if platform == "android":
            return _run(["adb", "shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), "400"]), None
        direction = "down" if y2 < y1 else "up"
        return _run(["bash", "scripts/ios/gesture.sh", "scroll", direction]), None

    if name == "type_text":
        text = str(args.get("text", ""))
        return _run([sys.executable, f"scripts/{platform}/type.py", text]), None

    if name == "back":
        if platform == "android":
            return _run(["adb", "shell", "input", "keyevent", "KEYCODE_BACK"]), None
        return _run(["bash", "scripts/ios/gesture.sh", "back"]), None

    if name == "relaunch":
        if platform == "android":
            _run(["adb", "shell", "am", "force-stop", app_id])
            return _run(["adb", "shell", "monkey", "-p", app_id, "-c", "android.intent.category.LAUNCHER", "1"]), None
        return _run(["bash", "scripts/ios/gesture.sh", "relaunch"]), None

    return f"Unknown tool: {name}", None


def call_gemini(
    model: str,
    api_key: str,
    oauth_token: str,
    contents: list[dict[str, Any]],
    system_instruction: str,
) -> dict[str, Any]:
    """Call Gemini generateContent REST API using API key or Google OAuth token."""
    url = f"{GEMINI_API_BASE}/{model}:generateContent"
    headers = {"Content-Type": "application/json"}
    if oauth_token:
        headers["Authorization"] = f"Bearer {oauth_token}"
    else:
        url = f"{url}?key={api_key}"

    payload = {
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "contents": contents,
        "tools": TOOLS_DECL,
        "generationConfig": {"temperature": 0.2},
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run_session() -> int:
    persona = os.environ["PERSONA"]
    platform = os.environ.get("PLATFORM", "android")
    app_id = os.environ.get("APP_ID", "")
    brief_path = Path(os.environ.get("BRIEF", "AGENT_BRIEF.md"))
    model = os.environ.get("MODEL", "gemini-2.5-flash")
    if model in ("sonnet", "haiku", "opus", ""):
        model = "gemini-2.5-flash"

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    oauth_token = os.environ.get("GOOGLE_OAUTH_TOKEN", "").strip()
    if not api_key and not oauth_token:
        print("GEMINI_API_KEY or GOOGLE_OAUTH_TOKEN is required", file=sys.stderr)
        return 1

    personas = json.loads(Path("personas.json").read_text(encoding="utf-8"))
    p_info = next((p for p in personas if p["id"] == persona), {})
    brief_text = brief_path.read_text(encoding="utf-8") if brief_path.exists() else ""

    system_prompt = (
        f"{brief_text}\n\n"
        f"Active Persona ({persona}):\n{json.dumps(p_info, indent=2)}\n\n"
        f"Platform: {platform}, App ID: {app_id}.\n"
        "Use the provided tools. Start by calling see_screen('01'). "
        "After every action, call see_screen with the next step number. "
        "When done (max ~30 actions), call finish_report."
    )

    contents: list[dict[str, Any]] = [
        {"role": "user", "parts": [{"text": f"Begin testing as persona '{persona}' on {platform}. Call see_screen('01') first."}]}
    ]

    t0 = time.time()
    in_tok = out_tok = turns = 0
    max_turns = int(os.environ.get("MAX_TURNS", "45"))
    finished = False
    is_error = False
    err_msg = ""

    for _ in range(max_turns):
        turns += 1
        try:
            resp = call_gemini(model, api_key, oauth_token, contents, system_prompt)
        except urllib.error.HTTPError as e:
            is_error = True
            err_msg = f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}"
            break
        except Exception as e:
            is_error = True
            err_msg = str(e)
            break

        usage = resp.get("usageMetadata") or {}
        in_tok += int(usage.get("promptTokenCount") or 0)
        out_tok += int(usage.get("candidatesTokenCount") or 0)

        cand = (resp.get("candidates") or [{}])[0]
        model_content = cand.get("content") or {"role": "model", "parts": []}
        contents.append(model_content)

        parts = model_content.get("parts") or []
        fn_calls = [p["functionCall"] for p in parts if "functionCall" in p]
        if not fn_calls:
            break

        fn_responses: list[dict[str, Any]] = []
        for fc in fn_calls:
            fname = fc["name"]
            fargs = fc.get("args") or {}
            if fname == "finish_report":
                out_dir = Path("results") / persona
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / "report.md").write_text(str(fargs.get("report_md", "")), encoding="utf-8")
                findings = {
                    "score": fargs.get("score"),
                    "goal_reached": fargs.get("goal_reached", ""),
                    "actions": turns,
                    "issues": fargs.get("issues", []),
                }
                (out_dir / "findings.json").write_text(json.dumps(findings, indent=2), encoding="utf-8")
                fn_responses.append({"functionResponse": {"name": fname, "response": {"status": "saved"}}})
                finished = True
            else:
                result_txt, img_path = _exec_tool(fname, fargs, platform, persona, app_id)
                fn_responses.append({"functionResponse": {"name": fname, "response": {"output": result_txt[:6000]}}})
                if img_path and img_path.exists():
                    b64 = base64.b64encode(img_path.read_bytes()).decode("ascii")
                    fn_responses.append({"inlineData": {"mimeType": "image/png", "data": b64}})

        contents.append({"role": "user", "parts": fn_responses})
        if finished:
            break

    in_rate, out_rate = MODEL_PRICING_PER_M.get(model, (0.15, 0.60))
    cost_usd = (in_tok * in_rate + out_tok * out_rate) / 1_000_000.0
    telemetry = {
        "total_cost_usd": round(cost_usd, 6),
        "duration_ms": int((time.time() - t0) * 1000),
        "num_turns": turns,
        "is_error": is_error,
        "result": err_msg,
        "usage": {"input_tokens": in_tok, "output_tokens": out_tok, "cache_read_input_tokens": 0},
        "modelUsage": {model: {"costUSD": round(cost_usd, 6)}},
    }
    Path("results").mkdir(parents=True, exist_ok=True)
    Path("results/agent-output.json").write_text(json.dumps(telemetry, indent=2), encoding="utf-8")
    return 1 if is_error else 0


if __name__ == "__main__":
    raise SystemExit(run_session())
