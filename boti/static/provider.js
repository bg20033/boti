"use strict";
function openProviderSetup(groupId) {
  const group=snapshot?.groups.find(g=>g.id===groupId);
  if(!group)return;
  dialog(`<h2>Lidh publikimin automatik</h2><p>${esc(group.name)} · TikTok, Instagram, YouTube dhe X</p><p>Lidhi së pari llogaritë në <a href="https://app.upload-post.com" target="_blank" rel="noopener noreferrer">Upload-Post ↗</a>. Vendose çelësin vetëm këtu; ruhet i enkriptuar.</p><form id="provider-form"><label>API key<input id="provider-key" type="password" autocomplete="off" required minlength="10"></label><label>Emri i profilit në Upload-Post<input id="provider-profile" required maxlength="100" placeholder="Emri i saktë te Manage users"></label><fieldset><legend>Platformat që i ke lidhur</legend>${["tiktok","instagram","youtube","x"].map(p=>`<label class="checkbox-row"><input name="provider-platform" type="checkbox" value="${p}" checked> ${platformNames[p]}</label>`).join("")}</fieldset><div class="settings-grid"><label>Maksimum postime / llogari / ditë<input id="provider-daily" type="number" value="3" min="1" max="100" required></label><label>Intervali minimal / minuta<input id="provider-interval" type="number" value="60" min="1" max="1440" required></label><label>Privatësia YouTube<select id="provider-youtube"><option value="private">Private · për provën e parë</option><option value="unlisted">Unlisted</option><option value="public">Public</option></select></label><label>Privatësia TikTok<select id="provider-tiktok"><option value="SELF_ONLY">Vetëm unë · për provën e parë</option><option value="PUBLIC_TO_EVERYONE">Publike</option><option value="FOLLOWER_OF_CREATOR">Ndjekësit</option><option value="MUTUAL_FOLLOW_FRIENDS">Miqtë</option></select></label></div><p class="muted">Instagram dhe X publikojnë në profilin e lidhur kur radha aktivizohet. TikTok duhet ta lejojë privatësinë e zgjedhur. Një dërgim në drafts nuk shënohet si postim publik.</p><button class="primary" type="submit">Verifiko dhe lidh profilin</button></form>`);
  $("provider-form").onsubmit=async event=>{
    event.preventDefault();const submit=event.target.querySelector("button");submit.disabled=true;
    try{
      const platforms=[...document.querySelectorAll('[name="provider-platform"]:checked')].map(x=>x.value);
      if(!platforms.length)throw new Error("Zgjidh të paktën një platformë");
      await api(`/api/groups/${encodeURIComponent(groupId)}/upload-post`,{method:"POST",body:{api_key:$("provider-key").value,profile:$("provider-profile").value,platforms,daily_limit:Number($("provider-daily").value),interval_minutes:Number($("provider-interval").value),youtube_privacy:$("provider-youtube").value,tiktok_privacy:$("provider-tiktok").value}});
      $("dialog").close();$("dialog-content").innerHTML="";notify("Profili u verifikua dhe platformat u lidhën. Publikimin e aktivizon te Konfigurimi.");await refresh();
    }catch(error){notify(error.message,true);}finally{submit.disabled=false;}
  };
}
$("setup-provider").onclick=()=>openProviderSetup($("setup-group").value);
