/* Mobile Agent Testers Studio PWA */
const STORAGE = {
  email: "mat_google_email",
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
  $("google-email").value = localStorage.getItem(STORAGE.email) || "";
  $("google-client-id").value = localStorage.getItem(STORAGE.clientId) || window.MAT_CONFIG?.googleClientId || "";
  const savedKey = localStorage.getItem(STORAGE.geminiKey) || "";
  $("gemini-key").value = savedKey;
  if (savedKey) showKeyBadge(savedKey, "saved");
  $("gh-repo").value = localStorage.getItem(STORAGE.ghRepo) || guessRepoFromLocation();
  $("gh-pat").value = localStorage.getItem(STORAGE.ghPat) || "";
  await loadPersonas();
});

function getClientId() {
  return (
    (window.MAT_CONFIG?.googleClientId || "").trim() ||
    $("google-client-id").value.trim() ||
    localStorage.getItem(STORAGE.clientId) ||
    ""
  );
}

function showKeyBadge(key, source) {
  const masked = key.length > 10 ? `${key.slice(0, 6)}...${key.slice(-4)}` : "***";
  const badge = $("gemini-key-badge");
  badge.textContent = `Gemini API Key ready (${source}): ${masked}`;
  badge.classList.remove("hidden");
}

function guessRepoFromLocation() {
  const host = location.hostname;
  if (host.endsWith(".github.io")) {
    const owner = host.split(".")[0];
    const repo = location.pathname.split("/").filter(Boolean)[0] || "";
    if (owner && repo) return `${owner}/${repo}`;
  }
  return "";
}

// ---------- Google Sign-In & Auto Gemini API Key ----------
$("save-gemini-btn").onclick = () => {
  const k = $("gemini-key").value.trim();
  localStorage.setItem(STORAGE.clientId, $("google-client-id").value.trim());
  localStorage.setItem(STORAGE.geminiKey, k);
  if (k) showKeyBadge(k, "manual");
  setStatus("gemini-status", "Manual Gemini key saved locally in browser storage.", "ok");
};

async function fetchOrCreateGeminiKey(projectId, token) {
  setStatus("gemini-status", `Looking up Gemini API key in GCP project '${projectId}'...`);
  const headers = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
  const base = `https://apikeys.googleapis.com/v2/projects/${encodeURIComponent(projectId)}/locations/global/keys`;

  const listResp = await fetch(base, { headers });
  if (!listResp.ok) {
    const err = await listResp.json().catch(() => ({}));
    throw new Error(err.error?.message || `API Keys API HTTP ${listResp.status} (enable apikeys.googleapis.com on project ${projectId})`);
  }
  const listData = await listResp.json();
  const keys = listData.keys || [];
  let targetKey = keys.find((k) => k.name.endsWith("/mat-studio-gemini-key")) || keys[0];

  if (!targetKey) {
    setStatus("gemini-status", `Creating Gemini API key in project '${projectId}'...`);
    const createResp = await fetch(`${base}?keyId=mat-studio-gemini-key`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        displayName: "MAT Studio Gemini Key",
        restrictions: { apiTargets: [{ service: "generativelanguage.googleapis.com" }] },
      }),
    });
    if (!createResp.ok) {
      const err = await createResp.json().catch(() => ({}));
      throw new Error(err.error?.message || `Create key failed: HTTP ${createResp.status}`);
    }
    const op = await createResp.json();
    for (let i = 0; i < 15; i++) {
      if (op.done && op.response) {
        targetKey = op.response;
        break;
      }
      await new Promise((r) => setTimeout(r, 1200));
      const poll = await fetch(`https://apikeys.googleapis.com/v2/${op.name}`, { headers }).then((r) => r.json());
      if (poll.done) {
        if (poll.error) throw new Error(poll.error.message || "Key creation operation failed");
        targetKey = poll.response;
        break;
      }
    }
  }

  if (!targetKey?.name) throw new Error("Could not resolve API key resource name.");
  const strResp = await fetch(`https://apikeys.googleapis.com/v2/${targetKey.name}/keyString`, { headers });
  if (!strResp.ok) throw new Error(`Failed to read keyString: HTTP ${strResp.status}`);
  const { keyString } = await strResp.json();
  if (!keyString) throw new Error("Empty keyString returned.");

  localStorage.setItem(STORAGE.geminiKey, keyString);
  $("gemini-key").value = keyString;
  showKeyBadge(keyString, `auto from ${projectId}`);
  setStatus("gemini-status", `Gemini API key automatically retrieved from project '${projectId}'!`, "ok");
}

async function loadGcpProjectsAndKey(token) {
  setStatus("gemini-status", "Signed in! Fetching your Google Cloud projects...");
  const projResp = await fetch(
    "https://cloudresourcemanager.googleapis.com/v1/projects?filter=lifecycleState:ACTIVE",
    { headers: { Authorization: `Bearer ${token}` } }
  );
  if (!projResp.ok) {
    throw new Error(`Cloud Resource Manager HTTP ${projResp.status}. Use manual fallback or enable Resource Manager API.`);
  }
  const { projects = [] } = await projResp.json();
  if (!projects.length) {
    throw new Error("No active Google Cloud projects found on this account.");
  }
  const sel = $("gcp-project");
  sel.innerHTML = projects.map((p) => `<option value="${esc(p.projectId)}">${esc(p.name || p.projectId)} (${esc(p.projectId)})</option>`).join("");
  $("gcp-project-wrap").classList.remove("hidden");
  $("fetch-key-btn").classList.remove("hidden");
  await fetchOrCreateGeminiKey(projects[0].projectId, token);
}

$("fetch-key-btn").onclick = async () => {
  const token = localStorage.getItem(STORAGE.oauthToken) || "";
  const pid = $("gcp-project").value;
  if (!token || !pid) return;
  try {
    await fetchOrCreateGeminiKey(pid, token);
  } catch (err) {
    setStatus("gemini-status", err.message, "err");
  }
};

$("google-signin-btn").onclick = () => {
  const email = $("google-email").value.trim();
  if (email) localStorage.setItem(STORAGE.email, email);
  const clientId = getClientId();
  if (!clientId) {
    setStatus(
      "gemini-status",
      "Deployment OAuth Client ID not configured yet. Repo owner: set GOOGLE_CLIENT_ID in GitHub Repo Variables (or open Advanced Config below).",
      "err"
    );
    return;
  }
  if (!window.google?.accounts?.oauth2) {
    setStatus("gemini-status", "Google Identity Services script still loading or blocked offline.", "err");
    return;
  }
  const tokenClient = google.accounts.oauth2.initTokenClient({
    client_id: clientId,
    ...(email ? { hint: email } : {}),
    scope: [
      "openid",
      "email",
      "profile",
      "https://www.googleapis.com/auth/cloud-platform",
      "https://www.googleapis.com/auth/generative-language.retriever",
    ].join(" "),
    callback: async (resp) => {
      if (resp.error) {
        setStatus("gemini-status", `Google Sign-In failed: ${resp.error}`, "err");
        return;
      }
      localStorage.setItem(STORAGE.oauthToken, resp.access_token);
      try {
        const u = await fetch("https://www.googleapis.com/oauth2/v3/userinfo", {
          headers: { Authorization: `Bearer ${resp.access_token}` },
        }).then((r) => r.json());
        if (u.email) {
          $("google-email").value = u.email;
          localStorage.setItem(STORAGE.email, u.email);
        }
        const pill = $("google-user");
        pill.innerHTML = `${u.picture ? `<img src="${esc(u.picture)}" alt="" />` : ""}<span>${esc(u.email || u.name || "Google User")}</span>`;
        pill.classList.remove("hidden");
      } catch {}
      try {
        await loadGcpProjectsAndKey(resp.access_token);
      } catch (err) {
        setStatus("gemini-status", `Signed in, but auto-key fetch failed: ${err.message}`, "err");
      }
    },
  });
  tokenClient.requestAccessToken({ prompt: "consent" });
};

$("test-gemini-btn").onclick = async () => {
  const key = $("gemini-key").value.trim() || localStorage.getItem(STORAGE.geminiKey) || "";
  const token = localStorage.getItem(STORAGE.oauthToken) || "";
  if (!key && !token) {
    setStatus("gemini-status", "Sign in with Google first.", "err");
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
