const state = {
  activeTab: "radar",
  pollTimer: null,
  targets: [],
  filter: "all",
  sortBy: "opportunity",
  totalPrograms: 0,
  activeScans: 0
};

function refreshIcons() {
  if (window.lucide && typeof window.lucide.createIcons === "function") {
    window.lucide.createIcons();
  }
}

function switchTab(tabName) {
  state.activeTab = tabName;
  document.querySelectorAll(".tab-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === tabName);
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.add("hidden");
  });
  const panel = document.getElementById(`tab-${tabName}`);
  if (panel) {
    panel.classList.remove("hidden");
  }
  if (tabName === "jobs") {
    loadJobs();
  }
}

function escapeHtml(value) {
  const text = value === null || value === undefined ? "" : String(value);
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function setStat(id, value) {
  const el = document.getElementById(id);
  if (el) {
    el.textContent = String(value);
  }
}

function tierBadge(tier) {
  const known = ["S", "A", "B", "C"].includes(tier) ? tier : "C";
  return `<span class="tier-badge tier-${known}"><i data-lucide="award" class="w-3.5 h-3.5"></i>${known}-Tier</span>`;
}

function formatHours(value) {
  if (!value || value <= 0) {
    return "N/A";
  }
  if (value < 1) {
    return `${Math.round(value * 60)}m`;
  }
  return `${Math.round(value)}h`;
}

async function parseApiResponse(response) {
  const contentType = response.headers.get("content-type") || "";
  const statusLine = `HTTP ${response.status}: ${response.statusText}`;
  if (contentType.includes("application/json")) {
    let data = null;
    try {
      data = await response.json();
    } catch (parseError) {
      return { ok: false, data: null, error: `${statusLine} (invalid JSON body)` };
    }
    if (!response.ok) {
      const message = data && data.error ? data.error : statusLine;
      return { ok: false, data, error: message };
    }
    return { ok: true, data, error: null };
  }
  let text = "";
  try {
    text = await response.text();
  } catch (readError) {
    text = "";
  }
  const readable = extractReadableText(text);
  const message = readable ? `${statusLine} - ${readable}` : statusLine;
  return { ok: false, data: null, error: response.ok ? `Unexpected response format. ${message}` : message };
}

function extractReadableText(rawText) {
  if (!rawText) {
    return "";
  }
  const parsed = new DOMParser().parseFromString(rawText, "text/html");
  const heading = parsed.querySelector("h1, title");
  const source = heading && heading.textContent.trim() ? heading.textContent : parsed.body ? parsed.body.textContent : rawText;
  const collapsed = (source || "").replace(/\s+/g, " ").trim();
  return collapsed.length > 200 ? `${collapsed.slice(0, 200)}...` : collapsed;
}

async function loadTargets(forceRefresh) {
  const statusEl = document.getElementById("radar-status");
  const resultsEl = document.getElementById("radar-results");
  const search = document.getElementById("radar-search").value.trim();
  state.sortBy = document.getElementById("radar-sort").value || "opportunity";
  statusEl.textContent = forceRefresh ? "Refreshing feed from source..." : "Loading active bounty programs...";
  resultsEl.innerHTML = "";
  const params = new URLSearchParams({ search, sort_by: state.sortBy });
  if (forceRefresh) {
    params.set("refresh", "true");
  }
  try {
    const response = await fetch(`/api/targets?${params.toString()}`);
    const { ok, data, error } = await parseApiResponse(response);
    if (!ok) {
      statusEl.textContent = `Failed to load targets: ${error}`;
      return;
    }
    state.targets = data.targets || [];
    state.totalPrograms = data.count || state.targets.length;
    setStat("stat-total", state.totalPrograms);
    renderHero(data.top_recommendation);
    applyFilterAndRender();
  } catch (error) {
    statusEl.textContent = `Network error while loading targets: ${error.message}`;
  }
}

function matchesFilter(target) {
  if (state.filter === "s-tier") {
    return target.tier === "S";
  }
  if (state.filter === "fast") {
    return Number(target.response_efficiency || 0) > 85;
  }
  if (state.filter === "wildcard") {
    return Boolean(target.has_wildcard);
  }
  return true;
}

function applyFilterAndRender() {
  const statusEl = document.getElementById("radar-status");
  const visible = state.targets.filter(matchesFilter);
  renderTargets(visible);
  statusEl.textContent = `${visible.length} of ${state.targets.length} in-scope program(s) shown.`;
}

function renderHero(top) {
  const heroEl = document.getElementById("hero-recommendation");
  if (!top) {
    heroEl.innerHTML = "";
    return;
  }
  const cleanTop = (top.in_scope_domains || []).length
    ? String(top.in_scope_domains[0]).replace(/^\*\./, "")
    : "";
  const tier = ["S", "A", "B", "C"].includes(top.tier) ? top.tier : "C";
  const scanAttr = cleanTop ? `data-scan-url="${escapeHtml(cleanTop)}"` : "disabled";
  heroEl.innerHTML = `
    <div class="hero-card">
      <div class="flex flex-col lg:flex-row lg:items-center gap-6">
        <div class="score-ring tier-${tier}">${escapeHtml(top.opportunity_score)}</div>
        <div class="flex-1 min-w-0">
          <p class="hero-eyebrow mb-1">&#127919; Top Target Recommendation</p>
          <div class="flex items-center gap-3 flex-wrap">
            <h2 class="text-2xl font-bold text-white truncate">${escapeHtml(top.name)}</h2>
            ${tierBadge(top.tier)}
          </div>
          <a href="${escapeHtml(top.url)}" target="_blank" rel="noopener noreferrer"
            class="text-xs text-accent hover:underline font-mono">@${escapeHtml(top.handle)}</a>
          <p class="text-sm text-slate-300 mt-2">${escapeHtml(top.recommendation_reason)}</p>
          <div class="metric-grid mt-4">
            <div class="metric-box"><div class="metric-value">${escapeHtml(formatHours(top.time_to_first_response))}</div><div class="metric-label">First Response</div></div>
            <div class="metric-box"><div class="metric-value">${Math.round(Number(top.response_efficiency || 0))}%</div><div class="metric-label">Response Rate</div></div>
            <div class="metric-box"><div class="metric-value">${escapeHtml(top.researcher_count || 0)}</div><div class="metric-label">Researchers</div></div>
            <div class="metric-box"><div class="metric-value">${escapeHtml(top.domain_count || (top.in_scope_domains || []).length)}</div><div class="metric-label">Domains</div></div>
          </div>
        </div>
        <div class="flex-shrink-0">
          <button class="btn-primary px-6 py-3 rounded-xl text-sm flex items-center gap-2" ${scanAttr}>
            <i data-lucide="zap" class="w-4 h-4"></i> Quick Scan
          </button>
        </div>
      </div>
    </div>`;
  refreshIcons();
}

function renderTargets(targets) {
  const resultsEl = document.getElementById("radar-results");
  if (!targets.length) {
    resultsEl.innerHTML = `<div class="text-sm text-slate-400">No matching programs found.</div>`;
    return;
  }
  resultsEl.innerHTML = targets
    .map((target) => {
      const domains = (target.in_scope_domains || [])
        .slice(0, 40)
        .map((domain) => {
          const cleanUrl = domain.replace(/^\*\./, "");
          return `
            <div class="domain-chip">
              <span class="truncate">${escapeHtml(domain)}</span>
              <button class="scan-chip-btn" data-scan-url="${escapeHtml(cleanUrl)}">&#9889; Scan API</button>
            </div>`;
        })
        .join("");
      const managed = target.managed_program
        ? `<span class="badge managed-badge"><i data-lucide="shield-check" class="w-3 h-3"></i>Managed Triage</span>`
        : "";
      return `
        <article class="target-card">
          <div class="flex items-start justify-between gap-4 mb-3">
            <div class="min-w-0">
              <div class="flex items-center gap-2 flex-wrap mb-1">
                ${tierBadge(target.tier)}
                <h3 class="font-semibold text-slate-100 truncate">${escapeHtml(target.name)}</h3>
              </div>
              <a href="${escapeHtml(target.url)}" target="_blank" rel="noopener noreferrer"
                class="text-xs text-accent hover:underline font-mono">@${escapeHtml(target.handle)}</a>
            </div>
            <div class="text-right flex-shrink-0">
              <div class="metric-value text-accent">${escapeHtml(target.opportunity_score)}</div>
              <div class="metric-label">Score</div>
            </div>
          </div>
          <div class="metric-grid mb-3">
            <div class="metric-box"><div class="metric-value">${escapeHtml(formatHours(target.time_to_first_response))}</div><div class="metric-label">SLA / Triage</div></div>
            <div class="metric-box"><div class="metric-value">${escapeHtml(target.researcher_count || 0)}</div><div class="metric-label">Researchers</div></div>
            <div class="metric-box"><div class="metric-value">${escapeHtml(target.resolved_report_count || 0)}</div><div class="metric-label">Resolved</div></div>
            <div class="metric-box"><div class="metric-value">${Math.round(Number(target.response_efficiency || 0))}%</div><div class="metric-label">Response</div></div>
          </div>
          ${managed ? `<div class="mb-3">${managed}</div>` : ""}
          <div class="grid sm:grid-cols-2 lg:grid-cols-3 gap-2">${domains}</div>
        </article>`;
    })
    .join("");
  refreshIcons();
}

async function startScan(url) {
  const manualStatus = document.getElementById("manual-status");
  const cleanUrl = (url || "").trim().replace(/^https?:\/\/\*\./, "https://").replace(/^\*\./, "");
  if (!cleanUrl) {
    if (manualStatus) {
      manualStatus.textContent = "Please enter a valid URL.";
    }
    return;
  }
  try {
    const response = await fetch("/api/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: cleanUrl })
    });
    const { ok, error } = await parseApiResponse(response);
    if (!ok) {
      if (manualStatus) {
        manualStatus.textContent = `Failed to start scan: ${error}`;
      }
      return;
    }
    switchTab("jobs");
    if (manualStatus) {
      manualStatus.textContent = `Scan started for ${cleanUrl}`;
    }
    loadJobs();
  } catch (error) {
    if (manualStatus) {
      manualStatus.textContent = `Network error: ${error.message}`;
    }
  }
}

function statusBadge(status) {
  const known = ["running", "completed", "failed"].includes(status) ? status : "running";
  return `<span class="badge badge-status-${known}">${escapeHtml(status)}</span>`;
}

function renderJob(job) {
  const canView = job.outputs && job.outputs.json;
  const swaggerLink = canView
    ? `<a href="/swagger/${encodeURIComponent(job.job_id)}" target="_blank" rel="noopener noreferrer"
         class="text-accent hover:underline text-xs flex items-center gap-1">Swagger UI</a>`
    : "";
  const viewButton = canView
    ? `<button class="text-accent2 hover:underline text-xs" data-view-job="${escapeHtml(job.job_id)}">Open in Spec Viewer</button>`
    : "";
  const downloads = canView
    ? `<div class="flex gap-3 text-xs mt-2">
         <a class="text-slate-300 hover:text-accent" href="/download/${encodeURIComponent(job.job_id)}/json">openapi.json</a>
         <a class="text-slate-300 hover:text-accent" href="/download/${encodeURIComponent(job.job_id)}/yaml">openapi.yaml</a>
         <a class="text-slate-300 hover:text-accent" href="/download/${encodeURIComponent(job.job_id)}/catalog">api_catalog.md</a>
       </div>`
    : "";
  const errorLine = job.error
    ? `<p class="text-xs text-red-400 mt-2">${escapeHtml(job.error)}</p>`
    : "";
  const logLines = (job.log || []).slice(-200).map(escapeHtml).join("\n");
  return `
    <article class="target-card" data-job-card="${escapeHtml(job.job_id)}">
      <div class="flex items-start justify-between gap-4 mb-2">
        <div>
          <p class="font-mono text-sm text-slate-100 break-all">${escapeHtml(job.url)}</p>
          <p class="text-xs text-slate-500 mt-1">${escapeHtml(job.created_at)}</p>
          ${job.target_dir ? `<p class="text-xs text-slate-500 font-mono mt-1 break-all">${escapeHtml(job.target_dir)}</p>` : ""}
        </div>
        <div class="flex items-center gap-3">${statusBadge(job.status)}${swaggerLink}</div>
      </div>
      ${viewButton}
      ${downloads}
      ${errorLine}
      <details class="mt-3">
        <summary class="text-xs text-slate-400 cursor-pointer">Logs</summary>
        <div class="log-view mt-2">${logLines || "No output captured yet."}</div>
      </details>
    </article>`;
}

async function loadJobs() {
  const listEl = document.getElementById("jobs-list");
  try {
    const response = await fetch("/api/scan");
    const { ok, data, error } = await parseApiResponse(response);
    if (!ok) {
      listEl.innerHTML = `<div class="text-sm text-red-400">Failed to load jobs: ${escapeHtml(error)}</div>`;
      scheduleJobPolling([]);
      return;
    }
    const jobs = data.jobs || [];
    state.activeScans = jobs.filter((job) => job.status === "running").length;
    setStat("stat-scans", state.activeScans);
    if (!jobs.length) {
      listEl.innerHTML = `<div class="text-sm text-slate-400">No scans yet. Launch one from the Target Radar or the manual scan box above.</div>`;
    } else {
      listEl.innerHTML = jobs.map(renderJob).join("");
    }
    refreshIcons();
    scheduleJobPolling(jobs);
  } catch (error) {
    listEl.innerHTML = `<div class="text-sm text-red-400">Failed to load jobs: ${escapeHtml(error.message)}</div>`;
  }
}

function scheduleJobPolling(jobs) {
  const hasRunning = jobs.some((job) => job.status === "running");
  if (state.pollTimer) {
    clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }
  if (hasRunning && state.activeTab === "jobs") {
    state.pollTimer = setTimeout(loadJobs, 2500);
  }
}

function openSpecViewer(jobId) {
  switchTab("spec");
  document.getElementById("spec-empty").classList.add("hidden");
  const wrap = document.getElementById("spec-frame-wrap");
  wrap.classList.remove("hidden");
  document.getElementById("spec-job-id").textContent = jobId;
  document.getElementById("spec-frame").src = `/swagger/${encodeURIComponent(jobId)}`;
}

function bindEvents() {
  document.querySelectorAll(".tab-button").forEach((button) => {
    button.addEventListener("click", () => switchTab(button.dataset.tab));
  });
  document.getElementById("radar-search-btn").addEventListener("click", () => loadTargets(false));
  document.getElementById("radar-refresh-btn").addEventListener("click", () => loadTargets(true));
  document.getElementById("radar-sort").addEventListener("change", () => loadTargets(false));
  document.getElementById("radar-search").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      loadTargets(false);
    }
  });
  document.getElementById("filter-pills").addEventListener("click", (event) => {
    const pill = event.target.closest("[data-filter]");
    if (!pill) {
      return;
    }
    state.filter = pill.dataset.filter;
    document.querySelectorAll(".filter-pill").forEach((item) => {
      item.classList.toggle("active", item === pill);
    });
    applyFilterAndRender();
  });
  document.getElementById("radar-results").addEventListener("click", (event) => {
    const button = event.target.closest("[data-scan-url]");
    if (button) {
      startScan(button.dataset.scanUrl);
    }
  });
  document.getElementById("hero-recommendation").addEventListener("click", (event) => {
    const button = event.target.closest("[data-scan-url]");
    if (button) {
      startScan(button.dataset.scanUrl);
    }
  });
  document.getElementById("manual-scan-btn").addEventListener("click", () => {
    const url = document.getElementById("manual-url").value.trim();
    if (url) {
      startScan(url);
    }
  });
  document.getElementById("manual-url").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      const url = event.target.value.trim();
      if (url) {
        startScan(url);
      }
    }
  });
  document.getElementById("jobs-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-view-job]");
    if (button) {
      openSpecViewer(button.dataset.viewJob);
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  bindEvents();
  switchTab("radar");
  loadTargets(false);
  loadJobs();
  refreshIcons();
});
