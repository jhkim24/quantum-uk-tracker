// UK Quantum Internships — site logic
// Fetches data/programmes.json + data/status.json + data/discoveries.json
// and renders programme cards + discovery list + filters.

const PROGRAMMES_URL = "data/programmes.json";
const STATUS_URL = "data/status.json";
const DISCOVERIES_URL = "data/discoveries.json";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

// --- Helpers ---------------------------------------------------------------

const escapeHTML = (str) =>
  String(str ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );

const fmtDate = (iso) => {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString("en-GB", {
      day: "numeric", month: "short", year: "numeric",
      hour: "2-digit", minute: "2-digit", timeZone: "UTC",
    }) + " UTC";
  } catch {
    return iso;
  }
};

const fmtRelativeDate = (iso) => {
  if (!iso) return "never";
  const d = new Date(iso);
  const now = new Date();
  const diffMs = now - d;
  const days = Math.floor(diffMs / (1000 * 60 * 60 * 24));
  if (days === 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 7) return `${days} days ago`;
  if (days < 30) return `${Math.floor(days / 7)} weeks ago`;
  return `${Math.floor(days / 30)} months ago`;
};

async function loadJSON(url) {
  try {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return await res.json();
  } catch (err) {
    console.error(`Failed to load ${url}:`, err);
    return null;
  }
}

// --- Rendering -------------------------------------------------------------

function renderEyebrow(statusData) {
  const el = $("#last-updated");
  if (!statusData?.generated_at) {
    el.textContent = "no data yet";
    return;
  }
  el.textContent = `Last scraped ${fmtRelativeDate(statusData.generated_at)} (${fmtDate(statusData.generated_at)})`;
}

function renderStats(statusData) {
  if (!statusData?.results) return;
  const counts = { open: 0, closed: 0, unknown: 0, error: 0, changed: 0 };
  for (const r of statusData.results) {
    counts[r.status] = (counts[r.status] || 0) + 1;
    if (r.changed) counts.changed += 1;
  }
  $("#stat-open").textContent = counts.open;
  $("#stat-closed").textContent = counts.closed;
  $("#stat-unknown").textContent = counts.unknown + counts.error;
  $("#stat-changed").textContent = counts.changed;
}

function renderProgrammes(programmes, statusData) {
  const grid = $("#programme-grid");
  grid.innerHTML = "";

  const statusById = {};
  if (statusData?.results) {
    for (const r of statusData.results) statusById[r.id] = r;
  }

  if (!programmes || programmes.length === 0) {
    grid.innerHTML = '<div class="empty">No programmes in registry</div>';
    return;
  }

  $("#programme-count").textContent = `${programmes.length} programmes tracked`;

  for (const p of programmes) {
    const status = statusById[p.id] || {};
    const card = document.createElement("div");
    const oxford = p.tags?.includes("oxford") ? " oxford" : "";
    const changed = status.changed ? " changed" : "";
    card.className = `card${changed}`;
    card.dataset.cat = `${p.category}${oxford}${status.status === "open" ? " open" : ""}${status.changed ? " changed" : ""}`;

    const tags = (p.tags || []).map(t => `<span class="tag">${escapeHTML(t)}</span>`).join("");

    let statusLabel = "Unknown";
    let statusClass = "unknown";
    if (status.status === "open") { statusLabel = "Open"; statusClass = "open"; }
    else if (status.status === "closed") { statusLabel = "Closed"; statusClass = "closed"; }
    else if (status.status === "error") { statusLabel = "Fetch Err"; statusClass = "error"; }

    const liveDeadline = status.deadline_text
      ? `<li><span class="key">Live deadline</span><span class="val warn">${escapeHTML(status.deadline_text)}</span></li>`
      : "";
    const lastChecked = status.fetched_at
      ? `<li><span class="key">Last checked</span><span class="val">${fmtRelativeDate(status.fetched_at)}</span></li>`
      : "";
    const snippet = status.snippet
      ? `<div class="snippet">${escapeHTML(status.snippet)}</div>`
      : "";

    card.innerHTML = `
      <div class="card-top">
        <div>
          <div class="org">${escapeHTML(p.org)}</div>
          <h3 class="title">${escapeHTML(p.name)}</h3>
        </div>
        <span class="tier ${statusClass}">${statusLabel}</span>
      </div>
      <div class="tag-row">${tags}</div>
      <p class="notes">${escapeHTML(p.notes || "")}</p>
      <ul class="meta-list">
        <li><span class="key">Eligibility</span><span class="val">${escapeHTML(p.eligibility || "—")}</span></li>
        <li><span class="key">Pay</span><span class="val">${escapeHTML(p.pay || "—")}</span></li>
        <li><span class="key">Duration</span><span class="val">${escapeHTML(p.duration || "—")}</span></li>
        <li><span class="key">Typical deadline</span><span class="val">${escapeHTML(p.typical_deadline || "—")}</span></li>
        ${liveDeadline}
        <li><span class="key">Apply via</span><span class="val">${escapeHTML(p.apply_route || "—")}</span></li>
        <li><span class="key">Source</span><span class="val"><a href="${escapeHTML(p.url)}" target="_blank" rel="noopener">${escapeHTML(p.url)}</a></span></li>
        ${lastChecked}
      </ul>
      ${snippet}
    `;
    grid.appendChild(card);
  }
}

function renderDiscoveries(discData) {
  const list = $("#discovery-list");
  list.innerHTML = "";

  if (!discData?.items || discData.items.length === 0) {
    list.innerHTML = '<div class="empty">No discoveries yet — first run pending</div>';
    $("#discovery-count").textContent = "0 candidates";
    return;
  }

  $("#discovery-count").textContent = `${discData.items.length} candidates`;

  for (const item of discData.items) {
    const div = document.createElement("div");
    div.className = "discovery";
    div.innerHTML = `
      <div>
        <div class="domain">${escapeHTML(item.domain || "")}</div>
        <div class="d-title">${escapeHTML(item.title || "")}</div>
        <div class="d-snippet">${escapeHTML(item.snippet || "")}</div>
      </div>
      <div class="d-meta">
        seen ${item.seen_count || 1}×<br>
        <a href="${escapeHTML(item.url)}" target="_blank" rel="noopener">Open →</a>
      </div>
    `;
    list.appendChild(div);
  }
}

function setupFilters() {
  $$(".chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      $$(".chip").forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      const f = chip.dataset.filter;
      $$(".card").forEach((card) => {
        const cats = (card.dataset.cat || "").split(/\s+/);
        if (f === "all" || cats.includes(f)) card.classList.remove("hidden");
        else card.classList.add("hidden");
      });
    });
  });
}

function setRepoLink() {
  // If hosted on github.io, infer the repo URL from the hostname
  try {
    const host = location.hostname;
    if (host.endsWith(".github.io")) {
      const user = host.replace(".github.io", "");
      const repo = location.pathname.split("/").filter(Boolean)[0] || `${user}.github.io`;
      $("#repo-link").href = `https://github.com/${user}/${repo}`;
    }
  } catch (e) { /* ignore */ }
}

// --- Boot ------------------------------------------------------------------

(async function () {
  setRepoLink();
  const [programmes, status, discoveries] = await Promise.all([
    loadJSON(PROGRAMMES_URL),
    loadJSON(STATUS_URL),
    loadJSON(DISCOVERIES_URL),
  ]);

  renderEyebrow(status);
  renderStats(status);
  renderProgrammes(programmes, status);
  renderDiscoveries(discoveries);
  setupFilters();
})();
