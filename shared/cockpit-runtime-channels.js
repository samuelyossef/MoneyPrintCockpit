/* Mapeamento, sincronização e conexão de canais. Script clássico: define window.CKRuntimeChannels. */
(function () {
  window.CKRuntimeChannels = function (deps) {
    const request = deps.request;
    const esc = deps.esc;
    const screenFormat = deps.screenFormat;
    const selectedCreateKey = deps.selectedCreateKey;
    const API = deps.API;
  const CHANNEL_PROFILE_KEYS = ["language", "publish_privacy", "voice_name", "voice_rate", "voice_volume", "subtitle_enabled", "subtitle_position", "font_size", "text_fore_color", "stroke_color", "stroke_width"];
  const channelProfilePayload = channel => ({ ...Object.fromEntries(CHANNEL_PROFILE_KEYS.map(key => [key, channel[key]])), video_defaults: channel.video_defaults || {}, visual_guides: channel.visual_guides || { short: "", long: "" } });
  /* Connected channels from the Gateway replace the old mock list in place, in the shape the
     Canais / Gerador / YouTube Shorts templates already read. */
  function mapChannel(c) {
    const stats = c.stats || {};
    return { id: c.id, name: c.name, platform: c.platform === "tiktok" ? "TikTok" : "YouTube", handle: c.handle, status: c.status,
      niche: c.niche, language: c.language, publish_privacy: c.publish_privacy || "private", system_prompt: c.system_prompt || "", visual_guides: c.visual_guides || { short: "", long: "" }, voice_name: c.voice_name, voice_rate: c.voice_rate, voice_volume: c.voice_volume,
      subtitle_enabled: c.subtitle_enabled, subtitle_position: c.subtitle_position, font_size: c.font_size, text_fore_color: c.text_fore_color,
      stroke_color: c.stroke_color, stroke_width: c.stroke_width, video_defaults: c.video_defaults, followers: stats.followers || 0, videos: stats.videos || 0, views: stats.views || 0, last: "—" };
  }

  /* Refills the channel selects in place: re-rendering Gerador would wipe what the user typed. */
  function fillChannelSelects() {
    const connected = window.CK.CHANNELS.filter(c => c.status === "connected");
    const youtube = connected.filter(c => c.platform === "YouTube");
    const option = c => `<option value="${esc(c.id)}">${esc(c.name)} · ${esc(c.platform)}</option>`;
    // Long videos only go to YouTube; Shorts to any connected channel.
    [["#genChannelSelect", youtube, "Selecione um canal"], ["#ytLibChannelSelect", screenFormat(document) === "long" ? youtube : connected, "Selecione um canal…"], ["#agStrategyChannel", connected, "Selecione um canal…"]].forEach(([selector, list, empty]) => {
      const select = document.querySelector(selector);
      if (!select) return;
      const current = select.value;
      select.innerHTML = `<option value="">${empty}</option>` + list.map(option).join("");
      const saved = selector === "#genChannelSelect" ? localStorage.getItem(selectedCreateKey("long")) : "";
      select.value = list.some(c => c.id === current) ? current : list.some(c => c.id === saved) ? saved : "";
      select.dispatchEvent(new Event("change"));
    });
    const list = document.querySelector("#genChannelList");
    if (!list) return;
    const checked = new Set([...list.querySelectorAll("input:checked")].map(input => input.value));
    const primary = localStorage.getItem(selectedCreateKey("short"));
    if (primary && connected.some(channel => channel.id === primary)) checked.add(primary);
    list.innerHTML = connected.length
      ? connected.map(c => `<label style="display:flex;align-items:center;gap:7px;font-size:13px"><input type="checkbox" class="cbx" name="genChannel" value="${esc(c.id)}" data-platform="${esc(c.platform)}" ${checked.has(c.id) ? "checked" : ""} /> ${esc(c.name)} · ${esc(c.platform)}</label>`).join("")
      : `<div class="card-note" style="font-family:inherit">Nenhum canal conectado ainda.</div>`;
    list.dispatchEvent(new Event("change"));
  }

  async function syncChannels(checkPending) {
    if (!window.CK || !window.CK.CHANNELS) return;
    try {
      let channels = await request("/channels");
      const pending = channels.filter(c => c.platform === "tiktok" && c.status === "pending");
      if (checkPending && pending.length) {
        await Promise.all(pending.map(c => request(`/channels/${c.id}/refresh`, { method: "POST" }).catch(() => null)));
        channels = await request("/channels");
      }
      window.CK.CHANNELS.length = 0;
      window.CK.CHANNELS.push(...channels.map(mapChannel));
    } catch (_) { return; /* Gateway offline: keep the last known list. */ }
    fillChannelSelects();
    // Canais list/detail have no free-text form to lose, so they are safe to repaint.
    if (window.CK.__refresh && (document.querySelector("#chBody") || document.querySelector("[data-ch-act]") || document.querySelector("#createRoot"))) window.CK.__refresh();
  }
  syncChannels();
  // Coming back from the Google / Upload-Post login tab is when a new channel shows up.
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") syncChannels(true); });

  function openIn(win, url) {
    if (win && !win.closed) { win.opener = null; win.location.href = url; }
    else window.open(url, "_blank", "noopener");
  }
  const youtubeConnectUrl = rec => `${API}/channels/youtube/connect?${new URLSearchParams({ niche: rec.niche || "", system_prompt: rec.system_prompt || "", ...channelProfilePayload(rec), video_defaults: JSON.stringify(rec.video_defaults || {}), visual_guides: JSON.stringify(rec.visual_guides || {}) })}`;

  async function connectChannel(rec, win) {
    if (rec.platform !== "TikTok") { openIn(win, youtubeConnectUrl(rec)); return; }
    const result = await request("/channels/tiktok/connect", { method: "POST", body: JSON.stringify({ niche: rec.niche || "", system_prompt: rec.system_prompt || "", ...channelProfilePayload(rec) }) });
    openIn(win, result.access_url);
    await syncChannels();
  }

  async function channelAction(rec, act, win) {
    if (act === "disconnect") { await request(`/channels/${rec.id}`, { method: "DELETE" }); return syncChannels(); }
    if (act === "refresh") { await request(`/channels/${rec.id}/refresh`, { method: "POST" }); return syncChannels(); }
    if (rec.platform !== "TikTok") { openIn(win, youtubeConnectUrl(rec)); return; }
    const link = await request(`/channels/${rec.id}/tiktok-link`, { method: "POST" });
    openIn(win, link.access_url);
  }

  async function saveChannel(id, patch) {
    await request(`/channels/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
    await syncChannels();
  }
    return { mapChannel, fillChannelSelects, syncChannels, connectChannel, channelAction, saveChannel };
  };
})();
