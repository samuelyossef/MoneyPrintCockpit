/* Same-origin browser adapter. It never receives provider credentials. */
(function () {
  const API = "/api";
  let activeProject = null;
  let activeJob = null;
  let stream = null;

  async function request(path, options) {
    const response = await fetch(API + path, { headers: { "Content-Type": "application/json", ...(options && options.headers) }, ...options });
    if (response.status === 204) return null;
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || "Não foi possível concluir a operação.");
    return body;
  }

  function value(body, selector, fallback) {
    const element = body.querySelector(selector);
    return element && element.value !== undefined ? element.value : fallback;
  }

  function integer(value, fallback) {
    const number = Number.parseInt(value, 10);
    return Number.isFinite(number) ? number : fallback;
  }

  function esc(text) {
    return String(text == null ? "" : text).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  /* "long" on Gerador de Vídeo, "short" on Shorts, null on screens without a generator. */
  function screenFormat(root) {
    const card = root.querySelector("[data-format]");
    return card ? card.dataset.format : null;
  }

  function selectedChannels(root) {
    const select = root.querySelector("#genChannelSelect");
    if (select) return select.value ? [select.value] : [];
    return [...root.querySelectorAll('input[name="genChannel"]:checked')].map(input => input.value);
  }

  function setPipeline(body, job) {
    const fill = body.querySelector("#genProgFill");
    const label = body.querySelector("#genProgLabel");
    const percent = body.querySelector("#genProgPct");
    const note = body.querySelector("#genPipelineNote");
    const title = body.querySelector("#genVideoTitle");
    const previewNote = body.querySelector("#genPreviewNote");
    if (job.state === "approved") job = { ...job, state: "completed" }; // same render, approval is a publishing concern
    const pct = Math.max(0, Math.min(100, job.progress || 0));
    // Turbo checkpoints (app/services/task.py): 5 script, 10 terms, 20 audio, 30 subtitle,
    // 40 stock download, 50 combining clips, 75+ final render - which is the slow part.
    const stage = pct >= 50 ? "Renderizando o vídeo (etapa mais demorada, pode levar 10–25 min)" : pct >= 40 ? "Baixando filmagens do Pexels" : pct >= 20 ? "Gerando narração e legendas" : pct >= 10 ? "Gerando palavras-chave" : "Escrevendo o roteiro com IA";
    const minutes = job.created_at ? Math.max(0, Math.floor((Date.now() - Date.parse(job.created_at)) / 60000)) : null;
    const running = job.state === "queued" || job.state === "running";
    if (fill) fill.style.width = pct + "%";
    if (percent) percent.textContent = pct + "%";
    if (title) title.textContent = job.subject || "Geração de vídeo";
    if (label) label.textContent = job.error || (running ? stage : ({ completed: "Concluído", failed: "Falhou", interrupted: "Interrompido" }[job.state] || job.state));
    if (note) note.textContent = running ? `Gerando${minutes !== null ? ` há ${minutes} min` : "…"}` : ({ completed: "Concluído", failed: "Falhou", interrupted: "Interrompido" }[job.state] || "Atualizando…");
    if (previewNote) previewNote.textContent = job.state === "completed" ? "Pronto" : (job.state === "failed" ? "Falhou" : "Gerando…");
    const steps = body.querySelectorAll(".gen-step");
    const completedSteps = job.state === "completed" ? steps.length : [10, 20, 40, 50].filter(limit => pct >= limit).length;
    steps.forEach((step, index) => {
      const icon = step.querySelector(".gen-step-ic");
      const text = step.querySelector(".gen-step-lb");
      const done = index < completedSteps || job.state === "completed";
      if (icon) { icon.style.background = done ? "var(--ok)" : "var(--surface)"; icon.style.borderColor = done ? "var(--ok)" : "var(--border)"; icon.style.color = done ? "#fff" : "var(--muted)"; }
      if (text) text.style.color = done ? "var(--ink)" : "var(--muted)";
    });
  }

  function showArtifact(body, job) {
    const artifact = (job.artifacts || []).find(item => item.kind === "video");
    if (!artifact) return;
    const slot = body.querySelector("#gen-preview");
    if (slot) {
      // <image-slot> renders into a shadow root without a <slot>, so a <video> appended
      // inside it is never painted (0px tall). Swap the placeholder for a plain container.
      const holder = document.createElement("div");
      holder.id = "gen-preview";
      holder.style.cssText = (job.format === "long" ? "width:100%;aspect-ratio:16/9;" : "width:100%;max-width:300px;margin:0 auto;aspect-ratio:9/16;") + "border-radius:12px;overflow:hidden;background:#000";
      const video = document.createElement("video");
      video.controls = true;
      video.preload = "metadata";
      video.style.cssText = "width:100%;height:100%;object-fit:contain;display:block;background:#000";
      video.src = `${API}/jobs/${job.id}/artifacts/${artifact.id}/stream`;
      holder.appendChild(video);
      slot.replaceWith(holder);
    }
    const download = body.querySelector("#genDownloadBtn");
    if (download) {
      download.disabled = false;
      download.onclick = () => { window.location.assign(`${API}/jobs/${job.id}/artifacts/${artifact.id}/download`); };
    }
    bindPublishing(body, job);
  }

  /* Review → approve → publish, mirroring Lumen's approval-first flow. Handlers are
     reassigned (not added) so repeated showArtifact calls don't stack them. */
  function channelById(id) { return ((window.CK && window.CK.CHANNELS) || []).find(c => c.id === id) || null; }
  const PUB_LABELS = { pending: "aguardando publicação", uploading: "enviando…", processing: "enviado, processando no TikTok…", failed: "falhou", reconciliation_required: "resultado incerto — confira na plataforma antes de reenviar" };
  function publicationText(pub) {
    const link = pub.platform === "youtube" ? `https://youtu.be/${pub.external_id}` : (/^https?:/.test(pub.external_id || "") ? pub.external_id : "");
    if (pub.state === "published") return `publicado (privado)${pub.external_id && link ? ": " + link : ""}`;
    return `${PUB_LABELS[pub.state] || pub.state}${pub.error ? " — " + pub.error : ""}`;
  }
  function publicationLine(pub) {
    const channel = channelById(pub.channel_id);
    return `${channel ? channel.name : (pub.platform === "tiktok" ? "TikTok" : "YouTube")}: ${publicationText(pub)}`;
  }
  const OPEN_STATES = ["pending", "failed", "reconciliation_required"];
  async function publish(job, channelIds) {
    const uncertain = (job.publications || []).some(p => p.state === "reconciliation_required");
    if (uncertain && !window.confirm("Um envio anterior pode ter chegado à plataforma. Confirme que o vídeo NÃO está publicado para enviar de novo.")) return job;
    const result = await request(`/jobs/${job.id}/publish`, { method: "POST", body: JSON.stringify({ channel_ids: channelIds && channelIds.length ? channelIds : null, confirm_not_uploaded: uncertain }) });
    if (result.errors && result.errors.length) window.alert(result.errors.map(error => error.detail).join("\n"));
    return result;
  }

  function bindPublishing(body, job) {
    const $ = selector => body.querySelector(selector);
    const box = $("#genMetaBox"), publishBtn = $("#genPublishBtn");
    if (!box || !publishBtn) return;
    box.hidden = false;
    // Channels picked at generation time are pending publications; without them, use what is selected now.
    const chosen = () => (job.publications || []).some(p => p.channel_id) ? [] : selectedChannels(body);
    const states = () => {
      const pubs = job.publications || [];
      const open = pubs.filter(p => OPEN_STATES.includes(p.state)).length + chosen().length;
      const channelsLabel = `${open} ${open === 1 ? "canal" : "canais"}`;
      $("#genPublishStatus").textContent = job.state !== "approved" && !pubs.some(p => p.state !== "pending") ? "Revise e aprove antes de publicar"
        : pubs.length ? pubs.map(publicationLine).join(" · ")
        : open ? `Aprovado — pronto para publicar em ${channelsLabel}` : "Aprovado — escolha o canal";
      $("#genApproveBtn").disabled = job.state !== "completed";
      publishBtn.disabled = job.state !== "approved" || !open || pubs.some(p => p.state === "uploading");
      publishBtn.lastChild.textContent = open ? ` Publicar em ${channelsLabel}` : " Publicar (privado)";
    };
    const fill = () => {
      const meta = job.metadata || {};
      $("#genMetaTitle").value = meta.title || ""; $("#genMetaDescription").value = meta.description || ""; $("#genMetaTags").value = (meta.tags || []).join(", ");
    };
    const save = () => request(`/jobs/${job.id}/metadata`, { method: "PUT", body: JSON.stringify({ title: $("#genMetaTitle").value, description: $("#genMetaDescription").value, tags: $("#genMetaTags").value }) });
    const act = (button, task, refill) => {
      button.onclick = async () => {
        button.disabled = true;
        try { job = await task(); if (refill) fill(); }
        catch (error) { window.alert(error.message); job = await request(`/jobs/${job.id}`).catch(() => job); }
        states();
        if (job.state === "approved") syncVideos();
      };
    };
    act($("#genMetaAiBtn"), () => request(`/jobs/${job.id}/metadata/generate`, { method: "POST" }), true);
    act($("#genMetaSaveBtn"), save, true);
    act($("#genApproveBtn"), async () => { await save(); return request(`/jobs/${job.id}/approve`, { method: "POST" }); }, false);
    act(publishBtn, () => publish(job, chosen()), false);
    const picker = $("#genChannelSelect") || $("#genChannelList");
    if (picker) picker.onchange = () => setTimeout(states); // after the one-per-platform listener adjusts the boxes
    fill();
    states();
  }

  async function projectFor(body) {
    const name = value(body, "#genProjectName", "Meu projeto").trim() || "Meu projeto";
    const defaults = { video_language: value(body, "#genLanguageSelect", "pt-BR"), voice_name: value(body, "#genVoiceSelect", "pt-BR-FranciscaNeural") };
    if (activeProject && activeProject.name === name) return activeProject;
    const projects = await request("/projects");
    const found = projects.find(project => project.name === name);
    if (found) { activeProject = found; return found; }
    activeProject = await request("/projects", { method: "POST", body: JSON.stringify({ name, language: defaults.video_language, defaults, publish: { auto_publish: false, platforms: [], youtube_privacy: "private" } }) });
    return activeProject;
  }

  function watch(body, job, notify) {
    if (stream) stream.close();
    activeJob = job;
    setPipeline(body, job);
    stream = new EventSource(`${API}/jobs/${job.id}/events`);
    stream.addEventListener("job.updated", event => {
      const current = JSON.parse(event.data);
      activeJob = current;
      setPipeline(body, current);
      if (current.state === "completed") {
        showArtifact(body, current); notify("Vídeo concluído e salvo no projeto."); stream.close();
        syncVideos().then(() => { if (window.CK.__go) setTimeout(() => window.CK.__go("overview"), 1500); });
      }
      if (current.state === "failed" || current.state === "interrupted") { notify(current.error || "A geração não foi concluída."); stream.close(); syncVideos(); }
    });
    // No onerror close: EventSource reconnects by itself (e.g. Gateway restart) and the
    // server replays the current job state on connect, so a blip no longer freezes the bar.
  }

  /* Re-attaches the progress panel to an in-flight job after switching screens or
     reloading the page, instead of showing "Aguardando início 0%" while Turbo renders. */
  function resumeActiveJob() {
    const body = document.getElementById("viewBody");
    if (!body || !body.querySelector("#genProgFill") || !lastNotify) return;
    const format = screenFormat(body);
    const running = job => job.format === format && (job.state === "queued" || job.state === "running");
    const live = activeJob && running(activeJob) ? activeJob : (window.CK && window.CK.PENDING_JOBS || []).find(running);
    if (!live) { showLatestJob(body); return; }
    if (stream && stream.readyState !== EventSource.CLOSED && activeJob && activeJob.id === live.id) setPipeline(body, activeJob);
    else watch(body, live, lastNotify);
  }
  let lastNotify = null;

  /* With nothing generating, show the most recent finished video in the pipeline and
     player instead of an empty "Aguardando início 0%" panel. */
  function showLatestJob(body) {
    const latest = window.CK && window.CK.LATEST_JOB && window.CK.LATEST_JOB[screenFormat(body)];
    const slot = body.querySelector("#gen-preview");
    if (!latest || !slot || slot.querySelector("video")) return;
    setPipeline(body, latest);
    showArtifact(body, latest);
  }

  /* Paints the Gerador "Jobs em andamento / com falha" card in place, so a status change
     shows up without re-rendering the whole form the user may be filling in. */
  function renderPendingJobs() {
    const box = document.getElementById("genPendingJobs");
    if (!box) return;
    const format = screenFormat(document);
    const jobs = ((window.CK && window.CK.PENDING_JOBS) || []).filter(job => job.format === format);
    const labels = { queued: "Na fila", running: "Gerando…", failed: "Falhou", interrupted: "Interrompido" };
    box.hidden = !jobs.length;
    box.innerHTML = jobs.length ? `
      <div class="card" style="margin-bottom:0">
        <div class="card-hd"><span class="card-title">Jobs em andamento / com falha</span></div>
        <div class="card-bd" style="display:flex;flex-direction:column;gap:2px">
          ${jobs.map(j => `
            <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--border)">
              <div style="min-width:0">
                <div style="font-weight:500;color:var(--ink);font-size:13px">${esc(j.subject)}</div>
                <div style="font-size:11.5px;color:var(--muted)">${esc(j.projectName)} · ${esc(labels[j.state] || j.state)}${j.error ? " — " + esc(j.error) : ""}</div>
              </div>
              ${j.state === "failed" || j.state === "interrupted" ? `<button class="btn sm" data-retry-job="${esc(j.id)}" style="flex-shrink:0">Repetir</button>` : ""}
            </div>`).join("")}
        </div>
      </div>` : "";
    if (box.dataset.bound) return;
    box.dataset.bound = "true";
    box.addEventListener("click", async event => {
      const btn = event.target.closest("[data-retry-job]");
      if (!btn) return;
      btn.disabled = true;
      try { await retryJob(btn.dataset.retryJob); if (lastNotify) lastNotify("Job reenviado para o Turbo"); }
      catch (error) { btn.disabled = false; if (lastNotify) lastNotify(error.message); }
    });
  }

  /* Replaces the Dashboard/History mock catalog (window.CK.VIDEOS) with real
     completed jobs from the Gateway, in the same [aspect, platform, title, channel]
     shape the existing render code already expects (plus a 5th job-id field so
     the grid's delete action can call the real API). Also fills window.CK.PENDING_JOBS
     (queued/running/failed/interrupted) so the Gerador screen can offer retry. */
  async function syncVideos() {
    if (!window.CK || !window.CK.VIDEOS) return;
    try {
      const projects = await request("/projects");
      const completed = [], pending = [];
      for (const project of projects) {
        const jobs = await request(`/projects/${project.id}/jobs`);
        for (const job of jobs) {
          if (job.state === "completed" || job.state === "approved") {
            const video = (job.artifacts || []).find(item => item.kind === "video");
            if (video) completed.push({ job, project, video });
          } else if (job.state !== "deleted" && job.state !== "deleting") {
            pending.push({ id: job.id, subject: job.subject, state: job.state, error: job.error, projectName: project.name, format: job.format, created_at: job.created_at });
          }
        }
      }
      completed.sort((a, b) => (b.job.completed_at || "").localeCompare(a.job.completed_at || ""));
      const vert = [], horiz = [];
      for (const { job, project, video } of completed) {
        const published = (job.publications || []).find(p => p.state === "published");
        const aspect = (job.request && job.request.video_aspect) || "9:16";
        const entry = [aspect, published ? published.platform : "Local", job.subject || "Vídeo gerado", project.name, job.id, video.id];
        (aspect === "9:16" ? vert : horiz).push(entry);
      }
      window.CK.VIDEOS.vert.length = 0; window.CK.VIDEOS.vert.push(...vert);
      window.CK.VIDEOS.horiz.length = 0; window.CK.VIDEOS.horiz.push(...horiz);
      window.CK.PENDING_JOBS = pending;
      const latestOf = format => (completed.find(item => item.job.format === format) || {}).job || null;
      window.CK.LATEST_JOB = { long: latestOf("long"), short: latestOf("short") };
      // Repaint only the video grid view: a full refresh on the Gerador screen would
      // wipe fields the user is typing in. Other views read the updated data on next render.
      if (window.CK.__refresh && document.querySelector("#vidBody")) window.CK.__refresh();
      renderPendingJobs();
      resumeActiveJob();
      clearTimeout(syncTimer);
      if (pending.some(job => job.state === "queued" || job.state === "running")) syncTimer = setTimeout(syncVideos, 15000);
    } catch (_) { /* Gateway/Turbo offline: keep last known videos on screen. */ }
  }
  let syncTimer = null;
  syncVideos();

  async function checkHealth() {
    try { window.CK && (window.CK.__health = await fetch(`${API}/health`).then(r => r.json())); }
    catch (_) { window.CK && (window.CK.__health = { status: "offline" }); }
    if (window.CK && window.CK.__refreshTopbar) window.CK.__refreshTopbar();
  }
  checkHealth();

  async function fetchProviders() {
    try { window.CK && (window.CK.__providers = await request("/settings/providers")); }
    catch (_) { /* Gateway offline: leave providers panel showing its last known state. */ }
    // Provider badges only live on Configurações; repainting any other view would wipe its form.
    if (window.CK && window.CK.__refresh && document.querySelector("#cfgBody")) window.CK.__refresh();
  }
  fetchProviders();

  async function deleteJob(jobId) { await request(`/jobs/${jobId}`, { method: "DELETE" }); await syncVideos(); }
  async function retryJob(jobId) { await request(`/jobs/${jobId}/retry`, { method: "POST" }); await syncVideos(); }
  async function deleteProject(projectId) { await request(`/projects/${projectId}`, { method: "DELETE" }); if (activeProject && activeProject.id === projectId) activeProject = null; }

  async function populateProjectSelect(body) {
    const select = body.querySelector("#genProjectSelect");
    if (!select) return;
    try {
      const projects = await request("/projects");
      select.innerHTML = `<option value="">Novo projeto…</option>` + projects.map(p => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("");
      const nameInput = body.querySelector("#genProjectName");
      const current = projects.find(p => p.name === (nameInput && nameInput.value));
      if (current) select.value = current.id;
    } catch (_) { /* Gateway offline: keep the plain name field working. */ }
  }

  function bindProjectControls(body, notify) {
    const select = body.querySelector("#genProjectSelect");
    const nameInput = body.querySelector("#genProjectName");
    const deleteBtn = body.querySelector("#genProjectDeleteBtn");
    if (select && !select.dataset.bound) {
      select.dataset.bound = "true";
      select.addEventListener("change", () => {
        if (!select.value) { nameInput.value = ""; activeProject = null; return; }
        nameInput.value = select.selectedOptions[0].textContent;
        activeProject = { id: select.value, name: nameInput.value };
      });
    }
    if (deleteBtn && !deleteBtn.dataset.bound) {
      deleteBtn.dataset.bound = "true";
      deleteBtn.addEventListener("click", async () => {
        const projectId = select && select.value;
        if (!projectId) { notify("Selecione um projeto existente para excluir."); return; }
        if (!window.confirm(`Excluir o projeto "${nameInput.value}" e todo o seu histórico?`)) return;
        try { await deleteProject(projectId); nameInput.value = ""; await populateProjectSelect(body); await syncVideos(); notify("Projeto excluído."); }
        catch (error) { notify(error.message); }
      });
    }
  }

  const cachedDefaults = {}; // "long" / "short" per screen, "base" for Configurações
  function applyDefaults(body, defaults) {
    const set = (selector, val) => {
      const el = body.querySelector(selector);
      if (!el || val === undefined) return;
      // A default saved before the options changed (e.g. a 45 s Short) would leave the select blank.
      if (el.tagName === "SELECT" && ![...el.options].some(option => option.value === String(val))) return;
      el.value = val;
    };
    set("#genLanguageSelect", defaults.video_language); set("#genVoiceSelect", defaults.voice_name);
    set("#genParagraphInput", defaults.paragraph_number); set("#genDurationInput", defaults.video_clip_duration);
    set("#genFontSizeInput", defaults.font_size); set("#genTextColorInput", defaults.text_fore_color);
    set("#genStrokeColorInput", defaults.stroke_color); set("#genBgmSelect", defaults.bgm_type);
    set("#genSubtitlePosition", defaults.subtitle_position);
    const subtitle = body.querySelector("#genSubtitleEnabled");
    if (subtitle && defaults.subtitle_enabled !== undefined) subtitle.checked = !!defaults.subtitle_enabled;
  }

  /* Re-applies cachedDefaults every bind, not just the first: window.CK.__refresh()
     (fired by syncVideos/checkHealth/fetchProviders resolving in the background)
     re-renders the whole Gerador template from scratch, wiping field values back
     to the hardcoded HTML defaults. A "loaded once" guard would leave that reset
     value on screen forever after the first background refresh (#found in QA). */
  async function bindGenerationDefaults(body, notify) {
    const saveBtn = body.querySelector("#genSaveDefaultsBtn");
    if (!saveBtn) return;
    const format = screenFormat(body);
    const url = `/settings/generation-defaults?format=${format}`;
    if (!saveBtn.dataset.bound) {
      saveBtn.dataset.bound = "true";
      saveBtn.addEventListener("click", async () => {
        try {
          cachedDefaults[format] = await request(url, { method: "PUT", body: JSON.stringify({ value: {
            video_language: value(body, "#genLanguageSelect", "pt-BR"), voice_name: value(body, "#genVoiceSelect", "pt-BR-FranciscaNeural"),
            ...(body.querySelector("#genParagraphInput") ? { paragraph_number: integer(value(body, "#genParagraphInput", 5), 5) } : {}),
            video_clip_duration: integer(value(body, "#genDurationInput", 5), 5),
            font_size: integer(value(body, "#genFontSizeInput", 56), 56), text_fore_color: value(body, "#genTextColorInput", "#FFFFFF"),
            stroke_color: value(body, "#genStrokeColorInput", "#000000"), bgm_type: value(body, "#genBgmSelect", ""),
            subtitle_position: value(body, "#genSubtitlePosition", "bottom"),
            subtitle_enabled: !body.querySelector("#genSubtitleEnabled") || body.querySelector("#genSubtitleEnabled").checked,
          } }) });
          notify(`Padrões de ${format === "long" ? "vídeo longo" : "Shorts"} salvos.`);
        } catch (error) { notify(error.message); }
      });
    }
    if (cachedDefaults[format]) { applyDefaults(body, cachedDefaults[format]); return; }
    try {
      cachedDefaults[format] = await request(url);
      // #viewBody is shared by every screen: if the user switched while this loaded, don't write long-video defaults into the Shorts form.
      if (screenFormat(body) === format) applyDefaults(body, cachedDefaults[format]);
    } catch (_) { /* Gateway offline: keep the built-in field defaults. */ }
  }

  function applyConfigDefaults(body, defaults) {
    const set = (selector, val) => { const el = body.querySelector(selector); if (el && val !== undefined) el.value = val; };
    set("#cfgVideoSource", defaults.video_source); set("#cfgDuration", defaults.video_clip_duration); set("#cfgParagraphs", defaults.paragraph_number);
    set("#cfgLanguage", defaults.video_language); set("#cfgVoice", defaults.voice_name);
    set("#cfgFontSize", defaults.font_size); set("#cfgTextColor", defaults.text_fore_color); set("#cfgStrokeColor", defaults.stroke_color);
    const subtitle = body.querySelector("#cfgSubtitleEnabled"); if (subtitle && defaults.subtitle_enabled !== undefined) subtitle.checked = defaults.subtitle_enabled;
  }

  const KEY_FIELDS = [["#cfgMoonshotKey", "moonshot_api_key", "moonshot"], ["#cfgPexelsKey", "pexels_api_key", "pexels"], ["#cfgPixabayKey", "pixabay_api_key", "pixabay"], ["#cfgCoverrKey", "coverr_api_key", "coverr"], ["#cfgUploadPostKey", "upload_post_api_key", "upload_post"]];
  const lastValidation = {}; // selector -> {key, valid} for the value that was actually checked

  /* Validates against the real provider API on blur, as soon as the user fills the
     field in - not just at save time. Result is cached per selector so bindConfigSettings
     can warn (not block) before saving a key that's already known to be rejected. */
  function bindKeyValidation(body) {
    KEY_FIELDS.forEach(([selector, _key, provider]) => {
      const input = body.querySelector(selector);
      const status = body.querySelector(`${selector}Status`);
      if (!input || input.dataset.validateBound) return;
      input.dataset.validateBound = "true";
      input.addEventListener("blur", async () => {
        const apiKey = input.value;
        if (!apiKey) { if (status) status.textContent = ""; return; }
        if (status) status.textContent = "Verificando…";
        try {
          const result = await request("/settings/providers/validate", { method: "POST", body: JSON.stringify({ provider, api_key: apiKey }) });
          lastValidation[selector] = { key: apiKey, valid: result.valid };
          const regionLabel = result.region === "global" ? " (plataforma Global)" : result.region === "china" ? " (plataforma China)" : "";
          if (status) status.textContent = result.valid === true ? `✓ Chave válida${regionLabel}` : result.valid === false ? `✗ ${result.detail || "Chave inválida"}` : (result.detail || "");
          if (status) status.style.color = result.valid === true ? "var(--ok)" : result.valid === false ? "var(--err)" : "var(--muted)";
        } catch (error) { if (status) { status.textContent = "Não foi possível verificar agora."; status.style.color = "var(--muted)"; } }
      });
    });
  }

  /* Reads only whichever cfg* fields are present in the currently active
     Configurações tab - the other tab's fields simply aren't found and are
     omitted from the payload. Safe because the Gateway now merges PUTs onto
     the stored settings, not onto factory defaults, so a partial save from
     one tab never wipes another tab's saved fields. */
  async function bindConfigSettings(body, notify) {
    bindKeyValidation(body);
    const saveBtn = body.querySelector("#cfgSaveBtn");
    if (!saveBtn) return;
    if (!saveBtn.dataset.bound) {
      saveBtn.dataset.bound = "true";
      saveBtn.addEventListener("click", async () => {
        const patch = {};
        const str = (selector, key) => { const el = body.querySelector(selector); if (el) patch[key] = el.value; };
        const num = (selector, key) => { const el = body.querySelector(selector); if (el && el.value.trim() !== "") patch[key] = integer(el.value, 0); };
        str("#cfgVideoSource", "video_source"); num("#cfgDuration", "video_clip_duration"); num("#cfgParagraphs", "paragraph_number");
        str("#cfgLanguage", "video_language"); str("#cfgVoice", "voice_name");
        num("#cfgFontSize", "font_size"); str("#cfgTextColor", "text_fore_color"); str("#cfgStrokeColor", "stroke_color");
        const subtitle = body.querySelector("#cfgSubtitleEnabled"); if (subtitle) patch.subtitle_enabled = subtitle.checked;
        const credentials = {};
        const rejected = [];
        KEY_FIELDS.forEach(([selector, key]) => {
          const el = body.querySelector(selector);
          if (!el || !el.value) return;
          credentials[key] = el.value;
          const checked = lastValidation[selector];
          if (checked && checked.key === el.value && checked.valid === false) rejected.push(selector);
        });
        const uploadUser = body.querySelector("#cfgUploadPostUser"); if (uploadUser && uploadUser.value) credentials.upload_post_username = uploadUser.value;
        const moonshotModel = body.querySelector("#cfgMoonshotModel"); if (moonshotModel && moonshotModel.value) credentials.moonshot_model_name = moonshotModel.value;
        if (rejected.length && !window.confirm("A validação indicou que pelo menos uma chave foi rejeitada pelo provedor. Salvar mesmo assim?")) return;
        try {
          if (Object.keys(patch).length) {
            cachedDefaults.base = await request("/settings/generation-defaults", { method: "PUT", body: JSON.stringify({ value: patch }) });
            delete cachedDefaults.long; delete cachedDefaults.short; // both screens layer on top of the base
          }
          if (Object.keys(credentials).length) {
            window.CK.__providers = await request("/settings/providers", { method: "PUT", body: JSON.stringify(credentials) });
            KEY_FIELDS.map(f => f[0]).concat(["#cfgUploadPostUser"]).forEach(selector => {
              const el = body.querySelector(selector); if (el) el.value = "";
              const status = body.querySelector(`${selector}Status`); if (status) status.textContent = "";
            });
            notify("Configurações salvas. Reinicie o container moneyprinterturbo para aplicar as chaves.");
          } else {
            notify("Configurações salvas.");
          }
          if (window.CK.__refresh) window.CK.__refresh();
        } catch (error) { notify(error.message); }
      });
    }
    if (cachedDefaults.base) { applyConfigDefaults(body, cachedDefaults.base); return; }
    try { cachedDefaults.base = await request("/settings/generation-defaults"); applyConfigDefaults(body, cachedDefaults.base); }
    catch (_) { /* Gateway offline: keep the built-in field defaults. */ }
  }

  // One draft per screen, so a long-video script never shows up on the Shorts form.
  const draftKey = body => `moneyprint-generation-draft-v1-${screenFormat(body)}`;

  function saveDraft(body) {
    const inputs = ["#genProjectName", "#genTopicInput", "#genScriptInput", "#genTermsInput", "#genLanguageSelect", "#genVoiceSelect"];
    const draft = Object.fromEntries(inputs.map(selector => [selector, value(body, selector, "")]));
    try { localStorage.setItem(draftKey(body), JSON.stringify(draft)); } catch (_) { /* storage full or blocked: drafts are a convenience */ }
  }

  function restoreDraft(body) {
    try {
      const draft = JSON.parse(localStorage.getItem(draftKey(body)) || "{}");
      Object.entries(draft).forEach(([selector, saved]) => { const input = body.querySelector(selector); if (input && saved) input.value = saved; });
    } catch (_) { /* Invalid local draft must not block the Cockpit. */ }
  }

  function bindGenerator(body, notify) {
    if (!body.querySelector("#genStartBtn")) return;
    const format = screenFormat(body);
    lastNotify = notify;
    renderPendingJobs();
    resumeActiveJob();
    syncVideos(); // a job may have started elsewhere (another tab, the API) since the last sync
    bindProjectControls(body, notify);
    populateProjectSelect(body);
    bindGenerationDefaults(body, notify);
    fillChannelSelects();
    const button = body.querySelector("#genStartBtn");
    if (!button || button.dataset.moneyprinterBound) return;
    button.dataset.moneyprinterBound = "true";
    const label = button.textContent;
    const channelList = body.querySelector("#genChannelList");
    // One channel per platform: ticking a second YouTube channel unticks the first.
    if (channelList) channelList.addEventListener("change", event => {
      const box = event.target;
      if (box.name !== "genChannel" || !box.checked) return;
      channelList.querySelectorAll(`input[data-platform="${box.dataset.platform}"]`).forEach(other => { if (other !== box) other.checked = false; });
    });
    const scriptInput = body.querySelector("#genScriptInput");
    const stats = body.querySelector("#genScriptStats");
    const updateStats = () => {
      if (!stats) return;
      const words = scriptInput.value.trim().split(/\s+/).filter(Boolean).length;
      const seconds = Math.round(words / 2.5); // ≈150 words/min, the same rate the long-script writer aims for
      const outOfRange = format === "short" && (seconds < 60 ? "curto demais" : seconds > 90 ? "longo demais" : "");
      stats.textContent = words ? `${words} palavras · ≈ ${seconds > 90 ? `${Math.round(seconds / 6) / 10} min` : `${seconds} s`} de narração${outOfRange ? ` — ${outOfRange} para um Short (60–90 s)` : ""}` : "";
      stats.style.color = outOfRange ? "var(--warn)" : "";
    };
    restoreDraft(body);
    updateStats();
    body.querySelectorAll("#genProjectName, #genTopicInput, #genScriptInput, #genTermsInput, #genLanguageSelect, #genVoiceSelect").forEach(input => input.addEventListener("input", () => saveDraft(body)));
    scriptInput.addEventListener("input", updateStats);
    button.addEventListener("click", async () => {
      const subject = value(body, "#genTopicInput", "").trim();
      if (!subject) { notify("Informe o tema do vídeo."); body.querySelector("#genTopicInput").focus(); return; }
      button.disabled = true;
      button.textContent = "Enviando para o Turbo…";
      try {
        const project = await projectFor(body);
        const number = (selector, fallback) => { const n = Number.parseFloat(value(body, selector, fallback)); return Number.isFinite(n) ? n : fallback; };
        const subtitle = body.querySelector("#genSubtitleEnabled");
        const job = await request(`/projects/${project.id}/jobs`, { method: "POST", body: JSON.stringify({ subject, format, channel_ids: selectedChannels(body), script: value(body, "#genScriptInput", ""), settings: {
          video_language: value(body, "#genLanguageSelect", "pt-BR"), voice_name: value(body, "#genVoiceSelect", "pt-BR-FranciscaNeural"),
          paragraph_number: integer(value(body, "#genParagraphInput", 5), 5), video_clip_duration: integer(value(body, "#genDurationInput", 5), 5),
          video_count: integer(value(body, "#genVideoCount", 1), 1), video_clip_speed: number("#genClipSpeed", 1),
          video_concat_mode: value(body, "#genConcatMode", "random"), video_transition_mode: value(body, "#genTransition", ""),
          video_terms: value(body, "#genTermsInput", "").trim(),
          voice_rate: number("#genVoiceRate", 1), voice_volume: number("#genVoiceVolume", 1),
          subtitle_enabled: subtitle ? subtitle.checked : true, subtitle_position: value(body, "#genSubtitlePosition", "bottom"),
          font_size: integer(value(body, "#genFontSizeInput", 56), 56), text_fore_color: value(body, "#genTextColorInput", "#FFFFFF"),
          stroke_color: value(body, "#genStrokeColorInput", "#000000"), stroke_width: number("#genStrokeWidth", 1.5),
          bgm_type: value(body, "#genBgmSelect", ""), bgm_volume: number("#genBgmVolume", 0.2),
        } }) });
        const slot = body.querySelector("#gen-preview"); if (slot) slot.innerHTML = "";
        const download = body.querySelector("#genDownloadBtn"); if (download) download.disabled = true;
        watch(body, job, notify);
      } catch (error) { notify(error.message); }
      finally { button.disabled = false; button.textContent = label; }
    });
    bindAiHelpers(body, notify);
  }

  /* Mirrors the WebUI's "generate script" / "generate keywords" buttons via
     Turbo's /api/v1/scripts and /api/v1/terms (proxied by the Gateway). */
  function bindAiHelpers(body, notify) {
    const run = (selector, label, task) => {
      const btn = body.querySelector(selector);
      if (!btn || btn.dataset.bound) return;
      btn.dataset.bound = "true";
      btn.addEventListener("click", async () => {
        const subject = value(body, "#genTopicInput", "").trim();
        if (!subject) { notify("Informe o tema do vídeo primeiro."); return; }
        btn.disabled = true; btn.textContent = "Gerando…";
        try { await task(subject); } catch (error) { notify(error.message); }
        finally { btn.disabled = false; btn.textContent = label; }
      });
    };
    run("#genScriptAiBtn", "Gerar roteiro com IA", async subject => {
      const result = await request("/llm/scripts", { method: "POST", body: JSON.stringify({ video_subject: subject, video_language: value(body, "#genLanguageSelect", "pt-BR"), paragraph_number: Math.min(10, Math.max(1, integer(value(body, "#genParagraphInput", 5), 5))) }) });
      body.querySelector("#genScriptInput").value = result.video_script || "";
      body.querySelector("#genScriptInput").dispatchEvent(new Event("input")); // saves the draft and refreshes the word count
    });
    run("#genLongScriptBtn", "Escrever roteiro longo com IA", async subject => {
      const channel = channelById(selectedChannels(body)[0]);
      const result = await request("/scripts/long", { method: "POST", body: JSON.stringify({
        subject, language: value(body, "#genLanguageSelect", "pt-BR"), minutes: integer(value(body, "#genTargetMinutes", 8), 8), niche: (channel && channel.niche) || "",
      }) });
      body.querySelector("#genTermsInput").value = (result.terms || []).join(", ");
      body.querySelector("#genScriptInput").value = result.script || "";
      body.querySelector("#genScriptInput").dispatchEvent(new Event("input"));
      notify(`Roteiro com ${result.sections.length} seções · ≈ ${result.estimated_minutes} min de narração`);
    });
    run("#genTermsAiBtn", "Gerar palavras-chave", async subject => {
      const result = await request("/llm/terms", { method: "POST", body: JSON.stringify({ video_subject: subject, video_script: value(body, "#genScriptInput", ""), amount: 5 }) });
      body.querySelector("#genTermsInput").value = (result.video_terms || []).join(", ");
    });
  }

  /* "YouTube Shorts" screen's "Publicar vídeo existente da biblioteca" card: picks a
     completed job from the library and runs the same metadata -> approve -> publish
     chain as the Gerador panel, in one click, since this card only has one button. */
  function splitDescriptionAndTags(raw) {
    const tokens = String(raw || "").split(/\s+/).filter(Boolean);
    const tags = tokens.filter(t => t.startsWith("#")).map(t => t.slice(1)).filter(Boolean);
    const description = tokens.filter(t => !t.startsWith("#")).join(" ");
    return { description, tags };
  }

  function bindYoutubeLibraryPublish(body, notify) {
    const button = body.querySelector("#ytPubBtn");
    if (!button || button.dataset.bound) return;
    button.dataset.bound = "true";
    const status = body.querySelector("#ytPubStatus");
    button.addEventListener("click", async () => {
      const jobId = value(body, "#ytLibrarySelect", "");
      const channelId = value(body, "#ytLibChannelSelect", "");
      if (!jobId) { notify("Selecione um vídeo da biblioteca."); return; }
      if (!channelId) { notify("Selecione o canal de destino."); return; }
      button.disabled = true;
      try {
        let job = await request(`/jobs/${jobId}`);
        if (job.state !== "completed" && job.state !== "approved") { notify("Este vídeo não está pronto para publicação."); return; }
        const title = value(body, "#ytPubTitle", "").trim() || (job.metadata && job.metadata.title) || "";
        if (!title) { notify("Informe o título do vídeo."); return; }
        const { description, tags } = splitDescriptionAndTags(value(body, "#ytPubDesc", ""));
        if (status) status.textContent = "Salvando metadados…";
        await request(`/jobs/${jobId}/metadata`, { method: "PUT", body: JSON.stringify({ title, description: description || (job.metadata && job.metadata.description) || "", tags: tags.length ? tags : (job.metadata && job.metadata.tags) || [] }) });
        if (job.state === "completed") { if (status) status.textContent = "Aprovando…"; job = await request(`/jobs/${jobId}/approve`, { method: "POST" }); }
        if (status) status.textContent = "Enviando…";
        const published = await publish(job, [channelId]);
        if (published === job) { if (status) status.textContent = ""; return; } // operator cancelled the re-send
        const pub = (published.publications || []).find(p => p.channel_id === channelId);
        if (status) status.textContent = pub ? publicationLine(pub) : "";
        notify(pub && pub.state === "processing" ? "Vídeo enviado; o TikTok ainda está processando." : "Vídeo publicado.");
        syncVideos();
      } catch (error) {
        if (status) status.textContent = "";
        notify(error.message);
      } finally {
        button.disabled = false;
      }
    });
  }

  /* Connected channels from the Gateway replace the old mock list in place, in the shape the
     Canais / Gerador / YouTube Shorts templates already read. */
  function mapChannel(c) {
    const stats = c.stats || {};
    return { id: c.id, name: c.name, platform: c.platform === "tiktok" ? "TikTok" : "YouTube", handle: c.handle, status: c.status,
      niche: c.niche, language: c.language, followers: stats.followers || 0, videos: stats.videos || 0, views: stats.views || 0, last: "—" };
  }

  /* Refills the channel selects in place: re-rendering Gerador would wipe what the user typed. */
  function fillChannelSelects() {
    const connected = window.CK.CHANNELS.filter(c => c.status === "connected");
    const youtube = connected.filter(c => c.platform === "YouTube");
    const option = c => `<option value="${esc(c.id)}">${esc(c.name)} · ${esc(c.platform)}</option>`;
    // Long videos only go to YouTube; Shorts to any connected channel.
    [["#genChannelSelect", youtube, "Escolher depois"], ["#ytLibChannelSelect", screenFormat(document) === "long" ? youtube : connected, "Selecione um canal…"]].forEach(([selector, list, empty]) => {
      const select = document.querySelector(selector);
      if (!select) return;
      const current = select.value;
      select.innerHTML = `<option value="">${empty}</option>` + list.map(option).join("");
      select.value = list.some(c => c.id === current) ? current : "";
      select.dispatchEvent(new Event("change"));
    });
    const list = document.querySelector("#genChannelList");
    if (!list) return;
    const checked = new Set([...list.querySelectorAll("input:checked")].map(input => input.value));
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
    if (window.CK.__refresh && (document.querySelector("#chBody") || document.querySelector("[data-ch-act]"))) window.CK.__refresh();
  }
  syncChannels();
  // Coming back from the Google / Upload-Post login tab is when a new channel shows up.
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") syncChannels(true); });

  function openIn(win, url) {
    if (win && !win.closed) { win.opener = null; win.location.href = url; }
    else window.open(url, "_blank", "noopener");
  }
  const youtubeConnectUrl = rec => `${API}/channels/youtube/connect?${new URLSearchParams({ niche: rec.niche || "", language: rec.language || "pt-BR" })}`;

  async function connectChannel(rec, win) {
    if (rec.platform !== "TikTok") { openIn(win, youtubeConnectUrl(rec)); return; }
    const result = await request("/channels/tiktok/connect", { method: "POST", body: JSON.stringify({ niche: rec.niche || "", language: rec.language || "pt-BR" }) });
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

  window.MoneyPrinterRuntime = { bindGenerator, deleteJob, retryJob, bindConfigSettings, bindYoutubeLibraryPublish, connectChannel, channelAction, saveChannel };
}());
