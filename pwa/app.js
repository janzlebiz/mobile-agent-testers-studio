/* Mobile Agent Testers Studio PWA */
const STORAGE = {
  clientId: "mat_google_client_id",
  geminiKey: "mat_gemini_api_key",
  oauthToken: "mat_google_oauth_token",
  ghRepo: "mat_gh_repo",
  ghPat: "mat_gh_pat",
  privPem: "mat_rsa_private_pem",
};

let deferredInstallPrompt = null;
let personasData = [];
let selectedPersonas = new Set(["anna", "malik", "clara", "jonas"]);

const $ = (id) => document.getElementById(id);

function setStatus(id, msg, kind = "") {
  const el = $(id);
  el.textContent = msg;
  el.className = "status" + (kind ? " " + kind : "");
  el.classList.remove("hidden");
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

// ---------- Service Worker & Install Prompt ----------
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("./sw.js").catch(() => {});
}
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  deferredInstallPrompt = e;
  $("install-btn").classList.remove("hidden");
});
$("install-btn").onclick = async () => {
  if (!deferredInstallPrompt) return;
  deferredInstallPrompt.prompt();
  await deferredInstallPrompt.userChoice;
  deferredInstallPrompt = null;
  $("install-btn").classList.add("hidden");
};

// ---------- Tabs ----------
document.querySelectorAll(".tab").forEach((btn) => {
  btn.onclick = () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    ["setup", "launch", "runs", "results"].forEach((t) => {
      $("tab-" + t).classList.toggle("hidden", btn.dataset.tab !== t);
    });
    if (btn.dataset.tab === "runs") refreshRuns();
  };
});

// ---------- Restore Saved Inputs ----------
window.addEventListener("DOMContentLoaded", async () => {
  $("google-client-id").value = localStorage.getItem(STORAGE.clientId) || "";
  $("gemini-key").value = localStorage.getItem(STORAGE.geminiKey) || "";
  $("gh-repo").value = localStorage.getItem(STORAGE.ghRepo) || guessRepoFromLocation();
  $("gh-pat").value = localStorage.getItem(STORAGE.ghPat) || "";
  await loadPersonas();
});

function guessRepoFromLocation() {
  const host = location.hostname;
  if (host.endsWith(".github.io")) {
    const owner = host.split(".")[0];
    const repo = location.pathname.split("/").filter(Boolean)[0] || "";
    if (owner && repo) return `${owner}/${repo}`;
  }
  return "";
}

// ---------- Google Sign-In & Gemini API ----------
$("save-gemini-btn").onclick = () => {
  localStorage.setItem(STORAGE.clientId, $("google-client-id").value.trim());
  localStorage.setItem(STORAGE.geminiKey, $("gemini-key").value.trim());
  setStatus("gemini-status", "Gemini settings saved locally in browser storage.", "ok");
};

$("google-signin-btn").onclick = () => {
  const clientId = $("google-client-id").value.trim();
  if (!clientId) {
    setStatus("gemini-status", "Enter a Google OAuth Client ID first, or paste a Gemini API Key directly.", "err");
    return;
  }
  localStorage.setItem(STORAGE.clientId, clientId);
  if (!window.google?.accounts?.oauth2) {
    setStatus("gemini-status", "Google Identity Services script still loading or blocked offline.", "err");
    return;
  }
  const tokenClient = google.accounts.oauth2.initTokenClient({
    client_id: clientId,
    scope: "openid email profile https://www.googleapis.com/auth/generative-language.retriever",
    callback: async (resp) => {
      if (resp.error) {
        setStatus("gemini-status", `Google Sign-In failed: ${resp.error}`, "err");
        return;
      }
      localStorage.setItem(STORAGE.oauthToken, resp.access_token);
      setStatus("gemini-status", "Signed in with Google! OAuth token active.", "ok");
      try {
        const u = await fetch("https://www.googleapis.com/oauth2/v3/userinfo", {
          headers: { Authorization: `Bearer ${resp.access_token}` },
        }).then((r) => r.json());
        const pill = $("google-user");
        pill.innerHTML = `${u.picture ? `<img src="${esc(u.picture)}" alt="" />` : ""}<span>${esc(u.email || u.name || "Google User")}</span>`;
        pill.classList.remove("hidden");
      } catch {}
    },
  });
  tokenClient.requestAccessToken({ prompt: "consent" });
};

$("test-gemini-btn").onclick = async () => {
  const key = $("gemini-key").value.trim() || localStorage.getItem(STORAGE.geminiKey) || "";
  const token = localStorage.getItem(STORAGE.oauthToken) || "";
  if (!key && !token) {
    setStatus("gemini-status", "Provide a Gemini API Key or sign in with Google first.", "err");
    return;
  }
  setStatus("gemini-status", "Testing Gemini API connection...");
  try {
    const url = key
      ? `https://generativelanguage.googleapis.com/v1beta/models?key=${encodeURIComponent(key)}`
      : "https://generativelanguage.googleapis.com/v1beta/models";
    const headers = key ? {} : { Authorization: `Bearer ${token}` };
    const r = await fetch(url, { headers });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error?.message || `HTTP ${r.status}`);
    const count = (data.models || []).length;
    setStatus("gemini-status", `Connected to Gemini API (${count} models available).`, "ok");
  } catch (err) {
    setStatus("gemini-status", `Gemini connection failed: ${err.message}`, "err");
  }
};

// ---------- GitHub Connection & Zero-Bash Setup ----------
function ghHeaders() {
  const pat = $("gh-pat").value.trim() || localStorage.getItem(STORAGE.ghPat) || "";
  return {
    Authorization: `Bearer ${pat}`,
    Accept: "application/vnd.github+json",
    "Content-Type": "application/json",
  };
}

function getRepo() {
  return ($("gh-repo").value.trim() || localStorage.getItem(STORAGE.ghRepo) || "").replace(/^https:\/\/github\.com\//, "");
}

$("save-gh-btn").onclick = async () => {
  const repo = getRepo();
  const pat = $("gh-pat").value.trim();
  if (!repo || !pat) {
    setStatus("gh-status", "Enter both GitHub repository (owner/repo) and Personal Access Token.", "err");
    return;
  }
  localStorage.setItem(STORAGE.ghRepo, repo);
  localStorage.setItem(STORAGE.ghPat, pat);
  try {
    const r = await fetch(`https://api.github.com/repos/${repo}`, { headers: ghHeaders() });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const info = await r.json();
    setStatus("gh-status", `Connected to ${info.full_name} (default branch: ${info.default_branch}).`, "ok");
  } catch (err) {
    setStatus("gh-status", `GitHub check failed: ${err.message}`, "err");
  }
};

function toPem(buf, label) {
  const b64 = btoa(String.fromCharCode(...new Uint8Array(buf)));
  const lines = b64.match(/.{1,64}/g).join("\n");
  return `-----BEGIN ${label}-----\n${lines}\n-----END ${label}-----\n`;
}

$("gen-keys-btn").onclick = async () => {
  const repo = getRepo();
  if (!repo || !($("gh-pat").value.trim() || localStorage.getItem(STORAGE.ghPat))) {
    setStatus("gh-status", "Save your GitHub repo and PAT first.", "err");
    return;
  }
  setStatus("gh-status", "Generating RSA-OAEP 2048-bit keypair in browser...");
  try {
    const kp = await crypto.subtle.generateKey(
      { name: "RSA-OAEP", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
      true,
      ["encrypt", "decrypt"]
    );
    const pubDer = await crypto.subtle.exportKey("spki", kp.publicKey);
    const privDer = await crypto.subtle.exportKey("pkcs8", kp.privateKey);
    const pubPem = toPem(pubDer, "PUBLIC KEY");
    const privPem = toPem(privDer, "PRIVATE KEY");
    localStorage.setItem(STORAGE.privPem, privPem);

    let sha = undefined;
    const existing = await fetch(`https://api.github.com/repos/${repo}/contents/results_public.pem`, { headers: ghHeaders() });
    if (existing.ok) sha = (await existing.json()).sha;

    const put = await fetch(`https://api.github.com/repos/${repo}/contents/results_public.pem`, {
      method: "PUT",
      headers: ghHeaders(),
      body: JSON.stringify({
        message: "chore: update results_public.pem from MAT Studio PWA",
        content: btoa(pubPem),
        ...(sha ? { sha } : {}),
      }),
    });
    if (!put.ok) throw new Error(`Push failed: HTTP ${put.status}`);

    const blob = new Blob([privPem], { type: "application/x-pem-file" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${repo.replace("/", "_")}_private.pem`;
    a.click();
    setStatus("gh-status", "RSA keypair generated! Public key pushed to repo & private key downloaded.", "ok");
  } catch (err) {
    setStatus("gh-status", `Key generation/push error: ${err.message}`, "err");
  }
};

$("push-secrets-btn").onclick = async () => {
  const repo = getRepo();
  const geminiKey = $("gemini-key").value.trim() || localStorage.getItem(STORAGE.geminiKey) || "";
  if (!repo || !geminiKey) {
    setStatus("gh-status", "Provide both GitHub repo/PAT and Gemini API Key first.", "err");
    return;
  }
  if (!window.sodium) {
    setStatus("gh-status", "libsodium not loaded yet. Wait a second or add GEMINI_API_KEY in GitHub Repo Settings -> Secrets.", "err");
    return;
  }
  setStatus("gh-status", "Encrypting and uploading GEMINI_API_KEY & ARTIFACT_PASSPHRASE...");
  try {
    await window.sodium.ready;
    const pkResp = await fetch(`https://api.github.com/repos/${repo}/actions/secrets/public-key`, { headers: ghHeaders() });
    if (!pkResp.ok) throw new Error(`Fetch repo public key failed: HTTP ${pkResp.status}`);
    const { key_id, key } = await pkResp.json();
    const binkey = window.sodium.from_base64(key, window.sodium.base64_variants.ORIGINAL);

    const passphrase = Array.from(crypto.getRandomValues(new Uint8Array(24)))
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");

    for (const [name, val] of [["GEMINI_API_KEY", geminiKey], ["ARTIFACT_PASSPHRASE", passphrase]]) {
      const binsec = window.sodium.from_string(val);
      const enc = window.sodium.crypto_box_seal(binsec, binkey);
      const encrypted_value = window.sodium.to_base64(enc, window.sodium.base64_variants.ORIGINAL);
      const put = await fetch(`https://api.github.com/repos/${repo}/actions/secrets/${name}`, {
        method: "PUT",
        headers: ghHeaders(),
        body: JSON.stringify({ encrypted_value, key_id }),
      });
      if (!put.ok) throw new Error(`Secret ${name} failed: HTTP ${put.status}`);
    }
    setStatus("gh-status", "GEMINI_API_KEY and ARTIFACT_PASSPHRASE synced to GitHub Actions Secrets!", "ok");
  } catch (err) {
    setStatus("gh-status", `Secret sync error: ${err.message}`, "err");
  }
};

// ---------- Personas & Launch ----------
async function loadPersonas() {
  try {
    const r = await fetch("../personas.json");
    if (r.ok) personasData = await r.json();
  } catch {}
  if (!personasData.length) {
    personasData = [
      { id: "anna", platform: "android", locale: "en-GB", profile: "Retired teacher, 68, large text" },
      { id: "malik", platform: "android", locale: "en-US", profile: "Student, 21, dark mode, fast" },
      { id: "clara", platform: "ios", locale: "fr-FR", profile: "Designer, 34, iPhone SE" },
      { id: "jonas", platform: "ios", locale: "de-DE", profile: "Engineer, 45, iPad large text" },
    ];
  }
  renderPersonaGrid();
}

function renderPersonaGrid() {
  $("persona-grid").innerHTML = personasData
    .map(
      (p) => `
    <div class="persona-chip ${selectedPersonas.has(p.id) ? "selected" : ""}" data-id="${esc(p.id)}">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <strong>${esc(p.id)}</strong>
        <span class="badge">${esc(p.platform)} · ${esc(p.locale)}</span>
      </div>
      <div style="color:var(--muted);margin-top:4px;">${esc((p.profile || "").slice(0, 70))}</div>
    </div>`
    )
    .join("");
  document.querySelectorAll(".persona-chip").forEach((el) => {
    el.onclick = () => {
      const id = el.dataset.id;
      if (selectedPersonas.has(id)) selectedPersonas.delete(id);
      else selectedPersonas.add(id);
      el.classList.toggle("selected");
    };
  });
}

$("pick-fast").onclick = () => {
  selectedPersonas = new Set(["anna", "malik", "clara", "jonas"]);
  renderPersonaGrid();
};
$("pick-all").onclick = () => {
  selectedPersonas = new Set(personasData.map((p) => p.id));
  renderPersonaGrid();
};
$("pick-none").onclick = () => {
  selectedPersonas.clear();
  renderPersonaGrid();
};

$("launch-run-btn").onclick = async () => {
  const repo = getRepo();
  if (!repo || !localStorage.getItem(STORAGE.ghPat)) {
    setStatus("launch-status", "Connect GitHub in Tab 1 first.", "err");
    return;
  }
  const androidList = personasData.filter((p) => p.platform === "android" && selectedPersonas.has(p.id)).map((p) => p.id);
  const iosList = personasData.filter((p) => p.platform === "ios" && selectedPersonas.has(p.id)).map((p) => p.id);
  if (!androidList.length && !iosList.length) {
    setStatus("launch-status", "Select at least one persona.", "err");
    return;
  }
  setStatus("launch-status", "Dispatching GitHub Actions workflow...");
  try {
    const r = await fetch(`https://api.github.com/repos/${repo}/actions/workflows/agent-testers.yml/dispatches`, {
      method: "POST",
      headers: ghHeaders(),
      body: JSON.stringify({
        ref: "main",
        inputs: {
          android_personas: androidList.join(",") || "none",
          ios_personas: iosList.join(",") || "none",
          source_repo: $("run-source-repo").value.trim(),
          app_dir: $("run-app-dir").value.trim() || "demo-app",
          app_id: $("run-app-id").value.trim() || "com.example.tinytodo",
          model: $("run-model").value,
          brief: $("run-brief").value,
          bug_report: $("run-bug").value.trim(),
        },
      }),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    setStatus("launch-status", "Test run dispatched! Switch to '3. Live Runs' to monitor progress.", "ok");
  } catch (err) {
    setStatus("launch-status", `Dispatch failed: ${err.message}`, "err");
  }
};

// ---------- Live Runs ----------
async function refreshRuns() {
  const repo = getRepo();
  if (!repo || !localStorage.getItem(STORAGE.ghPat)) return;
  try {
    const r = await fetch(`https://api.github.com/repos/${repo}/actions/workflows/agent-testers.yml/runs?per_page=10`, {
      headers: ghHeaders(),
    });
    if (!r.ok) return;
    const data = await r.json();
    const runs = data.workflow_runs || [];
    $("runs-tbody").innerHTML = runs.length
      ? runs
          .map(
            (run) => `<tr>
          <td>#${run.id}</td>
          <td>${esc(run.status)}</td>
          <td>${esc(run.conclusion || "—")}</td>
          <td>${new Date(run.created_at).toLocaleString()}</td>
          <td><a href="${esc(run.html_url)}" target="_blank" rel="noreferrer" style="color:var(--accent);">Open on GitHub</a></td>
        </tr>`
          )
          .join("")
      : `<tr><td colspan="5" style="color:var(--muted);">No runs yet.</td></tr>`;
  } catch {}
}
$("refresh-runs-btn").onclick = refreshRuns;

// ---------- Results Viewer ----------
$("findings-file").onchange = async (e) => {
  const file = e.target.files?.[0];
  if (!file) return;
  try {
    const data = JSON.parse(await file.text());
    const issues = data.issues || [];
    $("results-preview").innerHTML = `
      <div style="margin-bottom:10px;"><strong>Score:</strong> ${esc(data.score ?? "—")}/10 · <strong>Issues:</strong> ${issues.length}</div>
      ${issues
        .map(
          (i) => `<div class="card" style="padding:12px;margin-bottom:8px;">
          <span class="badge">${esc(i.severity || "minor")}</span>
          <strong style="margin-left:8px;">${esc(i.title)}</strong>
          <div style="font-size:12px;color:var(--muted);margin-top:4px;">Screen: ${esc(i.screen || "—")}</div>
        </div>`
        )
        .join("")}
    `;
  } catch (err) {
    $("results-preview").innerHTML = `<div class="status err">Invalid JSON: ${esc(err.message)}</div>`;
  }
};
