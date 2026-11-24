"use strict";

const defaultEditing = {caption_color:"classic",caption_size:"normal",caption_position:"lower",caption_motion:"none",color_enhance:false,progress_bar:false,vignette:"off",background_dim:false,title_enabled:false,title_text:"",title_size:"normal",title_appearance:"box",title_seconds:4,title_full:false};
const socialEditing = {...defaultEditing,caption_color:"yellow",caption_size:"large",caption_position:"lower",caption_motion:"pop",color_enhance:true,progress_bar:true,title_enabled:true};
const proEditing = {...socialEditing,vignette:"soft",background_dim:true,title_size:"normal",title_appearance:"outline",title_seconds:4};
const editingFields = {"edit-color":"caption_color","edit-size":"caption_size","edit-position":"caption_position","edit-motion":"caption_motion","edit-enhance":"color_enhance","edit-progress":"progress_bar","edit-vignette":"vignette","edit-background-dim":"background_dim","edit-title-enabled":"title_enabled","edit-title-text":"title_text","edit-title-size":"title_size","edit-title-appearance":"title_appearance","edit-title-seconds":"title_seconds","edit-title-full":"title_full"};

function loadEditingControls(value) {
  const editing = {...defaultEditing,...value};
  Object.entries(editingFields).forEach(([id,key]) => { if ($(id).type === "checkbox") $(id).checked = editing[key]; else $(id).value = editing[key]; });
  previewEditing();
}

function readEditingControls() {
  return Object.fromEntries(Object.entries(editingFields).map(([id,key]) => [key,$(id).type === "checkbox" ? $(id).checked : $(id).type === "number" ? Number($(id).value) : $(id).value]));
}

function previewEditing() {
  const style = readEditingControls();
  const preview = $("editing-controls").querySelector(".edit-preview");
  preview.style.setProperty("--caption-accent", {classic:"#ffe866",yellow:"#ffdf00",lime:"#d5ef99",cyan:"#7ad4ff"}[style.caption_color]);
  preview.classList.toggle("caption-large", style.caption_size === "large");
  preview.classList.toggle("caption-middle", style.caption_position === "middle");
  preview.classList.toggle("caption-pop", style.caption_motion === "pop");
  preview.classList.toggle("has-progress", style.progress_bar);
  preview.classList.toggle("has-vignette", style.vignette === "soft");
  preview.classList.toggle("dim-background", style.background_dim);
  $("edit-title-preview").hidden = !style.title_enabled;
  $("edit-title-preview").textContent = style.title_text.trim() || "Momenti më i mirë";
  preview.classList.toggle("has-title", style.title_enabled);
  preview.classList.toggle("title-outline", style.title_appearance === "outline");
  preview.classList.toggle("title-large", style.title_size === "large");
  $("edit-title-seconds").disabled = style.title_full;
}

function renderEditReadiness(s) {
  const workers = s.workers.filter(w => !w.paused && s.server_time - (w.last_seen || 0) < 30 && w.capabilities.includes("render"));
  const editing = s.settings.editing || {};
  const revision = editing.vignette === "soft" || editing.background_dim ? 3 : editing.title_enabled ? 2 : 1;
  const modern = workers.filter(w => w.hardware.render_revision >= revision);
  const old = workers.filter(w => !(w.hardware.render_revision >= revision));
  $("edit-worker-status").textContent = `${modern.length} PC ${modern.length === 1 ? "mbështet" : "mbështesin"} efektet e reja.${old.length ? " Përditëso dhe rinis: " + old.map(w=>w.name).join(", ") + "." : ""} Punët me efekte merren vetëm nga një worker që i mbështet.`;
}

function renderAutomationReadiness(s) {
  const state = s.automation;
  if (!state) return;
  $("autopilot-state").textContent = state.configured ? "Konfigurimi gati" : "Ka hapa për përfundim";
  $("autopilot-state").className = "badge " + (state.configured ? "good" : "warn");
  $("autopilot-checks").innerHTML = state.checks.map(check => `<article><span class="badge ${check.ready?"good":"warn"}">${check.ready?"Gati":"Mungon"}</span><h3>${esc(check.label)}</h3><p>${esc(check.detail)}</p><button class="text-button" data-view="${esc(check.view)}" ${check.section?`data-section="${esc(check.section)}"`:""}>${check.ready?"Shiko":"Përfundo"} ↗</button></article>`).join("");
  const missing = state.platforms.filter(p => !p.ready).map(p => platformNames[p.platform]);
  $("autopilot-detail").textContent = [missing.length ? "Lidhjet që mungojnë: " + missing.join(", ") + "." : "",state.worker_updates.length ? "Për titujt dhe arkivin lokal përditëso: " + state.worker_updates.join(", ") + "." : "", ...state.campaign_issues.map(c => c.channel + ": " + c.issues.join("; ") + "."),state.note].filter(Boolean).join(" ");
}

function renderAccountSetup(s) {
  const select = $("setup-group"), signature = s.groups.map(g=>g.id+":"+g.name).join("|");
  if (select.dataset.groups !== signature) {
    const chosen = select.value;
    select.innerHTML = s.groups.map(group=>`<option value="${esc(group.id)}">${esc(group.name)}</option>`).join("");
    if (s.groups.some(group=>group.id===chosen)) select.value=chosen;
    select.dataset.groups=signature;
  }
  const group = s.groups.find(group=>group.id===select.value);
  if (!group) return;
  const content = JSON.stringify(group.accounts);
  if ($("account-onboarding").dataset.content === content) return;
  $("account-onboarding").dataset.content = content;
  const platforms = [
    {id:"tiktok",tag:"TT",url:"https://www.tiktok.com/signup",info:"Krijo profilin në TikTok dhe lidhe në Upload-Post për publikim automatik nga Boti.",mode:"Upload-Post"},
    {id:"instagram",tag:"IG",url:"https://www.instagram.com/accounts/emailsignup/",info:"Përdor profil Business/Creator dhe lidhe në Upload-Post. Lidhja direkte me Meta mbetet gjithashtu e disponueshme.",mode:"Upload-Post / Meta"},
    {id:"youtube",tag:"YT",url:"https://www.youtube.com/create_channel",info:"Krijo kanalin YouTube. Mund ta lidhësh përmes Upload-Post ose Google OAuth direkt.",mode:"Upload-Post / OAuth"},
    {id:"x",tag:"X",url:"https://x.com/i/flow/signup",info:"Krijo profilin në X dhe lidhe në Upload-Post. Publikimi përdor radhën dhe kufijtë e këtij grupi.",mode:"Upload-Post"}
  ];
  $("account-onboarding").innerHTML = platforms.map(platform=>{
    const account = group.accounts.find(account=>account.platform===platform.id);
    const connected = account?.status === "connected" || (platform.id === "tiktok" && account?.name);
    return `<article class="onboarding-card"><div class="onboarding-top"><span class="platform-icon">${platform.tag}</span><h3>${platformNames[platform.id]}</h3><span class="badge ${connected?"good":"warn"}">${connected?account?.status==="connected"?"Lidhur":"Emri u ruajt":"Pa lidhje"}</span></div><p>${platform.info}</p><small>${esc(account?.name || platform.mode)}</small><a href="${platform.url}" target="_blank" rel="noopener noreferrer">1. Krijo / hap llogarinë ↗</a><button class="secondary" data-setup-connect="${esc(account?.id || "")}" data-platform="${platform.id}" data-group="${esc(group.id)}" ${!account?"disabled":""}>2. ${connected?"Shiko lidhjen":"Lidhe me Botin"}</button></article>`;
  }).join("");
}

$("setup-group").onchange = () => { if (snapshot) renderAccountSetup(snapshot); };
Object.keys(editingFields).forEach(id=>$(id).addEventListener("change",previewEditing));
$("apply-pro-style").onclick = () => { $("setting-captions").value="highlight";loadEditingControls(proEditing);notify("Stili Pro i lehtë u zgjodh. Ruaje konfigurimin për klipet e reja."); };
$("apply-social-style").onclick = () => { $("setting-captions").value="highlight";loadEditingControls(socialEditing);notify("Stili Social u zgjodh. Shtyp Ruaj konfigurimin për ta përdorur."); };
document.addEventListener("click",event=>{
  const button=event.target.closest("[data-setup-connect]");
  if (button?.dataset.setupConnect) connectAccount(button.dataset.setupConnect,button.dataset.platform,button.dataset.group);
});
