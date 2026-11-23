"use strict";

let feedRows = [];

function renderFeeds(rows) {
  feedRows = rows;
  $("feed-list").innerHTML = rows.map(row => {
    const config = row.data;
    const checked = row.checked_at ? ago(row.checked_at) : "Ende pa kontroll";
    const mode = config.trial ? `Provë ${config.trial.duration_seconds}s` : "VOD i plotë";
    return `<article class="feed-row">
      <div class="section-title"><strong>${esc(config.channel)}</strong>
        <span class="badge ${config.enabled ? "good" : ""}">${config.enabled ? "Aktiv" : "Pezulluar"}</span></div>
      <p class="muted">${esc(mode)} · çdo ${config.interval_seconds / 60} min · ${row.pending} në pritje · ${row.imported} të shtuara</p>
      <p class="muted">Kontrolli i fundit ${esc(checked)}</p>
      ${row.error ? `<p class="quality-hold">${esc(row.error)}</p>` : ""}
      <div class="feed-actions">
        <button class="text-button" data-feed-edit="${esc(row.id)}">Ndrysho</button>
        <button class="text-button" data-feed-toggle="${esc(row.id)}">${config.enabled ? "Pezullo" : "Vazhdo"}</button>
        <button class="text-button" data-feed-check="${esc(row.id)}">Kontrollo tani</button>
        <button class="text-button" data-feed-remove="${esc(row.id)}">Hiq ndjekjen</button>
      </div>
    </article>`;
  }).join("") || '<p class="muted">Shto kanalet që do të furnizojnë radhën me video.</p>';
}

async function feedForm(identity) {
  const campaigns = await api("/api/campaigns");
  const row = feedRows.find(item => item.id === identity);
  const config = row?.data || {
    channel: "", campaign_id: null, group_ids: [snapshot.groups[0]?.id],
    enabled: true, include_existing: true, interval_seconds: 300, pending_limit: 2, trial: null
  };
  dialog(`<h2>${row ? "Ndrysho ndjekjen" : "Shto kanale Kick"}</h2>
    <form id="feed-form">
      <label>${row ? "Kanali" : "Kanalet, një për rresht ose të ndara me presje"}
        <textarea id="feed-channels" rows="3" required ${row ? "readonly" : ""}
          placeholder="deenthegreat&#10;gmhikaru">${esc(config.channel)}</textarea></label>
      <label>Fushata<select id="feed-campaign"><option value="">Pa fushatë</option>
        ${campaigns.map(c => `<option value="${esc(c.id)}" ${config.campaign_id === c.id ? "selected" : ""}>${esc(c.data.name)}</option>`).join("")}
      </select></label>
      <label>Grupet destinacion</label><div id="feed-groups" class="target-groups">
        ${snapshot.groups.filter(g => !g.paused).map(g => `<label><input type="checkbox" value="${esc(g.id)}" ${config.group_ids.includes(g.id) ? "checked" : ""}>${esc(g.name)}</label>`).join("")}
      </div>
      <div class="settings-grid">
        <label>Kontrollo çdo / minuta<input id="feed-interval" type="number" min="1" max="1440" value="${config.interval_seconds / 60}" required></label>
        <label>Kufiri i videove në radhë<input id="feed-limit" type="number" min="1" max="20" value="${config.pending_limit}" required></label>
      </div>
      <label>Përpunimi<select id="feed-mode">
        <option value="full" ${!config.trial ? "selected" : ""}>VOD i plotë</option>
        <option value="trial" ${config.trial ? "selected" : ""}>Provë me segment, pa publikim</option>
      </select></label>
      <div id="feed-trial" class="settings-grid" ${!config.trial ? "hidden" : ""}>
        <label>Fillimi / sekonda<input id="feed-start" type="number" min="0" max="604800" value="${config.trial?.start_seconds ?? 60}"></label>
        <label>Kohëzgjatja / sekonda<input id="feed-duration" type="number" min="30" max="900" value="${config.trial?.duration_seconds ?? 180}"></label>
      </div>
      <label><input id="feed-history" type="checkbox" ${config.include_existing ? "checked" : ""}> Merr edhe VOD-et ekzistuese në kontrollin e parë</label>
      <label><input id="feed-enabled" type="checkbox" ${config.enabled ? "checked" : ""}> Aktivizo ndjekjen</label>
      <p id="feed-result" class="quality-hold" hidden></p>
      <button class="primary" type="submit">Ruaj ndjekjen</button>
    </form>`);
  $("feed-mode").onchange = () => { $("feed-trial").hidden = $("feed-mode").value !== "trial"; };
  $("feed-campaign").onchange = () => {
    const campaign = campaigns.find(c => c.id === $("feed-campaign").value);
    document.querySelectorAll("#feed-groups input").forEach(input => {
      input.disabled = Boolean(campaign && !campaign.data.group_ids.includes(input.value));
      if (input.disabled) input.checked = false;
    });
  };
  $("feed-campaign").dispatchEvent(new Event("change"));
  $("feed-form").onsubmit = async event => {
    event.preventDefault();
    const button = event.target.querySelector('button[type="submit"]');
    button.disabled = true;
    const failures = [];
    try {
      const channels = [...new Set($("feed-channels").value.split(/[\n,]+/).map(s => s.trim()).filter(Boolean))];
      const body = {
        campaign_id: $("feed-campaign").value || null,
        group_ids: [...document.querySelectorAll("#feed-groups input:checked")].map(input => input.value),
        enabled: $("feed-enabled").checked, include_existing: $("feed-history").checked,
        interval_seconds: Number($("feed-interval").value) * 60, pending_limit: Number($("feed-limit").value),
        trial: $("feed-mode").value === "trial" ? {
          start_seconds: Number($("feed-start").value),
          duration_seconds: Number($("feed-duration").value),
          max_clips: config.trial?.max_clips ?? 3
        } : null
      };
      for (const channel of channels) {
        try {
          await api(identity ? `/api/feeds/${identity}` : "/api/feeds", {
            method: identity ? "PUT" : "POST", body: {...body, channel}
          });
        } catch (error) { failures.push({channel, message: error.message}); }
      }
      await refresh();
      if (failures.length) {
        $("feed-channels").value = failures.map(item => item.channel).join("\n");
        $("feed-result").textContent = failures.map(item => `${item.channel} — ${item.message}`).join(" · ");
        $("feed-result").hidden = false;
      } else {
        $("dialog").close();
        notify("Ndjekja automatike u ruajt");
      }
    } catch (error) { notify(error.message, true); }
    finally { button.disabled = false; }
  };
}

$("add-feed").onclick = () => feedForm().catch(error => notify(error.message, true));
document.addEventListener("click", async event => {
  const data = event.target.closest("button")?.dataset;
  if (!data) return;
  try {
    if (data.feedEdit) await feedForm(data.feedEdit);
    if (data.feedToggle) {
      const row = feedRows.find(item => item.id === data.feedToggle);
      await api(`/api/feeds/${row.id}`, {method: "PUT", body: {...row.data, enabled: !row.data.enabled}});
      await refresh();
    }
    if (data.feedCheck) {
      await api(`/api/feeds/${data.feedCheck}/check`, {method: "POST"});
      notify("Kontrolli u kërkua; fillon sapo radha dhe disku e lejojnë");
    }
    if (data.feedRemove) {
      await api(`/api/feeds/${data.feedRemove}`, {method: "DELETE"});
      await refresh();
    }
  } catch (error) { notify(error.message, true); }
});
