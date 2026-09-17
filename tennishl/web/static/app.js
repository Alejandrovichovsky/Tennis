/* tennishl web UI. Vanilla JS, two views: job list and job detail. */

const $ = (sel, el = document) => el.querySelector(sel);
const app = $("#app");
const CATS = { serve: "Serve", long_rally: "Lång duell", fast_exchange: "Snabbt utbyte", winner: "Vinnande slag",
  net_play: "Nätspel", baseline_rally: "Grundslagsduell", rally: "Poäng" };
const REASONS = { duration: "längden", shots: "antalet slag", intensity: "tempot",
  coverage: "att spelarna rörde sig över hela banan", finish: "ett tydligt avslut", serve: "en tydlig servestart" };

const fmt = s => { s = Math.max(0, s | 0); const m = (s / 60) | 0, r = s % 60; const h = (m / 60) | 0;
  return h ? `${h}:${String(m % 60).padStart(2, "0")}:${String(r).padStart(2, "0")}` : `${m}:${String(r).padStart(2, "0")}`; };
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const api = async (url, opts = {}) => {
  const r = await fetch(url, opts);
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch {} throw new Error(m); }
  return r.json();
};

/* ----------------------------------------------------------------- state */
let pollHandle = null, job = null, truth = [], labeling = false, pendingIn = null, stopAt = null, activeId = null;

/* ---------------------------------------------------------------- router */
window.addEventListener("hashchange", route);
route();
function route() {
  stopPolling();
  const m = location.hash.match(/^#\/job\/([a-z0-9]+)/);
  if (m) showJob(m[1]); else showList();
}

/* ------------------------------------------------------------------ list */
async function showList() {
  app.innerHTML = `
    <h1>Matcher</h1>
    <div class="panel">
      <div class="drop" id="drop">Släpp en videofil här, eller <label><a>välj fil</a><input type="file" id="file" accept="video/*" hidden></label></div>
      <div class="row" style="margin-top:12px">
        <label class="field">eller lokal sökväg (kopieras inte)<input type="text" id="path" size="48" placeholder="/Users/andre/Movies/match.mp4"></label>
        <label class="field">läge<select id="mode"><option value="all">alla rallyn</option><option value="top">topp N</option></select></label>
        <label class="field">N<input type="number" id="topn" value="12" min="1"></label>
        <label class="field">bara första N s (test)<input type="number" id="maxs" placeholder="" min="10"></label>
        <label class="field"><span>&nbsp;</span><span><input type="checkbox" id="noball"> hoppa över boll</span></label>
        <button class="primary" id="go">Analysera</button>
      </div>
      <div class="muted" id="upmsg" style="margin-top:8px"></div>
    </div>
    <h2>Tidigare</h2>
    <div class="panel"><table id="jobs"><thead><tr><th>Namn</th><th>Status</th><th>Skapad</th><th></th></tr></thead><tbody></tbody></table></div>`;

  const drop = $("#drop"), fileIn = $("#file");
  let file = null;
  const setFile = f => { file = f; drop.textContent = f ? `${f.name} (${(f.size / 1e6).toFixed(0)} MB)` : ""; };
  fileIn.onchange = () => setFile(fileIn.files[0]);
  ["dragenter", "dragover"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", e => setFile(e.dataTransfer.files[0]));

  $("#go").onclick = async () => {
    const fd = new FormData();
    const path = $("#path").value.trim();
    if (file) fd.append("file", file); else if (path) fd.append("path", path);
    else { $("#upmsg").textContent = "Välj en fil eller ange en sökväg."; return; }
    fd.append("select_mode", $("#mode").value);
    fd.append("max_highlights", $("#topn").value || 12);
    if ($("#maxs").value) fd.append("max_seconds", $("#maxs").value);
    fd.append("skip_ball", $("#noball").checked ? "true" : "false");
    $("#go").disabled = true; $("#upmsg").textContent = file ? "Laddar upp..." : "Startar...";
    try {
      const job = await api("/api/jobs", { method: "POST", body: fd });
      location.hash = `#/job/${job.id}`;
    } catch (e) { $("#upmsg").innerHTML = `<span class="error">${esc(e.message)}</span>`; $("#go").disabled = false; }
  };
  await refreshList();
  pollHandle = setInterval(refreshList, 3000);
}

async function refreshList() {
  const jobs = await api("/api/jobs");
  const tb = $("#jobs tbody"); if (!tb) return;
  tb.innerHTML = jobs.map(j => `
    <tr class="link" data-id="${j.id}">
      <td>${esc(j.name)}</td>
      <td><span class="pill ${j.status}">${j.status}${j.status === "running" ? ` · ${esc(j.stage)} ${(j.fraction * 100) | 0}%` : ""}</span></td>
      <td class="muted">${new Date(j.created_at * 1000).toLocaleString("sv-SE")}</td>
      <td><button data-del="${j.id}">ta bort</button></td></tr>`).join("") || `<tr><td colspan="4" class="muted">Inga matcher än.</td></tr>`;
  tb.querySelectorAll("tr.link").forEach(tr => tr.onclick = e => { if (!e.target.dataset.del) location.hash = `#/job/${tr.dataset.id}`; });
  tb.querySelectorAll("[data-del]").forEach(b => b.onclick = async e => {
    e.stopPropagation(); if (!confirm("Ta bort jobbet och alla filer?")) return;
    await api(`/api/jobs/${b.dataset.del}`, { method: "DELETE" }); refreshList();
  });
}

/* ------------------------------------------------------------------- job */
function stopPolling() { if (pollHandle) clearInterval(pollHandle); pollHandle = null; document.onkeydown = null; }

async function showJob(id) {
  job = await api(`/api/jobs/${id}`);
  truth = (job.truth && job.truth.points) ? job.truth.points.map(p => ({ ...p })) : [];
  renderJob();
  pollHandle = setInterval(async () => {
    const fresh = await api(`/api/jobs/${id}`);
    const wasRunning = job.status === "running" || job.status === "queued";
    job = fresh;
    updateStatus();
    if (wasRunning && !(job.status === "running" || job.status === "queued")) renderJob();
  }, 700);
  document.onkeydown = onKey;
}

function selectedIds() { return new Set([...document.querySelectorAll(".card")].filter(c => c.querySelector("input").checked).map(c => c.dataset.id)); }

function renderJob() {
  const hl = (job.highlights && job.highlights.highlights) || [];
  const a = job.analysis;
  const cfg = job.config || {};
  const seg = cfg.segmentation || {}, act = cfg.activity || {}, clip = cfg.clip || {};
  const byTime = [...hl].sort((x, y) => x.clip_start_s - y.clip_start_s);
  const chosen = hl.filter(h => h.selected);
  const total = chosen.reduce((s, h) => s + h.clip_duration_s, 0);

  app.innerHTML = `
    <div class="row" style="justify-content:space-between">
      <h1 style="margin:0">${esc(job.name)}</h1>
      <div class="row">${job.urls && job.urls.montage ? `<a class="btn" href="${job.urls.montage}" download>Ladda ner montage</a>` : ""}
        ${job.urls && job.urls.report ? `<a class="btn" href="${job.urls.report}" target="_blank">report.md</a>` : ""}</div>
    </div>
    <div class="panel" id="status"></div>

    <div class="grid" style="margin-top:16px">
      <div>
        <video id="video" controls preload="metadata" ${job.source_url ? `src="${job.source_url}"` : ""}></video>
        <canvas class="timeline" id="tl"></canvas>
        <div class="legend"><span><i style="background:var(--accent)"></i>a(t)</span><span><i style="background:var(--green)"></i>poäng</span>
          <span><i style="background:var(--blue)"></i>förkastat</span><span><i style="background:var(--orange)"></i>facit</span>
          <span class="muted">klicka för att hoppa</span></div>
        <div class="panel" style="margin-top:12px">
          <div class="row" style="justify-content:space-between">
            <b>Facit</b>
            <span class="row"><button id="label-toggle">${labeling ? "Sluta märka" : "Märk poäng"}</button>
              <button id="truth-save" ${truth.length ? "" : "disabled"}>Spara facit</button></span>
          </div>
          <div class="muted" style="margin-top:6px">I märkläge: <span class="kbd">I</span> = poäng börjar, <span class="kbd">O</span> = poäng slutar,
            <span class="kbd">Space</span> = play/pause, <span class="kbd">←</span>/<span class="kbd">→</span> = 2 s, <span class="kbd">J</span>/<span class="kbd">L</span> = 10 s. Klicka på en punkt för att ta bort.</div>
          <div class="truthlist" id="truthlist"></div>
          <div id="eval" style="margin-top:10px"></div>
        </div>
      </div>
      <div>
        <div class="row" style="justify-content:space-between;margin-bottom:8px">
          <span><b>${chosen.length}</b> klipp valda · ${fmt(total)} <span class="muted">· ${hl.length} poäng hittade${a ? `, ${a.segmentation.rejected.length} förkastade` : ""}</span></span>
          <button class="primary" id="render" ${hl.length ? "" : "disabled"}>Rendera montage</button>
        </div>
        <div class="cards" id="cards">${byTime.map(cardHtml).join("") || `<div class="muted">Inga poäng än.</div>`}</div>
      </div>
    </div>

    <details class="panel" style="margin-top:16px" id="tune">
      <summary>Justera trösklar och kör om (utan att avkoda videon igen)</summary>
      <div class="cfg">
        ${num("segmentation.enter_frac", "starttröskel", seg.enter_frac, 0.01)}
        ${num("segmentation.exit_frac", "stopptröskel", seg.exit_frac, 0.01)}
        ${num("segmentation.min_duration_s", "min längd s", seg.min_duration_s, 0.1)}
        ${num("segmentation.max_duration_s", "max längd s", seg.max_duration_s, 1)}
        ${num("segmentation.merge_gap_s", "brygga luckor s", seg.merge_gap_s, 0.1)}
        ${num("segmentation.min_gap_s", "dröj innan stopp s", seg.min_gap_s, 0.1)}
        ${num("segmentation.require_both_sides_frac", "kräv båda sidor", seg.require_both_sides_frac, 0.05)}
        ${num("activity.w_foreground", "vikt rörelse", act.w_foreground, 0.05)}
        ${num("activity.w_player_speed", "vikt hastighet", act.w_player_speed, 0.05)}
        ${num("activity.w_spread", "vikt båda sidor", act.w_spread, 0.05)}
        ${num("activity.smooth_seconds", "utjämning s", act.smooth_seconds, 0.1)}
        ${num("clip.pre_roll_s", "marginal före s", clip.pre_roll_s, 0.1)}
        ${num("clip.post_roll_s", "marginal efter s", clip.post_roll_s, 0.1)}
        ${num("clip.max_highlights", "topp N", clip.max_highlights, 1)}
        <label class="field">läge<select data-key="clip.select_mode"><option value="all" ${clip.select_mode === "all" ? "selected" : ""}>alla rallyn</option><option value="top" ${clip.select_mode === "top" ? "selected" : ""}>topp N</option></select></label>
      </div>
      <div class="row" style="margin-top:12px">
        <button class="primary" id="retune" ${job.has_observations ? "" : "disabled"}>Kör om analysen</button>
        <label><input type="checkbox" id="retune-ball"> spåra boll</label>
        <label><input type="checkbox" id="retune-clips"> klipp direkt</label>
        ${job.urls && job.urls.signal_plot ? `<a href="${job.urls.signal_plot}" target="_blank">signal.png</a>` : ""}
      </div>
    </details>`;

  updateStatus();
  wireVideo(hl);
  drawTimeline();
  renderTruth();

  $("#render").onclick = async () => {
    const on = selectedIds();
    const selection = { version: 1, clips: hl.map(h => ({ id: h.id, rank: h.rank, start_s: h.clip_start_s, end_s: h.clip_end_s, selected: on.has(h.id) })) };
    await api(`/api/jobs/${job.id}/render`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ selection }) });
    job.status = "queued"; updateStatus();
  };
  $("#retune").onclick = async () => {
    const config = {};
    document.querySelectorAll("#tune [data-key]").forEach(el => {
      const [a, b] = el.dataset.key.split("."); config[a] = config[a] || {};
      config[a][b] = el.tagName === "SELECT" ? el.value : parseFloat(el.value);
    });
    try {
      await api(`/api/jobs/${job.id}/retune`, { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ config, skip_ball: !$("#retune-ball").checked, skip_clips: !$("#retune-clips").checked }) });
      job.status = "queued"; updateStatus();
    } catch (e) { alert(e.message); }
  };
  $("#label-toggle").onclick = () => { labeling = !labeling; pendingIn = null; renderJob(); };
  $("#truth-save").onclick = async () => {
    job = await api(`/api/jobs/${job.id}/truth`, { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ points: truth }) });
    renderJob();
  };
}

function num(key, label, val, step) {
  return `<label class="field">${label}<input type="number" data-key="${key}" value="${val ?? ""}" step="${step}"></label>`;
}

function cardHtml(h) {
  const f = h.features;
  const cats = h.categories.map(c => `<span class="tag">${CATS[c] || c}</span>`).join("");
  const top = Object.entries(h.contributions || {}).sort((a, b) => b[1] - a[1]).slice(0, 2).filter(([, v]) => v > 0).map(([k]) => REASONS[k] || k);
  const trail = h.ball_track && h.ball_track.confidence >= 0.55 ? `bollspår ${h.ball_track.confidence.toFixed(2)}` : "inget bollspår";
  return `<div class="card ${h.selected ? "" : "off"}" data-id="${h.id}" data-start="${h.clip_start_s}" data-end="${h.clip_end_s}">
    <input type="checkbox" ${h.selected ? "checked" : ""}>
    ${h.thumb_url ? `<img src="${h.thumb_url}" alt="">` : `<div class="noimg"></div>`}
    <div>
      <div class="title">#${h.rank} · ${fmt(h.clip_start_s)}–${fmt(h.clip_end_s)} · ${h.clip_duration_s.toFixed(1)} s · score ${h.score.toFixed(2)}
        ${h.clip_url ? `<a href="${h.clip_url}" target="_blank" style="margin-left:8px;font-weight:400">klipp</a>` : ""}</div>
      <div>${cats}</div>
      <div class="why">${f.duration_s.toFixed(1)} s, ~${f.shot_count.toFixed(0)} slag (${f.shot_source === "ball" ? "bollspår" : "rörelse"}). ${top.length ? "Valdes på grund av " + top.join(" och ") + "." : ""} ${trail}.</div>
      <div class="nums">tempo ${f.mean_intensity.toFixed(2)} · täckning ${f.coverage.toFixed(2)} · avslut ${f.finish.toFixed(2)} · serve ${f.serve_onset.toFixed(2)} · nät ${f.net_approach.toFixed(2)}</div>
    </div></div>`;
}

function updateStatus() {
  const el = $("#status"); if (!el) return;
  const running = job.status === "running" || job.status === "queued";
  el.innerHTML = `<div class="row" style="justify-content:space-between">
      <span><span class="pill ${job.status}">${job.status}</span> <span class="muted">${esc(job.action)}${running ? " · " + esc(job.stage) : ""}</span></span>
      <span class="muted">${job.error ? `<span class="error">${esc(job.error)}</span>` : ""}</span></div>
    ${running ? `<div class="progress"><div style="width:${job.fraction * 100}%"></div></div>` : ""}
    ${job.log && job.log.length ? `<div class="log">${esc(job.log.slice(-6).join("\n"))}</div>` : ""}`;
  $("#top-status").textContent = running ? `${job.stage} ${(job.fraction * 100) | 0}%` : "";
}

/* ----------------------------------------------------------- video + cards */
function wireVideo(hl) {
  const v = $("#video");
  v.addEventListener("timeupdate", () => {
    drawTimeline();
    if (stopAt !== null && v.currentTime >= stopAt) { v.pause(); stopAt = null; }
  });
  v.addEventListener("loadedmetadata", drawTimeline);
  document.querySelectorAll(".card").forEach(card => {
    card.querySelector("input").addEventListener("click", e => { e.stopPropagation(); card.classList.toggle("off", !e.target.checked); });
    card.addEventListener("click", () => {
      document.querySelectorAll(".card.active").forEach(c => c.classList.remove("active"));
      card.classList.add("active"); activeId = card.dataset.id;
      v.currentTime = parseFloat(card.dataset.start); stopAt = parseFloat(card.dataset.end); v.play();
    });
  });
  $("#tl").addEventListener("click", e => {
    const r = e.target.getBoundingClientRect();
    const dur = v.duration || (job.analysis && job.analysis.video.duration_s) || 1;
    v.currentTime = (e.clientX - r.left) / r.width * dur; stopAt = null;
  });
}

function drawTimeline() {
  const c = $("#tl"); if (!c) return;
  const v = $("#video");
  const W = c.clientWidth * devicePixelRatio, H = c.clientHeight * devicePixelRatio;
  if (c.width !== W || c.height !== H) { c.width = W; c.height = H; }
  const ctx = c.getContext("2d"); ctx.clearRect(0, 0, W, H);
  const a = job.analysis; if (!a) return;
  const dur = a.video.duration_s || v.duration || 1;
  const x = t => t / dur * W;
  const bandH = H * 0.2;
  for (const s of a.segmentation.rejected) { ctx.fillStyle = "rgba(79,124,255,.35)"; ctx.fillRect(x(s.start_s), 0, Math.max(1, x(s.end_s) - x(s.start_s)), H); }
  for (const s of a.segmentation.segments) { ctx.fillStyle = "rgba(63,191,111,.35)"; ctx.fillRect(x(s.start_s), 0, Math.max(1, x(s.end_s) - x(s.start_s)), H); }
  for (const p of truth) { ctx.fillStyle = "rgba(255,159,67,.8)"; ctx.fillRect(x(p.start_s), H - bandH, Math.max(2, x(p.end_s) - x(p.start_s)), bandH); }
  if (pendingIn !== null) { ctx.fillStyle = "rgba(255,159,67,.5)"; ctx.fillRect(x(pendingIn), H - bandH, 2 * devicePixelRatio, bandH); }
  const sig = a.signal;
  ctx.strokeStyle = "#d8ff3a"; ctx.lineWidth = 1.5 * devicePixelRatio; ctx.beginPath();
  sig.t.forEach((t, i) => { const y = H - bandH - sig.activity[i] * (H - bandH - 4); i ? ctx.lineTo(x(t), y) : ctx.moveTo(x(t), y); });
  ctx.stroke();
  ctx.strokeStyle = "rgba(63,191,111,.7)"; ctx.setLineDash([4, 4]); ctx.lineWidth = devicePixelRatio;
  const yThr = H - bandH - a.segmentation.enter_threshold * (H - bandH - 4);
  ctx.beginPath(); ctx.moveTo(0, yThr); ctx.lineTo(W, yThr); ctx.stroke(); ctx.setLineDash([]);
  if (v && v.currentTime) { ctx.fillStyle = "#fff"; ctx.fillRect(x(v.currentTime) - devicePixelRatio, 0, 2 * devicePixelRatio, H); }
}

/* --------------------------------------------------------------- labeling */
function onKey(e) {
  const v = $("#video"); if (!v || e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
  const k = e.key.toLowerCase();
  if (k === " ") { e.preventDefault(); v.paused ? v.play() : v.pause(); return; }
  if (k === "arrowleft") { v.currentTime -= 2; return; } if (k === "arrowright") { v.currentTime += 2; return; }
  if (k === "j") { v.currentTime -= 10; return; } if (k === "l") { v.currentTime += 10; return; }
  if (!labeling) return;
  if (k === "i") { pendingIn = v.currentTime; drawTimeline(); }
  if (k === "o" && pendingIn !== null && v.currentTime > pendingIn) {
    truth.push({ start_s: +pendingIn.toFixed(2), end_s: +v.currentTime.toFixed(2) }); truth.sort((a, b) => a.start_s - b.start_s);
    pendingIn = null; renderTruth(); drawTimeline(); $("#truth-save").disabled = false;
  }
}

function renderTruth() {
  const el = $("#truthlist"); if (!el) return;
  el.innerHTML = truth.map((p, i) => `<span class="pill" data-i="${i}" title="ta bort">${fmt(p.start_s)}–${fmt(p.end_s)}</span>`).join("")
    || `<span class="muted">Inga märkta poäng${labeling ? " – spela videon och tryck I / O" : ""}.</span>`;
  el.querySelectorAll("[data-i]").forEach(s => s.onclick = () => { truth.splice(+s.dataset.i, 1); renderTruth(); drawTimeline(); });
  const ev = $("#eval"); if (!ev) return;
  const r = job.eval;
  if (!r) { ev.innerHTML = truth.length ? `<span class="muted">Spara facit för att jämföra med detektionen.</span>` : ""; return; }
  ev.innerHTML = `<div class="row">
      <span class="stat"><b>${(r.recall * 100).toFixed(0)}%</b><span>recall (${r.matched}/${r.n_truth})</span></span>
      <span class="stat"><b>${(r.precision * 100).toFixed(0)}%</b><span>precision (${r.matched}/${r.n_detected})</span></span>
      <span class="stat"><b>${r.mean_start_error_s.toFixed(1)} s</b><span>startfel</span></span>
      <span class="stat"><b>${r.mean_end_error_s.toFixed(1)} s</b><span>slutfel</span></span></div>
    ${r.missed.length ? `<div style="margin-top:6px"><span class="muted">missade:</span> ${r.missed.map(m => `<a href="#" data-seek="${m.start_s}">${fmt(m.start_s)}</a>`).join(", ")}</div>` : ""}
    ${r.false_positives.length ? `<div><span class="muted">falska:</span> ${r.false_positives.map(m => `<a href="#" data-seek="${m.start_s}">${fmt(m.start_s)}</a>`).join(", ")}</div>` : ""}`;
  ev.querySelectorAll("[data-seek]").forEach(a => a.onclick = e => { e.preventDefault(); const v = $("#video"); v.currentTime = +a.dataset.seek; v.play(); });
}
