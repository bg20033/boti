"use strict";

let clipFilter = "all", libraryRequest = 0, librarySignature = "", recentSignature = "";
let recentClips = [], recentChecked = 0, recentLoading = false, showOffline = false;
const viewNames = {dashboard:"Përmbledhja", clips:"Biblioteka", campaigns:"Fushatat", accounts:"Llogaritë", settings:"Konfigurimi"};
const previewObserver = new IntersectionObserver(entries => {
  entries.forEach(entry => {
    if (entry.isIntersecting && !entry.target.getAttribute("src")) {
      entry.target.src = entry.target.dataset.previewSrc;
      entry.target.preload = "metadata";
      previewObserver.unobserve(entry.target);
    }
  });
}, {rootMargin:"120px"});

function hydratePreviews() {
  previewObserver.disconnect();
  document.querySelectorAll("video[data-preview-src]").forEach(video => {
    if (video.closest(".view")?.hidden || (video.closest("dialog") && !video.closest("dialog").open)) {
      video.pause();
      if (video.getAttribute("src")) { video.removeAttribute("src"); video.load(); }
    } else if (!video.getAttribute("src")) previewObserver.observe(video);
  });
}

function showView(name, updateHash = true) {
  if (!viewNames[name]) name = "dashboard";
  currentView = name;
  document.querySelectorAll(".view").forEach(view => view.hidden = view.id !== `view-${name}`);
  document.querySelectorAll(".nav-item").forEach(item => {
    item.classList.toggle("active", item.dataset.view === name);
    if (item.dataset.view === name) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  });
  $("view-title").textContent = viewNames[name];
  window.scrollTo({top:0, behavior:"instant"});
  if (updateHash) history.replaceState(null, "", `#${name}`);
  if (name === "clips") loadClips();
  if (name === "settings") loadDoctor();
  if (name === "campaigns" && typeof loadCampaigns === "function") { loadCampaigns(); loadSubmissions(); }
  document.querySelectorAll(".view video").forEach(video => { if (video.closest(".view").hidden) video.pause(); });
  hydratePreviews();
}

function clipSource(clip) { return `${(clip.provider || "").toUpperCase()}${clip.run_mode === "trial" ? " · PROVË" : ""}`; }
function safeVideoUrl(clip) { return clip.video_url === `/api/clips/${clip.id}/video` ? clip.video_url : null; }
function clipPreview(clip) {
  const url = safeVideoUrl(clip);
  return `<div class="clip-preview">${url ? `<video controls preload="none" playsinline data-preview-src="${esc(url)}#t=0.1" aria-label="${esc(clip.metadata.title || "Klip")}"></video>` : `<div class="preview-pending"><svg class="icon" aria-hidden="true"><use href="#i-play"/></svg><span>${["preparing", "rendering"].includes(clip.status) ? "Videoja po përgatitet" : "Videoja nuk është në disk"}</span></div>`}<span class="duration-tag">${Math.round(clip.end - clip.start)}s</span></div>`;
}

function clipCard(clip, compact = false) {
  const heading = esc(clip.metadata.title || "Moment pa titull");
  if (compact) return `<article class="recent-clip">${clipPreview(clip)}<div class="recent-clip-body"><div class="clip-source">${esc(clipSource(clip))}${badge(clip.status)}</div><h3>${heading}</h3><button class="text-button" data-open-clip="${esc(clip.id)}">Shiko klipin ↗</button></div></article>`;
  const reports = clip.quality_reports || [];
  const issues = clip.compliance && !clip.compliance.passed ? clip.compliance.issues.map(i => i.message).join("; ") : "";
  return `<article class="clip-card" data-clip-id="${esc(clip.id)}">
    ${clipPreview(clip)}
    <div class="clip-body"><div class="clip-source"><span>${esc(clipSource(clip))}</span>${badge(clip.status)}</div><h3>${heading}</h3>
    <div class="clip-meta"><span>${esc(clip.group_name)}</span><span>${Math.round(clip.score)}/100 · v${clip.current_version || 1}</span></div>
    ${clip.review_passed === 0 ? '<p class="quality-hold">Publikimi është ndalur nga vlerësimi yt.</p>' : ""}
    ${issues ? `<p class="quality-hold">${esc(issues)}</p>` : ""}
    ${clip.quality_reason || reports.length ? `<details class="quality-details"><summary>${["skipped", "technical_failed"].includes(clip.status) ? "Pse nuk kaloi kontrollin?" : "Kontrolli i cilësisë"}</summary>${clip.quality_reason ? `<p class="review-note">${esc(clip.quality_reason)}</p>` : ""}${reports.map(report => `<p>v${report.version} · ${report.passed ? "Kaloi" : "Nuk kaloi"}: ${esc((report.result.verdict?.reasons || []).join("; "))}</p>`).join("")}</details>` : ""}
    ${clip.review_notes ? `<p class="review-note">${esc(clip.review_notes)}</p>` : ""}
    ${clip.metadata.verification_reason ? `<details class="selection-reason"><summary>Pse u zgjodh ky moment?</summary><p>${esc(clip.metadata.verification_reason)}</p></details>` : ""}
    <div class="clip-actions">${safeVideoUrl(clip) ? `<a class="secondary" href="${esc(safeVideoUrl(clip))}" download="boti-${esc(clip.id)}.mp4">↓ Video MP4</a>` : ""}<button class="secondary" data-caption="${esc(clip.id)}">Përshkrimi</button>${clip.can_edit_title?`<button class="secondary" data-title-edit="${esc(clip.id)}">Titull sipër</button>`:""}${clip.status === "ready" && clip.run_mode !== "trial" ? `<a class="secondary" href="/api/clips/${esc(clip.id)}/tiktok.zip">Paketa TikTok ↓</a>` : ""}</div>
    ${clip.can_edit_title && clip.metadata.source_context?.length ? `<button class="text-button" data-refine="${esc(clip.id)}">Përmirëso prerjen ↗</button>` : ""}
    <details class="clip-review"><summary>Vlerëso klipin</summary><p class="muted">Vlerësimi yt ruhet veçmas nga kontrolli automatik.</p><div class="clip-actions"><button class="secondary" data-review="${esc(clip.id)}" data-passed="true">${clip.review_passed === 1 ? "✓ " : ""}Në rregull</button><button class="secondary" data-review="${esc(clip.id)}" data-passed="false">Përmirëso</button></div></details>
    ${(clip.publications || []).length ? `<details class="publication-details"><summary>Publikimet</summary><div class="platforms">${clip.publications.map(p => `<div class="platform-line"><span>${platformNames[p.platform]}</span>${p.url ? `<a href="${safeHref(p.url)}" target="_blank" rel="noopener noreferrer">Hap postimin ↗</a>` : badge(p.display_status || p.status)}${p.platform === "tiktok" && p.status === "manual_ready" ? `<button class="text-button" data-published="${esc(p.id)}">E publikova</button>` : ""}${["needs_reconciliation", "failed"].includes(p.status) ? `<button class="text-button" data-resolve="${esc(p.id)}">Verifiko</button>` : ""}</div>`).join("")}</div></details>` : ""}
    </div></article>`;
}

async function loadLibrary(quiet = false) {
  const request = ++libraryRequest;
  try {
    const params = new URLSearchParams({limit:12, offset:clipOffset, state:clipFilter, q:$("clip-search").value.trim()});
    const result = await api(`/api/clips?${params}`);
    if (request !== libraryRequest) return;
    const total = result.total ?? result.clips.length;
    $("library-summary").textContent = `${total} momente${clipFilter === "all" ? " gjithsej" : " në këtë filtër"} · Videot e refuzuara mbeten të shikueshme.`;
    $("clip-page").textContent = `Faqja ${Math.floor(clipOffset / 12) + 1} / ${Math.max(1, Math.ceil(total / 12))}`;
    $("prev-clips").disabled = clipOffset === 0;
    $("next-clips").disabled = clipOffset + 12 >= total;
    const signature = JSON.stringify([clipFilter, params.get("q"), result.clips]);
    if (signature === librarySignature) return;
    if (quiet && [...$("clip-grid").querySelectorAll("video")].some(video => !video.paused)) return;
    librarySignature = signature;
    clips = result.clips;
    const expanded = new Set([...$("clip-grid").querySelectorAll("details[open]")].map(detail => detail.closest("[data-clip-id]").dataset.clipId + ":" + detail.className));
    $("clip-grid").innerHTML = clips.length ? clips.map(clip => clipCard(clip)).join("") : `<div class="empty library-empty"><svg class="icon" aria-hidden="true"><use href="#i-play"/></svg><strong>${$("clip-search").value.trim() ? "Nuk u gjet asnjë klip." : "Ende asnjë klip në këtë filtër."}</strong><p>${clipFilter === "ready" ? "Klipet shfaqen këtu pasi kalojnë kontrollin. Te “Të gjitha” i sheh edhe provat." : "Provo një filtër tjetër ose prit përpunimin e videos së radhës."}</p></div>`;
    $("clip-grid").querySelectorAll("details").forEach(detail => { detail.open = expanded.has(detail.closest("[data-clip-id]").dataset.clipId + ":" + detail.className); });
    hydratePreviews();
  } catch (error) { if (request === libraryRequest) notify(error.message, true); }
}

async function loadRecent() {
  if (recentLoading || Date.now() - recentChecked < 15000) return;
  recentLoading = true;
  try {
    const result = await api("/api/clips?limit=3&offset=0");
    recentChecked = Date.now();
    const signature = JSON.stringify(result.clips);
    if (signature === recentSignature || [...$("recent-clips").querySelectorAll("video")].some(video => !video.paused)) return;
    recentSignature = signature;
    recentClips = result.clips;
    $("recent-clips").innerHTML = recentClips.length ? recentClips.map(clip => clipCard(clip, true)).join("") : '<div class="empty"><strong>Momenti i parë po vjen.</strong>Kur workerat e përgatisin klipin, mund ta shikosh këtu.</div>';
    hydratePreviews();
  } catch (error) { $("recent-clips").innerHTML = '<div class="empty">Klipet nuk u ngarkuan. <button class="text-button" id="retry-recent">Provo përsëri</button></div>'; }
  finally { recentLoading = false; }
}

function renderWorkers(s) {
  const primaryId = s.settings.analysis_worker_id;
  const workers = [...s.workers].sort((a, b) => (b.id === primaryId) - (a.id === primaryId) || (s.server_time - (b.last_seen || 0) < 30) - (s.server_time - (a.last_seen || 0) < 30) || a.id.localeCompare(b.id));
  const online = workers.filter(w => s.server_time - (w.last_seen || 0) < 30);
  const offlineCount = workers.length - online.length;
  $("worker-summary").textContent = `${online.length} të lidhur · ${online.filter(w => w.task).length} në punë`;
  $("toggle-offline").hidden = !offlineCount;
  $("toggle-offline").textContent = `${showOffline ? "Fshih" : "Shfaq"} ${offlineCount} offline`;
  $("toggle-offline").setAttribute("aria-expanded", String(showOffline));
  $("workers").innerHTML = workers.map(worker => {
    const active = s.server_time - (worker.last_seen || 0) < 30, busy = active && worker.task;
    const primary = worker.id === primaryId, h = worker.hardware;
    const cpu = Math.max(0, Math.min(100, Number(h.cpu_percent) || 0));
    const ram = Math.max(0, Math.min(100, Number(h.ram_percent) || 0));
    const label = primary ? "MAIN" : worker.id.replace("pc-", "W").toUpperCase();
    return `<article class="worker-card ${primary ? "primary-worker" : ""} ${active ? busy ? "busy" : "online" : "offline"}" ${!active && !showOffline ? "hidden" : ""}>
      <div class="worker-top"><div class="worker-identity"><span class="worker-avatar">${esc(label)}</span><div><strong>${esc(primary ? "PC-ja kryesore" : worker.name)}</strong><small>${primary ? "Gjetja e momenteve" : "Përpunimi i videove"}</small></div></div><span class="badge ${worker.paused ? "warn" : active ? "good" : ""}">${worker.paused ? "Pezulluar" : active ? busy ? "Në punë" : "Gati" : "Offline"}</span></div>
      <div class="worker-task">${busy ? `<span class="task-pulse"></span><b>${esc(status(worker.task.kind))}</b><button class="text-button" data-job="${esc(worker.task.job_id)}">Hap punën ↗</button>` : active ? '<span class="idle-dot"></span><span>Pret detyrën e radhës</span>' : '<span>Nuk është lidhur</span>'}</div>
      <div class="resource-row"><span>CPU <b>${active ? Math.round(cpu) + "%" : "—"}</b></span><div class="resource-track"><i style="width:${active ? cpu : 0}%"></i></div><span>RAM <b>${active ? Math.round(ram) + "%" : "—"}</b></span></div>
      <div class="worker-tools">${(worker.assigned_capabilities || []).map(status).map(esc).join(" · ") || "Pa role aktive"}</div><div class="worker-foot"><span>${h.ram_gb ? esc(h.ram_gb) + " GB RAM" : "Pa të dhëna"}</span><button class="text-button" data-worker-pause="${esc(worker.id)}" data-paused="${worker.paused ? "1" : "0"}">${worker.paused ? "Vazhdo" : "Pezullo"}</button></div>
    </article>`;
  }).join("");
}

function renderJobs(s) {
  const filter = $("job-filter").value;
  const jobs = s.jobs.filter(job => filter === "all" || (filter === "active" ? !["complete", "failed"].includes(job.status) : job.status === filter));
  $("jobs").innerHTML = jobs.length ? jobs.map(job => {
    const progress = job.total_tasks ? Math.round(job.completed_tasks / job.total_tasks * 100) : 0;
    return `<tr><td><span class="video-name" title="${esc(job.title)}">${esc(job.title || (job.provider || "Video").toUpperCase() + " · Në përgatitje")}</span><small>${esc((job.provider || "video").toUpperCase())} ${job.run_mode === "trial" ? "· PROVË " : ""} · ${esc(ago(job.created))}</small>${job.status === "failed" && job.error ? `<details class="job-error"><summary>Shiko gabimin</summary><p>${esc(job.error)}</p></details>` : ""}</td><td>${badge(job.status === "failed" ? "failed" : job.stage)}</td><td><div class="progress-track"><span style="width:${progress}%"></span></div><small>${job.completed_tasks}/${job.total_tasks} detyra</small></td><td><strong>${job.clip_count}</strong><small class="clip-count-note">${job.accepted_clips || 0} gati · ${job.skipped_clips || 0} të refuzuara</small></td><td><button class="secondary small-button" data-job="${esc(job.id)}">${job.run_mode === "trial" ? "Shiko klipet" : "Hap raportin"} ↗</button>${job.status === "failed" ? `<button class="text-button retry-job" data-job-retry="${esc(job.id)}">Riprovo</button>` : ""}</td></tr>`;
  }).join("") : '<tr><td colspan="5" class="empty"><strong>Asnjë video në këtë pamje.</strong>Zgjidh “Të gjitha” për historinë e punëve.</td></tr>';
}

function renderOverview(s) {
  const count = s.counts, total = Object.values(count).reduce((sum, value) => sum + value, 0);
  const ready = (count.ready || 0), review = (count.checking || 0) + (count.correcting || 0) + (count.approved || 0);
  const pending = s.job_counts?.pending ?? 0;
  $("nav-clips").textContent = total;
  $("stats").innerHTML = [[total,"Momente të gjetura","Të gjitha në bibliotekë"],[ready,"Klipe gati","Kaluan kontrollin e cilësisë"],[review,"Në kontroll","Kontrolli ose korrigjimi aktiv"],[pending,"Video në punë","Radha mbushet automatikisht"]].map(([number,label,note]) => `<div class="stat"><span>${label}</span><strong>${number.toLocaleString("de-DE")}</strong><small>${note}</small></div>`).join("");
  renderWorkers(s); renderJobs(s);
  const feeds = (s.feeds || []).filter(feed => feed.data.enabled);
  const paused = s.settings.paused || s.disk.paused;
  $("automation-state").textContent = paused ? "Në pritje" : feeds.length ? "Aktiv" : "Pa kanale";
  $("automation-state").className = "badge " + (paused ? "warn" : "good");
  const durations = [...new Set(feeds.map(feed => feed.data.trial ? `${feed.data.trial.duration_seconds / 60} min / video` : "VOD i plotë"))];
  $("automation-description").textContent = feeds.length ? `${feeds.length} kanale të ndjekura. ${durations.join(" · ")}. ${s.settings.publishing_enabled ? "Publikimi automatik aktiv." : "Pa publikim automatik."}` : "Shto kanale Kick që Boti të marrë VOD-et e reja vetë.";
  $("automation-channels").innerHTML = feeds.slice(0, 8).map(feed => `<span>${esc(feed.data.channel)}</span>`).join("") + (feeds.length > 8 ? `<span>+${feeds.length - 8}</span>` : "");
  $("automation-backlog").textContent = `${feeds.reduce((sum, feed) => sum + feed.pending, 0)} video në pritje · ${s.disk.free_gb} GB të lira`;
  if (currentView === "dashboard") loadRecent();
}

document.addEventListener("click", event => {
  const button = event.target.closest("button");
  if (!button) return;
  if (button.dataset.view) {
    if ($("dialog").open) $("dialog").close();
    showView(button.dataset.view);
    if (button.dataset.section) $(button.dataset.section)?.scrollIntoView({behavior:"smooth", block:"start"});
  }
  if (button.dataset.clipFilter) {
    clipFilter = button.dataset.clipFilter; clipOffset = 0;
    document.querySelectorAll("[data-clip-filter]").forEach(item => { const active = item === button; item.classList.toggle("active", active); item.setAttribute("aria-pressed", String(active)); });
    loadClips();
  }
  if (button.dataset.titleEdit) {
    const clip = clips.find(item=>item.id===button.dataset.titleEdit);
    if (clip) {
      dialog(`<h2>Titulli sipër videos</h2><p class="title-edit-note">Ndrysho titullin e këtij klipi. Krijohet version i ri me renderim dhe kontroll të ri cilësie.</p><form id="clip-title-form"><label>Teksti i titullit<textarea id="clip-title-text" rows="3" maxlength="120" required>${esc(clip.metadata.top_title_override || clip.metadata.title)}</textarea></label><p class="muted">Madhësia, sfondi dhe kohëzgjatja merren nga Konfigurimi → Titra dhe efekte.</p><button class="primary" type="submit">Ruaj dhe riedito</button></form>`);
      $("clip-title-form").onsubmit=async event=>{
        event.preventDefault();
        const submit=event.target.querySelector("button"); submit.disabled=true;
        try {
          await api(`/api/clips/${encodeURIComponent(clip.id)}/title`,{method:"POST",body:{text:$("clip-title-text").value,expected_version:clip.current_version}});
          $("dialog").close(); librarySignature=""; recentChecked=0; await loadClips(); await refresh();
          notify("Titulli u ruajt; klipi po rieditohet.");
        } catch(error){notify(error.message,true);}
        finally{submit.disabled=false;}
      };
    }
  }
  if (button.dataset.refine) {
    const clip = clips.find(item=>item.id===button.dataset.refine);
    if (clip) {
      dialog(`<h2>Përmirëso prerjen</h2><p>${esc(clip.metadata.title)}</p><p>Kërkon fillim dhe fund më të plotë të fjalisë, brenda kufijve të fushatës. Versioni i vjetër ruhet dhe rezultati kalon sërish në kontroll cilësie.</p><p class="muted">Nëse nuk ka prerje më të përshtatshme, videoja mbetet e njëjtë. Renderimi pret nëse disku është nën prag.</p><button class="primary" id="confirm-refine">Kërko prerje më të mirë</button>`);
      $("confirm-refine").onclick=async()=>{
        const submit=$("confirm-refine");submit.disabled=true;
        try {
          const result=await api(`/api/clips/${encodeURIComponent(clip.id)}/refine`,{method:"POST",body:{expected_version:clip.current_version}});
          $("dialog").close();librarySignature="";recentChecked=0;await loadClips();await refresh();
          notify(result.changed?"Prerja u përmirësua; versioni i ri pret renderimin.":"Prerja aktuale përputhet me kufijtë e përshtatshëm të fjalisë.");
        }catch(error){notify(error.message,true);}
        finally{submit.disabled=false;}
      };
    }
  }
  if (button.dataset.openClip) {
    const clip = recentClips.find(item => item.id === button.dataset.openClip);
    if (clip) { dialog(`<div class="preview-dialog"><p class="eyebrow">${esc(clipSource(clip))}</p><h2>${esc(clip.metadata.title)}</h2>${badge(clip.status)}${clipPreview(clip)}<p>${esc(clip.quality_reason || "Kontrolli i cilësisë nuk ka përfunduar.")}</p>${safeVideoUrl(clip) ? `<a class="secondary" href="${esc(safeVideoUrl(clip))}" download="boti-${esc(clip.id)}.mp4">↓ Shkarko MP4</a>` : ""}<button class="text-button" data-view="clips">Hap bibliotekën ↗</button></div>`); }
  }
  if (button.id === "retry-recent") { recentChecked = 0; loadRecent(); }
});
$("toggle-offline").onclick = () => { showOffline = !showOffline; if (snapshot) { renderWorkers(snapshot); renderRouting(snapshot); } };
$("job-filter").onchange = () => { if (snapshot) renderJobs(snapshot); };
$("open-ingest").onclick = () => { $("ingest-panel").open = true; $("ingest-panel").scrollIntoView({behavior:"smooth", block:"center"}); $("vod-url").focus({preventScroll:true}); };
let searchTimer;
$("clip-search").oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { clipOffset = 0; loadClips(); }, 250); };
$("dialog").addEventListener("close", hydratePreviews);
document.addEventListener("play", event => { if (event.target.tagName === "VIDEO") document.querySelectorAll("video").forEach(video => { if (video !== event.target) video.pause(); }); }, true);
window.addEventListener("hashchange", () => { if (!$("studio").hidden) showView(location.hash.slice(1), false); });
