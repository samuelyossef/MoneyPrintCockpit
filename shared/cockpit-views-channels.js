/* Renderização das telas de canais. Script clássico: define window.CKVChannels. */
(function () {
  window.CKVChannels = function (deps) {
    const D = deps.D;
    const esc = deps.esc;
  /* ── Canais ─────────────────────────────────── */
  function canaisHead() {
    const yt = D.CHANNELS.filter(c => c.platform.startsWith("YouTube")).length;
    const tk = D.CHANNELS.filter(c => c.platform === "TikTok").length;
    const stat = (color, label, val, icon) => `
      <div style="display:flex;align-items:center;gap:11px;padding:10px 16px;border:1px solid var(--border);border-radius:var(--r-lg);background:var(--surface)">
        <span style="width:34px;height:34px;border-radius:9px;background:${color};flex-shrink:0;display:grid;place-items:center">${icon}</span>
        <div><div style="font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted-2);font-weight:600">${label}</div>
          <div style="font-size:19px;font-weight:600;color:var(--ink);line-height:1.2">${val}</div></div>
      </div>`;
    const ytIcon = `<svg viewBox="0 0 24 24" width="18" height="18" fill="#fff"><path d="M9 7.5v9l8-4.5-8-4.5Z"/></svg>`;
    const tkIcon = `<svg viewBox="0 0 24 24" width="17" height="17" fill="#fff"><path d="M16.6 5.8c-.7-.7-1.1-1.7-1.1-2.7h-2.9v12.6c0 1.3-1.1 2.4-2.4 2.4a2.4 2.4 0 1 1 0-4.8c.2 0 .5 0 .7.1V10.3a5.3 5.3 0 0 0-.7 0 5.3 5.3 0 1 0 5.3 5.3V9.1a7 7 0 0 0 4.1 1.3V7.5c-1.1 0-2.1-.4-3-1.1a4.8 4.8 0 0 1 0-.6Z"/></svg>`;
    return `<div class="channel-head" style="display:flex;align-items:center;gap:14px;width:100%">
      <div style="flex:1"><div class="view-hd-title">Canais</div><div class="view-hd-sub">Contas conectadas, plataformas e métricas de publicação</div></div>
      <div class="channel-head-stats" style="display:flex;gap:12px">${stat("#dc2626", "Canais YouTube", yt, ytIcon)}${stat("#000000", "Canais TikTok", tk, tkIcon)}</div>
    </div>`;
  }
  const CH_STATUS = { connected: ["active", "Conectado"], pending: ["pending", "Aguardando login"], expired: ["past_due", "Expirado"] };
  const CH_LANGS = [["pt-BR", "Português (Brasil)"], ["en-US", "English (US)"], ["es-ES", "Español"], ["de-DE", "Deutsch"]];
  const CH_VOICES = [["pt-BR-FranciscaNeural", "Português (Brasil) — Francisca (F)"], ["pt-BR-AntonioNeural", "Português (Brasil) — Antonio (M)"], ["en-US-JennyNeural", "English (US) — Jenny (F)"], ["en-US-GuyNeural", "English (US) — Guy (M)"], ["es-ES-ElviraNeural", "Español — Elvira (F)"], ["es-ES-AlvaroNeural", "Español — Álvaro (M)"], ["de-DE-KatjaNeural", "Deutsch — Katja (F)"], ["de-DE-ConradNeural", "Deutsch — Conrad (M)"]];
  function chStatus(status) {
    const [dot, label] = CH_STATUS[status] || CH_STATUS.expired;
    return `<span class="status-pill"><span class="status-dot ${dot}"></span>${label}</span>`;
  }
  function canais(scope) {
    const rows = D.CHANNELS;
    const empty = `<tr><td colspan="7"><div class="empty"><div class="empty-t">Nenhum canal conectado</div>
      <div class="empty-s">Conecte um canal do YouTube ou TikTok para escolher onde publicar ao gerar vídeos.</div></div></td></tr>`;
    return `
      <div class="view-filters" style="margin-top:16px">
        <div class="view-filters-spacer"></div><span class="rows-count" id="chCount">${rows.length} canais</span>
        <button class="btn primary sm" id="newChBtn">+ Conectar canal</button>
      </div>
      <div class="tablewrap"><table class="channel-table">
        <thead><tr><th>Canal</th><th>Plataforma</th><th class="num">Seguidores</th><th class="num">Vídeos</th><th class="num">Views totais</th><th>Idioma</th><th>Status</th></tr></thead>
        <tbody id="chBody">${rows.length ? canalRows(rows) : empty}</tbody>
      </table></div>`;
  }
  function canalRows(rows) {
    return rows.map(r => `<tr class="user-row" data-ch-row="${esc(r.id)}">
          <td style="font-weight:500;color:var(--ink)">${esc(r.name)}<div class="card-note" style="font-weight:400">${esc(r.handle)}</div></td>
          <td>${esc(r.platform)}</td>
          <td class="num">${D.fmtInt(r.followers)}</td>
          <td class="num">${D.fmtInt(r.videos)}</td>
          <td class="num">${D.fmtInt(r.views)}</td>
          <td>${esc(r.language)}</td>
          <td>${chStatus(r.status)}</td>
        </tr>`).join("");
  }
  function canalRec(id) {
    return D.CHANNELS.find(x => x.id === id) || null;
  }
  function newCanalRec() {
    const media = { voice_name: "pt-BR-FranciscaNeural", voice_rate: 1, voice_volume: 1, subtitle_enabled: true, text_fore_color: "#FFFFFF", stroke_color: "#000000", stroke_width: 1.5 };
    return { platform: "YouTube", niche: "", language: "pt-BR", publish_privacy: "private", visual_guides: { short: "", long: "" }, voice_name: media.voice_name, voice_rate: media.voice_rate, voice_volume: media.voice_volume, subtitle_enabled: media.subtitle_enabled, subtitle_position: "bottom", font_size: 56, text_fore_color: media.text_fore_color, stroke_color: media.stroke_color, stroke_width: media.stroke_width,
      video_defaults: {
        short: { ...media, subtitle_position: "center", font_size: 64, paragraph_number: 5, video_count: 1, video_clip_duration: 4, video_concat_mode: "script", video_transition_mode: "", video_clip_speed: 1, bgm_type: "", bgm_volume: 0.2 },
        long: { ...media, subtitle_position: "bottom", font_size: 48, target_minutes: 8, video_clip_duration: 6, video_concat_mode: "script", video_transition_mode: "", video_clip_speed: 1, bgm_type: "", bgm_volume: 0.2 },
      } };
  }
  function canal(c, tab, editing, isNew) {
    editing = editing || isNew;
    const tabs = isNew ? [["dados", "Conectar"]] : [["dados", "Dados da conta"], ["conexao", "Conexão"]];
    tab = tabs.some(t => t[0] === tab) ? tab : "dados";
    const txt = (f, v, ph) => editing
      ? `<input class="np-input" style="width:100%" data-cfield="${f}" value="${esc(v)}" placeholder="${ph || ""}" />`
      : esc(v || "—");
    const area = (f, v, ph) => editing
      ? `<textarea class="np-input" style="width:100%;min-height:150px;resize:vertical" data-cfield="${f}" maxlength="4000" placeholder="${ph || ""}">${esc(v)}</textarea>`
      : `<span style="white-space:pre-wrap">${esc(v || "Nenhum prompt de sistema configurado.")}</span>`;
    const sel = (f, v, opts) => editing
      ? `<select class="set-select" style="width:100%" data-cfield="${f}">${opts.map(([val, label]) => `<option value="${val}" ${val === v ? "selected" : ""}>${label}</option>`).join("")}</select>`
      : esc((opts.find(o => o[0] === v) || [v, v])[1]);
    const profileField = (label, value) => `<div><label class="dr-sec" style="margin:0 0 7px">${label}</label><div class="channel-profile-value">${value}</div></div>`;
    const videoDefaults = c.video_defaults || newCanalRec().video_defaults;
    const videoSelect = (format, field, current, options) => editing
      ? `<select class="set-select" style="width:100%" data-vfield="${format}.${field}">${options.map(([value, label]) => `<option value="${value}" ${String(value) === String(current) ? "selected" : ""}>${label}</option>`).join("")}</select>`
      : esc((options.find(([value]) => String(value) === String(current)) || [current, current])[1]);
    const videoNumber = (format, field, current, min, max, step = "1") => editing
      ? `<input class="np-input" style="width:100%" data-vfield="${format}.${field}" type="number" min="${min}" max="${max}" step="${step}" value="${esc(current)}" />`
      : esc(current);
    const videoProfileFields = format => {
      const current = videoDefaults[format] || newCanalRec().video_defaults[format];
      const duration = format === "short"
        ? profileField("Duração alvo", videoSelect(format, "paragraph_number", current.paragraph_number, [[4, "60 s"], [5, "75 s"], [6, "90 s"]]))
        : profileField("Duração alvo", videoSelect(format, "target_minutes", current.target_minutes, [[5, "5 minutos"], [8, "8 minutos"], [10, "10 minutos"], [15, "15 minutos"]]));
      // Keep every language in the DOM: changing the language while editing must
      // reveal its voices without re-rendering the channel form.
      const voiceOptions = CH_VOICES;
      const subtitleEnabled = editing
        ? `<label style="display:flex;align-items:center;gap:7px;height:34px"><input type="checkbox" class="cbx" data-vfield="${format}.subtitle_enabled" ${current.subtitle_enabled ? "checked" : ""} /> Ativadas por padrão</label>`
        : esc(current.subtitle_enabled ? "Ativadas" : "Desativadas");
      return `
        <div class="dr-sec" style="margin:4px 0 10px">Voz e legendas</div>
        <div class="grid2 channel-profile-grid" style="margin:0 0 18px">
          ${profileField("Voz sintética padrão", videoSelect(format, "voice_name", current.voice_name, voiceOptions))}
          ${profileField("Velocidade da voz", videoSelect(format, "voice_rate", current.voice_rate, [[0.8, "0.8×"], [1, "1.0×"], [1.2, "1.2×"], [1.5, "1.5×"]]))}
          ${profileField("Volume da voz", videoSelect(format, "voice_volume", current.voice_volume, [[0.6, "60%"], [1, "100%"], [1.5, "150%"], [2, "200%"]]))}
          ${profileField("Legendas", subtitleEnabled)}
          ${profileField("Posição da legenda", videoSelect(format, "subtitle_position", current.subtitle_position, [["bottom", "Inferior"], ["center", "Centro"], ["top", "Superior"]]))}
          ${profileField("Tamanho da legenda", videoNumber(format, "font_size", current.font_size, 12, 120))}
          ${profileField("Espessura da borda", videoNumber(format, "stroke_width", current.stroke_width, 0, 10, "0.1"))}
          ${profileField("Cor do texto", editing ? `<input class="np-input" style="width:100%" data-vfield="${format}.text_fore_color" value="${esc(current.text_fore_color)}" />` : esc(current.text_fore_color))}
          ${profileField("Cor da borda", editing ? `<input class="np-input" style="width:100%" data-vfield="${format}.stroke_color" value="${esc(current.stroke_color)}" />` : esc(current.stroke_color))}
        </div>
        <div class="dr-sec" style="margin:4px 0 10px">Parâmetros de vídeo</div>
        <div class="grid2 channel-profile-grid" style="margin:0">
          ${duration}
          ${profileField("Duração de cada clipe", videoNumber(format, "video_clip_duration", current.video_clip_duration, 1, 30) + " s")}
          ${format === "short" ? profileField("Vídeos gerados", videoSelect(format, "video_count", current.video_count, [[1, "1"], [2, "2"], [3, "3"], [4, "4"], [5, "5"]])) : ""}
          ${profileField("Imagens", videoSelect(format, "video_concat_mode", current.video_concat_mode, [["script", "Seguir o roteiro"], ["random", "Aleatória"]]))}
          ${profileField("Transição", videoSelect(format, "video_transition_mode", current.video_transition_mode, [["", "Nenhuma"], ["Shuffle", "Aleatória"], ["FadeIn", "Fade in"], ["FadeOut", "Fade out"], ["SlideIn", "Slide in"], ["SlideOut", "Slide out"], ["ZoomIn", "Zoom in"], ["ZoomOut", "Zoom out"]]))}
          ${profileField("Velocidade do clipe", videoSelect(format, "video_clip_speed", current.video_clip_speed, [[0.8, "0.8×"], [1, "1.0×"], [1.2, "1.2×"], [1.5, "1.5×"]]))}
          ${profileField("Trilha sonora (BGM)", videoSelect(format, "bgm_type", current.bgm_type, [["", "Sem música de fundo"], ["random", "Aleatória"]]))}
          ${profileField("Volume da música", videoSelect(format, "bgm_volume", current.bgm_volume, [[0.1, "10%"], [0.2, "20%"], [0.4, "40%"], [0.6, "60%"]]))}
        </div>
        <div style="margin-top:18px"><label class="dr-sec" style="display:block;margin-bottom:7px">Guia visual deste formato</label>
          <div class="card-note" style="margin-bottom:7px">Descreva o estilo desejado e elementos que devem ser evitados. O curador confere cada imagem e clipe com este guia.</div>
          ${editing ? `<textarea class="np-input" style="width:100%;min-height:100px;resize:vertical" data-visual-guide="${format}" maxlength="2000">${esc((c.visual_guides || {})[format] || "")}</textarea>` : `<div style="white-space:pre-wrap">${esc((c.visual_guides || {})[format] || "Nenhum guia visual definido.")}</div>`}
        </div>`;
    };
    const videoProfile = () => {
      const formats = c.platform === "TikTok" ? [["short", "Shorts"]] : [["short", "Shorts"], ["long", "Vídeo longo"]];
      return `<div class="card" style="margin:14px 0 0;grid-column:1/-1;width:100%"><div class="card-hd"><span class="card-title">Configuração por formato</span></div>
        <div class="card-bd"><div class="view-tabs" data-channel-video-tabs style="margin:0 0 14px">${formats.map(([format, label], index) => `<button class="view-tab ${index === 0 ? "is-active" : ""}" type="button" data-channel-video-tab="${format}">${label}</button>`).join("")}</div>
          ${formats.map(([format], index) => `<div data-channel-video-pane="${format}"${index ? " hidden" : ""}>${videoProfileFields(format)}</div>`).join("")}
        </div></div>`;
    };
    const profile = (fullRow = false) => `
      <div class="card" style="margin:14px 0 0;${fullRow ? "grid-column:1/-1;width:100%" : "max-width:760px"}"><div class="card-hd"><span class="card-title">Idioma e publicação</span></div>
        <div class="card-bd"><div class="grid2 ${fullRow ? "channel-profile-grid" : ""}" style="margin:0">
          ${profileField("Idioma da narração padrão", sel("language", c.language, CH_LANGS))}
          ${profileField("Visibilidade padrão", sel("publish_privacy", c.publish_privacy || "private", [["private", "Sempre como privado"], ["public", "Sempre como público"]]))}
        </div></div></div>`;
    const kpi = (label, value) => `<div class="kpi" style="cursor:default"><div class="kpi-label">${label}</div><div class="kpi-value">${D.fmtInt(value || 0)}</div></div>`;
    const panes = {
      dados: isNew ? `
        <div class="card" style="margin-bottom:0;max-width:560px">
          <div class="card-hd"><span class="card-title">Conectar canal</span></div>
          <div class="card-bd" style="display:flex;flex-direction:column;gap:16px">
            <div class="md-field"><label>Plataforma</label>${sel("platform", c.platform, [["YouTube", "YouTube (Shorts e vídeos longos)"], ["TikTok", "TikTok (via Upload-Post)"]])}</div>
            <div class="md-field"><label>Nicho do canal</label>${txt("niche", c.niche, "Ex: Curiosidades, Histórias Reais, Tecnologia")}</div>
            <div class="md-field"><label>Visibilidade padrão</label>${sel("publish_privacy", c.publish_privacy || "private", [["private", "Sempre como privado"], ["public", "Sempre como público"]])}</div>
            <div class="card-note" style="font-family:inherit;line-height:1.55">
              <b>YouTube:</b> abre o login do Google em outra aba; escolha o canal e autorize. Nome, @handle e inscritos vêm do próprio YouTube.<br>
              <b>TikTok:</b> exige a chave Upload-Post em Configurações; abre a página do Upload-Post para você entrar na conta TikTok.<br>
              Voz, legendas e o prompt do canal ficam na página do canal depois da conexão.
            </div>
          </div></div>` : `
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px">
          <div class="card" style="margin-bottom:0;grid-column:1/-1">
            <div class="card-hd"><span class="card-title">Conta</span>${editing ? '<span class="spacer"></span><span class="card-note">em edição</span>' : ""}</div>
            <div class="card-bd"><dl class="kv">
              <dt>Nome do canal</dt><dd>${esc(c.name)}</dd>
              <dt>Plataforma</dt><dd>${esc(c.platform)}</dd>
              <dt>Handle</dt><dd>${esc(c.handle || "—")}</dd>
              <dt>Nicho</dt><dd>${txt("niche", c.niche, "Ex: Curiosidades")}</dd>
              <dt>Prompt de sistema</dt><dd>${area("system_prompt", c.system_prompt, "Defina tom, público, estrutura, vocabulário e regras deste canal.")}</dd>
              <dt>Status</dt><dd>${chStatus(c.status)}</dd>
            </dl></div></div>
          ${profile(true)}${videoProfile()}${c.platform === "YouTube" ? kpi("Inscritos", c.followers) + kpi("Vídeos", c.videos) + kpi("Views totais", c.views) : ""}
        </div>`,
      conexao: `
        <div class="card" style="margin-bottom:0;max-width:560px"><div class="card-hd"><span class="card-title">Conexão</span></div>
          <div class="card-bd"><dl class="kv">
            <dt>Status</dt><dd>${chStatus(c.status)}</dd>
            <dt>Via</dt><dd>${c.platform === "TikTok" ? "Upload-Post" : "Google OAuth (YouTube Data API)"}</dd>
            <dt>Publicação</dt><dd>Sempre como <b>${c.publish_privacy === "public" ? "público" : "privado"}</b></dd>
          </dl>
          <div style="margin-top:14px;display:flex;gap:8px;flex-wrap:wrap">
            <button class="btn sm primary" data-ch-act="reconnect">Reconectar</button>
            <button class="btn sm" data-ch-act="refresh">Verificar conexão</button>
            <button class="btn sm danger" data-ch-act="disconnect">Desconectar</button>
          </div></div></div>`,
    };
    const initials = (c.name || "?").split(" ").map(w => w[0]).slice(0, 2).join("").toUpperCase();
    const color = c.platform === "TikTok" ? "#000000" : "#dc2626";
    const header = `
      <div style="flex:1;min-width:0">
      <div style="display:flex;align-items:center;gap:14px;margin-bottom:18px;flex-wrap:wrap">
        <span class="u-avatar" style="width:52px;height:52px;border-radius:12px;display:grid;place-items:center;font-size:17px;font-weight:600;color:#fff;background:${color};flex-shrink:0">${isNew ? "+" : esc(initials)}</span>
        <div style="min-width:0;overflow:hidden"><div class="view-hd-title" style="margin-bottom:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${isNew ? "Novo canal" : esc(c.name)}</div>
          <div class="view-hd-sub" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${isNew
            ? "Conecte uma conta de YouTube ou TikTok"
            : `${esc(c.platform)}${c.handle ? " · " + esc(c.handle) : ""}`}</div></div>
        <div style="flex:1"></div>
        ${isNew
          ? `<button class="btn primary" style="flex-shrink:0;white-space:nowrap" data-create-ch="1">Conectar canal</button>
             <button class="btn" style="flex-shrink:0;white-space:nowrap" data-cancel-ch="1">Cancelar</button>`
          : editing
            ? `<button class="btn primary" style="flex-shrink:0;white-space:nowrap" data-save-ch="1">Salvar</button>
               <button class="btn" style="flex-shrink:0;white-space:nowrap" data-cancel-ch="1">Cancelar</button>`
            : `<button class="btn primary" style="flex-shrink:0;white-space:nowrap" data-edit-ch="${esc(c.id)}">Editar canal</button>
               <button class="btn" style="flex-shrink:0;white-space:nowrap" data-view="clientes">‹ Voltar</button>`}
      </div>
      <div class="view-tabs" style="flex-wrap:wrap">${tabs.map(t =>
        `<button class="view-tab ${t[0] === tab ? "is-active" : ""}" data-chtab="${t[0]}">${t[1]}</button>`).join("")}</div>
      </div>`;
    return { header, body: panes[tab] || panes.dados };
  }
    return { canais, canaisHead, canalRows, canalRec, newCanalRec, canal, chStatus };
  };
})();
