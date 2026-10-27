/* Same-origin browser adapter. It never receives provider credentials. */
(function () {
  const API = "/api";
  let activeProject = null;
  let activeJob = null;
  let stream = null;
  let csrfToken = sessionStorage.getItem("cockpit-csrf") || "";
  let loginDisabled = false;
  let assistantMessages = [];

  async function request(path, options = {}) {
    const method = (options.method || "GET").toUpperCase();
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (["POST", "PUT", "PATCH", "DELETE"].includes(method) && csrfToken) headers["X-CSRF-Token"] = csrfToken;
    const response = await fetch(API + path, { credentials: "same-origin", ...options, headers });
    if (response.status === 204) return null;
    const body = await response.json().catch(() => ({}));
    if (response.status === 401 && path !== "/auth/session" && !loginDisabled) {
      sessionStorage.removeItem("cockpit-csrf");
      window.location.replace("/Login.html");
    }
    if (!response.ok) throw new Error(body.detail || "Não foi possível concluir a operação.");
    return body;
  }

  async function streamLongScript(payload, onEvent) {
    const headers = { "Content-Type": "application/json" };
    if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
    const response = await fetch(API + "/scripts/long/stream", {
      method: "POST", credentials: "same-origin", headers, body: JSON.stringify(payload),
    });
    if (response.status === 401 && !loginDisabled) {
      sessionStorage.removeItem("cockpit-csrf");
      window.location.replace("/Login.html");
      throw new Error("Sua sessão expirou.");
    }
    if (!response.ok || !response.body) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || "Não foi possível iniciar o roteiro.");
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "", event = "message", completed = null;
    const consume = block => {
      const lines = block.split(/\r?\n/);
      const eventLine = lines.find(line => line.startsWith("event:"));
      const dataLine = lines.find(line => line.startsWith("data:"));
      if (!dataLine) return;
      const name = eventLine ? eventLine.slice(6).trim() : "message";
      let data;
      try { data = JSON.parse(dataLine.slice(5).trim()); } catch (_) { throw new Error("O servidor enviou um progresso inválido."); }
      if (name === "error") throw new Error(data.message || "Não foi possível gerar o roteiro.");
      onEvent(name, data);
      if (name === "completed") completed = data;
    };
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      let boundary;
      while ((boundary = buffer.search(/\r?\n\r?\n/)) >= 0) {
        const block = buffer.slice(0, boundary); buffer = buffer.slice(boundary).replace(/^\r?\n\r?\n/, "");
        consume(block);
      }
      if (done) break;
    }
    if (!completed) throw new Error("A geração do roteiro foi encerrada antes de concluir.");
    return completed;
  }

  async function establishSession() {
    const session = await request("/auth/session");
    loginDisabled = !!session.login_disabled;
    csrfToken = session.csrf_token || "";
    sessionStorage.setItem("cockpit-csrf", csrfToken);
    window.CK && (window.CK.__admin = session);
    return session;
  }

  async function logout() {
    if (loginDisabled) return;
    await request("/auth/logout", { method: "POST" });
    csrfToken = "";
    sessionStorage.removeItem("cockpit-csrf");
    window.location.replace("/Login.html");
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
  const preferredChannel = format => localStorage.getItem(`moneyprint-create-channel-${format}`);

  function selectedChannels(root) {
    const select = root.querySelector("#genChannelSelect");
    if (select) return select.value ? [select.value] : [];
    const ids = [...root.querySelectorAll('input[name="genChannel"]:checked')].map(input => input.value);
    const primary = localStorage.getItem(`moneyprint-create-channel-${screenFormat(root)}`);
    return primary && ids.includes(primary) ? [primary, ...ids.filter(id => id !== primary)] : ids;
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
    const stopBtn = body.querySelector("#genStopBtn");
    if (stopBtn) { stopBtn.style.display = running ? "" : "none"; stopBtn.dataset.jobId = job.id; }
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
    const copyLink = body.querySelector("#genCopyLinkBtn");
    if (copyLink) {
      copyLink.disabled = false;
      copyLink.onclick = async () => {
        try {
          await navigator.clipboard.writeText(new URL(`${API}/jobs/${encodeURIComponent(job.id)}/artifacts/${encodeURIComponent(artifact.id)}/download`, location.href).href);
          if (lastNotify) lastNotify("Link de download copiado.");
        } catch (_) {
          if (lastNotify) lastNotify("Não foi possível copiar o link neste navegador.");
        }
      };
    }
    bindPublishing(body, job);
  }

  /* Review → approve → publish, mirroring Lumen's approval-first flow. Handlers are
     reassigned (not added) so repeated showArtifact calls don't stack them. */
  function channelById(id) { return ((window.CK && window.CK.CHANNELS) || []).find(c => c.id === id) || null; }
  const PUB_LABELS = { auto_reserved: "preparando envio programado…", pending: "aguardando publicação", uploading: "enviando…", processing: "enviado, processando no TikTok…", failed: "falhou", reconciliation_required: "resultado incerto — confira na plataforma antes de reenviar" };
  function publicationText(pub) {
    const link = pub.platform === "youtube" ? `https://youtu.be/${pub.external_id}` : (/^https?:/.test(pub.external_id || "") ? pub.external_id : "");
    if (pub.state === "published") return (pub.auto_publish ? "publicado (público)" : "publicado (privado)") + (pub.external_id && link ? ": " + link : "");
    if (pub.state === "platform_scheduled") return "publicação pública programada no YouTube para " + when(pub.scheduled_at);
    if (pub.state === "scheduled") return `agendado para ${when(pub.scheduled_at)}`;
    return `${PUB_LABELS[pub.state] || pub.state}${pub.error ? " — " + pub.error : ""}`;
  }
  function publicationLine(pub) {
    const channel = channelById(pub.channel_id);
    return `${channel ? channel.name : (pub.platform === "tiktok" ? "TikTok" : "YouTube")}: ${publicationText(pub)}`;
  }
  const OPEN_STATES = ["pending", "failed", "reconciliation_required"];
  const when = iso => iso ? new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "—";
  /* publishAt is a datetime-local value (local time, no zone); empty publishes now. */
  async function publish(job, channelIds, publishAt) {
    const uncertain = (job.publications || []).some(p => p.state === "reconciliation_required");
    if (uncertain && !window.confirm("Um envio anterior pode ter chegado à plataforma. Confirme que o vídeo NÃO está publicado para enviar de novo.")) return job;
    const result = await request(`/jobs/${job.id}/publish`, { method: "POST", body: JSON.stringify({
      channel_ids: channelIds && channelIds.length ? channelIds : null, confirm_not_uploaded: uncertain, publish_at: publishAt ? new Date(publishAt).toISOString() : null,
    }) });
    if (result.errors && result.errors.length) window.alert(result.errors.map(error => error.detail).join("\n"));
    return result;
  }

  function bindPublishing(body, job) {
    const $ = selector => body.querySelector(selector);
    const box = $("#genMetaBox"), publishBtn = $("#genPublishBtn"), publishAt = $("#genPublishAt");
    if (!box || !publishBtn) return;
    box.hidden = false;
    // Channels picked at generation time are pending publications; without them, use what is selected now.
    const chosen = () => (job.publications || []).some(p => p.channel_id) ? [] : selectedChannels(body);
    const states = () => {
      const pubs = job.publications || [];
      const openPublications = pubs.filter(p => OPEN_STATES.includes(p.state));
      const selectedChannelIds = [...new Set([...openPublications.map(p => p.channel_id).filter(Boolean), ...chosen()])];
      const visibilities = [...new Set(selectedChannelIds.map(id => channelById(id)?.publish_privacy || "private"))];
      const visibilityLabel = visibilities.length > 1 ? "visibilidade por canal" : visibilities[0] === "public" ? "público" : "privado";
      const open = openPublications.length + chosen().length;
      const channelsLabel = `${open} ${open === 1 ? "canal" : "canais"}`;
      $("#genPublishStatus").textContent = job.state !== "approved" && !pubs.some(p => p.state !== "pending") ? "Revise e aprove antes de publicar"
        : pubs.length ? pubs.map(publicationLine).join(" · ")
        : open ? `Aprovado — pronto para publicar em ${channelsLabel}` : "Aprovado — escolha o canal";
      $("#genApproveBtn").disabled = job.state !== "completed";
      publishBtn.disabled = job.state !== "approved" || !open || pubs.some(p => p.state === "uploading");
      publishBtn.lastChild.textContent = open ? ` ${publishAt && publishAt.value ? "Agendar" : "Publicar"} (${visibilityLabel}) em ${channelsLabel}` : " Publicar (privado)";
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
        finally { button.disabled = false; } // states() only re-enables genApproveBtn/publishBtn; other buttons rely on this.
        states();
        if (job.state === "approved") syncVideos();
      };
    };
    act($("#genMetaAiBtn"), () => request(`/jobs/${job.id}/metadata/generate`, { method: "POST" }), true);
    act($("#genMetaSaveBtn"), save, true);
    act($("#genApproveBtn"), async () => { await save(); return request(`/jobs/${job.id}/approve`, { method: "POST" }); }, false);
    const reviewButton = $("#genReviewBtn"), reviewResult = $("#genReviewResult");
    if (reviewButton) {
      reviewButton.disabled = !["completed", "approved"].includes(job.state);
      reviewResult.textContent = job.review?.summary || "";
      reviewButton.onclick = async () => {
        reviewButton.disabled = true; reviewButton.textContent = "Revisando…";
        try {
          job = await request(`/jobs/${job.id}/review`, { method: "POST" });
          const mismatches = (job.review?.frames || []).filter(frame => !frame.matches);
          reviewResult.innerHTML = `${esc(job.review?.summary || "")}${mismatches.map(frame => `<div>Quadro ${frame.index + 1}: ${esc(frame.reason || "não combina com o roteiro")}</div>`).join("")}`;
        } catch (error) { window.alert(error.message); }
        finally { reviewButton.disabled = false; reviewButton.textContent = "Revisar imagens com IA"; }
      };
    }
    const shortsBtn = $("#genDeriveShortsBtn");
    if (shortsBtn) shortsBtn.onclick = async () => {
      shortsBtn.disabled = true;
      try {
        const result = await request(`/jobs/${job.id}/shorts`, { method: "POST" });
        if (lastNotify) lastNotify(`${result.created} Shorts entraram na fila de publicação do canal.`);
      } catch (error) { if (lastNotify) lastNotify(error.message); }
      finally { shortsBtn.disabled = false; }
    };
    act(publishBtn, () => publish(job, chosen(), publishAt && publishAt.value), false);
    const picker = $("#genChannelSelect") || $("#genChannelList");
    if (picker) picker.onchange = () => setTimeout(states); // after the one-per-platform listener adjusts the boxes
    if (publishAt) publishAt.oninput = states;
    fill();
    states();
  }

  // One project per screen, created on first use - the picker is gone, so there is nothing left to name.
  const PROJECT_NAMES = { long: "Vídeos longos", short: "Shorts" };
  async function projectFor(body) {
    const name = PROJECT_NAMES[screenFormat(body)] || "Meu projeto";
    if (activeProject && activeProject.name === name) return activeProject;
    const projects = await request("/projects");
    const found = projects.find(project => project.name === name);
    if (found) { activeProject = found; return found; }
    activeProject = await request("/projects", { method: "POST", body: JSON.stringify({ name, language: "pt-BR", defaults: {}, publish: { auto_publish: false, platforms: [], youtube_privacy: "private" } }) });
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
      // #viewBody is shared by both screens with identical ids inside (ponytail: same guard
      // bindGenerationDefaults already uses) - only paint this update if its screen is still open.
      const onScreen = screenFormat(document.getElementById("viewBody")) === current.format;
      if (onScreen) setPipeline(body, current);
      if (current.state === "completed") {
        if (onScreen) showArtifact(body, current);
        notify("Vídeo concluído e salvo no projeto."); stream.close();
        syncVideos().then(() => { if (window.CK.__go) setTimeout(() => window.CK.__go("producoes"), 1500); });
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
    const channelId = preferredChannel(format);
    const running = job => job.format === format && (!channelId || job.channelId === channelId || (job.publications || []).some(publication => publication.channel_id === channelId)) && (job.state === "queued" || job.state === "running");
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

  /* Paints the "Jobs e workers" page in place, so a status change shows up without a full
     re-render. Lives on its own screen (not the Gerador/Shorts forms) and covers both formats -
     retry only applies to failed/interrupted; delete is blocked server-side for queued/running. */
  function renderPendingJobs() {
    const box = document.getElementById("jobsWorkersBody");
    if (!box) return;
    const jobs = (window.CK && window.CK.PENDING_JOBS) || [];
    const labels = { queued: "Na fila", running: "Gerando…", failed: "Falhou", interrupted: "Interrompido" };
    const formatLabels = { long: "Vídeo longo", short: "Short" };
    box.innerHTML = jobs.length ? jobs.map(j => `
      <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid var(--border)">
        <div style="min-width:0">
          <div style="font-weight:500;color:var(--ink);font-size:13px">${esc(j.subject)}</div>
          <div style="font-size:11.5px;color:var(--muted)">${esc(j.projectName)} · ${esc(formatLabels[j.format] || j.format)} · ${esc(labels[j.state] || j.state)}${j.error ? " — " + esc(j.error) : ""}</div>
        </div>
        <div style="display:flex;gap:6px;flex-shrink:0">
          <button class="btn sm" data-open-job="${esc(j.id)}">Abrir</button>
          ${j.state === "queued" || j.state === "running" ? `<button class="btn sm" data-stop-job="${esc(j.id)}">Parar</button>` : ""}
          ${j.state === "failed" || j.state === "interrupted" ? `<button class="btn sm" data-retry-job="${esc(j.id)}">Repetir</button>
          <button class="btn sm" data-delete-job="${esc(j.id)}">Deletar</button>` : ""}
        </div>
      </div>`).join("") : `<div class="empty"><div class="empty-t">Nenhum job em andamento ou com falha</div><div class="empty-s">Jobs na fila, gerando ou com erro aparecem aqui.</div></div>`;
    if (box.dataset.bound) return;
    box.dataset.bound = "true";
    box.addEventListener("click", async event => {
      const retry = event.target.closest("[data-retry-job]");
      const del = event.target.closest("[data-delete-job]");
      const stop = event.target.closest("[data-stop-job]");
      const open = event.target.closest("[data-open-job]");
      if (open) { openJob(open.dataset.openJob).catch(error => { if (lastNotify) lastNotify(error.message); }); return; }
      if (stop) {
        if (!window.confirm("Parar esta geração? O vídeo em andamento será perdido.")) return;
        stop.disabled = true;
        try { await stopJob(stop.dataset.stopJob); if (lastNotify) lastNotify("Geração interrompida."); }
        catch (error) { stop.disabled = false; if (lastNotify) lastNotify(error.message); }
      } else if (retry) {
        retry.disabled = true;
        try { await retryJob(retry.dataset.retryJob); if (lastNotify) lastNotify("Job reenviado para o Turbo"); }
        catch (error) { retry.disabled = false; if (lastNotify) lastNotify(error.message); }
      } else if (del) {
        if (!window.confirm("Excluir este job? Essa ação não pode ser desfeita.")) return;
        del.disabled = true;
        try { await deleteJob(del.dataset.deleteJob); if (lastNotify) lastNotify("Job excluído"); }
        catch (error) { del.disabled = false; if (lastNotify) lastNotify(error.message); }
      }
    });
  }

  function bindJobsWorkers(body, notify) {
    if (!body.querySelector("#jobsWorkersBody")) return;
    lastNotify = notify;
    renderPendingJobs();
    syncVideos(); // a job may have started/finished elsewhere since the last sync
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
            pending.push({ id: job.id, subject: job.subject, state: job.state, error: job.error, projectName: project.name, format: job.format, channelId: (job.publications || [])[0]?.channel_id || job.channel_id || null, created_at: job.created_at });
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
      const latestOf = format => {
        const channelId = preferredChannel(format);
        return (completed.find(item => item.job.format === format && (!channelId || (item.job.publications || []).some(publication => publication.channel_id === channelId))) || {}).job || null;
      };
      window.CK.LATEST_JOB = { long: latestOf("long"), short: latestOf("short") };
      // A full repaint of Produções would rebind its workers and restart this sync.
      const videoBody = document.querySelector("#vidBody");
      if (videoBody && document.querySelector("#productionsRoot")) {
        videoBody.innerHTML = window.CKV.videoGrids("all");
        const library = document.querySelector("#ytLibrarySelect");
        if (library) {
          const selected = library.value;
          const videos = screenFormat(document) === "long" ? horiz : vert;
          library.innerHTML = '<option value="">Selecione um vídeo da biblioteca…</option>' + videos.map(video => `<option value="${esc(video[4])}">${esc(video[2])}</option>`).join("");
          library.value = videos.some(video => video[4] === selected) ? selected : "";
        }
      } else if (window.CK.__refresh && videoBody) window.CK.__refresh();
      renderPendingJobs();
      resumeActiveJob();
      clearTimeout(syncTimer);
      if (pending.some(job => job.state === "queued" || job.state === "running") || document.querySelector("#productionsRoot")) {
        syncTimer = setTimeout(syncVideos, 15000);
      }
    } catch (_) { /* Gateway/Turbo offline: keep last known videos on screen. */ }
  }
  let syncTimer = null;
  syncVideos();

  async function syncNotifications() {
    if (!window.CK || !window.CK.NOTIFICATIONS) return;
    try {
      const data = await request("/notifications");
      const mapKind = kind => kind === "job_failed" ? "critical" : kind === "job_interrupted" ? "warning" : "ok";
      const items = (data.items || []).map(item => ({
        id: item.id, sev: mapKind(item.kind), title: item.title, sub: item.detail || "",
        time: item.created_at ? new Date(item.created_at).toLocaleString("pt-BR") : "", read: Boolean(item.read_at), view: item.job_id ? "producoes" : "notificacoes", job_id: item.job_id || null,
      }));
      window.CK.NOTIFICATIONS.length = 0; window.CK.NOTIFICATIONS.push(...items);
      window.CK.ALERTS.length = 0; window.CK.ALERTS.push(...items);
      if (window.CK.__refreshTopbar) window.CK.__refreshTopbar();
    } catch (_) { /* The dedicated view will show the Gateway error if it becomes unavailable. */ }
  }

  const CHANNEL_PROFILE_KEYS = ["language", "publish_privacy", "voice_name", "voice_rate", "voice_volume", "subtitle_enabled", "subtitle_position", "font_size", "text_fore_color", "stroke_color", "stroke_width"];
  const CHANNEL_FORMAT_PROFILE_KEYS = ["voice_name", "voice_rate", "voice_volume", "subtitle_enabled", "subtitle_position", "font_size", "text_fore_color", "stroke_color", "stroke_width"];
  const CHANNEL_VIDEO_PROFILE_KEYS = {
    short: [...CHANNEL_FORMAT_PROFILE_KEYS, "paragraph_number", "video_clip_duration", "video_count", "video_concat_mode", "video_transition_mode", "video_clip_speed", "bgm_type", "bgm_volume"],
    long: [...CHANNEL_FORMAT_PROFILE_KEYS, "target_minutes", "video_clip_duration", "video_concat_mode", "video_transition_mode", "video_clip_speed", "bgm_type", "bgm_volume"],
  };
  const channelProfilePayload = channel => ({ ...Object.fromEntries(CHANNEL_PROFILE_KEYS.map(key => [key, channel[key]])), video_defaults: channel.video_defaults || {}, visual_guides: channel.visual_guides || { short: "", long: "" } });

  function applyChannelProfile(body) {
    const channels = selectedChannels(body).map(channelById).filter(Boolean);
    const note = body.querySelector("#genChannelProfileNote");
    if (!channels.length) {
      if (note) { note.textContent = "Selecione um canal para gerar."; note.style.color = ""; }
      return;
    }
    const primary = channels[0];
    const format = screenFormat(body) || "short";
    const videoProfile = primary.video_defaults && primary.video_defaults[format];
    const profileKeys = ["language", ...CHANNEL_VIDEO_PROFILE_KEYS[format]];
    const profileValue = (channel, key) => key === "language" ? channel.language : channel.video_defaults && channel.video_defaults[format] && channel.video_defaults[format][key];
    const conflicts = profileKeys.filter(key => channels.some(channel => String(profileValue(channel, key)) !== String(profileValue(primary, key))));
    const set = (selector, value) => { const input = body.querySelector(selector); if (input && value !== undefined) input.value = value; };
    set("#genLanguageSelect", primary.language);
    if (videoProfile) {
      set("#genVoiceSelect", videoProfile.voice_name); set("#genVoiceRate", videoProfile.voice_rate); set("#genVoiceVolume", videoProfile.voice_volume);
      set("#genSubtitlePosition", videoProfile.subtitle_position); set("#genFontSizeInput", videoProfile.font_size);
      set("#genTextColorInput", videoProfile.text_fore_color); set("#genStrokeColorInput", videoProfile.stroke_color);
      set("#genStrokeWidth", videoProfile.stroke_width);
      set("#genParagraphInput", videoProfile.paragraph_number); set("#genTargetMinutes", videoProfile.target_minutes);
      set("#genDurationInput", videoProfile.video_clip_duration); set("#genVideoCount", videoProfile.video_count);
      set("#genConcatMode", videoProfile.video_concat_mode); set("#genTransition", videoProfile.video_transition_mode);
      set("#genClipSpeed", videoProfile.video_clip_speed); set("#genBgmSelect", videoProfile.bgm_type); set("#genBgmVolume", videoProfile.bgm_volume);
    }
    const subtitle = body.querySelector("#genSubtitleEnabled"); if (subtitle && videoProfile) subtitle.checked = !!videoProfile.subtitle_enabled;
    if (note) { note.textContent = conflicts.length ? "Cada canal receberá seu próprio vídeo com os respectivos parâmetros." : `Padrões de voz, legendas e vídeo carregados de ${primary.name}. Você pode ajustá-los apenas para este vídeo.`; note.style.color = ""; }
  }
  syncNotifications();

  async function checkHealth() {
    try { window.CK && (window.CK.__health = await fetch(`${API}/health`).then(r => r.json())); }
    catch (_) { window.CK && (window.CK.__health = { status: "offline" }); }
    if (window.CK && window.CK.__refreshTopbar) window.CK.__refreshTopbar();
  }
  checkHealth();

  async function fetchProviders() {
    try {
      const [providers, nvidiaModels] = await Promise.all([
        request("/settings/providers"), request("/settings/providers/nvidia/models"),
      ]);
      if (window.CK) { window.CK.__providers = providers; window.CK.__nvidiaModels = nvidiaModels; }
    } catch (_) { /* Gateway offline: leave providers panel showing its last known state. */ }
    // Provider badges only live on Configurações; repainting any other view would wipe its form.
    if (window.CK && window.CK.__refresh && document.querySelector("#cfgBody")) window.CK.__refresh();
  }
  fetchProviders();

  async function deleteJob(jobId) { await request(`/jobs/${jobId}`, { method: "DELETE" }); await syncVideos(); }
  async function retryJob(jobId) { await request(`/jobs/${jobId}/retry`, { method: "POST" }); await syncVideos(); }
  async function stopJob(jobId) { await request(`/jobs/${jobId}/cancel`, { method: "POST" }); await syncVideos(); }

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
    set("#genVoiceRate", defaults.voice_rate); set("#genVoiceVolume", defaults.voice_volume);
    set("#genParagraphInput", defaults.paragraph_number); set("#genDurationInput", defaults.video_clip_duration);
    set("#genFontSizeInput", defaults.font_size); set("#genTextColorInput", defaults.text_fore_color);
    set("#genStrokeColorInput", defaults.stroke_color); set("#genBgmSelect", defaults.bgm_type);
    set("#genBgmVolume", defaults.bgm_volume); set("#genSubtitlePosition", defaults.subtitle_position);
    set("#genVideoCount", defaults.video_count); set("#genConcatMode", defaults.video_concat_mode);
    set("#genTransition", defaults.video_transition_mode); set("#genClipSpeed", defaults.video_clip_speed);
    set("#genTargetMinutes", defaults.target_minutes);
    const subtitle = body.querySelector("#genSubtitleEnabled");
    if (subtitle && defaults.subtitle_enabled !== undefined) subtitle.checked = !!defaults.subtitle_enabled;
    // Only fill these two if the field is still empty - an in-progress edit on screen (not yet
    // saved) must not be overwritten by a default that finishes loading a moment later.
    const setIfEmpty = (selector, val) => { const el = body.querySelector(selector); if (el && val !== undefined && !el.value) el.value = val; };
    setIfEmpty("#genScriptRequirements", defaults.video_script_prompt);
    setIfEmpty("#genSystemPrompt", defaults.custom_system_prompt);
  }

  /* Re-applies cachedDefaults every bind, not just the first: window.CK.__refresh()
     (fired by syncVideos/checkHealth/fetchProviders resolving in the background)
     re-renders the whole Gerador template from scratch, wiping field values back
     to the hardcoded HTML defaults. A "loaded once" guard would leave that reset
     value on screen forever after the first background refresh (#found in QA). */
  async function bindGenerationDefaults(body, notify) {
    const format = screenFormat(body);
    const url = `/settings/generation-defaults?format=${format}`;
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

  const KEY_FIELDS = [["#cfgMoonshotKey", "moonshot_api_key", "moonshot"], ["#cfgNvidiaKey", "nvidia_api_key", "nvidia"], ["#cfgPexelsKey", "pexels_api_key", "pexels"], ["#cfgPixabayKey", "pixabay_api_key", "pixabay"], ["#cfgCoverrKey", "coverr_api_key", "coverr"], ["#cfgUploadPostKey", "upload_post_api_key", "upload_post"]];
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
    const nvidiaSelect = body.querySelector("#cfgNvidiaTextModel");
    const nvidiaInfo = body.querySelector("#cfgNvidiaModelInfo");
    const nvidiaTest = body.querySelector("#cfgNvidiaTestBtn");
    const nvidiaTestStatus = body.querySelector("#cfgNvidiaTestStatus");
    const selectedModel = () => ((window.CK && window.CK.__nvidiaModels && window.CK.__nvidiaModels.models) || []).find(model => model.id === (nvidiaSelect && nvidiaSelect.value));
    const describeSelectedModel = () => {
      const model = selectedModel();
      if (!nvidiaInfo || !model) return;
      const capabilities = ["Texto", model.capabilities && model.capabilities.image ? "Imagem" : "", model.capabilities && model.capabilities.reasoning ? "Raciocínio" : "", model.capabilities && model.capabilities.streaming ? "Streaming" : ""].filter(Boolean).join(" · ");
      nvidiaInfo.textContent = `${model.description || ""}${capabilities ? ` ${capabilities}.` : ""}`;
    };
    if (nvidiaSelect && !nvidiaSelect.dataset.modelBound) {
      nvidiaSelect.dataset.modelBound = "true";
      nvidiaSelect.addEventListener("change", describeSelectedModel);
    }
    if (nvidiaTest && !nvidiaTest.dataset.bound) {
      nvidiaTest.dataset.bound = "true";
      nvidiaTest.addEventListener("click", async () => {
        if (!nvidiaSelect) return;
        nvidiaTest.disabled = true;
        if (nvidiaTestStatus) { nvidiaTestStatus.textContent = "Testando uma resposta mínima na NVIDIA…"; nvidiaTestStatus.style.color = "var(--muted)"; }
        try {
          const result = await request("/settings/providers/nvidia/test", { method: "POST", body: JSON.stringify({ model: nvidiaSelect.value }) });
          if (nvidiaTestStatus) {
            nvidiaTestStatus.textContent = result.ok ? `✓ ${result.detail} (${result.latency_ms} ms)` : `✗ ${result.detail}`;
            nvidiaTestStatus.style.color = result.ok ? "var(--ok)" : "var(--err)";
          }
        } catch (error) {
          if (nvidiaTestStatus) { nvidiaTestStatus.textContent = error.message; nvidiaTestStatus.style.color = "var(--err)"; }
        } finally { nvidiaTest.disabled = false; }
      });
    }
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
        const nvidiaTextModel = body.querySelector("#cfgNvidiaTextModel");
        if (nvidiaTextModel && nvidiaTextModel.value !== ((window.CK.__providers && window.CK.__providers.nvidia_text_model) || "moonshotai/kimi-k3")) credentials.nvidia_text_model = nvidiaTextModel.value;
        // A <select> always has a value, unlike the text fields above - only send it when it actually changed,
        // so saving unrelated settings doesn't needlessly re-trigger the "restart the container" notice.
        const providerSelect = body.querySelector("#cfgLlmProvider");
        if (providerSelect && providerSelect.value !== ((window.CK.__providers && window.CK.__providers.llm_provider) || "moonshot")) credentials.llm_provider = providerSelect.value;
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
            notify(window.CK.__providers.turbo_pending_restart
              ? "Configurações salvas. Reinicie o container moneyprinterturbo para aplicar o modelo aos Shorts."
              : "Configurações salvas.");
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

  const legacyDraftKey = format => `moneyprint-generation-draft-v1-${format}`;
  const selectedCreateKey = format => `moneyprint-create-channel-${format}`;
  const channelDraftKey = (format, channelId) => `moneyprint-generation-draft-v2-${format}-${encodeURIComponent(channelId)}`;
  const primaryChannel = body => {
    const selected = selectedChannels(body);
    const preferred = localStorage.getItem(selectedCreateKey(screenFormat(body)));
    return preferred && selected.includes(preferred) ? preferred : selected[0] || preferred || "";
  };
  const draftKey = body => primaryChannel(body) ? channelDraftKey(screenFormat(body), primaryChannel(body)) : legacyDraftKey(screenFormat(body));
  function selectCreateChannel(format, channelId) {
    localStorage.setItem(selectedCreateKey(format), channelId);
    const key = channelDraftKey(format, channelId);
    if (localStorage.getItem(key)) return;
    let legacy = {};
    try { legacy = JSON.parse(localStorage.getItem(legacyDraftKey(format)) || "{}"); } catch (_) { /* preserve invalid legacy data untouched */ }
    const oldChannels = Array.isArray(legacy.channel_ids) ? legacy.channel_ids : [];
    const draft = oldChannels.length === 0 || oldChannels[0] === channelId ? legacy : {};
    localStorage.setItem(key, JSON.stringify({ ...draft, channel_ids: oldChannels[0] === channelId ? oldChannels : [channelId] }));
  }
  const DRAFT_TEXT_SELECTORS = ["#genTopicInput", "#genScriptInput", "#genTermsInput", "#genScriptRequirements", "#genSystemPrompt"];
  const DRAFT_PARAMETER_SELECTORS = ["#genLanguageSelect", "#genVoiceSelect", "#genVoiceRate", "#genVoiceVolume", "#genParagraphInput", "#genTargetMinutes", "#genDurationInput", "#genVideoCount", "#genConcatMode", "#genTransition", "#genClipSpeed", "#genSubtitleEnabled", "#genSubtitlePosition", "#genStrokeWidth", "#genFontSizeInput", "#genTextColorInput", "#genStrokeColorInput", "#genBgmSelect", "#genBgmVolume", "#genVideoSource"];
  const GENERATOR_SETTING_FIELDS = {
    "#genLanguageSelect": "video_language", "#genVoiceSelect": "voice_name", "#genVoiceRate": "voice_rate", "#genVoiceVolume": "voice_volume",
    "#genParagraphInput": "paragraph_number", "#genTargetMinutes": "target_minutes", "#genDurationInput": "video_clip_duration", "#genVideoCount": "video_count",
    "#genConcatMode": "video_concat_mode", "#genTransition": "video_transition_mode", "#genClipSpeed": "video_clip_speed",
    "#genSubtitleEnabled": "subtitle_enabled", "#genSubtitlePosition": "subtitle_position", "#genStrokeWidth": "stroke_width", "#genFontSizeInput": "font_size",
    "#genTextColorInput": "text_fore_color", "#genStrokeColorInput": "stroke_color", "#genBgmSelect": "bgm_type", "#genBgmVolume": "bgm_volume", "#genVideoSource": "video_source",
  };

  function saveDraft(body, dirtyParams = [], dirtyText = [], metadataOverrides = {}) {
    const inputs = [...DRAFT_TEXT_SELECTORS, ...DRAFT_PARAMETER_SELECTORS];
    let metadata = {};
    try { metadata = JSON.parse(localStorage.getItem(draftKey(body)) || "{}"); } catch (_) { /* corrupt drafts are replaced safely */ }
    const draft = { ...metadata, ...metadataOverrides, ...Object.fromEntries(inputs.map(selector => {
      const element = body.querySelector(selector);
      return [selector, element ? (element.type === "checkbox" ? element.checked : element.value) : undefined];
    })) };
    draft.channel_ids = selectedChannels(body);
    draft.dirty_params = dirtyParams;
    draft.dirty_text = dirtyText;
    try { localStorage.setItem(draftKey(body), JSON.stringify(draft)); } catch (_) { /* storage full or blocked: drafts are a convenience */ }
  }

  function restoreDraft(body) {
    try {
      const format = screenFormat(body);
      const primary = localStorage.getItem(selectedCreateKey(format));
      const select = body.querySelector("#genChannelSelect");
      if (select && primary && [...select.options].some(option => option.value === primary)) select.value = primary;
      body.querySelectorAll('input[name="genChannel"]').forEach(input => { if (input.value === primary) input.checked = true; });
      const draft = JSON.parse(localStorage.getItem(draftKey(body)) || "{}");
      if (Object.keys(draft).length && !Array.isArray(draft.dirty_params)) draft.dirty_params = DRAFT_PARAMETER_SELECTORS.filter(selector => selector in draft);
      if (Object.keys(draft).length && !Array.isArray(draft.dirty_text)) draft.dirty_text = DRAFT_TEXT_SELECTORS.filter(selector => selector !== "#genSystemPrompt" && !!draft[selector]);
      DRAFT_TEXT_SELECTORS.forEach(selector => { const input = body.querySelector(selector); if (input && draft[selector]) input.value = draft[selector]; });
      const selected = new Set(Array.isArray(draft.channel_ids) ? draft.channel_ids : []);
      if (select && selected.has(select.value) === false && [...select.options].some(option => selected.has(option.value))) select.value = [...selected][0];
      body.querySelectorAll('input[name="genChannel"]').forEach(input => { input.checked = selected.has(input.value); });
      return draft;
    } catch (_) { /* Invalid local draft must not block the Cockpit. */ }
    return {};
  }

  function restoreParameterOverrides(body, draft) {
    const current = selectedChannels(body).slice().sort();
    const saved = Array.isArray(draft.channel_ids) ? draft.channel_ids.slice().sort() : [];
    if (current.join("|") !== saved.join("|")) return;
    (Array.isArray(draft.dirty_params) ? draft.dirty_params : []).forEach(selector => {
      if (!DRAFT_PARAMETER_SELECTORS.includes(selector)) return;
      if (!(selector in draft)) return;
      const input = body.querySelector(selector);
      if (!input) return;
      if (input.type === "checkbox") input.checked = !!draft[selector];
      else input.value = draft[selector];
    });
  }

  function bindGenerator(body, notify) {
    if (!body.querySelector("#genStartBtn")) return;
    const format = screenFormat(body);
    lastNotify = notify;
    resumeActiveJob();
    syncVideos(); // a job may have started elsewhere (another tab, the API) since the last sync
    fillChannelSelects();
    let generationDraft = {};
    const applyProfileAndOverrides = () => {
      applyChannelProfile(body);
      restoreParameterOverrides(body, generationDraft);
    };
    const persistGeneratorDraft = () => {
      saveDraft(body, generationDraft.dirty_params || [], generationDraft.dirty_text || [], generationDraft);
      try { generationDraft = JSON.parse(localStorage.getItem(draftKey(body)) || "{}"); } catch (_) { generationDraft = {}; }
    };
    bindGenerationDefaults(body, notify).then(applyProfileAndOverrides);
    const genTabs = body.querySelector("#genTabs");
    if (genTabs && !genTabs.dataset.bound) {
      genTabs.dataset.bound = "true";
      genTabs.addEventListener("click", event => {
        const tab = event.target.closest("[data-gentab]");
        if (!tab) return;
        genTabs.querySelectorAll("[data-gentab]").forEach(b => b.classList.toggle("is-active", b === tab));
        body.querySelectorAll("[data-genpane]").forEach(pane => { pane.hidden = pane.dataset.genpane !== tab.dataset.gentab; });
      });
    }
    const stopBtn = body.querySelector("#genStopBtn");
    if (stopBtn && !stopBtn.dataset.bound) {
      stopBtn.dataset.bound = "true";
      stopBtn.addEventListener("click", async () => {
        if (!stopBtn.dataset.jobId || !window.confirm("Parar esta geração? O vídeo em andamento será perdido.")) return;
        stopBtn.disabled = true;
        try { await stopJob(stopBtn.dataset.jobId); notify("Geração interrompida."); }
        catch (error) { notify(error.message); }
        finally { stopBtn.disabled = false; }
      });
    }
    const button = body.querySelector("#genStartBtn");
    if (!button || button.dataset.moneyprinterBound) return;
    button.dataset.moneyprinterBound = "true";
    const label = button.textContent;
    const generatorLanguage = body.querySelector("#genLanguageSelect");
    const generatorVoice = body.querySelector("#genVoiceSelect");
    const syncGeneratorVoices = () => {
      if (!generatorLanguage || !generatorVoice) return;
      const prefix = `${generatorLanguage.value.split("-")[0]}-`;
      [...generatorVoice.options].forEach(option => { option.hidden = !option.value.startsWith(prefix); });
      if (!generatorVoice.value.startsWith(prefix)) generatorVoice.value = [...generatorVoice.options].find(option => option.value.startsWith(prefix))?.value || "";
    };
    syncGeneratorVoices();
    if (generatorLanguage) generatorLanguage.addEventListener("change", syncGeneratorVoices);
    const channelList = body.querySelector("#genChannelList");
    if (channelList) channelList.addEventListener("change", event => {
      const box = event.target;
      if (box.name !== "genChannel") return;
      const nextChannels = selectedChannels(body);
      const primary = localStorage.getItem(selectedCreateKey(format));
      if (primary && !nextChannels.includes(primary)) { box.checked = true; notify("Troque o canal principal em Criar."); return; }
      if (nextChannels.length > 2) {
        box.checked = false;
        notify("Selecione até dois canais por geração de Shorts.");
        return;
      }
      if (nextChannels.join("|") === (generationDraft.channel_ids || []).join("|")) return;
      generationDraft.channel_ids = nextChannels;
      generationDraft.dirty_params = [];
      applyChannelProfile(body);
      persistGeneratorDraft();
    });
    const channelSelect = body.querySelector("#genChannelSelect");
    if (channelSelect) channelSelect.addEventListener("change", () => {
      const nextChannels = selectedChannels(body);
      if (nextChannels[0]) localStorage.setItem(selectedCreateKey(format), nextChannels[0]);
      if (nextChannels.join("|") === (generationDraft.channel_ids || []).join("|")) return;
      generationDraft.channel_ids = nextChannels;
      generationDraft.dirty_params = [];
      applyChannelProfile(body);
      persistGeneratorDraft();
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
    generationDraft = restoreDraft(body);
    applyProfileAndOverrides();
    updateStats();
    body.querySelectorAll([...DRAFT_TEXT_SELECTORS, ...DRAFT_PARAMETER_SELECTORS].join(",")).forEach(input => {
      const record = () => {
        const selector = `#${input.id}`;
        if (DRAFT_PARAMETER_SELECTORS.includes(selector)) generationDraft.dirty_params = [...new Set([...(generationDraft.dirty_params || []), selector])];
        if (DRAFT_TEXT_SELECTORS.includes(selector)) generationDraft.dirty_text = [...new Set([...(generationDraft.dirty_text || []), selector])];
        persistGeneratorDraft();
      };
      input.addEventListener("input", record);
      input.addEventListener("change", record);
      if (input.id === "genScriptInput") {
        input.addEventListener("input", event => {
          if (!event.isTrusted) return;
          generationDraft.script_origin = "manual";
          persistGeneratorDraft();
        });
      }
    });
    scriptInput.addEventListener("input", () => {
      updateStats();
      if (format === "long" && body.dataset.termsScript && body.dataset.termsScript !== scriptInput.value.trim()) {
        body.dataset.termsState = "stale";
        const terms = body.querySelector("#genTermsInput"); if (terms) terms.value = "";
      }
    });
    const batchStorageKey = `moneyprint-active-generation-${format}`;
    const pendingStorageKey = `moneyprint-pending-generation-${format}`;
    const readStored = key => { try { return JSON.parse(localStorage.getItem(key) || "null"); } catch (_) { return null; } };
    const batchBox = body.querySelector("#genBatchResults");
    const batchStatus = body.querySelector("#genBatchStatus");
    const stageLabels = { queued: "Na fila", repairing: "Corrigindo vídeo", topic: "Roteirista: tema", topic_done: "Tema pronto", requirements: "Roteirista: requisitos", requirements_done: "Requisitos prontos", script: "Roteirista: roteiro", script_done: "Roteiro pronto", orthographic_review: "Agente ortográfico", orthographic_review_done: "Ortografia revisada", editorial_review: "Revisor editorial", editorial_repair: "Aprimorando o roteiro", editorial_review_done: "Revisão editorial concluída", terms: "Planejando cenas", terms_done: "Cenas planejadas", visual_curator: "Curador visual: avaliando materiais", visual_curator_done: "Materiais aprovados", submitting: "Enviando render", rendering: "Renderizando", failed: "Falhou", paused: "Pausado para revisão", reconciliation_required: "Verificação necessária" };
    let displayedBatch = null;
    let pollTimer = null;
    const renderBatch = batch => {
      if (!body.isConnected || !batchBox) return;
      displayedBatch = batch;
      if (batchStatus) batchStatus.textContent = batch.items.some(item => item.state === "paused" || ["blocked", "error"].includes(item.quality?.state)) ? "Pausado para revisão" : ({ processing: "Em andamento", completed: "Concluído", failed: "Falhou", partial_failure: "Falha parcial" }[batch.status] || batch.status);
      const running = batch.status === "processing";
      button.disabled = running;
      button.textContent = running ? "Gerando por canal…" : label;
      const prepProgress = { queued: 0, repairing: 5, topic: 5, topic_done: 12, requirements: 16, requirements_done: 25, script: 30, script_done: 40, orthographic_review: 41, orthographic_review_done: 42, editorial_review: 43, editorial_repair: 46, editorial_review_done: 48, terms: 50, terms_done: 55, visual_curator: 58, visual_curator_done: 68, submitting: 70, rendering: 72 };
      const percentages = batch.items.map(item => item.quality?.state === "approved" ? 100 : ["completed", "approved"].includes(item.job_state) ? 90 : item.job_state ? 72 + 0.18 * (item.job_progress || 0) : prepProgress[item.stage] || 0);
      const progress = Math.round(percentages.reduce((sum, current) => sum + current, 0) / Math.max(percentages.length, 1));
      const pipelineNote = body.querySelector("#genPipelineNote");
      const progressLabel = body.querySelector("#genProgLabel");
      const progressPct = body.querySelector("#genProgPct");
      const progressFill = body.querySelector("#genProgFill");
      const videoTitle = body.querySelector("#genVideoTitle");
      const globalStop = body.querySelector("#genStopBtn");
      if (pipelineNote) pipelineNote.textContent = `${batch.items.length} ${batch.items.length === 1 ? "canal" : "canais"} · ${batchStatus?.textContent || batch.status}`;
      if (progressLabel) progressLabel.textContent = running ? "Preparando e renderizando por canal" : (batchStatus?.textContent || batch.status);
      if (progressPct) progressPct.textContent = `${progress}%`;
      if (progressFill) progressFill.style.width = `${progress}%`;
      if (videoTitle) videoTitle.textContent = batch.items.map(item => item.channel_name).join(" · ");
      if (globalStop && batch.items.length > 1) globalStop.style.display = "none";
      batchBox.innerHTML = batch.items.map(item => {
        const generated = item.generated || {};
        const activeBatchId = item.active_batch_id || batch.batch_id;
        const repair = generated.repair_plan;
        const fields = [["Tema", generated.subject], ["Requisitos personalizados", generated.requirements], ["Roteiro", generated.script], ["Palavras-chave das filmagens", generated.terms]];
        const scenes = (generated.scenes || []).map(scene => `<div style="border:1px solid var(--border);border-radius:8px;padding:10px;margin-top:8px">
          <strong>Trecho ${esc(scene.index + 1)}</strong> · ${esc(scene.query || "")}<div class="card-note" style="white-space:pre-wrap;margin-top:4px">${esc(scene.narration || "")}</div>
          <div style="display:flex;gap:10px;overflow:auto;margin-top:8px">${(scene.candidates || []).map(candidate => `<div style="min-width:140px;max-width:180px">
            ${candidate.filename ? `<img alt="Prévia do material ${esc(candidate.provider)}" loading="lazy" style="width:140px;height:90px;object-fit:cover;border-radius:6px" src="${API}/generation-batches/${encodeURIComponent(activeBatchId)}/items/${encodeURIComponent(item.id)}/scenes/${scene.index}/preview?candidate_id=${encodeURIComponent(candidate.id)}" />` : ""}
            <div class="card-note">${esc(candidate.provider)} · ${esc(candidate.status === "approved" ? "Aprovado" : "Rejeitado")}${candidate.id === scene.selected ? " · Selecionado" : ""}</div>
            ${candidate.id === scene.selected && scene.selection_reason ? `<div class="card-note">${esc(scene.selection_reason)}</div>` : ""}
            <div class="card-note">${esc(candidate.reason || "")}</div>
            ${candidate.source_page ? `<a href="${esc(candidate.source_page)}" target="_blank" rel="noopener noreferrer">Fonte</a>` : ""}
            ${(item.state === "paused" || ["blocked", "error"].includes(item.quality?.state)) && candidate.filename ? `<button class="btn sm" type="button" data-select-scene="${scene.index}" data-select-candidate="${esc(candidate.id)}" data-select-item="${esc(item.id)}">Usar este material</button>` : ""}
          </div>`).join("")}</div></div>`).join("");
        const report = item.quality?.report || {};
        const qualityReason = report.reason || (report.videos || []).filter(video => !video.approved).map(video => video.reason).filter(Boolean).join(" · ");
        const artifactUrl = item.job_state !== "deleted" && item.artifact_id && item.job_id ? `${API}/jobs/${encodeURIComponent(item.job_id)}/artifacts/${encodeURIComponent(item.artifact_id)}` : "";
        const retry = ["failed", "paused"].includes(item.state) ? `<button class="btn sm" type="button" data-retry-item="${esc(item.id)}">${item.state === "paused" ? "Buscar novamente e retomar" : "Repetir este canal"}</button>` : ["failed", "interrupted"].includes(item.job_state) ? `<button class="btn sm" type="button" data-retry-job="${esc(item.job_id)}">Repetir render deste canal</button>` : "";
        const stop = ["queued", "running"].includes(item.job_state) ? `<button class="btn sm" type="button" data-stop-batch-job="${esc(item.job_id)}">Parar este canal</button>` : "";
        return `<section style="border:1px solid var(--border);border-radius:12px;padding:14px;margin-bottom:12px;min-width:0">
          <div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap"><strong>${esc(item.channel_name)}</strong><span class="card-note">${esc(stageLabels[item.stage] || item.stage)}${item.job_progress != null ? ` · ${esc(Math.round(item.job_progress))}%` : ""}${item.job_state ? ` · ${esc(item.job_state)}` : ""}</span></div>
          ${item.repair_attempt ? `<div class="card-note" style="margin-top:8px">Correção automática ${esc(item.repair_attempt)} de ${esc(item.repair_max_attempts)}${repair?.reason ? ` · ${esc(repair.reason)}` : ""}</div>` : ""}
          ${(item.repair_history || []).length > 1 ? `<details style="margin-top:8px"><summary>Histórico das montagens</summary>${item.repair_history.map(entry => `<div class="card-note" style="margin-top:6px">Tentativa ${esc(entry.attempt)} · ${esc(entry.quality_state || entry.state || "preparando")}${entry.reason ? ` · ${esc(entry.reason)}` : ""}${entry.job_id ? ` · trabalho ${esc(entry.job_id)}` : ""}</div>`).join("")}</details>` : ""}
          ${item.job_id && item.job_state !== "deleted" ? `<button class="btn sm" type="button" data-open-active-job="${esc(item.job_id)}">Abrir trabalho atual</button>` : ""}
          ${item.error || item.job_error ? `<p style="color:var(--err);white-space:pre-wrap">${esc(item.error || item.job_error)}</p>` : ""}
          ${fields.map(([name, content]) => content ? `<details style="margin-top:10px"><summary style="cursor:pointer">${esc(name)}</summary><div style="white-space:pre-wrap;overflow-wrap:anywhere;margin-top:7px;max-height:280px;overflow:auto">${esc(content)}</div></details>` : "").join("")}
          ${generated.editorial_review ? `<div class="card-note" style="margin-top:10px">Revisor editorial: ${esc(generated.editorial_review.approved ? "aprovado" : "revisão necessária")} · ${esc(generated.editorial_review.reason || "")}</div>` : ""}
          ${generated.editorial_review?.narration ? `<div class="card-note" style="margin-top:6px">Narração: ${esc(generated.editorial_review.narration.words)} palavras · cerca de ${esc(generated.editorial_review.estimated_seconds)} s · ${esc(generated.editorial_review.narration.long_sentences)} frases longas${generated.editorial_repair_attempted ? " · roteiro passou por correção editorial automática" : ""}</div>` : ""}
          ${item.state === "paused" && generated.editorial_review?.approved === false && generated.script ? `<button class="btn sm" type="button" data-regenerate-script="${esc(item.id)}">Substituir roteiro por uma versão alinhada ao tema</button>` : ""}
          ${scenes ? `<details style="margin-top:10px"><summary>Curadoria visual · ${(generated.scenes || []).length} trechos</summary>${scenes}</details>` : ""}
          ${generated.scenes?.length ? `<div class="card-note">${generated.scenes.filter(scene => scene.selected).length} de ${generated.scenes.length} cenas selecionadas por relevância e variedade.</div>` : ""}
          ${item.quality ? `<div class="card-note" style="margin-top:10px;${["blocked", "error"].includes(item.quality.state) ? "color:var(--err)" : ""}">Revisor de montagem: ${esc(item.quality.state)}${qualityReason ? ` · ${esc(qualityReason)}` : ""}${report.repair_error ? ` · ${esc(report.repair_error)}` : ""}${(report.videos || []).map((video, index) => ` · Vídeo ${index + 1}: ${video.approved ? "aprovado" : "revisão necessária"}`).join("")}</div>` : ""}
          ${(report.videos || []).filter(video => video.audio_analysis).map(video => `<div class="card-note">Áudio: ${video.audio_analysis.audible ? "volume audível" : "volume muito baixo"} · ${video.audio_analysis.continuous ? "sem silêncio prolongado" : "silêncio prolongado detectado"}${video.audio_analysis.longest_silence_seconds != null ? ` · maior silêncio: ${esc(video.audio_analysis.longest_silence_seconds)} s` : ""}</div>`).join("")}
          ${generated.orthographic_review ? `<div class="card-note" style="margin-top:8px;${generated.orthographic_review.approved ? "" : "color:var(--err)"}">Agente ortográfico: ${generated.orthographic_review.approved ? (generated.orthographic_review.corrected ? "texto corrigido antes da voz e das legendas" : "texto revisado") : esc(generated.orthographic_review.reason || "revisão necessária")}${(generated.orthographic_review.corrections || []).length ? ` · ${generated.orthographic_review.corrections.map(esc).join("; ")}` : ""}</div>` : ""}
          ${(item.state === "paused" || ["blocked", "error"].includes(item.quality?.state)) && generated.script ? `<textarea class="np-input" data-review-script="${esc(item.id)}" style="width:100%;min-height:120px;margin-top:10px">${esc(generated.orthographic_review?.suggested_script || generated.script)}</textarea><button class="btn sm" data-submit-script="${esc(item.id)}">Salvar roteiro revisado e retomar</button>` : ""}
          ${(item.state === "paused" || ["blocked", "error"].includes(item.quality?.state)) && generated.terms ? `<div style="margin-top:10px"><label class="dr-sec">Revisar palavras-chave dos cortes</label><textarea class="np-input" data-review-terms="${esc(item.id)}" style="width:100%;min-height:60px">${esc(generated.terms)}</textarea><button class="btn sm" data-submit-terms="${esc(item.id)}">Buscar com estes termos</button></div>` : ""}
          ${artifactUrl ? `<video controls preload="metadata" style="display:block;max-width:100%;width:${format === "short" ? "260" : "500"}px;max-height:300px;margin-top:12px" src="${artifactUrl}/stream"></video><a class="btn sm" style="display:inline-block;margin-top:8px" href="${artifactUrl}/download">Baixar MP4</a>` : ""}
          ${item.quality?.state === "blocked" && !report.repair_error && (item.repair_attempt || 0) < (item.repair_max_attempts || 0) ? `<button class="btn sm" type="button" data-resume-auto-repair="${esc(item.id)}">Retomar correção automática</button>` : ""}
          ${item.quality?.state === "error" && item.job_state === "completed" ? `<button class="btn sm" type="button" data-retry-quality="${esc(item.id)}">Repetir análise do vídeo</button>` : ""}
          ${retry}${stop}</section>`;
      }).join("");
      if (batch.items.length === 1) {
        const generated = batch.items[0].generated || {};
        [["#genTopicInput", "subject"], ["#genScriptRequirements", "requirements"], ["#genScriptInput", "script"], ["#genTermsInput", "terms"]].forEach(([selector, key]) => {
          const input = body.querySelector(selector);
          if (input && generated[key] && !(generationDraft.dirty_text || []).includes(selector)) input.value = generated[key];
        });
        updateStats();
      }
    };
    const pollBatch = async batchId => {
      if (!body.isConnected || !batchId) return;
      try {
        const batch = await request(`/generation-batches/${encodeURIComponent(batchId)}`);
        renderBatch(batch);
        if (batch.status !== "processing" && batch.items.some(item => item.job_id)) syncVideos();
        if (batch.status === "processing") pollTimer = setTimeout(() => pollBatch(batchId), 4000);
      } catch (error) {
        if (batchStatus) batchStatus.textContent = error.message;
        pollTimer = setTimeout(() => pollBatch(batchId), 8000);
      }
    };
    const resume = readStored(batchStorageKey);
    if (resume && resume.batch_id) pollBatch(resume.batch_id);
    if (batchBox) batchBox.addEventListener("click", async event => {
      const retryItem = event.target.closest("[data-retry-item]");
      const retryRender = event.target.closest("[data-retry-job]");
      const stopRender = event.target.closest("[data-stop-batch-job]");
      const resumeRepair = event.target.closest("[data-resume-auto-repair]");
      const retryQuality = event.target.closest("[data-retry-quality]");
      const openActive = event.target.closest("[data-open-active-job]");
      const regenerateScript = event.target.closest("[data-regenerate-script]");
        const selectScene = event.target.closest("[data-select-scene]");
        const submitScript = event.target.closest("[data-submit-script]");
        const submitTerms = event.target.closest("[data-submit-terms]");
        if (!displayedBatch || (!retryItem && !retryRender && !stopRender && !selectScene && !submitScript && !submitTerms && !resumeRepair && !retryQuality && !openActive && !regenerateScript)) return;
      event.target.disabled = true;
      try {
        if (openActive) { await openJob(openActive.dataset.openActiveJob); return; }
        let reviewResult = null;
        const current = displayedBatch.items.find(item => item.id === (selectScene?.dataset.selectItem || submitScript?.dataset.submitScript || submitTerms?.dataset.submitTerms || retryItem?.dataset.retryItem || resumeRepair?.dataset.resumeAutoRepair || retryQuality?.dataset.retryQuality));
        const regenCurrent = displayedBatch.items.find(item => item.id === regenerateScript?.dataset.regenerateScript);
        const activeBatchId = current?.active_batch_id || displayedBatch.batch_id;
        if (selectScene) reviewResult = await request(`/generation-batches/${encodeURIComponent(activeBatchId)}/items/${encodeURIComponent(selectScene.dataset.selectItem)}/review`, { method: "POST", body: JSON.stringify({ scene_index: Number(selectScene.dataset.selectScene), candidate_id: selectScene.dataset.selectCandidate }) });
        else if (submitScript) reviewResult = await request(`/generation-batches/${encodeURIComponent(activeBatchId)}/items/${encodeURIComponent(submitScript.dataset.submitScript)}/review`, { method: "POST", body: JSON.stringify({ script: batchBox.querySelector(`[data-review-script="${CSS.escape(submitScript.dataset.submitScript)}"]`).value }) });
        else if (submitTerms) reviewResult = await request(`/generation-batches/${encodeURIComponent(activeBatchId)}/items/${encodeURIComponent(submitTerms.dataset.submitTerms)}/review`, { method: "POST", body: JSON.stringify({ terms: batchBox.querySelector(`[data-review-terms="${CSS.escape(submitTerms.dataset.submitTerms)}"]`).value }) });
        else if (resumeRepair) reviewResult = await request(`/generation-batches/${encodeURIComponent(activeBatchId)}/items/${encodeURIComponent(resumeRepair.dataset.resumeAutoRepair)}/auto-repair`, { method: "POST" });
        else if (regenerateScript) reviewResult = await request(`/generation-batches/${encodeURIComponent(regenCurrent?.active_batch_id || displayedBatch.batch_id)}/items/${encodeURIComponent(regenerateScript.dataset.regenerateScript)}/regenerate-script`, { method: "POST" });
        else if (retryQuality) await request(`/generation-batches/${encodeURIComponent(activeBatchId)}/items/${encodeURIComponent(retryQuality.dataset.retryQuality)}/retry-quality`, { method: "POST" });
        else if (retryItem) await request(`/generation-batches/${encodeURIComponent(activeBatchId)}/items/${encodeURIComponent(retryItem.dataset.retryItem)}/retry`, { method: "POST" });
        else if (retryRender) await retryJob(retryRender.dataset.retryJob);
        else await stopJob(stopRender.dataset.stopBatchJob);
        if (pollTimer) clearTimeout(pollTimer);
        const nextBatchId = reviewResult?.batch_id || displayedBatch.batch_id;
        localStorage.setItem(batchStorageKey, JSON.stringify({ batch_id: nextBatchId }));
        pollBatch(nextBatchId);
      } catch (error) { notify(error.message); event.target.disabled = false; }
    });
    button.addEventListener("click", async () => {
      const channels = selectedChannels(body);
      if (!channels.length) { notify("Selecione ao menos um canal antes de gerar."); return; }
      if (format === "short" && channels.length > 2) { notify("Selecione até dois canais para esta geração."); return; }
      button.disabled = true;
      button.textContent = "Preparando geração…";
      try {
        let pending = readStored(pendingStorageKey);
        if (!pending) {
          const settings = {};
          const numericSettings = new Set(["voice_rate", "voice_volume", "paragraph_number", "target_minutes", "video_clip_duration", "video_count", "video_clip_speed", "font_size", "stroke_width", "bgm_volume"]);
          (generationDraft.dirty_params || []).forEach(selector => {
            const input = body.querySelector(selector);
            if (!input || !GENERATOR_SETTING_FIELDS[selector]) return;
            const key = GENERATOR_SETTING_FIELDS[selector];
            settings[key] = input.type === "checkbox" ? input.checked : numericSettings.has(key) ? Number(input.value) : input.value;
          });
          pending = { payload: {
            format, channel_ids: channels, client_request_id: crypto.randomUUID(),
            subject: generationDraft.dirty_text?.includes("#genTopicInput") ? value(body, "#genTopicInput", "").trim() : "",
            script: generationDraft.dirty_text?.includes("#genScriptInput") ? value(body, "#genScriptInput", "").trim() : "",
            script_is_manual: generationDraft.dirty_text?.includes("#genScriptInput")
              ? generationDraft.script_origin !== "generated" : false,
            requirements: generationDraft.dirty_text?.includes("#genScriptRequirements") ? value(body, "#genScriptRequirements", "").trim() : "",
            terms: generationDraft.dirty_text?.includes("#genTermsInput") ? value(body, "#genTermsInput", "").trim() : "",
            screen_system_prompt: value(body, "#genSystemPrompt", "").trim(), settings,
          } };
          localStorage.setItem(pendingStorageKey, JSON.stringify(pending));
        }
        const project = await projectFor(body);
        const batch = await request(`/projects/${project.id}/jobs/auto`, { method: "POST", body: JSON.stringify(pending.payload) });
        localStorage.setItem(batchStorageKey, JSON.stringify({ batch_id: batch.batch_id }));
        localStorage.removeItem(pendingStorageKey);
        if (pollTimer) clearTimeout(pollTimer);
        pollBatch(batch.batch_id);
      } catch (error) { notify(error.message); button.disabled = false; button.textContent = label; }
    });
    bindAiHelpers(body, notify, () => {
      generationDraft.script_origin = "generated";
      persistGeneratorDraft();
    });
  }

  /* Mirrors the WebUI's "generate script" / "generate keywords" buttons via
     Turbo's /api/v1/scripts and /api/v1/terms (proxied by the Gateway). */
  function bindAiHelpers(body, notify, markGeneratedScript = () => {}) {
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
      const result = await request("/scripts/short", { method: "POST", body: JSON.stringify({
        subject, language: value(body, "#genLanguageSelect", "pt-BR"), paragraph_number: integer(value(body, "#genParagraphInput", 5), 5),
        voice_rate: Number.parseFloat(value(body, "#genVoiceRate", "1")) || 1,
        voice_name: value(body, "#genVoiceSelect", ""),
        script_prompt: value(body, "#genScriptRequirements", "").trim(),         system_prompt: [channelById(selectedChannels(body)[0])?.system_prompt, value(body, "#genSystemPrompt", "").trim()].filter(Boolean).join("\n\n"),
        proposal: value(body, "#genProposal", "").trim(), reference_brief: body.dataset.referenceBrief || "", persona: channelById(selectedChannels(body)[0])?.persona || "",
      }) });
      body.querySelector("#genScriptInput").value = result.script || "";
      markGeneratedScript();
      body.querySelector("#genScriptInput").dispatchEvent(new Event("input")); // saves the draft and refreshes the word count
      if (result.trimmed) notify("Roteiro cortado para caber na duração alvo do Short.");
      else if (result.too_short) notify(`Roteiro com ${result.words} palavras (≈ ${Math.round(result.words / 2.5)} s) — curto para 60–90 s. Gere de novo ou complete o texto.`);
    });
    // One search term per script chunk, in narration order, instead of a handful of generic
    // terms shared by the whole video - see visual_terms() in the gateway.
    const alignTermsToScript = async subject => {
      const script = value(body, "#genScriptInput", "").trim();
      if (!script) return null;
      const result = await request("/scripts/terms", { method: "POST", body: JSON.stringify({
        subject, script, clip_seconds: integer(value(body, "#genDurationInput", 5), 5), voice_rate: Number.parseFloat(value(body, "#genVoiceRate", "1")) || 1,
      }) });
      body.querySelector("#genTermsInput").value = result.terms.join(", ");
      body.querySelector("#genTermsInput").dispatchEvent(new Event("input"));
      return result;
    };
    const termsState = (state, script = "") => {
      body.dataset.termsState = state;
      if (script) body.dataset.termsScript = script;
      if (state === "idle") { delete body.dataset.termsScript; }
      body.dispatchEvent(new Event("moneyprinter:terms-state"));
    };
    const scheduleAlignedTerms = async (subject, { announce = false } = {}) => {
      const script = value(body, "#genScriptInput", "").trim();
      if (!script) { termsState("idle"); return null; }
      if (body.dataset.termsState === "pending" && body.dataset.termsScript === script) return null;
      const termsBtn = body.querySelector("#genTermsAiBtn");
      termsState("pending", script);
      if (termsBtn) { termsBtn.disabled = true; termsBtn.textContent = "Preparando filmagens…"; }
      try {
        const result = await alignTermsToScript(subject);
        if (value(body, "#genScriptInput", "").trim() !== script) {
          termsState("stale");
          if (announce) notify("O roteiro foi alterado; gere as palavras-chave novamente.");
          return null;
        }
        termsState("ready", script);
        if (announce) notify(`${result.cuts} cortes de ${result.clip_seconds}s alinhados ao roteiro.`);
        return result;
      } catch (error) {
        if (value(body, "#genScriptInput", "").trim() === script) termsState("failed", script);
        throw error;
      } finally {
        if (termsBtn) { termsBtn.disabled = false; termsBtn.textContent = "Gerar palavras-chave"; }
      }
    };
    run("#genLongScriptBtn", "Escrever roteiro longo com IA", async subject => {
      const channel = channelById(selectedChannels(body)[0]);
      const result = await streamLongScript({
        subject, language: value(body, "#genLanguageSelect", "pt-BR"), minutes: integer(value(body, "#genTargetMinutes", 8), 8), niche: (channel && channel.niche) || "",
        voice_rate: Number.parseFloat(value(body, "#genVoiceRate", "1")) || 1,
        voice_name: value(body, "#genVoiceSelect", ""),
        requirements: value(body, "#genScriptRequirements", "").trim(), system_prompt: [channel && channel.system_prompt, value(body, "#genSystemPrompt", "").trim()].filter(Boolean).join("\n\n"),
        proposal: value(body, "#genProposal", "").trim(), reference_brief: body.dataset.referenceBrief || "", persona: channel?.persona || "",
      }, (_event, progress) => {
        const btn = body.querySelector("#genLongScriptBtn");
        if (btn && progress.message) btn.textContent = progress.message;
      });
      body.querySelector("#genScriptInput").value = result.script || "";
      markGeneratedScript();
      body.querySelector("#genScriptInput").dispatchEvent(new Event("input"));
      notify(`Roteiro com ${result.sections.length} seções · ≈ ${result.estimated_minutes} min de narração${result.trimmed ? " · cortado para caber na duração alvo" : ""}`);
      scheduleAlignedTerms(subject).catch(error => notify(`Palavras-chave não foram geradas: ${error.message}`));
    });
    run("#genProposalBtn", "Pesquisar ângulo", async subject => {
      const channel = channelById(selectedChannels(body)[0]);
      const format = body.querySelector("[data-format]")?.dataset.format === "long" ? "long" : "short";
      const result = await request("/scripts/proposal", { method: "POST", body: JSON.stringify({
        subject, format, language: value(body, "#genLanguageSelect", "pt-BR"), channel_context: [channel?.niche, channel?.persona, channel?.system_prompt].filter(Boolean).join("\n").slice(0, 2000),
      }) });
      const box = body.querySelector("#genProposal");
      if (box) { box.value = result.brief; box.dispatchEvent(new Event("input")); }
      notify(result.angle);
    });
    const referenceFile = body.querySelector("#genReferenceFile");
    if (referenceFile && !referenceFile.dataset.bound) {
      referenceFile.dataset.bound = "true";
      referenceFile.addEventListener("change", async () => {
        const chosen = referenceFile.files && referenceFile.files[0];
        if (!chosen) return;
        const note = body.querySelector("#genReferenceNote");
        if (note) note.textContent = "Lendo a estrutura do vídeo…";
        try {
          const result = await request("/scripts/reference", { method: "POST", headers: { "Content-Type": chosen.type || "video/mp4" }, body: chosen });
          body.dataset.referenceBrief = result.brief;
          if (note) note.textContent = result.brief;
        } catch (error) {
          delete body.dataset.referenceBrief;
          if (note) note.textContent = "";
          notify(error.message);
        }
      });
    }
    run("#genTermsAiBtn", "Gerar palavras-chave", async subject => {
      const result = await scheduleAlignedTerms(subject, { announce: true });
      if (!result && !value(body, "#genScriptInput", "").trim()) notify("Escreva ou gere o roteiro primeiro.");
    });
    bindThumbnailTab(body, notify);
  }

  function bindThumbnailTab(body, notify) {
    const $ = selector => body.querySelector(selector), channelSelect = $("#thbChannel"), videoSelect = $("#thbVideo");
    if (!channelSelect || !videoSelect || channelSelect.dataset.bound) return;
    channelSelect.dataset.bound = "true";
    const videos = () => window.CK.VIDEOS[screenFormat(body) === "long" ? "horiz" : "vert"];
    const fillVideos = () => {
      const selected = videoSelect.value;
      videoSelect.innerHTML = `<option value="">Selecione um vídeo concluído…</option>` + videos().map(v => `<option value="${esc(v[4])}">${esc(v[2])}</option>`).join("");
      videoSelect.value = videos().some(v => v[4] === selected) ? selected : "";
    };
    const show = job => {
      $("#thbResult").hidden = !(job && job.thumbnail_updated_at);
      if (job && job.thumbnail_updated_at) {
        const url = `${API}/jobs/${job.id}/thumbnail?t=${job.thumbnail_updated_at}`;
        $("#thbImg").src = url; $("#thbDownload").href = url;
      }
    };
    channelSelect.onchange = () => { $("#thbSystemPrompt").value = channelById(channelSelect.value)?.thumbnail_prompt || ""; };
    videoSelect.onchange = async () => { show(videoSelect.value ? await request(`/jobs/${videoSelect.value}`).catch(() => null) : null); };
    const savePrompt = async () => {
      const channel = channelById(channelSelect.value);
      if (!channel) throw new Error("Selecione o canal.");
      const prompt = $("#thbSystemPrompt").value.trim();
      if (prompt !== (channel.thumbnail_prompt || "")) await saveChannel(channel.id, { thumbnail_prompt: prompt });
    };
    $("#thbSaveBtn").onclick = async () => {
      try { await savePrompt(); $("#thbSaveNote").textContent = "Salvo no canal."; }
      catch (error) { notify(error.message); }
    };
    $("#thbGenBtn").onclick = async event => {
      const button = event.currentTarget; button.disabled = true;
      try {
        if (!videoSelect.value) throw new Error("Selecione um vídeo concluído.");
        await savePrompt();
        const job = await request(`/jobs/${videoSelect.value}/thumbnail`, { method: "POST", body: JSON.stringify({ channel_id: channelSelect.value, prompt: $("#thbIdea").value.trim() }) });
        show(job); notify("Thumbnail gerada.");
      } catch (error) { notify(error.message); }
      finally { button.disabled = false; }
    };
    $("[data-gentab='thumbnail']")?.addEventListener("click", fillVideos);
    fillVideos();
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
        const publishAt = value(body, "#ytPubAt", "");
        if (status) status.textContent = publishAt ? "Agendando…" : "Enviando…";
        const published = await publish(job, [channelId], publishAt);
        if (published === job) { if (status) status.textContent = ""; return; } // operator cancelled the re-send
        const pub = (published.publications || []).find(p => p.channel_id === channelId);
        if (status) status.textContent = pub ? publicationLine(pub) : "";
        notify(pub && pub.state === "scheduled" ? "Publicação agendada." : pub && pub.state === "processing" ? "Vídeo enviado; o TikTok ainda está processando." : "Vídeo publicado.");
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
      niche: c.niche, language: c.language, publish_privacy: c.publish_privacy || "private", system_prompt: c.system_prompt || "", visual_guides: c.visual_guides || { short: "", long: "" }, voice_name: c.voice_name, voice_rate: c.voice_rate, voice_volume: c.voice_volume,
      persona: c.persona || "", visual_style: c.visual_style || "", image_source: c.image_source || "", thumbnail_prompt: c.thumbnail_prompt || "",
      subtitle_enabled: c.subtitle_enabled, subtitle_position: c.subtitle_position, font_size: c.font_size, text_fore_color: c.text_fore_color,
      stroke_color: c.stroke_color, stroke_width: c.stroke_width, video_defaults: c.video_defaults, followers: stats.followers || 0, videos: stats.videos || 0, views: stats.views || 0, last: "—" };
  }

  /* Refills the channel selects in place: re-rendering Gerador would wipe what the user typed. */
  function fillChannelSelects() {
    const connected = window.CK.CHANNELS.filter(c => c.status === "connected");
    const youtube = connected.filter(c => c.platform === "YouTube");
    const option = c => `<option value="${esc(c.id)}">${esc(c.name)} · ${esc(c.platform)}</option>`;
    // Long videos only go to YouTube; Shorts to any connected channel.
    [["#genChannelSelect", youtube, "Selecione um canal"], ["#ytLibChannelSelect", screenFormat(document) === "long" ? youtube : connected, "Selecione um canal…"], ["#thbChannel", screenFormat(document) === "long" ? youtube : connected, "Selecione um canal…"], ["#agStrategyChannel", connected, "Selecione um canal…"]].forEach(([selector, list, empty]) => {
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
  const youtubeConnectUrl = rec => `${API}/channels/youtube/connect?${new URLSearchParams({ niche: rec.niche || "", system_prompt: rec.system_prompt || "", persona: rec.persona || "", visual_style: rec.visual_style || "", image_source: rec.image_source || "", thumbnail_prompt: rec.thumbnail_prompt || "", ...channelProfilePayload(rec), video_defaults: JSON.stringify(rec.video_defaults || {}), visual_guides: JSON.stringify(rec.visual_guides || {}) })}`;

  async function connectChannel(rec, win) {
    if (rec.platform !== "TikTok") { openIn(win, youtubeConnectUrl(rec)); return; }
    const result = await request("/channels/tiktok/connect", { method: "POST", body: JSON.stringify({ niche: rec.niche || "", system_prompt: rec.system_prompt || "", persona: rec.persona || "", visual_style: rec.visual_style || "", image_source: rec.image_source || "", thumbnail_prompt: rec.thumbnail_prompt || "", video_defaults: rec.video_defaults || {}, visual_guides: rec.visual_guides || {}, ...channelProfilePayload(rec) }) });
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

  /* ── Agentes: each pane loads from the Gateway when its tab opens ── */
  const localInput = iso => { // ISO instant -> datetime-local value in the viewer's zone
    if (!iso) return "";
    const date = new Date(iso);
    return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  };
  const CADENCES = [[12, "A cada 12 h"], [24, "Diária"], [48, "A cada 2 dias"], [168, "Semanal"]];
  const emptyState = (title, text) => `<div class="empty"><div class="empty-t">${title}</div><div class="empty-s">${text}</div></div>`;
  const rowStyle = "display:flex;justify-content:space-between;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid var(--border)";

  function bindAgents(body, notify) {
    const card = body.querySelector("#agentsCard");
    if (!card || card.dataset.bound) return;
    card.dataset.bound = "true";
    const $ = selector => card.querySelector(selector);
    const busy = async (button, task) => {
      const label = button.textContent;
      button.disabled = true; button.textContent = "Aguarde…";
      try { await task(); } catch (error) { notify(error.message); } finally { button.disabled = false; button.textContent = label; }
    };
    fillChannelSelects();

    let topics = [];
    $("#agStrategyBtn").onclick = () => busy($("#agStrategyBtn"), async () => {
      const channelId = $("#agStrategyChannel").value, format = $("#agStrategyFormat").value;
      if (!channelId) { notify("Conecte e selecione um canal primeiro."); return; }
      topics = (await request("/agents/strategy", { method: "POST", body: JSON.stringify({ channel_id: channelId, format }) })).topics;
      $("#agStrategyList").innerHTML = topics.map((t, i) => `
        <div style="border:1px solid var(--border);border-radius:10px;padding:12px 14px">
          <div style="font-weight:600;color:var(--ink)">${esc(t.topic)}</div>
          ${t.angle ? `<div style="font-size:12.5px;margin-top:4px">${esc(t.angle)}</div>` : ""}
          ${t.rationale ? `<div class="card-note" style="font-family:inherit;margin-top:4px">${esc(t.rationale)}</div>` : ""}
          <button class="btn sm" type="button" style="margin-top:8px" data-use-topic="${i}" data-topic-format="${format}">Usar no ${format === "long" ? "Gerador" : "Shorts"}</button>
        </div>`).join("");
    });
    $("#agStrategyList").onclick = event => {
      const button = event.target.closest("[data-use-topic]");
      if (!button) return;
      const format = button.dataset.topicFormat, channelId = $("#agStrategyChannel").value;
      if (!channelId) { notify("Selecione um canal antes de usar o tema."); return; }
      selectCreateChannel(format, channelId);
      const key = channelDraftKey(format, channelId);
      try {
        const previous = JSON.parse(localStorage.getItem(key) || "{}");
        localStorage.setItem(key, JSON.stringify({ ...previous, channel_ids: [channelId], "#genTopicInput": topics[+button.dataset.useTopic].topic, "#genScriptInput": "", "#genTermsInput": "", dirty_text: [...new Set([...(previous.dirty_text || []), "#genTopicInput"])] }));
      }
      catch (_) { notify("Não foi possível guardar o tema neste navegador."); }
      window.CK.__go(format === "long" ? "gerador" : "youtube");
    };

    const duration = seconds => `${Math.floor(seconds / 60)}:${String(Math.round(seconds % 60)).padStart(2, "0")}`;
    const metric = (m, key, format) => m && m[key] != null ? format(m[key]) : "—";
    const renderAnalytics = data => {
      const videos = data.videos || [];
      $("#agAnalyticsBody").innerHTML = (data.errors || []).map(e => `<div class="card-note" style="font-family:inherit;color:var(--warn);margin-bottom:6px">${esc(e.detail)}</div>`).join("")
        + (videos.length ? `<div style="overflow-x:auto"><table class="perm" style="width:100%">
          <thead><tr><th>Vídeo</th><th>Canal</th><th>Views</th><th>Retenção</th><th>Duração média</th><th>Likes</th><th>Inscritos</th><th>Nota</th></tr></thead>
          <tbody>${videos.map(v => `<tr><td style="text-align:left">${esc(v.title)}</td><td>${esc(v.channel_name || "")}</td>
            <td>${metric(v.metrics, "views", String)}</td><td>${metric(v.metrics, "averageViewPercentage", n => Math.round(n) + "%")}</td>
            <td>${metric(v.metrics, "averageViewDuration", duration)}</td><td>${metric(v.metrics, "likes", String)}</td>
            <td>${metric(v.metrics, "subscribersGained", String)}</td><td><b>${v.score ?? "—"}</b></td></tr>`).join("")}</tbody></table></div>`
          : emptyState("Nenhum vídeo publicado no YouTube ainda", "As métricas aparecem aqui depois da primeira publicação."))
        + (data.recommendations || []).filter(r => r.items.length).map(r => `
          <div class="dr-sec" style="margin-top:18px">Recomendações · ${esc(r.channel_name)}</div>
          <ul style="margin:8px 0 0 18px;display:flex;flex-direction:column;gap:6px">${r.items.map(item => `<li>${esc(item)}</li>`).join("")}</ul>`).join("");
    };
    const loadAnalytics = async () => { try { renderAnalytics(await request("/agents/analytics")); } catch (error) { notify(error.message); } };
    $("#agAnalyticsBtn").onclick = () => busy($("#agAnalyticsBtn"), async () => renderAnalytics(await request("/agents/analytics/refresh", { method: "POST" })));

    const loadSchedule = async () => {
      const body = $("#agScheduleBody");
      body.innerHTML = '<div class="empty" role="status" aria-live="polite"><div class="empty-t">Carregando agendamentos…</div></div>';
      let items;
      try { items = await request("/agents/schedule"); } catch (error) {
        body.innerHTML = `<div class="empty" role="alert"><div class="empty-t">Não foi possível carregar os agendamentos</div><div class="empty-d">${esc(error.message)} <button class="btn sm" type="button" data-reload-schedule> Tentar novamente</button></div></div>`;
        return;
      }
      body.innerHTML = items.length ? items.map(s => `
        <div style="${rowStyle}">
          <div style="min-width:0"><div style="font-weight:500;color:var(--ink)">${esc(s.title || s.subject)}</div>
            <div class="card-note" style="font-family:inherit">${esc(s.channel_name || s.platform)} · ${esc(when(s.scheduled_at))}</div></div>
          ${s.auto_publish ? '<span class="card-note">Publicação automática no YouTube</span>' : '<button class="btn sm" type="button" data-cancel-job="' + esc(s.job_id) + '" data-cancel-channel="' + esc(s.channel_id) + '">Cancelar</button>'}
        </div>`).join("") : emptyState("Nada agendado", "Aprove um vídeo e preencha “Agendar para” no Gerador ou no Shorts.");
    };
    $("#agScheduleBody").onclick = event => {
      if (event.target.closest("[data-reload-schedule]")) { loadSchedule(); return; }
      const button = event.target.closest("[data-cancel-job]");
      if (!button) return;
      busy(button, async () => {
        await request(`/jobs/${button.dataset.cancelJob}/schedule?channel_id=${encodeURIComponent(button.dataset.cancelChannel)}`, { method: "DELETE" });
        notify("Agendamento cancelado; o vídeo voltou para aguardando publicação.");
        await loadSchedule();
      });
    };

    const reviewOperatorJob = async jobId => {
      const job = await request(`/jobs/${encodeURIComponent(jobId)}`);
      if (job.state !== "completed" || !(job.artifacts || []).some(item => item.kind === "video")) {
        throw new Error("Este vídeo ainda não está pronto para revisão.");
      }
      const targetChannel = (job.publications || []).find(publication => publication.channel_id)?.channel_id || job.channel_id;
      if (targetChannel) selectCreateChannel(job.format, targetChannel);
      window.CK.__go(job.format === "long" ? "gerador" : "youtube");
      const generator = document.getElementById("viewBody");
      setPipeline(generator, job);
      showArtifact(generator, job);
      generator.querySelector("#genMetaBox")?.scrollIntoView({ block: "nearest" });
    };

    const loadOperators = async () => {
      let data;
      try { data = await request("/agents/operators"); } catch (error) { notify(error.message); return; }
      const select = (field, options, current) => `<select class="set-select" style="width:100%" data-op-field="${field}">${options.map(([v, label]) => `<option value="${v}" ${String(v) === String(current) ? "selected" : ""}>${label}</option>`).join("")}</select>`;
      $("#agOperatorBody").innerHTML = (data.operators || []).length ? (data.operators || []).map(op => `
        <div class="card" style="margin:0" data-operator="${esc(op.channel_id)}">
          <div class="card-hd"><span class="card-title">${esc(op.channel_name)} · ${op.platform === "tiktok" ? "TikTok" : "YouTube"}</span><span class="spacer"></span>
            <label style="display:flex;align-items:center;gap:7px;font-size:13px"><input type="checkbox" class="cbx" data-op-field="enabled" ${op.enabled ? "checked" : ""} /> Ligado</label></div>
          <div class="card-bd" style="display:flex;flex-direction:column;gap:12px">
            <div style="display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(170px,1fr))">
              <div class="md-field"><label>Formato</label>${select("format", op.platform === "tiktok" ? [["short", "Short"]] : [["short", "Short"], ["long", "Vídeo longo"]], op.format)}</div>
              <div class="md-field"><label>Frequência</label>${select("cadence_hours", CADENCES, op.cadence_hours)}</div>
              <div class="md-field"><label>Próxima execução</label><input class="np-input" style="width:100%" type="datetime-local" data-op-field="next_run_at" value="${localInput(op.next_run_at)}" /></div>
            </div>
            ${op.platform === "youtube" ? '<div class="md-field"><label><input type="checkbox" class="cbx" data-op-field="auto_publish" ' + (op.auto_publish ? "checked" : "") + '> Publicar automaticamente no YouTube</label></div>' : ""}
            ${op.platform === "youtube" ? '<div class="md-field"><label>Dias da publicação pública</label><div style="display:flex;flex-wrap:wrap;gap:10px">' + ["Seg","Ter","Qua","Qui","Sex","Sáb","Dom"].map((day,index) => '<label style="display:flex;align-items:center;gap:4px;font-size:12px"><input type="checkbox" data-publish-day="' + index + '" ' + ((op.publish_days || []).includes(index) ? "checked" : "") + '>' + day + '</label>').join("") + '</div></div>' : ""}
            ${op.platform === "youtube" ? '<div style="display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(170px,1fr))"><div class="md-field"><label>Horário público</label><input class="np-input" type="time" data-op-field="publish_time" value="' + esc(op.publish_time || "12:00") + '"></div><div class="md-field"><label>Fuso IANA</label><input class="np-input" data-op-field="publish_timezone" placeholder="America/Sao_Paulo" value="' + esc(op.publish_timezone || "America/Sao_Paulo") + '"></div></div>' : ""}
            ${op.paused_reason ? '<div class="card-note" style="color:var(--warn)">Pausado: ' + esc(op.paused_reason) + '</div>' : ""}
            ${op.next_publication && op.next_publication.job_id ? '<div class="card-note">Próxima publicação: ' + esc(op.next_publication.subject) + ' · ' + esc(when(op.next_publication.scheduled_at)) + ' · ' + esc(op.next_publication.state) + '</div>' : ""}
            ${op.last_run && op.last_run.id ? '<div class="card-note">Execução registrada: ' + esc(op.last_run.state) + (op.last_run.job_id ? ' · job ' + esc(op.last_run.job_id) : '') + '</div>' : ""}
            <div class="card-note" style="font-family:inherit">Última execução: ${esc(when(op.last_run_at))}${op.last_error ? ` · <span style="color:var(--warn)">${esc(op.last_error)}</span>` : ""}</div>
            <div style="display:flex;gap:8px"><button class="btn sm primary" type="button" data-op-act="save">Salvar</button><button class="btn sm" type="button" data-op-act="run">Rodar agora</button></div>
          </div>
        </div>`).join("") : emptyState("Nenhum canal conectado", "Conecte um canal em Canais para configurar o operador.");
      const workLabels = { queued: "Na fila", running: "Gerando", failed: "Falhou", interrupted: "Interrompido" };
      $("#agInProgressBody").innerHTML = (data.in_progress || []).length ? data.in_progress.map(job => `
        <div style="${rowStyle}">
          <div style="min-width:0"><div style="font-weight:500;color:var(--ink)">${esc(job.subject)}</div>
            <div class="card-note" style="font-family:inherit">${esc(job.channel_name || "Canal removido")} · ${esc(workLabels[job.state] || job.state)}${job.error ? ` · ${esc(job.error)}` : ""}</div></div>
          ${job.state === "failed" || job.state === "interrupted" ? '<button class="btn sm" type="button" data-view="jobsWorkers">Ver trabalho</button>' : ""}
        </div>`).join("") : `<div class="card-note" style="font-family:inherit;margin-top:8px">Nenhum trabalho em andamento ou com falha.</div>`;
      $("#agAwaitingBody").innerHTML = (data.awaiting_approval || []).length ? data.awaiting_approval.map(job => `
        <div style="${rowStyle}">
          <div style="min-width:0"><div style="font-weight:500;color:var(--ink)">${esc(job.subject)}</div>
            <div class="card-note" style="font-family:inherit">${esc(job.channel_name || "Canal removido")} · ${job.format === "long" ? "Vídeo longo" : "Short"} · concluído em ${esc(when(job.completed_at))}</div></div>
          <button class="btn sm" type="button" data-review-operator-job="${esc(job.id)}">Revisar</button>
        </div>`).join("") : `<div class="card-note" style="font-family:inherit;margin-top:8px">Nenhum vídeo do operador aguardando.</div>`;
    };
    $("#agOperatorRefreshBtn").onclick = () => busy($("#agOperatorRefreshBtn"), loadOperators);
    $("#agAwaitingBody").onclick = event => {
      const button = event.target.closest("[data-review-operator-job]");
      if (button) busy(button, () => reviewOperatorJob(button.dataset.reviewOperatorJob));
    };
    $("#agOperatorBody").onclick = event => {
      const button = event.target.closest("[data-op-act]");
      if (!button) return;
      const box = button.closest("[data-operator]"), field = name => box.querySelector(`[data-op-field="${name}"]`);
      busy(button, async () => {
        if (button.dataset.opAct === "save") {
          const next = field("next_run_at").value;
          await request(`/agents/operators/${box.dataset.operator}`, { method: "PUT", body: JSON.stringify({
            enabled: field("enabled").checked, format: field("format").value, cadence_hours: Number(field("cadence_hours").value), next_run_at: next ? new Date(next).toISOString() : null,
            auto_publish: field("auto_publish")?.checked || false, publish_days: box.querySelector("[data-publish-day]") ? [...box.querySelectorAll("[data-publish-day]:checked")].map(el => Number(el.dataset.publishDay)) : [0,1,2,3,4,5,6],
            publish_time: field("publish_time")?.value || "12:00", publish_timezone: field("publish_timezone")?.value || "America/Sao_Paulo",
          }) });
          notify("Operador salvo.");
        } else {
          const result = await request(`/agents/operators/${box.dataset.operator}/run`, { method: "POST" });
          notify(result.error || "Execução enviada aos especialistas. Acompanhe em Revisões de vídeo.");
          syncVideos();
        }
        await loadOperators();
      });
    };

    const loadReviews = async () => {
      const batches = await request("/generation-batches?limit=20");
      $("#agReviewsBody").innerHTML = batches.length ? batches.map(batch => `<div style="${rowStyle}">
        <div><strong>${esc(batch.source === "operator" ? "Operador automático" : "Gerador")} · ${esc(batch.format === "short" ? "Shorts" : "Vídeo longo")}</strong>
          <div class="card-note">${esc(when(batch.created_at))} · ${batch.items.map(item => {
            const state = item.quality_state || item.stage || item.state;
            const status = item.repair_attempt && !["approved", "blocked", "error"].includes(state)
              ? `corrigindo ${item.repair_attempt}/${item.repair_max_attempts} · ${state}` : state;
            return `${esc(item.channel_name)}: ${esc(status)}${item.error || item.quality_reason ? ` (${esc(item.error || item.quality_reason)})` : ""}`;
          }).join(" · ")}</div></div>
        <button class="btn sm" type="button" data-open-generation="${esc(batch.batch_id)}" data-generation-format="${esc(batch.format)}">Abrir geração e revisão</button>
      </div>`).join("") : emptyState("Nenhuma geração registrada", "As execuções do gerador e do operador aparecem aqui.");
    };
    $("#agReviewsRefresh").onclick = () => busy($("#agReviewsRefresh"), loadReviews);
    $("#agReviewsClear").onclick = () => {
      const button = $("#agReviewsClear");
      if (!window.confirm("Limpar revisões concluídas? Somente revisões aprovadas sairão desta lista. Os vídeos e trabalhos serão mantidos; itens pendentes ou pausados continuarão aqui.")) return;
      busy(button, async () => {
        const result = await request("/generation-batches/reviews", { method: "DELETE" });
        await loadReviews();
        notify(result.cleared
          ? `${result.cleared} revisão${result.cleared === 1 ? "" : "ões"} concluída${result.cleared === 1 ? "" : "s"} removida${result.cleared === 1 ? "" : "s"} da lista.`
          : "Não há revisões concluídas para limpar.");
      });
    };
    $("#agReviewsBody").onclick = event => {
      const button = event.target.closest("[data-open-generation]");
      if (!button) return;
      localStorage.setItem(`moneyprint-active-generation-${button.dataset.generationFormat}`, JSON.stringify({ batch_id: button.dataset.openGeneration }));
      window.CK.__go(button.dataset.generationFormat === "long" ? "gerador" : "youtube");
    };
    const loaders = { analytics: loadAnalytics, agendamento: loadSchedule, operador: loadOperators, revisoes: loadReviews };
    $("#agTabs").onclick = event => {
      const tab = event.target.closest("[data-agtab]");
      if (!tab) return;
      card.querySelectorAll("[data-agtab]").forEach(item => item.classList.toggle("is-active", item === tab));
      card.querySelectorAll("[data-agpane]").forEach(pane => { pane.hidden = pane.dataset.agpane !== tab.dataset.agtab; });
      if (loaders[tab.dataset.agtab]) loaders[tab.dataset.agtab]();
    };
    if (loaders[card.dataset.initialTab]) loaders[card.dataset.initialTab]();
  }

  /* ── Analytics tab: real gateway data only, no mocked totals. ── */
  function bindAnalytics(body, notify) {
    const root = body.querySelector("#analyticsBody");
    if (!root || root.dataset.bound) return;
    root.dataset.bound = "true";
    const fmt = n => Math.round(Number(n) || 0).toLocaleString("pt-BR");
    const empty = (title, text) => `<div class="card" style="margin-bottom:0"><div class="empty"><div class="empty-t">${title}</div><div class="empty-s">${text}</div></div></div>`;
    const kpi = (label, val, sub) => `<div class="kpi" style="cursor:default"><div class="kpi-label">${label}</div><div class="kpi-value">${val}</div><div class="card-note" style="font-family:inherit">${sub}</div></div>`;
    const render = data => {
      const channels = data.channels || [], jobs = data.jobs || {}, yt = data.youtube || {};
      const top = yt.top || [];
      const topChannels = channels.slice().sort((a, b) => ((b.stats || {}).views || 0) - ((a.stats || {}).views || 0)).slice(0, 6);
      root.innerHTML = `
      <div class="kpi-row">
        ${kpi("Vídeos gerados", fmt(jobs.total), `${fmt(jobs.completed)} concluídos · ${fmt(jobs.active)} em andamento`)}
        ${kpi("Views no YouTube", fmt(yt.total_views), `${fmt(yt.videos_measured)} de ${fmt(yt.videos_published)} vídeos medidos`)}
        ${kpi("Seguidores totais", fmt(data.followers_total), `${channels.length} ${channels.length === 1 ? "canal conectado" : "canais conectados"}`)}
        ${kpi("Nota média", yt.avg_score != null ? String(yt.avg_score) : "—", yt.avg_retention != null ? `retenção média ${yt.avg_retention}%` : "sem medições ainda")}
      </div>
      ${jobs.failed ? `<div class="card-note" style="font-family:inherit;color:var(--warn);margin:0 0 12px">${fmt(jobs.failed)} job(s) com falha ou interrompidos — veja em Jobs e workers.</div>` : ""}
      <div class="grid2">
        <div class="card" style="margin-bottom:0"><div class="card-hd"><span class="card-title">Vídeos por nota</span><span class="spacer"></span><span class="card-note">YouTube Analytics real</span></div>
          ${top.length ? `<table><thead><tr><th>Vídeo</th><th>Canal</th><th class="num">Views</th><th class="num">Retenção</th><th class="num">Nota</th></tr></thead><tbody>${top.map(v => {
            const m = v.metrics || {};
            return `<tr><td style="font-weight:500;color:var(--ink)">${esc(v.title)}</td><td>${esc(v.channel_name || "")}</td><td class="num mono">${m.views != null ? fmt(m.views) : "—"}</td><td class="num mono">${m.averageViewPercentage != null ? Math.round(m.averageViewPercentage) + "%" : "—"}</td><td class="num mono">${v.score ?? "—"}</td></tr>`;
          }).join("")}</tbody></table>` : empty("Nenhum vídeo publicado no YouTube ainda", "As métricas aparecem aqui depois da primeira publicação. Para atualizar, use Agentes → Analytics → Atualizar métricas.")}</div>
        <div class="card" style="margin-bottom:0"><div class="card-hd"><span class="card-title">Canais por views</span><span class="spacer"></span><span class="card-note">top ${topChannels.length}</span></div>
          ${topChannels.length ? `<table><thead><tr><th>Canal</th><th>Plataforma</th><th class="num">Seguidores</th><th class="num">Views</th></tr></thead><tbody>${topChannels.map(c => `<tr><td style="font-weight:500;color:var(--ink)">${esc(c.name)}<div class="card-note" style="font-weight:400">${esc(c.handle || "")}</div></td><td>${esc(c.platform)}</td><td class="num mono">${fmt((c.stats || {}).followers)}</td><td class="num mono">${fmt((c.stats || {}).views)}</td></tr>`).join("")}</tbody></table>` : empty("Nenhum canal conectado", "Conecte um canal do YouTube ou TikTok em Canais.")}</div>
      </div>`;
    };
    request("/analytics/overview").then(render).catch(error => {
      root.innerHTML = `<div class="card" style="margin-bottom:0"><div class="empty"><div class="empty-t">Não foi possível carregar o Analytics</div><div class="empty-s">${esc(error.message)}</div><button class="btn sm" type="button" id="analyticsRetry" style="margin-top:10px">Tentar de novo</button></div></div>`;
      const retry = root.querySelector("#analyticsRetry");
      if (retry) retry.addEventListener("click", () => { delete root.dataset.bound; bindAnalytics(body, notify); });
      if (notify) notify(error.message);
    });
  }

  /* ── Logs e traces: eventos reais persistidos pelo Gateway. ── */
  function bindLogs(body, notify, onOpen) {
    const logBody = body.querySelector("#logBody");
    if (!logBody || logBody.dataset.bound) return;
    logBody.dataset.bound = "true";
    const search = body.querySelector("#lSearch"), level = body.querySelector("#lLevel"), service = body.querySelector("#lSvc");
    const count = body.querySelector("#lCount"), refresh = body.querySelector("#lRefresh");
    const traceBody = body.querySelector("#logTraceBody"), summaryBody = body.querySelector("#logSummaryBody");
    let events = [], visible = [];

    const renderSummary = () => {
      const totals = events.reduce((out, item) => { out[item.lvl] = (out[item.lvl] || 0) + 1; return out; }, {});
      summaryBody.innerHTML = `<dl class="kv" style="margin:0">
        <dt>Total carregado</dt><dd class="mono">${events.length}</dd>
        <dt>Erros</dt><dd class="mono" style="color:var(--err)">${(totals.ERROR || 0) + (totals.CRITICAL || 0)}</dd>
        <dt>Avisos</dt><dd class="mono" style="color:var(--warn)">${totals.WARN || 0}</dd>
        <dt>Informações</dt><dd class="mono">${totals.INFO || 0}</dd>
      </dl>`;
      const latest = events[0];
      if (!latest) { traceBody.innerHTML = `<div class="card-note">Nenhum evento registrado ainda.</div>`; return; }
      const spans = latest.spans || [], max = Math.max(1, latest.duration_ms || 0, ...spans.map(s => s.duration_ms || 0));
      traceBody.innerHTML = `<div class="card-note" style="font-family:var(--font-mono);margin-bottom:10px">${esc(latest.trace)} · ${Number(latest.duration_ms || 0).toLocaleString("pt-BR")} ms</div>
        <div class="trace"><div class="trace-row"><span class="trace-name">Gateway</span><span class="trace-track"><span class="trace-fill" style="width:100%"></span></span><span class="trace-ms">${Number(latest.duration_ms || 0).toLocaleString("pt-BR")}ms</span></div>
        ${spans.map(span => `<div class="trace-row"><span class="trace-name">${esc(span.service || span.name)}</span><span class="trace-track"><span class="trace-fill ${span.status_code >= 400 ? "slow" : ""}" style="width:${Math.max(2, (span.duration_ms || 0) / max * 100).toFixed(1)}%"></span></span><span class="trace-ms">${Number(span.duration_ms || 0).toLocaleString("pt-BR")}ms</span></div>`).join("")}</div>`;
    };
    const apply = () => {
      const needle = (search.value || "").toLowerCase();
      visible = events.filter(item =>
        (!needle || `${item.msg} ${item.svc} ${item.trace} ${item.path || ""}`.toLowerCase().includes(needle)) &&
        (level.value.startsWith("Todos") || item.lvl === level.value) &&
        (service.value.startsWith("Todos") || item.svc === service.value));
      logBody.innerHTML = window.CKV.logRows(visible);
      count.textContent = visible.length + (visible.length === 1 ? " linha" : " linhas");
    };
    const load = async () => {
      refresh.disabled = true; refresh.textContent = "Atualizando…";
      try {
        const data = await request("/logs?limit=200");
        events = (data.items || []).map(item => ({
          ...item, ts: item.timestamp ? new Date(item.timestamp).toLocaleTimeString("pt-BR") : "—",
          lvl: item.level, svc: item.service, msg: item.message, trace: item.trace_id,
        }));
        window.CK.LOGS.length = 0; window.CK.LOGS.push(...events);
        const current = service.value;
        service.innerHTML = `<option>Todos os serviços</option>` + (data.services || []).map(name => `<option>${esc(name)}</option>`).join("");
        if ([...service.options].some(option => option.value === current)) service.value = current;
        apply(); renderSummary();
      } catch (error) {
        logBody.innerHTML = `<div class="empty"><div class="empty-t">Não foi possível carregar os logs</div><div class="empty-s">${esc(error.message)}</div></div>`;
        count.textContent = "Falha ao carregar";
        notify(error.message);
      } finally { refresh.disabled = false; refresh.textContent = "Atualizar"; }
    };
    [search, level, service].forEach(element => element.addEventListener("input", apply));
    refresh.addEventListener("click", load);
    logBody.addEventListener("click", event => {
      const row = event.target.closest("[data-log]");
      if (row && visible[+row.dataset.log] && onOpen) onOpen(visible[+row.dataset.log]);
    });
    load();
  }


  function bindAssistantChat(body, prefix) {
    const id = prefix || "assistantChat";
    const root = body.querySelector("#" + id + "Root");
    if (!root || root.dataset.bound) return;
    root.dataset.bound = "true";
    const feed = root.querySelector("#" + id + "Feed");
    const form = root.querySelector("#" + id + "Form");
    const input = root.querySelector("#" + id + "Input");
    const sendButton = root.querySelector("#" + id + "Send");
    const channel = root.querySelector("#" + id + "Channel");

    const render = () => {
      if (!assistantMessages.length) {
        feed.innerHTML = '<div class="assistant-chat-welcome"><div class="assistant-avatar">MP</div><div><strong>Assistente MoneyPrinter</strong><p>Posso responder perguntas, calcular metas e oportunidades, criar um plano de crescimento ou gerar um roteiro. O que você precisa?</p></div></div>';
      } else {
        feed.innerHTML = assistantMessages.map(item =>
          '<div class="assistant-chat-row ' + (item.role === "user" ? "is-user" : "is-assistant") + '">' +
          '<div class="assistant-chat-bubble">' + esc(item.content) + '</div></div>'
        ).join("");
      }
      feed.scrollTop = feed.scrollHeight;
    };
    const addMessage = (role, content) => {
      assistantMessages.push({ role, content });
      assistantMessages = assistantMessages.slice(-32);
      render();
    };
    const send = async () => {
      const content = input.value.trim();
      if (!content || sendButton.disabled) return;
      addMessage("user", content);
      input.value = "";
      input.disabled = true;
      sendButton.disabled = true;
      sendButton.textContent = "Pensando…";
      try {
        const result = await request("/assistant/chat", {
          method: "POST",
          body: JSON.stringify({ messages: assistantMessages.slice(-16), channel_id: channel.value || null }),
        });
        addMessage("assistant", result.message || "Não recebi uma resposta.");
      } catch (error) {
        addMessage("assistant", "Não consegui concluir: " + error.message);
      } finally {
        input.disabled = false;
        sendButton.disabled = false;
        sendButton.textContent = "Enviar";
        input.focus();
      }
    };

    form.addEventListener("submit", event => { event.preventDefault(); send(); });
    input.addEventListener("keydown", event => {
      if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); send(); }
    });
    root.addEventListener("click", event => {
      const suggestion = event.target.closest("[data-chat-suggestion]");
      if (suggestion) { input.value = suggestion.textContent.trim(); input.focus(); return; }
      if (event.target.closest("#" + id + "NewChat")) { assistantMessages = []; render(); input.focus(); }
    });
    render();
  }

  function bindGrowth(body, notify) {
    const root = body.querySelector("#growthRoot");
    if (!root || root.dataset.bound) return;
    root.dataset.bound = "true";
    const number = id => {
      const input = root.querySelector(`#${id}`);
      if (!input.value.trim() || !input.checkValidity()) throw new Error(`Preencha ${input.labels?.[0]?.textContent || "todos os indicadores"} com um valor válido.`);
      return Number(input.value);
    };
    const channel = () => root.querySelector("#growthChannel").value;
    const topic = () => root.querySelector("#growthTopic").value.trim();
    const goal = () => ({
      subscribers: number("growthSubscribers"), subscriber_target: number("growthSubscriberTarget"),
      qualified_watch_hours: number("growthWatchHours"), watch_hour_target: number("growthWatchTarget"),
      qualified_shorts_views: number("growthShortViews"), shorts_view_target: number("growthShortTarget"),
      days_remaining: number("growthDays"), average_view_duration_minutes: number("growthAvd"),
    });
    const opportunity = () => ({
      demand: number("growthDemand"), audience_fit: number("growthAudience"), historical_retention: number("growthRetention"),
      subscriber_conversion: number("growthConversion"), expected_avd: number("growthExpectedAvd"), evergreen: number("growthEvergreen"),
      trend_velocity: number("growthVelocity"), monetization: number("growthMonetization"), stock_availability: number("growthStock"),
      competition: number("growthCompetition"), production_cost: number("growthCost"), saturation: number("growthSaturation"),
    });
    const requireContext = () => {
      if (!channel()) throw new Error("Selecione um canal conectado.");
      if (topic().length < 3) throw new Error("Informe um tema com pelo menos 3 caracteres.");
    };
    const showGoal = data => {
      const result = data.result || data;
      root.querySelector("#growthGoalResult").innerHTML = `<dl class="kv" style="margin:0">
        <dt>Gap de inscritos</dt><dd class="mono">${fmt(result.subscribers.gap)} · ${fmt(result.subscribers.required_daily_velocity)}/dia</dd>
        <dt>Gap de horas</dt><dd class="mono">${fmt(result.watch_hours.gap)} · ${fmt(result.watch_hours.required_daily_velocity)}h/dia</dd>
        <dt>Views longas necessárias</dt><dd class="mono">${fmt(result.required_qualified_long_views_per_day || 0)}/dia</dd>
        <dt>Escopo</dt><dd>Somente orgânico qualificado</dd></dl>`;
    };
    const showScore = data => {
      const result = data.result || data;
      root.querySelector("#growthOpportunityResult").innerHTML = `<div style="font-size:32px;font-weight:650;color:var(--ink)">${Number(result.score).toLocaleString("pt-BR", { maximumFractionDigits: 2 })}<span style="font-size:14px;color:var(--muted)">/100</span></div><div class="card-note">${esc(result.version)} · cálculo determinístico e auditável</div>`;
    };
    root.querySelector("#growthEvaluate").addEventListener("click", () => busy(root.querySelector("#growthEvaluate"), async () => {
      requireContext();
      const data = await request("/v1/growth/goals/evaluate", { method: "POST", body: JSON.stringify({ channel_id: channel(), metrics: goal() }) });
      showGoal(data);
    }).catch(error => notify(error.message)));
    root.querySelector("#growthScore").addEventListener("click", () => busy(root.querySelector("#growthScore"), async () => {
      requireContext();
      const data = await request("/v1/growth/opportunities/score", { method: "POST", body: JSON.stringify({ channel_id: channel(), topic: topic(), dimensions: opportunity() }) });
      showScore(data);
    }).catch(error => notify(error.message)));
    root.querySelector("#growthPlan").addEventListener("click", () => busy(root.querySelector("#growthPlan"), async () => {
      requireContext();
      const started = await request("/v1/growth/workflows", { method: "POST", body: JSON.stringify({
        channel_id: channel(), topic: topic(), ypp_goal: goal(), opportunity: opportunity(),
        production_target: { long: 1, shorts: 3 }, publish_mode: "review",
      }) });
      const panel = root.querySelector("#growthWorkflowResult");
      panel.innerHTML = `<div class="card-note">Workflow ${esc(started.id)} iniciado. Aguardando o worker…</div>`;
      for (let attempt = 0; attempt < 30; attempt += 1) {
        await new Promise(resolve => setTimeout(resolve, 1000));
        const current = await request(`/v1/growth/workflows/${started.id}`);
        panel.innerHTML = `<dl class="kv" style="margin:0"><dt>Estado</dt><dd>${esc(current.state)}</dd><dt>Etapa</dt><dd>${esc(current.current_step)}</dd><dt>Workflow</dt><dd class="mono">${esc(started.id)}</dd></dl>`;
        if (["completed", "failed", "cancelled"].includes(current.state)) {
          if (current.output) { showGoal(current.output.ypp_gap); showScore(current.output.opportunity); }
          break;
        }
      }
    }).catch(error => notify(error.message)));
  }

  async function openJob(jobId) {
    const job = await request(`/jobs/${encodeURIComponent(jobId)}`);
    const targetChannel = (job.publications || []).find(publication => publication.channel_id)?.channel_id || job.channel_id;
    if (targetChannel) selectCreateChannel(job.format, targetChannel);
    window.CK.__go(job.format === "long" ? "gerador" : "youtube");
    const generator = document.getElementById("viewBody");
    setPipeline(generator, job);
    showArtifact(generator, job);
    generator.querySelector("#genBatchResults")?.scrollIntoView({ block: "nearest" });
  }
  window.CK.__openJob = jobId => openJob(jobId).catch(error => { if (lastNotify) lastNotify(error.message); });
  window.MoneyPrinterRuntime = { request, establishSession, logout, syncNotifications, bindGenerator, deleteJob, retryJob, stopJob, bindConfigSettings, bindYoutubeLibraryPublish, connectChannel, channelAction, saveChannel, selectCreateChannel, bindAgents, bindAssistantChat, bindGrowth, bindAnalytics, bindJobsWorkers, bindLogs };
}());
