/* Renderização do estúdio (Gerador e Shorts). Script clássico: define window.CKVStudio. */
(function () {
  window.CKVStudio = function (deps) {
    const D = deps.D;
    const esc = deps.esc;
  // ==========================================================================
  // 8. Publicar da biblioteca (Gerador e Shorts)
  // ==========================================================================
  /* Long videos only go to YouTube; Shorts to any connected channel. */
  function libraryCard(format) {
    const isLong = format === "long";
    const channels = D.CHANNELS.filter(c => c.status === "connected" && (!isLong || c.platform === "YouTube"));
    const videos = isLong ? D.VIDEOS.horiz : D.VIDEOS.vert;
    return `
      <div class="card" style="margin-top:14px">
        <div class="card-hd"><span class="card-title">Publicar ${isLong ? "vídeo longo" : "Short"} existente da biblioteca</span></div>
        <div class="card-bd"><div class="grid2" style="margin:0">
          <div style="display:flex;flex-direction:column;gap:16px">
            <div class="md-field"><label>Canal de destino</label><select class="set-select" style="width:100%" id="ytLibChannelSelect"><option value="">Selecione um canal…</option>${channels.map(c => `<option value="${esc(c.id)}">${esc(c.name)} · ${esc(c.platform)}</option>`).join("")}</select></div>
            <div class="md-field"><label>Selecione o vídeo da biblioteca</label><select class="set-select" style="width:100%" id="ytLibrarySelect"><option value="">Selecione um vídeo da biblioteca…</option>${videos.map(v => `<option value="${esc(v[4])}">${esc(v[2])}</option>`).join("")}</select></div>
          </div>
          <div style="display:flex;flex-direction:column;gap:16px">
            <div class="md-field"><label>Título do vídeo</label><input class="np-input" style="width:100%" id="ytPubTitle" placeholder="Obrigatório na primeira publicação" /></div>
            <div class="md-field"><label>Descrição / tags</label><input class="np-input" style="width:100%" id="ytPubDesc" placeholder="Ex: Descrição do vídeo #Shorts, #Curiosidades, #Fatos" /></div>
            <div class="md-field"><label>Agendar para (opcional)</label><input class="np-input" style="width:100%" type="datetime-local" id="ytPubAt" /></div>
          </div>
        </div>
        <button class="btn primary" style="width:100%;padding:11px;margin-top:16px" id="ytPubBtn">Publicar no canal (privado)</button>
        <div class="card-note" id="ytPubStatus" role="status" style="margin-top:8px;font-family:inherit"></div></div>
      </div>`;
  }

  // ==========================================================================
  // 9. Tela: Gerador de Vídeo HD (Pipeline Completo de IA)
  // ==========================================================================
  /**
   * Renderiza o estúdio principal de criação de vídeos:
   * - Configuração do tópico e seleção de canal
   * - Roteiro personalizado opcional
   * - Seleção de canal cadastrado
   * - Monitor do pipeline em tempo real com 5 etapas:
   *   1. Roteiro IA -> 2. Keywords -> 3. Voz e Legendas -> 4. Filmagens Stock -> 5. Render HD
   * - Player de pré-visualização integrado com ações de download e publicação
   */
  /* Every screen tab shares one id scheme ("genTabs"/data-gentab/data-genpane): Gerador and Shorts
     are never open together, so both can reuse the same ids without clashing, like every other "gen*" element. */
  function screenTabs(tabs) {
    return `
      <div class="view-tabs" id="genTabs" style="margin-bottom:14px">${tabs.map((t, i) =>
        `<button class="view-tab ${i === 0 ? "is-active" : ""}" type="button" data-gentab="${t[0]}">${t[1]}</button>`).join("")}</div>
      ${tabs.map((t, i) => `<div data-genpane="${t[0]}"${i === 0 ? "" : " hidden"}>${t[2]}</div>`).join("")}`;
  }
  function systemPromptPane(format) {
    const isLong = format === "long";
    return `
      <div class="card" style="margin-bottom:0">
        <div class="card-hd"><span class="card-title">Prompt do sistema (opcional)</span></div>
        <div class="card-bd">
          <div class="card-note" style="font-family:inherit;margin-bottom:10px">${isLong
            ? `O roteiro longo é escrito em várias chamadas de IA (uma por seção); a primeira precisa responder em JSON, então este texto entra como instrução de estilo em todas elas, em vez de substituí-las por completo. Usado por "Escrever roteiro longo com IA".`
            : `Substitui por completo as instruções que a IA recebe para escrever o roteiro do Short — tanto no botão "Gerar roteiro com IA" quanto se o roteiro for deixado em branco para o Turbo escrever sozinho.`} Deixe vazio para usar o prompt padrão.</div>
          <textarea class="np-input" id="genSystemPrompt" rows="16" style="width:100%;resize:vertical" placeholder="Deixe vazio para usar o prompt padrão"></textarea>
        </div>
      </div>`;
  }
  function gerador(scope) {
    return `<div class="card-note" style="margin-bottom:14px"><button class="btn sm" type="button" data-view="criar">Trocar canal ou formato</button> · Os parâmetros seguem o perfil do canal; alterações nesta tela valem apenas para esta geração.</div>
      ${studioCreative(scope, "long")}
      <details class="card" id="genAdvanced" style="margin-top:16px"><summary class="card-hd" style="cursor:pointer"><span class="card-title">Opções avançadas desta geração</span></summary><div class="card-bd">${studioParams("long")}${systemPromptPane("long")}</div></details>`;
  }
  function youtubeShorts(scope) {
    return `<div class="card-note" style="margin-bottom:14px"><button class="btn sm" type="button" data-view="criar">Trocar canal ou formato</button> · Os parâmetros seguem o perfil do canal; alterações nesta tela valem apenas para esta geração.</div>
      ${studioCreative(scope, "short")}
      <details class="card" id="genAdvanced" style="margin-top:16px"><summary class="card-hd" style="cursor:pointer"><span class="card-title">Opções avançadas desta geração</span></summary><div class="card-bd">${studioParams("short")}${systemPromptPane("short")}</div></details>`;
  }
  /* One form, two formats: the engine (Turbo, approval, publishing) is shared; what differs is the
     aspect, the duration control, the script writer and which channels may receive the video.
     Split in two: studioCreative (topic/script/channels + the live pipeline/preview) sits on the
     "Gerador" tab; studioParams (voice/render/subtitle knobs) gets its own tab. */
  function studioCreative(scope, format) {
    const isLong = format === "long";
    const steps = [["Roteiro IA", "doc"], ["Keywords", "search"], ["Voz e Legendas", "mic"], ["Filmagens Stock", "grid"], ["Render HD", "activity"]];
    return `
      <div class="grid-2-1 studio-layout">
        <div class="card" style="margin-bottom:0" data-format="${format}">
          <div class="card-hd"><span class="card-title">${isLong ? "Vídeo longo para YouTube" : "Short para YouTube e TikTok"}</span><span class="spacer"></span><span class="badge plan-pro">${isLong ? "16:9 · 5–15 min" : "9:16 · 60–90 s"}</span></div>
          <div class="card-bd" style="display:flex;flex-direction:column;gap:16px">
            <div class="card-note" style="font-family:inherit">Usa o perfil e a IA do canal. O tema é opcional.</div>
            <div>
              <label class="dr-sec" style="margin:0 0 7px" for="genTopicInput">Tema / tópico do vídeo (opcional)</label>
              <textarea class="np-input" id="genTopicInput" rows="2" style="width:100%;resize:vertical" placeholder="Deixe vazio para gerar a partir do canal, ou escreva uma ideia para adaptar"></textarea>
            </div>
            <details id="genReview">
              <summary class="dr-sec" style="margin:0;cursor:pointer">Revisar antes de gerar</summary>
              <div style="display:flex;flex-direction:column;gap:16px;margin-top:12px">
            <div>
              <div style="display:flex;justify-content:space-between;align-items:center;margin:0 0 7px"><label class="dr-sec" style="margin:0" for="genProposal">Proposta antes do roteiro (opcional)</label><button class="btn sm" type="button" id="genProposalBtn">Pesquisar ângulo</button></div>
              <textarea class="np-input" id="genProposal" rows="3" style="width:100%;resize:vertical" placeholder="Pesquise um ângulo ou cole a proposta confirmada. O roteiro só usa este texto se ele estiver preenchido."></textarea>
              <label class="card-note" for="genReferenceFile" style="display:block;margin-top:8px;font-family:inherit">Vídeo de referência local</label>
              <input id="genReferenceFile" type="file" accept="video/mp4,video/webm,video/quicktime" />
              <div class="card-note" id="genReferenceNote" style="margin-top:6px;font-family:inherit"></div>
            </div>
            <div>
              <div style="display:flex;justify-content:space-between;align-items:center;margin:0 0 7px"><label class="dr-sec" style="margin:0" for="genScriptInput">Roteiro (opcional)</label>${isLong
                ? `<button class="btn sm" type="button" id="genLongScriptBtn">Escrever roteiro longo com IA</button>`
                : `<button class="btn sm" type="button" id="genScriptAiBtn">Gerar roteiro com IA</button>`}</div>
              <textarea class="np-input" id="genScriptInput" rows="${isLong ? 10 : 4}" style="width:100%;resize:vertical" placeholder="${isLong ? "Escreva por seções com a IA ou cole o roteiro completo" : "Deixe vazio para a IA escrever, ou gere/cole e edite aqui"}"></textarea>
              <div class="card-note" id="genScriptStats" style="margin-top:6px;font-family:inherit"></div>
            </div>
                <div>
                  <label class="dr-sec" style="margin:0 0 7px" for="genScriptRequirements">Requisitos personalizados do roteiro</label>
                  <textarea class="np-input" id="genScriptRequirements" rows="2" style="width:100%;resize:vertical" placeholder="Ex: use um tom mais leve, estilo TikTok, com uma abertura mais intrigante"></textarea>
                </div>
            <div id="genTermsRow">
              <div style="display:flex;justify-content:space-between;align-items:center;margin:0 0 7px"><label class="dr-sec" style="margin:0" for="genTermsInput">Palavras-chave das filmagens (opcional)</label><button class="btn sm" type="button" id="genTermsAiBtn">Gerar palavras-chave</button></div>
              <input class="np-input" id="genTermsInput" style="width:100%" placeholder="deep ocean, ocean life — separadas por vírgula (em inglês o Pexels acha mais)" />
            </div>
              </div>
            </details>
            <div id="genChannelRow" ${isLong && localStorage.getItem("moneyprint-create-channel-long") ? "hidden" : ""}>
              <label class="dr-sec" style="margin:0 0 7px">${isLong ? "Selecione o canal" : "Segundo canal (opcional)"}</label>
              ${isLong
                ? `<select class="set-select" id="genChannelSelect" style="width:100%"><option value="">Selecione um canal</option>${D.CHANNELS.filter(c => c.status === "connected" && c.platform === "YouTube").map(c => `<option value="${esc(c.id)}">${esc(c.name)} · YouTube</option>`).join("")}</select>`
                : `<div id="genChannelList" style="display:flex;flex-direction:column;gap:6px"></div>`}
              <div class="card-note" style="margin-top:6px;font-family:inherit">${isLong ? "Vídeos longos vão só para um canal do YouTube por geração." : "O canal principal veio de Criar. Você pode adicionar mais um canal; cada um receberá seu próprio Short."}</div>
            </div>
            <div class="card-note" id="genChannelProfileNote" style="font-family:inherit">Selecione um canal para gerar.</div>
            <button class="btn primary" id="genStartBtn" style="padding:11px">${isLong ? "Gerar vídeo longo" : "Gerar Short"}</button>
          </div>
        </div>
        <div id="studioProgress" hidden>
          <div class="card" style="margin-bottom:0">
            <div class="card-hd"><span class="card-title">Resultados da geração automática por canal</span><span class="spacer"></span><span class="card-note" id="genBatchStatus">Aguardando</span></div>
            <div class="card-bd" id="genBatchResults" aria-live="polite"><span class="card-note">Os temas, requisitos, roteiros e palavras-chave gerados aparecerão aqui, separados por canal.</span></div>
          </div>
          <div class="card" style="margin-bottom:0">
            <div class="card-hd"><span class="card-title">Pipeline em tempo real</span><span class="spacer"></span><button class="btn sm" id="genStopBtn" style="display:none">Parar</button><span class="card-note" id="genPipelineNote">Pronto para gerar</span></div>
            <div class="card-bd">
              <div id="genVideoTitle" style="font-size:13px;font-weight:500;color:var(--ink);margin-bottom:10px;min-height:18px"></div>
              <div style="height:7px;border-radius:99px;background:var(--line);overflow:hidden;margin-bottom:14px">
                <div id="genProgFill" style="height:100%;width:0%;border-radius:99px;background:linear-gradient(90deg,var(--accent),#8b5cf6);transition:width .5s ease"></div>
              </div>
              <div style="display:flex;justify-content:space-between;font-size:12.5px;color:var(--muted);margin-bottom:16px">
                <span id="genProgLabel">Aguardando início…</span><span class="mono" id="genProgPct">0%</span>
              </div>
              <div style="display:flex;justify-content:space-between;gap:8px;position:relative" id="genSteps">
                <div style="position:absolute;left:19px;right:19px;top:19px;height:2px;background:var(--border);z-index:0"></div>
                ${steps.map((s, i) => `
                  <div class="gen-step" data-step="${i}" style="display:flex;flex-direction:column;align-items:center;gap:8px;flex:1;position:relative;z-index:1">
                    <span class="gen-step-ic" style="width:38px;height:38px;border-radius:50%;display:grid;place-items:center;background:var(--surface);color:var(--muted);border:1.5px solid var(--border);transition:background .3s,border-color .3s,color .3s">${ic2(s[1])}</span>
                    <span class="gen-step-lb" style="font-size:11px;font-weight:500;text-align:center;color:var(--muted);transition:color .3s">${s[0]}</span>
                  </div>`).join("")}
              </div>
            </div>
          </div>
          <div class="card" style="margin-bottom:0">
            <div class="card-hd"><span class="card-title">Player de pré-visualização</span><span class="spacer"></span><span class="card-note" id="genPreviewNote">Nenhum vídeo</span></div>
            <div class="card-bd"><image-slot id="gen-preview" shape="rounded" placeholder="O vídeo finalizado será exibido aqui para pré-visualização e download" style="${isLong ? "width:100%;aspect-ratio:16/9" : "width:100%;max-width:300px;aspect-ratio:9/16;margin:0 auto"};display:block"></image-slot>
              <div style="display:flex;gap:8px;margin-top:14px">
                <button class="btn primary" id="genDownloadBtn" style="flex:1" disabled><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:15px;height:15px"><path d="M12 3v12M7 10l5 5 5-5M5 21h14"/></svg> Baixar MP4</button>
                <button class="btn" id="genPublishBtn" style="flex:1" disabled><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:15px;height:15px"><path d="M12 21V9M7 14l5-5 5 5M5 3h14"/></svg> Publicar (privado)</button>
                <button class="btn" id="genCopyLinkBtn" type="button" disabled><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:15px;height:15px"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg> Copiar link</button>
              </div>
              <div id="genMetaBox" hidden>
                <div class="dr-sec" style="margin-top:18px">Metadados da publicação</div>
                <div style="display:flex;flex-direction:column;gap:8px;margin-top:8px">
                  <label for="genMetaTitle" class="card-note" style="font-family:inherit">Título</label>
                  <input class="np-input" id="genMetaTitle" maxlength="100" style="width:100%" />
                  <label for="genMetaDescription" class="card-note" style="font-family:inherit">Descrição</label>
                  <textarea class="np-input" id="genMetaDescription" rows="4" style="width:100%;resize:vertical"></textarea>
                  <label for="genMetaTags" class="card-note" style="font-family:inherit">Tags (separadas por vírgula)</label>
                  <input class="np-input" id="genMetaTags" style="width:100%" />
                  <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
                    <button class="btn sm" id="genMetaAiBtn" type="button">Gerar com IA</button>
                    <button class="btn sm" id="genMetaSaveBtn" type="button">Salvar</button>
                    <button class="btn sm primary" id="genApproveBtn" type="button">Aprovar</button>
                    ${isLong ? `<button class="btn sm" id="genDeriveShortsBtn" type="button">Cortar Shorts</button>` : ""}
                    <label class="card-note" style="font-family:inherit;display:flex;align-items:center;gap:6px">Agendar para <input class="np-input" type="datetime-local" id="genPublishAt" style="padding:4px 6px" /></label>
                    <span class="card-note" id="genPublishStatus" role="status" style="font-family:inherit"></span>
                  </div>
                </div>
              </div></div>
          </div>
        </div>
      </div>`;
  }
  /* Voice/render/subtitle knobs are loaded from the chosen channel and remain editable for this job. */
  function studioParams(format) {
    const isLong = format === "long";
    return `
      <div class="card" style="margin-bottom:0">
        <div class="card-hd"><span class="card-title">Parâmetros de vídeo</span></div>
        <div class="card-bd" style="display:flex;flex-direction:column;gap:16px">
          <div class="grid2" style="margin:0">
            <div><label class="dr-sec" style="margin:0 0 7px">Idioma da narração</label>
              <select class="set-select" id="genLanguageSelect" style="width:100%"><option value="pt-BR">Português (Brasil)</option><option value="en-US">English (US)</option><option value="es-ES">Español</option><option value="de-DE">Deutsch</option></select></div>
            <div><label class="dr-sec" style="margin:0 0 7px">Voz sintética</label>
              <select class="set-select" id="genVoiceSelect" style="width:100%">
                <optgroup label="Português (Brasil)"><option value="pt-BR-FranciscaNeural">Francisca — Feminina</option><option value="pt-BR-AntonioNeural">Antonio — Masculina</option></optgroup>
                <optgroup label="English (US)"><option value="en-US-JennyNeural">Jenny — Female</option><option value="en-US-GuyNeural">Guy — Male</option></optgroup>
                <optgroup label="Español"><option value="es-ES-ElviraNeural">Elvira — Femenina</option><option value="es-ES-AlvaroNeural">Álvaro — Masculino</option></optgroup>
                <optgroup label="Deutsch"><option value="de-DE-KatjaNeural">Katja — Weiblich</option><option value="de-DE-ConradNeural">Conrad — Männlich</option></optgroup>
              </select></div>
          </div>
          <div class="grid2" style="margin:0">
            <div><label class="dr-sec" style="margin:0 0 7px">Velocidade da voz</label>
              <select class="set-select" id="genVoiceRate" style="width:100%"><option value="0.8">0.8×</option><option value="1" selected>1.0×</option><option value="1.2">1.2×</option><option value="1.5">1.5×</option></select></div>
            <div><label class="dr-sec" style="margin:0 0 7px">Volume da voz</label>
              <select class="set-select" id="genVoiceVolume" style="width:100%"><option value="0.6">60%</option><option value="1" selected>100%</option><option value="1.5">150%</option><option value="2">200%</option></select></div>
          </div>
          <div class="grid2" style="margin:0">
            <div><label class="dr-sec" style="margin:0 0 7px">Duração alvo</label>
              ${isLong
                ? `<select class="set-select" id="genTargetMinutes" style="width:100%">${[5, 8, 10, 15].map(m => `<option value="${m}" ${m === 8 ? "selected" : ""}>${m} minutos</option>`).join("")}</select>
              <div class="card-note" style="margin-top:6px">Usada pelo roteiro longo. O render leva de 30 a 90 min.</div>`
                : `<select class="set-select" id="genParagraphInput" style="width:100%">${[[4, "60 s"], [5, "75 s"], [6, "90 s"]].map(([n, label]) => `<option value="${n}" ${n === 5 ? "selected" : ""}>${label}</option>`).join("")}</select>
              <div class="card-note" style="margin-top:6px">Tamanho do roteiro gerado pela IA.</div>`}</div>
            <div><label class="dr-sec" style="margin:0 0 7px">Duração de cada clipe (segundos)</label>
              <input class="np-input" id="genDurationInput" style="width:100%" type="number" min="1" max="30" value="5" />
              <div class="card-note" style="margin-top:6px">Tempo de corte dinâmico entre cenas.</div></div>
          </div>
          <div class="grid2" style="margin:0;grid-template-columns:repeat(${isLong ? 3 : 4},1fr)">
            ${isLong ? "" : `<div><label class="dr-sec" style="margin:0 0 7px">Vídeos gerados</label>
              <input class="np-input" id="genVideoCount" style="width:100%" type="number" min="1" max="5" value="1" /></div>`}
            <div><label class="dr-sec" style="margin:0 0 7px">Imagens</label>
              <select class="set-select" id="genConcatMode" style="width:100%"><option value="script" selected>Seguir o roteiro (recomendado)</option><option value="random">Aleatória</option></select>
              <div class="card-note" style="margin-top:6px">"Seguir o roteiro" busca uma filmagem para cada trecho narrado, na ordem. As palavras-chave são preparadas ao clicar em Gerar.</div></div>
            <div><label class="dr-sec" style="margin:0 0 7px">Transição</label>
              <select class="set-select" id="genTransition" style="width:100%"><option value="">Nenhuma</option><option value="Shuffle">Aleatória</option><option value="FadeIn">Fade in</option><option value="FadeOut">Fade out</option><option value="SlideIn">Slide in</option><option value="SlideOut">Slide out</option><option value="ZoomIn">Zoom in</option><option value="ZoomOut">Zoom out</option></select></div>
            <div><label class="dr-sec" style="margin:0 0 7px">Velocidade do clipe</label>
              <select class="set-select" id="genClipSpeed" style="width:100%"><option value="0.8">0.8×</option><option value="1" selected>1.0×</option><option value="1.2">1.2×</option><option value="1.5">1.5×</option></select></div>
          </div>
          <div class="grid2" style="margin:0;grid-template-columns:1fr 1fr 1fr">
            <div><label class="dr-sec" style="margin:0 0 7px">Legendas</label>
              <label style="display:flex;align-items:center;gap:7px;height:34px"><input type="checkbox" class="cbx" id="genSubtitleEnabled" checked /> Ativadas</label></div>
            <div><label class="dr-sec" style="margin:0 0 7px">Posição da legenda</label>
              <select class="set-select" id="genSubtitlePosition" style="width:100%"><option value="bottom">Inferior</option><option value="center">Centro</option><option value="top">Superior</option></select></div>
            <div><label class="dr-sec" style="margin:0 0 7px">Espessura da borda</label>
              <input class="np-input" id="genStrokeWidth" style="width:100%" type="number" min="0" max="10" step="0.5" value="1.5" /></div>
          </div>
          <div class="grid2" style="margin:0;grid-template-columns:1fr 1fr 1fr">
            <div><label class="dr-sec" style="margin:0 0 7px">Tamanho da legenda</label><input class="np-input" id="genFontSizeInput" style="width:100%" type="number" value="56" /></div>
            <div><label class="dr-sec" style="margin:0 0 7px">Cor do texto</label><input class="np-input" id="genTextColorInput" style="width:100%" type="color" value="#FFFFFF" /></div>
            <div><label class="dr-sec" style="margin:0 0 7px">Cor da borda</label><input class="np-input" id="genStrokeColorInput" style="width:100%" type="color" value="#000000" /></div>
          </div>
          <div class="grid2" style="margin:0">
            <div><label class="dr-sec" style="margin:0 0 7px">Trilha sonora de fundo (BGM)</label>
              <select class="set-select" id="genBgmSelect" style="width:100%"><option value="">Sem música de fundo</option><option value="random">Aleatória (músicas do Turbo)</option></select></div>
            <div><label class="dr-sec" style="margin:0 0 7px">Volume da música</label>
              <select class="set-select" id="genBgmVolume" style="width:100%"><option value="0.1">10%</option><option value="0.2" selected>20%</option><option value="0.4">40%</option><option value="0.6">60%</option></select></div>
          </div>
        </div>
      </div>`;
  }
  function ic2(n) {
    const paths = {
      doc: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>',
      search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
      mic: '<rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10a7 7 0 0 0 14 0M12 19v3"/>',
      grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
      activity: '<path d="M3 12h4l3 8 4-16 3 8h4"/>',
    };
    return `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${paths[n] || ""}</svg>`;
  }
    return { gerador, youtubeShorts, libraryCard, studioParams, systemPromptPane, screenTabs };
  };
})();
