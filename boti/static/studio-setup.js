"use strict";

const defaultEditing = {caption_color:"classic",caption_size:"normal",caption_position:"lower",caption_motion:"none",color_enhance:false,progress_bar:false};
const socialEditing = {caption_color:"yellow",caption_size:"large",caption_position:"lower",caption_motion:"pop",color_enhance:true,progress_bar:true};
const editingFields = {"edit-color":"caption_color","edit-size":"caption_size","edit-position":"caption_position","edit-motion":"caption_motion","edit-enhance":"color_enhance","edit-progress":"progress_bar"};

function loadEditingControls(value) {
  const editing = {...defaultEditing,...value};
  Object.entries(editingFields).forEach(([id,key]) => { if ($(id).type === "checkbox") $(id).checked = editing[key]; else $(id).value = editing[key]; });
  previewEditing();
}

function readEditingControls() {
  return Object.fromEntries(Object.entries(editingFields).map(([id,key]) => [key,$(id).type === "checkbox" ? $(id).checked : $(id).value]));
}

function previewEditing() {
  const style = readEditingControls();
  const preview = $("editing-controls").querySelector(".edit-preview");
  preview.style.setProperty("--caption-accent", {classic:"#ffe866",yellow:"#ffdf00",lime:"#d5ef99",cyan:"#7ad4ff"}[style.caption_color]);
  preview.classList.toggle("caption-large", style.caption_size === "large");
  preview.classList.toggle("caption-middle", style.caption_position === "middle");
  preview.classList.toggle("caption-pop", style.caption_motion === "pop");
  preview.classList.toggle("has-progress", style.progress_bar);
}

function renderEditReadiness(s) {
  const workers = s.workers.filter(w => !w.paused && s.server_time - (w.last_seen || 0) < 30 && w.capabilities.includes("render"));
  const modern = workers.filter(w => w.hardware.render_revision >= 1);
  const old = workers.filter(w => !(w.hardware.render_revision >= 1));
  $("edit-worker-status").textContent = `${modern.length} PC ${modern.length === 1 ? "mbështet" : "mbështesin"} efektet e reja.${old.length ? " Përditëso dhe rinis: " + old.map(w=>w.name).join(", ") + "." : ""} Punët me efekte merren vetëm nga një worker që i mbështet.`;
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
    {id:"tiktok",tag:"TT",url:"https://www.tiktok.com/signup",info:"Krijo profilin në TikTok. Boti ruan emrin dhe përgatit MP4 + përshkrimin; postimin e përfundon në TikTok.",mode:"Postim manual"},
    {id:"instagram",tag:"IG",url:"https://www.instagram.com/accounts/emailsignup/",info:"Për lidhjen aktuale të Botit përdor profil Business/Creator të lidhur me Facebook Page. Lidhja verifikohet me Instagram ID dhe Page access token.",mode:"Kërkon Meta API"},
    {id:"youtube",tag:"YT",url:"https://www.youtube.com/create_channel",info:"Krijo kanalin YouTube. Boti përdor autorizimin Google OAuth me Desktop client nga projekti yt Google Cloud.",mode:"Google OAuth"}
  ];
  $("account-onboarding").innerHTML = platforms.map(platform=>{
    const account = group.accounts.find(account=>account.platform===platform.id);
    const connected = account?.status === "connected" || (platform.id === "tiktok" && account?.name);
    return `<article class="onboarding-card"><div class="onboarding-top"><span class="platform-icon">${platform.tag}</span><h3>${platformNames[platform.id]}</h3><span class="badge ${connected?"good":"warn"}">${connected?platform.id==="tiktok"?"Emri u ruajt":"Lidhur":"Pa lidhje"}</span></div><p>${platform.info}</p><small>${esc(account?.name || platform.mode)}</small><a href="${platform.url}" target="_blank" rel="noopener noreferrer">1. Krijo / hap llogarinë ↗</a><button class="secondary" data-setup-connect="${esc(account?.id || "")}" data-platform="${platform.id}" data-group="${esc(group.id)}" ${!account?"disabled":""}>2. ${connected?"Shiko lidhjen":"Lidhe me Botin"}</button></article>`;
  }).join("");
}

$("setup-group").onchange = () => { if (snapshot) renderAccountSetup(snapshot); };
Object.keys(editingFields).forEach(id=>$(id).addEventListener("change",previewEditing));
$("apply-social-style").onclick = () => { $("setting-captions").value="highlight";loadEditingControls(socialEditing);notify("Stili Social u zgjodh. Shtyp Ruaj konfigurimin për ta përdorur."); };
document.addEventListener("click",event=>{
  const button=event.target.closest("[data-setup-connect]");
  if (button?.dataset.setupConnect) connectAccount(button.dataset.setupConnect,button.dataset.platform,button.dataset.group);
});
