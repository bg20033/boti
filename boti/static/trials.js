"use strict";
let trialPoll = null;
let trialReportGeneration = 0;
function trialClock(value) {
  if (!/^\d{1,3}:[0-5]\d:[0-5]\d$/.test(value)) throw new Error("Shkruaj fillimin si 00:10:00 (orë:minuta:sekonda)");
  const [h,m,s] = value.split(":").map(Number);
  return h*3600+m*60+s;
}
$("start-trial").onclick=()=>{
  const groups=[...$("target-groups").querySelectorAll("input:checked")].map(input=>input.value);
  const source={url:$("vod-url").value.trim(),file:$("inbox-file").value||null,source_label:$("source-label").value,group_ids:groups,campaign_id:$("job-campaign").value||null,review_mode:"automatic"};
  if(!groups.length){notify("Zgjidh së paku një grup",true);return;}
  if(Boolean(source.url)===Boolean(source.file)){notify("Zgjidh një link ose një skedar për provën",true);return;}
  dialog(`<h2>Provoje këtë VOD</h2><p>${esc(source.file||source.url)}</p><p>Përpunohen vetëm minutat që zgjedh. Synimi është deri në 3–5 klipe sipas cilësisë. Prova nuk publikon video.</p><form id="trial-form"><label>Fillimi (orë:minuta:sekonda)<input id="trial-start" value="00:00:00" pattern="[0-9]{1,3}:[0-5][0-9]:[0-5][0-9]" required></label><label>Kohëzgjatja<select id="trial-duration"><option value="30">30 sekonda</option><option value="60">1 minutë</option><option value="300" selected>5 minuta</option><option value="600">10 minuta</option><option value="900">15 minuta</option></select></label><label>Maksimumi i klipeve<select id="trial-limit"><option value="3">3 klipe</option><option value="4">4 klipe</option><option value="5" selected>5 klipe</option></select></label><p class="muted">Nëse mungojnë agjentët për AI/editim, prova pret lidhjen e tyre. Pragu i cilësisë ruhet.</p><button class="primary" type="submit">Nis provën</button></form>`);
  $("trial-form").onsubmit=async(event)=>{
    event.preventDefault();const button=event.target.querySelector("button");button.disabled=true;
    try{const result=await api("/api/jobs",{method:"POST",body:{...source,trial:{start_seconds:trialClock($("trial-start").value),duration_seconds:Number($("trial-duration").value),max_clips:Number($("trial-limit").value)}}});await refresh();await openTrialReport(result.id);}
    catch(error){notify(error.message,true);button.disabled=false;}
  };
};
async function openTrialReport(id){
  clearInterval(trialPoll);
  const generation = ++trialReportGeneration;
  dialog('<h2>Raporti i provës</h2><div id="trial-report">Duke lexuar…</div>');
  let signature="";
  async function update(){
    if(generation !== trialReportGeneration)return;
    if(!$("trial-report")||!$("dialog").open){clearInterval(trialPoll);return;}
    try{
      const r=await api(`/api/jobs/${encodeURIComponent(id)}/trial-report`);
      if(generation !== trialReportGeneration||!$("trial-report")||!$("dialog").open)return;
      // Preserve playing video nodes while only elapsed time changes.
      const state=JSON.stringify({...r,elapsed_seconds:0,processing_seconds:0});
      if(state!==signature){
        signature=state;
        $("trial-report").innerHTML=`<p>${badge(r.status)} · ${r.range.seconds}s nga sekonda ${r.range.start} · maksimumi ${r.clip_limit} klipe</p><p id="trial-times"></p><p>${r.counts.ready} kaluan · ${r.counts.skipped} u anashkaluan · ${r.counts.technical_failed} gabime teknike</p>${r.error?`<pre>${esc(r.error)}</pre>`:""}${!["complete","failed"].includes(r.status)&&r.readiness.reasons.length?`<p class="quality-hold">${r.readiness.reasons.map(esc).join(" · ")}</p>`:""}<p class="muted">${esc(r.note)}</p><a class="secondary" href="/api/jobs/${encodeURIComponent(id)}/trial-report" download="boti-prova-${id}.json">Shkarko raportin JSON</a><details><summary>Detyrat dhe gabimet</summary>${r.tasks.map(t=>`<p>${esc(status(t.kind))} · ${esc(status(t.status))} · ${t.attempts} përpjekje${t.error?` · ${esc(t.error)}`:""}</p>`).join("")||"Prova pret nisjen."}</details><div class="trial-clips">${r.clips.map(c=>`<article><h3>${esc(c.title)} · ${badge(c.status)}</h3>${c.video_url?`<video controls preload="none" playsinline src="/api/clips/${encodeURIComponent(c.id)}/video" aria-label="${esc(c.title)}"></video>`:""}<p>${c.source_start.toFixed(1)}–${c.source_end.toFixed(1)}s në burim · ${c.score}/100 · v${c.version}</p><p>${esc(c.selection_reason)}</p><p>${esc(c.quality_reason||"")}</p><details><summary>Kontrollet e cilësisë</summary>${c.quality_reports.map(q=>`<p>v${q.version}: ${q.passed?"Kaloi":"Nuk kaloi"} · ${esc((q.result.verdict?.reasons||[]).join("; "))}</p>`).join("")}</details></article>`).join("")||`<p>${r.status==="complete"?"Nuk u gjetën klipe që kalojnë pragun në këtë interval.":"Klipet shfaqen pas përzgjedhjes."}</p>`}</div>`;
      }
      $("trial-times").textContent=`Koha nga kërkesa: ${elapsed(r.elapsed_seconds)} · Nga nisja e përpunimit: ${r.processing_seconds===null?"ende s’ka nisur":elapsed(r.processing_seconds)}`;
      if(["complete","failed"].includes(r.status))clearInterval(trialPoll);
    }catch(error){if(generation !== trialReportGeneration)return;clearInterval(trialPoll);notify(error.message,true);}
  }
  trialPoll=setInterval(update,5000);
  await update();
}
