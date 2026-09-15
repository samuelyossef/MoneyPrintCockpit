/**
 * ============================================================================
 * Lattice Cockpit — Módulo de Renderização de Views e Conteúdo de Drawers (CKV)
 * ============================================================================
 * Este arquivo contém as funções de renderização em HTML para cada tela da SPA:
 * - overview: Visão geral com KPIs, cards de vídeos recentes e filtros
 * - gerador: Estúdio de criação HD com pipeline de 5 etapas e player de preview
 * - youtubeShorts: Modo automatizado para Shorts com agendamento
 * - canais: Gestão de contas (YouTube, TikTok), métricas e perfil do canal
 * - custosIA (Analytics): Gráficos de evolução, comparativos e ranking de receita
 * - usuarios & perfilUsuario: Gestão de usuários e permissões RBAC
 * - logs & logDrawer: Console de observabilidade e inspeção profunda de traces
 * - configuracoes: Parâmetros de IA, vozes, modelos e chaves de API
 * - notificacoes: Central de alertas e eventos do sistema
 * ============================================================================
 */
window.CKV = (function () {
  const D = window.CK;

  /**
   * Helper de sanitização de strings para evitar injeção de HTML / XSS
   */
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // ==========================================================================
  // 1. Gráficos Vetoriais em SVG (Zero Bibliotecas Externas)
  // ==========================================================================

  /**
   * Renderiza gráfico de área com gradiente suave em SVG
   * @param {Array<number>} data Pontos da série temporal
   * @param {string} color Cor da linha e preenchimento
   * @param {number} h Altura do gráfico em pixels
   */
  function area(data, color, h) {
    h = h || 150;
    const w = 600, max = Math.max(...data), min = Math.min(...data), rng = (max - min) || 1;
    const pts = data.map((v, i) => [i / (data.length - 1) * w, h - 8 - ((v - min) / rng) * (h - 26)]);
    const d = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
    return `<svg class="chart-svg" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
      <path d="${d} L${w} ${h} L0 ${h} Z" fill="${color}" opacity=".12"/>
      <path d="${d}" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>
    </svg>`;
  }

  /**
   * Renderiza mini gráfico tipo sparkline para os cards de KPI
   */
  function spark(data, color) {
    const w = 200, h = 26, max = Math.max(...data), min = Math.min(...data), rng = (max - min) || 1;
    const pts = data.map((v, i) => [i / (data.length - 1) * w, h - 3 - ((v - min) / rng) * (h - 8)]);
    const d = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
    return `<svg class="kpi-spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
      <path d="${d} L${w} ${h} L0 ${h} Z" fill="${color}" opacity=".10"/>
      <path d="${d}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>
    </svg>`;
  }

  /**
   * Renderiza gráfico simples de barras verticais
   */
  function bars(data, color) {
    const max = Math.max(...data) || 1;
    return `<div class="bars-lite">${data.map(v =>
      `<div class="bl-col" style="height:${(v / max * 100).toFixed(1)}%;background:${color}"></div>`).join("")}</div>`;
  }

  /**
   * Rótulos do eixo X de acordo com o período selecionado
   */
  function xaxis(scope) {
    const per = D.PERIODS.find(p => p.id === scope.period);
    if (per.id === "7d") return ["Sáb", "Dom", "Seg", "Ter", "Qua", "Qui", "Hoje"];
    if (per.id === "90d") return ["Jun", "", "", "Jul", "", "", "Ago", "", "", "Set", "", "Hoje"];
    return ["6 ago", "", "", "16 ago", "", "", "26 ago", "", "Hoje"];
  }

  /**
   * Card container para gráficos com título, nota e rótulos
   */
  const chartCard = (title, note, body, x) => `
    <div class="card" style="margin-bottom:0">
      <div class="card-hd"><span class="card-title">${title}</span><span class="spacer"></span><span class="card-note">${note}</span></div>
      <div class="card-bd">${body}${x ? `<div class="chart-x">${x.map(l => `<span>${l}</span>`).join("")}</div>` : ""}</div>
    </div>`;

  // ==========================================================================
  // 2. Tela: Visão Geral (Overview)
  // ==========================================================================
  function overview(scope) {
    const per = D.PERIODS.find(p => p.id === scope.period);
    const org = D.ORGS.find(o => o.id === scope.org);
    const n = per.n > 12 ? 9 : per.n;
    const k = D.kpis(scope);
    const kpiHtml = k.map(c => {
      const up = c.invert ? c.delta < 0 : c.delta > 0;
      const sign = c.delta > 0 ? "↑ " : c.delta < 0 ? "↓ " : "";
      const val = c.raw ? Math.round(Math.abs(c.delta)) : Math.abs(c.delta).toFixed(1).replace(".", ",");
      return `<button class="kpi" data-goto="${c.view}">
        <div class="kpi-label">${c.label}<span class="kpi-go">abrir ›</span></div>
        <div class="kpi-value">${c.value}</div>
        <div class="kpi-delta ${up ? "up" : "down"}">${sign}${val}${c.suffix} <span class="prior">vs período anterior</span></div>
        ${spark(D.series(c.seed, 14, 100, up ? 0.5 : -0.3, 0.4), c.color)}
      </button>`;
    }).join("");

    const alerts = D.ALERTS.filter(a => scope.org === "all" || a.org === org.name || a.org === "Plataforma");
    const counts = { critical: 0, warning: 0, info: 0 };
    alerts.forEach(a => counts[a.sev]++);

    return `

      <div class="card" style="margin-bottom:0">
        <div class="card-hd" style="flex-wrap:wrap;row-gap:8px"><span class="card-title" style="flex-shrink:0">Vídeos recentes</span><span class="spacer"></span>
          <button class="btn sm" id="vidAdvBtn" style="margin-right:8px"><svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 6h16M7 12h10M10 18h4"/></svg> Filtro avançado</button>
          <div class="sev-tabs" id="vidTabs">
            <button class="sev-tab is-on" data-vidfmt="all">Todos</button>
            <button class="sev-tab" data-vidfmt="vert">Verticais</button>
            <button class="sev-tab" data-vidfmt="horiz">Horizontais</button>
          </div>
          <div class="sev-tabs" id="vidView" style="margin-left:8px">
            <button class="sev-tab is-on" data-vidview="grid" aria-label="Ver em grade"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg></button>
            <button class="sev-tab" data-vidview="list" aria-label="Ver em lista"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/></svg></button>
          </div>
        </div>
        <div class="view-filters" id="vidAdvPanel" style="display:none;margin-top:0;padding:12px 16px;border-bottom:1px solid var(--border)">
          <label class="view-search" style="flex:1 1 220px"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
            <input id="vidFilterTitle" placeholder="Buscar por título" /></label>
          <select class="set-select" id="vidFilterChannel"><option value="">Todos os canais</option>${D.CHANNELS.map(c => `<option>${esc(c.name)}</option>`).join("")}</select>
          <select class="set-select" id="vidFilterPlatform"><option value="">Todas as plataformas</option>${[...new Set(D.VIDEOS.vert.concat(D.VIDEOS.horiz).map(v => v[1]))].map(p => `<option>${p}</option>`).join("")}</select>
          <div class="view-filters-spacer"></div>
          <button class="btn sm" id="vidFilterClear">Limpar</button>
        </div>
        <div class="bulkbar" id="vidBulkBar">
          <span class="bb-count" id="vidBbCount">0 selecionados</span>
          <span class="spacer"></span>
          <button class="btn sm danger" id="vidDeleteBtn">Excluir selecionados</button>
        </div>
        <div class="card-bd" id="vidBody">${videoGrids("all")}</div>
      </div>

      `;
  }

  // Real Gateway videos carry [.., jobId, finalArtifactId] at tuple indexes 4-5.
  const videoUrl = (v, mode) => v[4] && v[5] ? `/api/jobs/${encodeURIComponent(v[4])}/artifacts/${encodeURIComponent(v[5])}/${mode}` : "";
  const videoThumb = (v, id, small) => {
    const src = videoUrl(v, "stream");
    if (!src) return `<image-slot id="${id}" shape="rect" placeholder="${small ? "" : "Miniatura do vídeo"}"${small ? ' style="width:44px;height:44px;display:block"' : ""}></image-slot>`;
    // #t=0.5 makes the browser paint an actual frame instead of a black box.
    return small
      ? `<video src="${src}#t=0.5" preload="metadata" muted playsinline style="width:44px;height:44px;object-fit:cover;border-radius:6px;display:block;background:#000"></video>`
      : `<video src="${src}#t=0.5" preload="metadata" muted playsinline controls style="width:100%;height:100%;object-fit:cover;display:block;background:#000"></video>`;
  };
  const videoMenu = (v, kind, i) => {
    const download = videoUrl(v, "download");
    return `
      ${download ? `<a class="pop-item" href="${download}" download style="text-decoration:none;color:inherit"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 3v12M7 10l5 5 5-5M5 21h14"/></svg><span>Baixar MP4</span></a>` : ""}
      <div class="pop-item" data-toast="Publicação ainda não configurada: conecte o Upload-Post"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 21V9M7 14l5-5 5 5M5 3h14"/></svg><span>Publicar no YouTube</span></div>
      ${download ? `<div class="pop-item" data-copy="${download}"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg><span>Copiar link</span></div>` : ""}
      <div class="pop-item" data-vid-del-kind="${kind}" data-vid-del-idx="${i}" style="color:var(--err)"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0-1 14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2L4 6h16Z"/></svg><span>Excluir vídeo</span></div>`;
  };

  function videoGrids(fmt, view, filt) {
    view = view || "grid";
    filt = filt || {};
    const match = v => (!filt.channel || v[3] === filt.channel) && (!filt.platform || v[1] === filt.platform) && (!filt.title || v[2].toLowerCase().includes(filt.title.toLowerCase()));
    const vert = D.VIDEOS.vert.map((v, i) => [v, i]).filter(([v]) => match(v));
    const horiz = D.VIDEOS.horiz.map((v, i) => [v, i]).filter(([v]) => match(v));
    if (view === "list") return videoList(fmt, filt);
    const card = (v, i, kind, id) => `
      <div class="vid-card ${v[0] === "9:16" ? "is-vert" : "is-horiz"}">
        <label class="vid-cbx-wrap" style="position:absolute;top:6px;left:6px;z-index:2"><input type="checkbox" class="cbx vid-cbx" data-vid-kind="${kind}" data-vid-idx="${i}" /></label>
        <div class="vid-thumb" style="position:relative">${videoThumb(v, id)}
          <span class="vid-fmt">${v[1]}</span></div>
        <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:6px">
          <div style="min-width:0"><div class="vid-cap">${esc(v[2])}</div>
            ${v[3] ? `<div style="font-size:11px;color:var(--muted-2)">${esc(v[3])}</div>` : ""}</div>
          <div class="ctx" style="flex-shrink:0">
            <button class="icon-btn" data-pop="vidMenu-${kind}-${i}" aria-label="Mais ações" style="width:22px;height:22px"><svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><circle cx="12" cy="5" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="12" cy="19" r="1.8"/></svg></button>
            <div class="pop to-right" id="vidMenu-${kind}-${i}">${videoMenu(v, kind, i)}</div>
          </div>
        </div>
      </div>`;
    let out = "";
    if (fmt !== "horiz") out += `<div class="vid-grid">${vert.map(([v, i], k) => card(v, i, "vert", "ov-vid-" + k)).join("")}</div>`;
    if (fmt === "all" && horiz.length) out += `<div class="vid-grid" style="margin-top:12px">${horiz.map(([v, i], k) => card(v, i, "horiz", "ov-vid-h-" + k)).join("")}</div>`;
    if (fmt === "horiz") out += `<div class="vid-grid">${horiz.map(([v, i], k) => card(v, i, "horiz", "ov-vid-h-" + k)).join("")}</div>`;
    if (!vert.length && !horiz.length) return `<div class="empty"><div class="empty-t">Nenhum vídeo encontrado</div><div class="empty-s">Ajuste os filtros para ver os vídeos gerados.</div></div>`;
    return out;
  }

  function videoList(fmt, filt) {
    filt = filt || {};
    const match = v => (!filt.channel || v[3] === filt.channel) && (!filt.platform || v[1] === filt.platform) && (!filt.title || v[2].toLowerCase().includes(filt.title.toLowerCase()));
    const tagged = D.VIDEOS.vert.map((v, i) => [v, i, "vert"]).concat(D.VIDEOS.horiz.map((v, i) => [v, i, "horiz"]));
    const all = tagged.filter(([v]) => match(v));
    const rows = fmt === "vert" ? all.filter(([v]) => v[0] === "9:16") : fmt === "horiz" ? all.filter(([v]) => v[0] === "16:9") : all;
    if (!rows.length) return `<div class="empty"><div class="empty-t">Nenhum vídeo encontrado</div><div class="empty-s">Ajuste os filtros para ver os vídeos gerados.</div></div>`;
    return `<div class="tablewrap"><table style="min-width:0;width:100%">
      <thead><tr><th class="tick-col"><input type="checkbox" class="cbx" id="vidSelAll" aria-label="Selecionar tudo" /></th><th></th><th>Título</th><th>Canal</th><th>Plataforma</th><th>Formato</th><th></th></tr></thead>
      <tbody>${rows.map(([v, i, kind], k) => `
        <tr class="user-row">
          <td class="tick-col"><input type="checkbox" class="cbx vid-cbx" data-vid-kind="${kind}" data-vid-idx="${i}" aria-label="Selecionar vídeo" /></td>
          <td style="width:56px">${videoThumb(v, "ov-list-" + k, true)}</td>
          <td style="font-weight:500;color:var(--ink)">${esc(v[2])}</td>
          <td>${v[3] ? esc(v[3]) : "—"}</td>
          <td>${v[1]}</td>
          <td><span class="badge plan-pro">${v[0]}</span></td>
          <td style="width:36px"><div class="ctx"><button class="icon-btn" data-pop="vidMenuL-${kind}-${i}" aria-label="Mais ações"><svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><circle cx="12" cy="5" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="12" cy="19" r="1.8"/></svg></button>
            <div class="pop to-right" id="vidMenuL-${kind}-${i}">${videoMenu(v, kind, i)}</div></div></td>
        </tr>`).join("")}</tbody>
    </table></div>`;
  }

  function alertRows(list, sev) {
    const rows = list.filter(a => sev === "all" || a.sev === sev);
    if (!rows.length) return `<div class="empty"><div class="empty-t">Nada nesse nível</div><div class="empty-s">Nenhum alerta ${sev === "critical" ? "crítico" : "de aviso"} no escopo atual.</div></div>`;
    return rows.map(a => `
      <div class="alert-row" data-goto="${a.view}">
        <span class="sev-dot ${a.sev}"></span>
        <span class="alert-main"><span class="alert-title">${a.title}</span>
          <div class="alert-meta"><span>${a.org}</span><span class="mono">${a.meta}</span></div></span>
        <span class="alert-time">${a.time}</span>
      </div>`).join("");
  }

  function filtersBar(scope) {
    const per = D.PERIODS.find(p => p.id === scope.period);
    const org = D.ORGS.find(o => o.id === scope.org);
    return `<div class="gfilters">
      <div class="ctx"><button class="chipsel" data-pop="fPeriod"><span class="cs-k">Período</span> ${per.label} ${chev()}</button>
        <div class="pop" id="fPeriod">${D.PERIODS.map(p => `<button class="pop-item ${p.id === scope.period ? "is-on" : ""}" data-set="period" data-val="${p.id}">${p.label}<span class="tick">✓</span></button>`).join("")}</div></div>
      <div class="ctx" hidden><button class="chipsel" data-pop="fOrg"><span class="cs-k">Organização</span> ${org.id === "all" ? "Todas" : org.name} ${chev()}</button>
        <div class="pop" id="fOrg">${D.ORGS.map(o => `<button class="pop-item ${o.id === scope.org ? "is-on" : ""}" data-set="org" data-val="${o.id}"><span class="org-dot" style="background:${o.color}">${o.short}</span>${o.id === "all" ? "Todas" : o.name}<span class="tick">✓</span></button>`).join("")}</div></div>
      <div class="ctx"><button class="chipsel" data-pop="fEnv"><span class="cs-k">Ambiente</span> ${scope.env} ${chev()}</button>
        <div class="pop" id="fEnv">${D.ENVS.map(e => `<button class="pop-item ${e === scope.env ? "is-on" : ""}" data-set="env" data-val="${e}">${e}<span class="tick">✓</span></button>`).join("")}</div></div>
      <button class="chipsel" data-reset="1"><span class="cs-k">Limpar</span></button>
      <span class="scope-note">escopo aplicado a todos os widgets</span>
    </div>`;
  }
  const chev = () => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="m6 9 6 6 6-6"/></svg>`;

  /* ── Usuários ───────────────────────────────── */
  function usuarios(scope) {
    const org = D.ORGS.find(o => o.id === scope.org);
    const rows = D.USERS.filter(u => scope.org === "all" || u.org === org.name.replace(" Organization", ""));
    return `
      <div class="view-filters" style="margin-top:16px">
        <label class="view-search"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
          <input id="uSearch" placeholder="Buscar por nome, e-mail ou papel" /></label>
        <select class="set-select" id="uRole"><option>Todos os papéis</option><option>Owner</option><option>Admin</option><option>Membro</option><option>Suporte</option><option>Somente ver</option></select>
        <select class="set-select" id="uStatus"><option>Todos os status</option><option>active</option><option>pending</option><option>canceled</option></select>
        <div class="view-filters-spacer"></div>
        <span class="rows-count" id="uCount">${rows.length} usuários</span>
      </div>
      <div class="bulkbar" id="bulkbar">
        <span class="bb-count" id="bbCount">0 selecionados</span>
        <span class="spacer"></span>
        <button class="btn sm" data-bulk="Papel alterado para Membro">Alterar papel</button>
        <button class="btn sm" data-bulk="MFA resetado">Resetar MFA</button>
        <button class="btn sm" data-bulk="Sessões revogadas">Revogar sessões</button>
        <button class="btn sm danger" data-bulk="Usuários desativados">Desativar</button>
      </div>
      <div class="tablewrap">
        <table>
          <thead><tr>
            <th class="tick-col"><input type="checkbox" class="cbx" id="selAll" aria-label="Selecionar tudo" /></th>
            <th>Usuário</th><th>Papel</th><th>Organização</th><th>Workspace</th><th>Status</th><th>Última atividade</th><th>MFA</th>
          </tr></thead>
          <tbody id="uBody">${userRows(rows)}</tbody>
        </table>
        <div class="foot-pag"><span>Mostrando ${rows.length} de 1.284</span>
          <span class="pg-btns"><button class="pg-btn">‹</button><button class="pg-btn">›</button></span></div>
      </div>`;
  }
  function userRows(rows) {
    if (!rows.length) return `<tr><td colspan="8"><div class="empty"><div class="empty-t">Nenhum usuário encontrado</div><div class="empty-s">Ajuste a busca ou os filtros de escopo.</div></div></td></tr>`;
    return rows.map(u => `
      <tr class="user-row" data-user="${esc(u.email)}">
        <td class="tick-col"><input type="checkbox" class="cbx row-cbx" aria-label="Selecionar ${esc(u.name)}" /></td>
        <td><span class="user-cell"><span class="u-avatar" style="background:${u.color}">${u.name.split(" ").map(w => w[0]).slice(0, 2).join("")}</span>
          <span class="u-meta"><span class="u-name">${esc(u.name)}</span><span class="u-email">${esc(u.email)}</span></span></span></td>
        <td><span class="role-tag">${u.role}</span></td>
        <td>${u.org}</td><td>${u.ws}</td>
        <td><span class="status-pill"><span class="status-dot ${u.status}"></span>${u.status === "active" ? "Ativo" : u.status === "pending" ? "Convidado" : "Desativado"}</span></td>
        <td>${u.last}</td>
        <td>${u.mfa ? '<span style="color:var(--ok)">✓</span>' : '<span style="color:var(--muted-2)">—</span>'}</td>
      </tr>`).join("");
  }

  /* ── Logs ───────────────────────────────────── */
  function logs(scope) {
    return `
      <div class="view-filters" style="margin-top:16px">
        <label class="view-search"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
          <input id="lSearch" placeholder="Buscar mensagem, serviço ou trace ID" /></label>
        <select class="set-select" id="lLevel"><option>Todos os níveis</option><option>CRITICAL</option><option>ERROR</option><option>WARN</option><option>INFO</option></select>
        <select class="set-select" id="lSvc"><option>Todos os serviços</option>${[...new Set(D.LOGS.map(l => l.svc))].map(s => `<option>${s}</option>`).join("")}</select>
        <div class="view-filters-spacer"></div>
        <span class="rows-count" id="lCount">${D.LOGS.length} linhas</span>
      </div>
      <div class="console" id="logBody">${logRows(D.LOGS)}</div>
      <div class="grid2" style="margin-top:20px">
        ${chartCard("Trace tr_9f2b41c8 — geração de vídeo", "total 3,4 s", `<div class="trace">${D.TRACE.map(t =>
          `<div class="trace-row"><span class="trace-name">${t.name}</span>
            <span class="trace-track"><span class="trace-fill ${t.slow ? "slow" : ""}" style="width:${(t.ms / 3500 * 100).toFixed(1)}%"></span></span>
            <span class="trace-ms">${t.ms < 1000 ? t.ms + "ms" : (t.ms / 1000).toFixed(1).replace(".", ",") + "s"}</span></div>`).join("")}</div>`)}
        ${chartCard("Erros por hora", "últimas 24 h", bars(D.series(91, 24, 100, -0.1, 0.8), "var(--err)"))}
      </div>`;
  }
  function logRows(rows) {
    if (!rows.length) return `<div class="empty"><div class="empty-t">Nenhuma linha corresponde ao filtro</div><div class="empty-s">Limpe os filtros para ver o fluxo completo.</div></div>`;
    return rows.map((l, i) => `
      <div class="log-row" data-log="${i}">
        <span class="log-ts">${l.ts}</span><span class="log-lvl ${l.lvl}">${l.lvl}</span>
        <span class="log-svc">${l.svc}</span><span class="log-msg">${esc(l.msg)}</span>
      </div>`).join("");
  }

  /* ── Analytics ──────────────────────────────── */
  function custosIA(scope) {
    const per = D.PERIODS.find(p => p.id === scope.period);
    const n = per.n > 12 ? 9 : per.n;
    const totalVideos = D.VIDEOS.vert.length + D.VIDEOS.horiz.length;
    const totalFollowers = D.CHANNELS.reduce((s, c) => s + c.followers, 0);
    const ytViews = D.CHANNELS.filter(c => c.platform.startsWith("YouTube")).length;
    const tkViews = D.CHANNELS.filter(c => c.platform === "TikTok").length;
    const platforms = [
      ["YouTube", "#dc2626", Math.round(ytViews / (D.CHANNELS.length || 1) * 100)],
      ["TikTok", "#000000", Math.round(tkViews / (D.CHANNELS.length || 1) * 100)],
    ];
    const top = D.CHANNELS.slice().sort((a, b) => b.followers - a.followers).slice(0, 6);
    const totalRevenue = D.CHANNELS.reduce((s, c) => s + Number(String(c.revenue || "").replace(/\D/g, "") || 0), 0);
    return `
      <div class="kpi-row">
        <div class="kpi" style="cursor:default"><div class="kpi-label">Vídeos gerados</div><div class="kpi-value">${totalVideos}</div>
          <div class="kpi-delta up">↑ 12,4% <span class="prior">vs período anterior</span></div>${spark(D.series(5, 14, 100, 0.4, 0.5), "var(--accent)")}</div>
        <div class="kpi" style="cursor:default"><div class="kpi-label">Views totais</div><div class="kpi-value">33,6M</div>
          <div class="kpi-delta up">↑ 9,1% <span class="prior">vs período anterior</span></div>${spark(D.series(9, 14, 100, 0.3, 0.4), "var(--ok)")}</div>
        <div class="kpi" style="cursor:default"><div class="kpi-label">Seguidores totais</div><div class="kpi-value">${D.fmtInt(totalFollowers)}</div>
          <div class="kpi-delta up">↑ 4,8% <span class="prior">vs período anterior</span></div>${spark(D.series(13, 14, 100, 0.35, 0.4), "var(--warn)")}</div>
        <div class="kpi" style="cursor:default"><div class="kpi-label">Gasto com IA</div><div class="kpi-value">R$ 48.920</div>
          <div class="kpi-delta down">↓ 3,6% <span class="prior">vs período anterior</span></div>${spark(D.series(21, 14, 100, -0.2, 0.3), "var(--muted)")}</div>
        <div class="kpi" style="cursor:default"><div class="kpi-label">Receita total</div><div class="kpi-value">R$ ${D.fmtInt(totalRevenue)}</div>
          <div class="kpi-delta up">↑ 6,7% <span class="prior">vs período anterior</span></div>${spark(D.series(27, 14, 100, 0.3, 0.4), "var(--ok)")}</div>
      </div>
      <div class="grid2">
        ${chartCard("Vídeos gerados ao longo do tempo", `${per.label.toLowerCase()} · ${per.step}`,
          area(D.series(31, n, 100, 0.36, 0.4), "var(--accent)"), xaxis(scope))}
        ${chartCard("Views por dia", "todas as plataformas",
          bars(D.series(47, n, 100, 0.3, 0.5), "var(--ok)"), xaxis(scope))}
      </div>
      <div class="grid2" style="margin-top:20px">
        <div class="card" style="margin-bottom:0"><div class="card-hd"><span class="card-title">Desempenho por plataforma</span><span class="spacer"></span><span class="card-note">${per.label.toLowerCase()}</span></div>
          <div class="card-bd" style="display:flex;flex-direction:column;gap:11px">${platforms.map(p => `
            <div>
              <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:5px"><span>${p[0]}</span><span class="mono" style="color:var(--muted)">${p[2]}%</span></div>
              <div style="height:7px;border-radius:99px;background:var(--line)"><div style="height:100%;width:${p[2]}%;border-radius:99px;background:${p[1]}"></div></div>
            </div>`).join("")}</div></div>
        <div class="card" style="margin-bottom:0"><div class="card-hd"><span class="card-title">Canais com melhor desempenho</span><span class="spacer"></span><span class="card-note">top ${top.length}</span></div>
          <table><thead><tr><th>Canal</th><th class="num">Seguidores</th><th class="num">Views</th><th class="num">Receita</th></tr></thead>
          <tbody>${top.map(c => `<tr data-ch-row="${esc(c.name)}" style="cursor:pointer"><td style="font-weight:500;color:var(--ink)">${esc(c.name)}</td><td class="num mono">${D.fmtInt(c.followers)}</td><td class="num mono">${c.views}</td><td class="num mono">${esc(c.revenue)}</td></tr>`).join("")}</tbody></table></div>
      </div>`;
  }

  /* ── Billing ────────────────────────────────── */
  function billing(scope) {
    const org = D.ORGS.find(o => o.id === scope.org);
    const f = org.f;
    const cards = [
      ["MRR", D.fmtBRL(124800 * f), "+8,2%", "up"],
      ["ARR", D.fmtBRL(1497600 * f), "+9,1%", "up"],
      ["Nova receita", D.fmtBRL(18400 * f), "+14,0%", "up"],
      ["Expansão", D.fmtBRL(7200 * f), "+3,4%", "up"],
      ["Churn de receita", D.fmtBRL(2620 * f), "−0,3 p.p.", "down"],
    ];
    return `
      <div class="kpi-row">${cards.map(c => `
        <div class="kpi" style="cursor:default">
          <div class="kpi-label">${c[0]}</div><div class="kpi-value">${c[1]}</div>
          <div class="kpi-delta ${c[3]}">${c[2]} <span class="prior">vs período anterior</span></div>
        </div>`).join("")}</div>

      <div class="card" style="margin-bottom:20px">
        <div class="card-hd"><span class="card-title">Faturas</span><span class="spacer"></span>
          <span class="card-note">2 em atraso</span></div>
        <table>
          <thead><tr><th>Fatura</th><th>Organização</th><th>Plano</th><th class="num">Valor</th><th>Vencimento</th><th>Status</th></tr></thead>
          <tbody>${D.INVOICES.map(i => `<tr>
            <td style="font-family:var(--font-mono);font-size:12px">${i.id}</td>
            <td>${i.org}</td><td>${i.plan}</td><td class="num">${i.amount}</td><td>${i.due}</td>
            <td><span class="status-pill"><span class="status-dot ${i.status}"></span>${i.status === "paid" ? "Paga" : i.status === "overdue" ? "Em atraso" : "Pendente"}</span></td>
          </tr>`).join("")}</tbody>
        </table>
      </div>

      <div class="card">
        <div class="card-hd"><span class="card-title">Planos</span><span class="spacer"></span><button class="btn sm">Editar planos</button></div>
        <table>
          <thead><tr><th>Plano</th><th class="num">Preço/mês</th><th class="num">Usuários</th><th class="num">Tokens</th><th class="num">Projetos</th><th class="num">Chamadas de API</th><th class="num">Orgs</th></tr></thead>
          <tbody>${D.PLANS.map(p => `<tr>
            <td><span class="badge plan-${p.name.toLowerCase() === "pro" ? "pro" : p.name.toLowerCase() === "enterprise" ? "enterprise" : p.name.toLowerCase() === "business" ? "team" : "free"}">${p.name}</span>${p.current ? ' <span class="card-note">mais assinado</span>' : ""}</td>
            <td class="num">${p.price}</td><td class="num">${p.users}</td><td class="num">${p.tokens}</td>
            <td class="num">${p.projects}</td><td class="num">${p.api}</td><td class="num">${p.orgs}</td>
          </tr>`).join("")}</tbody>
        </table>
      </div>`;
  }

  /* ── Clientes ───────────────────────────────── */
  function clientes(scope) {
    const rows = D.CUSTOMERS;
    return `
      <div class="view-filters" style="margin-top:16px">
        <label class="view-search"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
          <input placeholder="Buscar organização" /></label>
        <select class="set-select"><option>Todos os planos</option><option>Enterprise</option><option>Business</option><option>Pro</option></select>
        <select class="set-select"><option>Health score</option><option>Abaixo de 60</option><option>60 a 80</option><option>Acima de 80</option></select>
        <div class="view-filters-spacer"></div><span class="rows-count" id="orgCount">${rows.length} organizações</span>
        <button class="btn primary sm" id="newOrgBtn">+ Nova organização</button>
      </div>
      <div class="tablewrap"><table>
        <thead><tr><th>Cliente</th><th>Plano</th><th class="num">Usuários</th><th class="num">MRR</th><th class="num">Uso</th><th class="num">Health</th><th>Status</th></tr></thead>
        <tbody id="orgBody">${orgRows(rows)}</tbody>
      </table></div>`;
  }
  function orgRows(rows) {
    return rows.map(r => `<tr class="user-row" data-org-row="${r[0]}">
          <td style="font-weight:500;color:var(--ink)">${r[0]}</td>
          <td><span class="badge plan-${r[1].toLowerCase() === "pro" ? "pro" : r[1].toLowerCase() === "enterprise" ? "enterprise" : r[1].toLowerCase() === "business" ? "team" : "free"}">${r[1]}</span></td>
          <td class="num">${r[2]}</td><td class="num">${r[3]}</td><td class="num">${r[4]}%</td>
          <td class="num" style="color:${r[5] < 60 ? "var(--err)" : r[5] < 80 ? "var(--warn)" : "var(--ok)"}">${r[5]}</td>
          <td><span class="status-pill"><span class="status-dot ${r[6]}"></span>${{ active: "Ativa", past_due: "Em atraso", trialing: "Trial", canceled: "Cancelada" }[r[6]]}</span></td>
        </tr>`).join("");
  }


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
    return `<div style="display:flex;align-items:center;gap:14px;width:100%">
      <div style="flex:1"><div class="view-hd-title">Canais</div><div class="view-hd-sub">Contas conectadas, plataformas e métricas de publicação</div></div>
      <div style="display:flex;gap:12px">${stat("#dc2626", "Canais YouTube", yt, ytIcon)}${stat("#000000", "Canais TikTok", tk, tkIcon)}</div>
    </div>`;
  }
  const CH_STATUS = { connected: ["active", "Conectado"], pending: ["pending", "Aguardando login"], expired: ["past_due", "Expirado"] };
  const CH_LANGS = [["pt-BR", "Português (Brasil)"], ["en-US", "English (US)"], ["es-ES", "Español"]];
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
      <div class="tablewrap"><table>
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
    return { platform: "YouTube", niche: "", language: "pt-BR" };
  }
  function canal(c, tab, editing, isNew) {
    editing = editing || isNew;
    const tabs = isNew ? [["dados", "Conectar"]] : [["dados", "Dados da conta"], ["conexao", "Conexão"]];
    tab = tabs.some(t => t[0] === tab) ? tab : "dados";
    const txt = (f, v, ph) => editing
      ? `<input class="np-input" style="width:100%" data-cfield="${f}" value="${esc(v)}" placeholder="${ph || ""}" />`
      : esc(v || "—");
    const sel = (f, v, opts) => editing
      ? `<select class="set-select" style="width:100%" data-cfield="${f}">${opts.map(([val, label]) => `<option value="${val}" ${val === v ? "selected" : ""}>${label}</option>`).join("")}</select>`
      : esc((opts.find(o => o[0] === v) || [v, v])[1]);
    const kpi = (label, value) => `<div class="kpi" style="cursor:default"><div class="kpi-label">${label}</div><div class="kpi-value">${D.fmtInt(value || 0)}</div></div>`;
    const panes = {
      dados: isNew ? `
        <div class="card" style="margin-bottom:0;max-width:560px">
          <div class="card-hd"><span class="card-title">Conectar canal</span></div>
          <div class="card-bd" style="display:flex;flex-direction:column;gap:16px">
            <div class="md-field"><label>Plataforma</label>${sel("platform", c.platform, [["YouTube", "YouTube (Shorts e vídeos longos)"], ["TikTok", "TikTok (via Upload-Post)"]])}</div>
            <div class="md-field"><label>Nicho do canal</label>${txt("niche", c.niche, "Ex: Curiosidades, Histórias Reais, Tecnologia")}</div>
            <div class="md-field"><label>Idioma dos vídeos</label>${sel("language", c.language, CH_LANGS)}</div>
            <div class="card-note" style="font-family:inherit;line-height:1.55">
              <b>YouTube:</b> abre o login do Google em outra aba; escolha o canal e autorize. Nome, @handle e inscritos vêm do próprio YouTube.<br>
              <b>TikTok:</b> exige a chave Upload-Post em Configurações; abre a página do Upload-Post para você entrar na conta TikTok.<br>
              Os vídeos são sempre publicados como <b>privados</b>.
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
              <dt>Idioma</dt><dd>${sel("language", c.language, CH_LANGS)}</dd>
              <dt>Status</dt><dd>${chStatus(c.status)}</dd>
            </dl></div></div>
          ${c.platform === "YouTube" ? kpi("Inscritos", c.followers) + kpi("Vídeos", c.videos) + kpi("Views totais", c.views) : ""}
        </div>`,
      conexao: `
        <div class="card" style="margin-bottom:0;max-width:560px"><div class="card-hd"><span class="card-title">Conexão</span></div>
          <div class="card-bd"><dl class="kv">
            <dt>Status</dt><dd>${chStatus(c.status)}</dd>
            <dt>Via</dt><dd>${c.platform === "TikTok" ? "Upload-Post" : "Google OAuth (YouTube Data API)"}</dd>
            <dt>Publicação</dt><dd>Sempre como <b>privado</b></dd>
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

  /* ── Drawers ────────────────────────────────── */
  function userDrawer(u) {
    return {
      title: u.name,
      sub: `${u.title} · ${u.role} · ${u.org}`,
      body: `
        <div class="dr-sec">Perfil</div>
        <dl class="kv">
          <dt>E-mail</dt><dd class="mono">${esc(u.email)}</dd>
          <dt>Papel</dt><dd>${u.role}</dd>
          <dt>Organização</dt><dd>${u.org}</dd>
          <dt>Workspace</dt><dd>${u.ws}</dd>
          <dt>Status</dt><dd><span class="status-pill"><span class="status-dot ${u.status}"></span>${u.status === "active" ? "Ativo" : u.status === "pending" ? "Convidado" : "Desativado"}</span></dd>
          <dt>Última atividade</dt><dd>${u.last}</dd>
          <dt>MFA</dt><dd>${u.mfa ? "Habilitado (TOTP)" : "Não configurado"}</dd>
        </dl>
        <div class="dr-sec">Escopo das permissões</div>
        <div class="scope-tags"><span class="tag is-on">Organização</span><span class="tag is-on">Workspace</span><span class="tag">Projeto</span></div>
        <div class="dr-sec">Permissões</div>
        <table class="perm">
          <thead><tr><th>Recurso</th><th>Ver</th><th>Criar</th><th>Excluir</th><th>Gerir</th></tr></thead>
          <tbody>${D.PERMS.map(p => `<tr><td>${p[0]}</td>${p.slice(1).map(v =>
            `<td class="${v ? "yes" : "no"}">${v ? "✓" : "×"}</td>`).join("")}</tr>`).join("")}</tbody>
        </table>
        <div class="dr-sec">Sessões ativas</div>
        <dl class="kv">
          <dt>macOS · Chrome</dt><dd class="mono">191.34.20.11 · há 2 min</dd>
          <dt>iOS · App</dt><dd class="mono">191.34.20.62 · há 5 h</dd>
        </dl>
        <div class="dr-sec">Consumo no período</div>
        <dl class="kv">
          <dt>Requisições</dt><dd class="mono">18.402</dd>
          <dt>Tokens</dt><dd class="mono">4,2M</dd>
          <dt>Custo de IA</dt><dd class="mono">US$ 312,80</dd>
        </dl>`,
      footer: `<button class="btn primary" data-open-profile="${esc(u.email)}">Abrir perfil completo</button>
               <button class="btn" data-toast="MFA resetado — usuário notificado">Resetar MFA</button>
               <button class="btn danger" data-toast="Usuário desativado">Desativar</button>`,
    };
  }

  /* ── Perfil completo do usuário ─────────────── */
  function perfilUsuario(u, tab, editing) {
    tab = tab || "perfil";
    const tabs = [
      ["perfil", "Perfil"], ["acesso", "Acesso"], ["permissoes", "Permissões"], ["atividade", "Atividade"],
      ["sessoes", "Sessões"], ["api", "Acesso a API"], ["seguranca", "Segurança"], ["uso", "Uso"], ["auditoria", "Auditoria"],
    ];
    const uid = "usr_" + u.email.split("@")[0].slice(0, 6);
    u.lang = u.lang || "Português (BR)";
    u.tz = u.tz || "America/Sao_Paulo";
    u.dept = u.dept || "Engineering";
    u.phone = u.phone || "+55 11 90000-0000";
    u.authm = u.authm || "SSO · Okta";
    u.perms = u.perms || D.PERMS.map(p => p.slice());
    const txt = (f, v) => editing
      ? `<input class="np-input" style="width:100%" data-field="${f}" value="${esc(v)}" />`
      : esc(v);
    const sel = (f, v, opts) => editing
      ? `<select class="set-select" style="width:100%" data-field="${f}">${opts.map(o =>
          `<option value="${o[0]}" ${o[0] === v ? "selected" : ""}>${o[1]}</option>`).join("")}</select>`
      : (opts.find(o => o[0] === v) || ["", v])[1];
    const chk = (f, v, label) => editing
      ? `<label style="display:flex;align-items:center;gap:7px"><input type="checkbox" class="cbx" data-field="${f}" ${v ? "checked" : ""} /> ${label}</label>`
      : (v ? label : "Não");
    const ROLES = [["Owner", "Owner"], ["Admin", "Admin"], ["Membro", "Membro"], ["Suporte", "Suporte"], ["Somente ver", "Somente ver"]];
    const ORGOPTS = D.CUSTOMERS.map(c => [c[0], c[0]]);
    if (!ORGOPTS.some(o => o[0] === u.org)) ORGOPTS.unshift([u.org, u.org]);
    const WSOPTS = D.WORKSPACES.map(w => [w, w]);
    if (!WSOPTS.some(w => w[0] === u.ws)) WSOPTS.unshift([u.ws, u.ws]);
    const STATUS = [["active", "Ativo"], ["pending", "Convidado"], ["canceled", "Desativado"]];
    const initials = u.name.split(" ").map(w => w[0]).slice(0, 2).join("");
    const panes = {
      perfil: `
        <div class="grid2">
          <div class="card" style="margin-bottom:0"><div class="card-hd"><span class="card-title">Dados</span>${editing ? '<span class="spacer"></span><span class="card-note">em edição</span>' : ""}</div>
            <div class="card-bd"><dl class="kv">
              <dt>Nome</dt><dd>${txt("name", u.name)}</dd>
              <dt>Cargo</dt><dd>${txt("title", u.title)}</dd>
              <dt>Departamento</dt><dd>${txt("dept", u.dept)}</dd>
              <dt>E-mail</dt><dd class="${editing ? "" : "mono"}">${txt("email", u.email)}</dd>
              <dt>Telefone</dt><dd class="${editing ? "" : "mono"}">${txt("phone", u.phone)}</dd>
              <dt>Papel</dt><dd>${sel("role", u.role, ROLES)}</dd>
              <dt>Organização</dt><dd>${editing
                ? sel("org", u.org, ORGOPTS) + '<div class="card-note" style="margin-top:5px">Todo usuário precisa estar vinculado a uma organização.</div>'
                : `<a href="#" data-open-org="${esc(u.org)}" style="color:var(--accent)">${esc(u.org)}</a>`}</dd>
              <dt>Workspace</dt><dd>${sel("ws", u.ws, WSOPTS)}</dd>
              <dt>Status</dt><dd>${editing ? sel("status", u.status, STATUS)
                : `<span class="status-pill"><span class="status-dot ${u.status}"></span>${(STATUS.find(s => s[0] === u.status) || ["", u.status])[1]}</span>`}</dd>
              <dt>Última atividade</dt><dd>${u.last}</dd>
            </dl></div></div>
          <div class="card" style="margin-bottom:0"><div class="card-hd"><span class="card-title">Identidade e preferências</span></div>
            <div class="card-bd"><dl class="kv">
              <dt>ID de usuário</dt><dd class="mono">${uid}</dd>
              <dt>Idioma</dt><dd>${txt("lang", u.lang)}</dd>
              <dt>Fuso horário</dt><dd>${txt("tz", u.tz)}</dd>
              <dt>MFA</dt><dd>${chk("mfa", u.mfa, "Habilitado")}</dd>
              <dt>Método de login</dt><dd>${txt("authm", u.authm)}</dd>
              <dt>Criado em</dt><dd>${u.status === "pending" ? "convite pendente" : "10/01/2025"}</dd>
            </dl></div></div>
        </div>`,
      acesso: `
        <div class="card" style="margin-bottom:14px"><div class="card-hd"><span class="card-title">Organização</span></div>
          <table><thead><tr><th>Organização</th><th>Vínculo</th><th>Papel</th><th>Entrada</th><th>Status</th></tr></thead>
          <tbody><tr><td>${u.org}</td><td>Employee</td><td>${u.role}</td><td>10/01/2025</td>
            <td><span class="status-pill"><span class="status-dot ${u.status}"></span>${u.status === "active" ? "Ativo" : u.status === "pending" ? "Convidado" : "Desativado"}</span></td></tr></tbody></table></div>
        <div class="card" style="margin-bottom:14px"><div class="card-hd"><span class="card-title">Workspaces</span></div>
          <table><thead><tr><th>Workspace</th><th>Papel</th><th>Status</th><th>Último acesso</th></tr></thead>
          <tbody>
            <tr><td>${u.ws}</td><td>${u.role}</td><td>active</td><td>${u.last}</td></tr>
            <tr><td>Finance</td><td>Viewer</td><td>active</td><td>há 6 d</td></tr>
          </tbody></table></div>
        <div class="card"><div class="card-hd"><span class="card-title">Projetos</span></div>
          <table><thead><tr><th>Projeto</th><th>Papel</th><th>Permissões</th><th>Último acesso</th></tr></thead>
          <tbody>
            <tr><td>Contract AI</td><td>Reviewer</td><td class="mono" style="font-size:11.5px">document.read, rag.query, approval.approve</td><td>há 2 h</td></tr>
          </tbody></table></div>`,
      permissoes: `
        <div class="card"><div class="card-hd"><span class="card-title">Matriz de permissões</span><span class="spacer"></span><span class="card-note">${editing ? "marque para conceder" : "escopo: " + u.ws}</span></div>
          <div class="card-bd"><table class="perm">
            <thead><tr><th>Recurso</th><th>Ver</th><th>Criar</th><th>Excluir</th><th>Gerir</th></tr></thead>
            <tbody>${u.perms.map((p, ri) => `<tr><td>${p[0]}</td>${p.slice(1).map((v, ci) => editing
              ? `<td><input type="checkbox" class="cbx" data-perm="${ri}-${ci}" ${v ? "checked" : ""} aria-label="${p[0]}" /></td>`
              : `<td class="${v ? "yes" : "no"}">${v ? "✓" : "×"}</td>`).join("")}</tr>`).join("")}</tbody>
          </table></div></div>`,
      atividade: `
        <div class="card"><div class="card-hd"><span class="card-title">Atividade recente</span></div>
          ${D.ACTIVITY.map(a => `<div class="act-row" style="cursor:default">
            <span class="u-avatar" style="width:26px;height:26px;border-radius:50%;display:grid;place-items:center;font-size:10.5px;font-weight:600;color:#fff;background:${a.color}">${a.who.split(" ").map(w => w[0]).slice(0, 2).join("")}</span>
            <span class="act-txt"><span class="act-title">${a.who}</span><div class="act-sub">${a.what}</div></span><span class="alert-time">${a.when}</span></div>`).join("")}</div>`,
      sessoes: `
        <div class="card"><div class="card-hd"><span class="card-title">Sessões ativas</span><span class="spacer"></span><button class="btn sm danger" data-toast="Todas as sessões foram revogadas">Revogar todas</button></div>
          <table><thead><tr><th>Dispositivo</th><th>IP</th><th>Local</th><th>Última atividade</th><th></th></tr></thead>
          <tbody>
            <tr><td>macOS · Chrome</td><td class="mono">191.34.20.11</td><td>São Paulo, BR</td><td>há 2 min</td><td><button class="btn sm danger" data-toast="Sessão revogada">Revogar</button></td></tr>
            <tr><td>iOS · App</td><td class="mono">191.34.20.62</td><td>São Paulo, BR</td><td>há 5 h</td><td><button class="btn sm danger" data-toast="Sessão revogada">Revogar</button></td></tr>
          </tbody></table></div>`,
      api: `
        <div class="card"><div class="card-hd"><span class="card-title">Chaves de API</span><span class="spacer"></span><button class="btn sm primary" data-toast="Nova chave de API criada">+ Criar chave</button></div>
          <table><thead><tr><th>Nome</th><th>Escopo</th><th>Criada</th><th>Último uso</th><th></th></tr></thead>
          <tbody>
            <tr><td class="mono">sk_live_••••8f2c</td><td>read</td><td>12/06/2026</td><td>há 2 h</td><td><button class="btn sm danger" data-toast="Chave revogada">Revogar</button></td></tr>
            <tr><td class="mono">sk_live_••••1a90</td><td>read+write</td><td>03/03/2026</td><td>há 34 d</td><td><button class="btn sm danger" data-toast="Chave revogada">Revogar</button></td></tr>
          </tbody></table></div>`,
      seguranca: `
        <div class="grid2">
          <div class="card" style="margin-bottom:0"><div class="card-hd"><span class="card-title">Autenticação</span></div>
            <div class="card-bd"><dl class="kv">
              <dt>MFA</dt><dd>${u.mfa ? "Habilitado (TOTP)" : "Não configurado"}</dd>
              <dt>SSO</dt><dd>Okta · habilitado</dd>
              <dt>Última troca de senha</dt><dd>há 62 dias</dd>
            </dl></div></div>
          <div class="card" style="margin-bottom:0"><div class="card-hd"><span class="card-title">Ações</span></div>
            <div class="card-bd" style="display:flex;flex-direction:column;gap:8px">
              <button class="btn" data-toast="MFA resetado — usuário notificado">Resetar MFA</button>
              <button class="btn" data-toast="E-mail de redefinição de senha enviado">Forçar redefinição de senha</button>
              <button class="btn danger" data-toast="Usuário desativado">Desativar usuário</button>
            </div></div>
        </div>`,
      uso: `
        <div class="kpi-row" style="grid-template-columns:repeat(3,1fr)">
          <div class="kpi" style="cursor:default"><div class="kpi-label">Requisições</div><div class="kpi-value">18.402</div></div>
          <div class="kpi" style="cursor:default"><div class="kpi-label">Tokens</div><div class="kpi-value">4,2M</div></div>
          <div class="kpi" style="cursor:default"><div class="kpi-label">Custo de IA</div><div class="kpi-value">US$ 312,80</div></div>
        </div>
        ${chartCard("Uso ao longo do tempo", "últimos 30 dias", area(D.series(u.email.length, 30, 100, 0.2, 0.4), "var(--accent)"))}`,
      auditoria: `
        <div class="card"><div class="card-hd"><span class="card-title">Log de auditoria</span><span class="spacer"></span><span class="card-note">últimos 30 dias</span></div>
          <table><thead><tr><th>Horário</th><th>Ação</th><th>Escopo</th><th>IP</th></tr></thead>
          <tbody>
            <tr><td class="mono" style="font-size:12px">04/09 17:03</td><td>login via SSO</td><td>${u.org}</td><td class="mono" style="font-size:12px;color:var(--muted)">191.34.20.11</td></tr>
            <tr><td class="mono" style="font-size:12px">03/09 09:12</td><td>executou Research Agent</td><td>${u.ws}</td><td class="mono" style="font-size:12px;color:var(--muted)">191.34.20.11</td></tr>
            <tr><td class="mono" style="font-size:12px">01/09 14:40</td><td>criou chave de API</td><td>${u.org}</td><td class="mono" style="font-size:12px;color:var(--muted)">191.34.20.62</td></tr>
          </tbody></table></div>`,
    };
    return {
      header: `
        <div style="flex:1;min-width:0">
        <div style="display:flex;align-items:center;gap:14px;margin-bottom:18px">
          <span class="u-avatar" style="width:52px;height:52px;border-radius:50%;display:grid;place-items:center;font-size:18px;font-weight:600;color:#fff;background:${u.color};flex-shrink:0">${initials}</span>
          <div style="min-width:0;overflow:hidden"><div class="view-hd-title" style="margin-bottom:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(u.name)}</div>
            <div class="view-hd-sub" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(u.title)} · ${u.role} · ${u.org} · ${u.mfa ? "MFA habilitado" : "MFA desabilitado"}</div></div>
          <div style="flex:1"></div>
          ${editing
            ? `<button class="btn primary" style="flex-shrink:0;white-space:nowrap" data-save-profile="1">Salvar</button>
               <button class="btn" style="flex-shrink:0;white-space:nowrap" data-cancel-profile="1">Cancelar</button>`
            : `<button class="btn primary" style="flex-shrink:0;white-space:nowrap" data-edit-profile="${esc(u.email)}">Editar perfil</button>
               <button class="btn" style="flex-shrink:0;white-space:nowrap" data-view="usuarios">‹ Voltar</button>`}
        </div>
        <div class="view-tabs" id="profileTabs" style="flex-wrap:wrap">${tabs.map(t =>
          `<button class="view-tab ${t[0] === tab ? "is-active" : ""}" data-ptab="${t[0]}">${t[1]}</button>`).join("")}</div>
        </div>`,
      body: panes[tab] || panes.perfil,
    };
  }

  function logDrawer(l) {
    return {
      title: l.lvl === "INFO" ? "Evento de sistema" : "Falha em " + l.svc,
      sub: `${l.msg}`,
      body: `
        <div class="dr-sec">Contexto</div>
        <dl class="kv">
          <dt>Nível</dt><dd><span class="log-lvl ${l.lvl}">${l.lvl}</span></dd>
          <dt>Serviço</dt><dd class="mono">${l.svc}</dd>
          <dt>Horário</dt><dd class="mono">${l.ts} · 04/09/2026</dd>
          <dt>Organização</dt><dd>${l.org}</dd>
          <dt>Usuário</dt><dd class="mono">${esc(l.user)}</dd>
          <dt>Modelo</dt><dd class="mono">${l.model}</dd>
          <dt>Trace ID</dt><dd class="mono">${l.trace}</dd>
          <dt>Request ID</dt><dd class="mono">rq_${l.trace.slice(3, 11)}</dd>
          <dt>Tentativas</dt><dd class="mono">${l.retries}</dd>
        </dl>
        <div class="dr-sec">Trace</div>
        <div class="trace">${D.TRACE.map(t => `
          <div class="trace-row"><span class="trace-name">${t.name}</span>
            <span class="trace-track"><span class="trace-fill ${t.slow ? "slow" : ""}" style="width:${(t.ms / 2200 * 100).toFixed(1)}%"></span></span>
            <span class="trace-ms">${t.ms < 1000 ? t.ms + "ms" : (t.ms / 1000).toFixed(1).replace(".", ",") + "s"}</span></div>`).join("")}</div>
        <div class="dr-sec">Stack trace</div>
        <pre style="font-family:var(--font-mono);font-size:11.5px;color:var(--ink-2);background:var(--rail-2);border:1px solid var(--border);border-radius:8px;padding:12px;overflow-x:auto;margin:0">at ProviderClient.stream (ai-gateway/providers/openai.ts:212)
at RetryPolicy.execute (ai-gateway/retry.ts:64)
at RagQueryHandler.answer (rag/query.ts:118)
at HttpRouter.dispatch (server/router.ts:91)</pre>`,
      footer: `<button class="btn primary" data-toast="Job reenfileirado para retry">Reprocessar</button>
               <button class="btn" data-toast="Trace aberto no explorador">Abrir trace</button>
               <button class="btn" data-toast="Contexto copiado para o suporte">Copiar contexto</button>`,
    };
  }

  function orgDrawer(name) {
    return {
      title: name,
      sub: "Cockpit da organização",
      body: `
        <div class="dr-sec">Resumo</div>
        <dl class="kv">
          <dt>Plano</dt><dd>Enterprise · ciclo anual</dd>
          <dt>MRR</dt><dd class="mono">R$ 8.500</dd>
          <dt>Usuários</dt><dd class="mono">98 (12 admins)</dd>
          <dt>Workspaces</dt><dd>4</dd>
          <dt>Health score</dt><dd style="color:var(--warn)">64 — atenção</dd>
          <dt>Uso de tokens</dt><dd class="mono">18,2M / 20M (91%)</dd>
        </dl>
        <div class="dr-sec">Sinais abertos</div>
        <div class="alert-row" style="padding-left:0"><span class="sev-dot critical"></span>
          <span class="alert-main"><span class="alert-title">Fatura INV-2091 recusada</span><div class="alert-meta"><span class="mono">R$ 8.500 · cartão recusado</span></div></span></div>
        <div class="alert-row" style="padding-left:0"><span class="sev-dot warning"></span>
          <span class="alert-main"><span class="alert-title">91% do limite de tokens</span><div class="alert-meta"><span class="mono">reset em 12 dias</span></div></span></div>`,
      footer: `<button class="btn primary" data-toast="Cockpit do cliente aberto">Abrir cockpit</button>
               <button class="btn" data-toast="Cobrança reprocessada com sucesso">Reprocessar cobrança</button>`,
    };
  }

  /* ── Registro de organização ────────────────── */
  const slug = s => s.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  function orgRec(name) {
    D.ORGREC = D.ORGREC || {};
    if (D.ORGREC[name]) return D.ORGREC[name];
    const r = D.CUSTOMERS.find(c => c[0] === name) || [name, "Pro", 1, "R$ 0", 0, 100, "trialing"];
    const owner = D.USERS.find(u => u.org === name && u.role === "Owner") || D.USERS.find(u => u.org === name);
    const rec = {
      name: r[0], plan: r[1], users: r[2], mrr: r[3], usage: r[4], health: r[5], status: r[6],
      slug: slug(r[0]), domain: slug(r[0]) + ".com", segment: r[1] === "Enterprise" ? "Enterprise" : "Mid-market",
      region: "São Paulo (br-se1)", env: "Produção", cycle: r[1] === "Enterprise" ? "Anual" : "Mensal",
      seats: Math.max(Number(r[2]) || 1, 10), tokenLimit: "20M", payment: "Cartão de crédito", po: "—",
      provider: "Anthropic", providerModel: "", providerKey: "", providerBase: "", providerFallback: "Nenhum",
      owner: owner ? owner.name : "", ownerEmail: owner ? owner.email : "", phone: "+55 11 90000-0000",
      ws: "AI Platform", created: "10/01/2025",
      sso: r[1] === "Enterprise" ? 1 : 0, mfaReq: 1, ipAllow: 0, retention: "90 dias", notes: "",
      workspaces: D.WORKSPACES.map((w, i) => ({ name: w, owner: owner ? owner.name : "—", users: 8 + i * 5, projects: 2 + i, env: i ? "Produção" : "Produção" })),
      audit: [
        ["04/09 17:03", owner ? owner.name : "Sistema", "alterou plano da organização", "191.34.20.11"],
        ["02/09 10:22", "Sistema", "emitiu fatura do ciclo", "—"],
        ["28/08 09:14", owner ? owner.name : "Sistema", "habilitou SSO", "191.34.20.62"],
      ],
    };
    D.ORGREC[name] = rec; return rec;
  }
  function newOrgRec() {
    return { name: "", plan: "Pro", users: 1, mrr: "R$ 0", usage: 0, health: 100, status: "trialing",
      slug: "", domain: "", segment: "Mid-market", region: "São Paulo (br-se1)", env: "Produção",
      cycle: "Mensal", seats: 10, tokenLimit: "5M", payment: "Cartão de crédito", po: "—",
      provider: "Anthropic", providerModel: "", providerKey: "", providerBase: "", providerFallback: "Nenhum",
      owner: "", ownerEmail: "", phone: "", ws: "AI Platform", created: "hoje",
      sso: 0, mfaReq: 1, ipAllow: 0, retention: "90 dias", notes: "", workspaces: [], audit: [] };
  }

  /* ── Organização: página completa (ver / editar / criar) ── */
  function organizacao(o, tab, editing, isNew) {
    editing = editing || isNew;
    const tabs = isNew
      ? [["dados", "Dados"], ["plano", "Plano e cobrança"], ["provedor", "Provedor de IA"], ["seguranca", "Segurança"]]
      : [["dados", "Dados"], ["plano", "Plano e cobrança"], ["provedor", "Provedor de IA"], ["workspaces", "Workspaces"], ["usuarios", "Usuários"],
         ["uso", "Uso"], ["seguranca", "Segurança"], ["auditoria", "Auditoria"]];
    tab = tabs.some(t => t[0] === tab) ? tab : "dados";
    const txt = (f, v, ph) => editing
      ? `<input class="np-input" style="width:100%" data-ofield="${f}" value="${esc(v || "")}" placeholder="${ph || ""}" />`
      : (v ? esc(v) : "—");
    const sel = (f, v, opts) => editing
      ? `<select class="set-select" style="width:100%" data-ofield="${f}">${opts.map(x =>
          `<option value="${x}" ${x === v ? "selected" : ""}>${x}</option>`).join("")}</select>`
      : esc(v);
    const chk = (f, v, label) => editing
      ? `<label style="display:flex;align-items:center;gap:7px"><input type="checkbox" class="cbx" data-ofield="${f}" ${v ? "checked" : ""} /> ${label}</label>`
      : (v ? label : "Não");
    const PLANS = ["Free", "Starter", "Pro", "Business", "Enterprise"];
    const STATUS = [["active", "Ativa"], ["trialing", "Trial"], ["past_due", "Em atraso"], ["canceled", "Cancelada"]];
    const statusLabel = s => (STATUS.find(x => x[0] === s) || ["", s])[1];
    const orgUsers = D.USERS.filter(u => u.org === o.name);
    if (!o.workspaces) o.workspaces = [];
    if (!o.audit) o.audit = [];
    const orgInvoices = D.INVOICES.filter(i => i.org === o.name);
    const initials = (o.name || "?").split(" ").map(w => w[0]).slice(0, 2).join("").toUpperCase();
    const color = (D.ORGS.find(x => x.name === o.name) || {}).color || "var(--accent)";
    const PROVIDERS = {
      "Moonshot / Kimi": { models: ["kimi-k2", "moonshot-v1-128k", "moonshot-v1-32k"], base: "https://api.moonshot.cn/v1", key: "sk-moon-•••" },
      "OpenAI": { models: ["gpt-4.1", "gpt-4.1-mini", "o4-mini"], base: "https://api.openai.com/v1", key: "sk-•••" },
      "Anthropic": { models: ["claude-sonnet-4", "claude-opus-4", "claude-haiku-3.5"], base: "https://api.anthropic.com/v1", key: "sk-ant-•••" },
      "Gemini": { models: ["gemini-2.5-pro", "gemini-2.5-flash"], base: "https://generativelanguage.googleapis.com/v1", key: "AIza•••" },
      "OpenRouter": { models: ["auto", "anthropic/claude-sonnet-4", "openai/gpt-4.1", "moonshotai/kimi-k2"], base: "https://openrouter.ai/api/v1", key: "sk-or-•••" },
    };
    const provName = PROVIDERS[o.provider] ? o.provider : "Anthropic";
    const prov = PROVIDERS[provName];
    const provModel = prov.models.includes(o.providerModel) ? o.providerModel : prov.models[0];
    const panes = {
      dados: `
        <div class="grid2">
          <div class="card" style="margin-bottom:0"><div class="card-hd"><span class="card-title">Identificação</span>${editing ? '<span class="spacer"></span><span class="card-note">' + (isNew ? "nova organização" : "em edição") + "</span>" : ""}</div>
            <div class="card-bd"><dl class="kv">
              <dt>Nome</dt><dd>${txt("name", o.name, "Ex: Fintech Nova Ltda")}</dd>
              <dt>Slug</dt><dd class="${editing ? "" : "mono"}">${txt("slug", o.slug, "fintech-nova")}</dd>
              <dt>Domínio principal</dt><dd class="${editing ? "" : "mono"}">${txt("domain", o.domain, "empresa.com")}</dd>
              <dt>Segmento</dt><dd>${sel("segment", o.segment, ["Startup", "Mid-market", "Enterprise", "Setor público"])}</dd>
              <dt>Região de dados</dt><dd>${sel("region", o.region, ["São Paulo (br-se1)", "Virgínia (us-east1)", "Frankfurt (eu-central1)"])}</dd>
              <dt>Ambiente padrão</dt><dd>${sel("env", o.env, ["Produção", "Staging", "Sandbox"])}</dd>
              <dt>Workspace inicial</dt><dd>${sel("ws", o.ws, D.WORKSPACES)}</dd>
              <dt>Status</dt><dd>${editing
                ? `<select class="set-select" style="width:100%" data-ofield="status">${STATUS.map(s =>
                    `<option value="${s[0]}" ${s[0] === o.status ? "selected" : ""}>${s[1]}</option>`).join("")}</select>`
                : `<span class="status-pill"><span class="status-dot ${o.status}"></span>${statusLabel(o.status)}</span>`}</dd>
            </dl></div></div>
          <div class="card" style="margin-bottom:0"><div class="card-hd"><span class="card-title">Contato responsável</span></div>
            <div class="card-bd"><dl class="kv">
              <dt>Owner</dt><dd>${txt("owner", o.owner, "Nome do responsável")}</dd>
              <dt>E-mail do owner</dt><dd class="${editing ? "" : "mono"}">${txt("ownerEmail", o.ownerEmail, "owner@empresa.com")}</dd>
              <dt>Telefone</dt><dd class="${editing ? "" : "mono"}">${txt("phone", o.phone, "+55 11 90000-0000")}</dd>
              <dt>Criada em</dt><dd>${esc(o.created)}</dd>
              <dt>Health score</dt><dd style="color:${o.health < 60 ? "var(--err)" : o.health < 80 ? "var(--warn)" : "var(--ok)"}">${o.health}</dd>
              <dt>Observações</dt><dd>${editing
                ? `<input class="np-input" style="width:100%" data-ofield="notes" value="${esc(o.notes)}" placeholder="Contexto do contrato, riscos, etc." />`
                : (o.notes ? esc(o.notes) : "—")}</dd>
            </dl></div></div>
        </div>`,
      plano: `
        <div class="grid2">
          <div class="card" style="margin-bottom:0"><div class="card-hd"><span class="card-title">Plano</span>${editing ? '<span class="spacer"></span><span class="card-note">em edição</span>' : ""}</div>
            <div class="card-bd"><dl class="kv">
              <dt>Plano</dt><dd>${editing ? sel("plan", o.plan, PLANS) : `<span class="badge plan-${o.plan.toLowerCase() === "enterprise" ? "enterprise" : o.plan.toLowerCase() === "pro" ? "pro" : o.plan.toLowerCase() === "business" ? "team" : "free"}">${o.plan}</span>`}</dd>
              <dt>Ciclo</dt><dd>${sel("cycle", o.cycle, ["Mensal", "Anual"])}</dd>
              <dt>Assentos contratados</dt><dd class="${editing ? "" : "mono"}">${txt("seats", o.seats)}</dd>
              <dt>MRR</dt><dd class="${editing ? "" : "mono"}">${txt("mrr", o.mrr, "R$ 0")}</dd>
              <dt>Limite de tokens</dt><dd class="${editing ? "" : "mono"}">${txt("tokenLimit", o.tokenLimit, "20M")}</dd>
            </dl></div></div>
          <div class="card" style="margin-bottom:0"><div class="card-hd"><span class="card-title">Cobrança</span></div>
            <div class="card-bd"><dl class="kv">
              <dt>Forma de pagamento</dt><dd>${sel("payment", o.payment, ["Cartão de crédito", "Boleto", "Fatura (NET 30)", "PIX"])}</dd>
              <dt>Ordem de compra</dt><dd class="${editing ? "" : "mono"}">${txt("po", o.po)}</dd>
              <dt>Uso do limite</dt><dd class="mono">${o.usage}%</dd>
              <dt>Usuários ativos</dt><dd class="mono">${o.users}</dd>
            </dl></div></div>
        </div>
        ${isNew ? "" : `<div class="card" style="margin-top:14px"><div class="card-hd"><span class="card-title">Faturas</span><span class="spacer"></span><span class="card-note">${orgInvoices.length} no período</span></div>
          <table><thead><tr><th>Fatura</th><th>Plano</th><th>Vencimento</th><th class="num">Valor</th><th>Status</th></tr></thead>
          <tbody>${orgInvoices.length ? orgInvoices.map(i => `<tr style="cursor:pointer" data-open-invoice="${esc(i.id)}"><td class="mono">${i.id}</td><td>${i.plan}</td><td>${i.due}</td><td class="num">${i.amount}</td>
            <td><span class="status-pill"><span class="status-dot ${i.status === "paid" ? "active" : i.status === "overdue" ? "canceled" : "pending"}"></span>${i.status === "paid" ? "Paga" : i.status === "overdue" ? "Em atraso" : "Pendente"}</span></td></tr>`).join("")
            : `<tr><td colspan="5" style="color:var(--muted)">Nenhuma fatura emitida.</td></tr>`}</tbody></table></div>`}`,
      provedor: `
        <div class="grid2">
          <div class="card" style="margin-bottom:0"><div class="card-hd"><span class="card-title">Provedor de modelo</span>${editing ? '<span class="spacer"></span><span class="card-note">em edição</span>' : ""}</div>
            <div class="card-bd">
              ${editing ? `<div class="prov-grid">${Object.keys(PROVIDERS).map(p => `
                <label class="prov-opt ${p === provName ? "is-on" : ""}">
                  <input type="radio" name="providerRadio" value="${p}" data-ofield="provider" ${p === provName ? "checked" : ""} />
                  <span class="prov-name">${p}</span>
                  <span class="prov-base mono">${PROVIDERS[p].base.replace("https://", "")}</span>
                </label>`).join("")}</div>` : ""}
              <dl class="kv" ${editing ? 'style="margin-top:16px"' : ""}>
                ${editing ? "" : `<dt>Provedor</dt><dd><span class="badge plan-pro">${provName}</span></dd>`}
                <dt>Modelo padrão</dt><dd>${sel("providerModel", provModel, prov.models)}</dd>
                <dt>Modelo de fallback</dt><dd>${sel("providerFallback", prov.models.concat(["Nenhum"]).includes(o.providerFallback) ? o.providerFallback : "Nenhum", ["Nenhum"].concat(prov.models))}</dd>
                <dt>Endpoint</dt><dd class="${editing ? "" : "mono"}">${txt("providerBase", o.providerBase || prov.base, prov.base)}</dd>
                <dt>Chave de API</dt><dd class="${editing ? "" : "mono"}">${editing
                  ? `<input class="np-input" style="width:100%" type="password" data-ofield="providerKey" value="${esc(o.providerKey)}" placeholder="${prov.key}" />`
                  : (o.providerKey ? "••••••••" + esc(o.providerKey).slice(-4) : "Não configurada")}</dd>
              </dl>
            </div></div>
          <div class="card" style="margin-bottom:0"><div class="card-hd"><span class="card-title">Situação</span></div>
            <div class="card-bd"><dl class="kv">
              <dt>Vinculado a</dt><dd>${esc(o.name || "—")}</dd>
              <dt>Escopo</dt><dd>Todos os workspaces da organização</dd>
              <dt>Limite de tokens</dt><dd class="mono">${esc(o.tokenLimit)}</dd>
              <dt>Conexão</dt><dd>${o.providerKey
                ? '<span class="status-pill"><span class="status-dot active"></span>Chave configurada</span>'
                : '<span class="status-pill"><span class="status-dot pending"></span>Aguardando chave</span>'}</dd>
            </dl>
            ${editing ? "" : `<div style="display:flex;gap:8px;margin-top:14px">
              <button class="btn sm" data-test-prov="1">Testar conexão</button>
              <button class="btn sm" data-edit-org="${esc(o.name)}">Trocar provedor</button>
              <button class="btn sm danger" data-clear-key="1">Remover chave</button></div>
              ${o.providerTest ? `<div class="card-note" style="margin-top:9px">${esc(o.providerTest)}</div>` : ""}`}
            </div></div>
        </div>`,
      workspaces: `
        <div class="card"><div class="card-hd"><span class="card-title">Workspaces</span><span class="spacer"></span><span class="card-note">${o.workspaces.length} ativos</span>
          <button class="btn sm" data-ws-add="1" style="margin-left:10px">+ Novo workspace</button></div>
          <table><thead><tr><th>Workspace</th><th>Owner</th><th class="num">Usuários</th><th class="num">Projetos</th><th>Ambiente</th><th></th></tr></thead>
          <tbody id="wsBody">
            <tr id="wsNewRow" hidden>
              <td><input class="np-input" id="wsNewName" placeholder="Nome do workspace" style="width:100%" /></td>
              <td>${esc(o.owner || "—")}</td><td class="num">0</td><td class="num">0</td>
              <td><select class="set-select" id="wsNewEnv"><option>Produção</option><option>Staging</option><option>Sandbox</option></select></td>
              <td class="num" style="white-space:nowrap"><button class="btn sm primary" data-ws-save="1">Adicionar</button>
                <button class="btn sm" data-ws-cancel="1">Cancelar</button></td></tr>
            ${o.workspaces.length ? o.workspaces.map((w, i) => `<tr><td style="font-weight:500;color:var(--ink)">${esc(w.name)}</td><td>${esc(w.owner || o.owner || "—")}</td>
              <td class="num">${w.users}</td><td class="num">${w.projects}</td>
              <td><select class="set-select" data-ws-env="${i}">${["Produção", "Staging", "Sandbox"].map(e => `<option ${e === w.env ? "selected" : ""}>${e}</option>`).join("")}</select></td>
              <td class="num" style="white-space:nowrap"><button class="btn sm" data-ws-rename="${i}">Renomear</button>
                <button class="btn sm danger" data-ws-del="${i}">Excluir</button></td></tr>`).join("")
              : `<tr id="wsEmpty"><td colspan="6" style="color:var(--muted)">Nenhum workspace. Crie o primeiro.</td></tr>`}
          </tbody></table></div>`,
      usuarios: `
        <div class="card"><div class="card-hd"><span class="card-title">Usuários</span><span class="spacer"></span><span class="card-note">${orgUsers.length} vinculados</span>
          <button class="btn sm" data-view="usuarios" style="margin-left:10px">Gerenciar usuários</button></div>
          <table><thead><tr><th>Nome</th><th>Papel</th><th>Workspace</th><th>Status</th><th>Última atividade</th><th></th></tr></thead>
          <tbody>${orgUsers.length ? orgUsers.map(u => `<tr class="user-row" data-open-profile="${esc(u.email)}"><td style="font-weight:500;color:var(--ink)">${esc(u.name)}</td><td>${u.role}</td><td>${u.ws}</td>
            <td><span class="status-pill"><span class="status-dot ${u.status}"></span>${u.status === "active" ? "Ativo" : u.status === "pending" ? "Convidado" : "Desativado"}</span></td><td>${u.last}</td>
            <td class="num" style="white-space:nowrap"><button class="btn sm" data-user-toggle="${esc(u.email)}">${u.status === "canceled" ? "Reativar" : "Desativar"}</button></td></tr>`).join("")
            : `<tr><td colspan="6" style="color:var(--muted)">Nenhum usuário vinculado.</td></tr>`}</tbody></table></div>`,
      uso: `
        <div class="kpi-row">${[
          ["Gasto total", "$0,02", "↓ 99.9%", "down", 0.62],
          ["Pedidos", "7", "↓ 99.3%", "down", 0.58],
          ["Volume de tokens", "236K", "↓ 99.8%", "down", 0.66],
          ["Taxa de acerto no cache", "89.5%", "↑ 5.4%", "up", 0.44],
          ["Blended $/1M", "$0,08", "↓ 56.9%", "down", 0.51],
        ].map((c, i) => `
          <div class="kpi" style="cursor:default">
            <div class="kpi-label">${c[0]}</div><div class="kpi-value">${c[1]}</div>
            <div class="kpi-delta ${c[3]}">${c[2]} <span class="prior">vs período anterior</span></div>
            ${spark(D.series((o.name || "x").length + i * 7, 14, 100, c[3] === "up" ? 0.5 : -0.55, c[4]), c[3] === "up" ? "var(--ok)" : "var(--err)")}
          </div>`).join("")}</div>
        <div class="kpi-row" style="grid-template-columns:repeat(3,1fr)">
          <div class="kpi" style="cursor:default"><div class="kpi-label">Uso do limite</div><div class="kpi-value">${o.usage}%</div></div>
          <div class="kpi" style="cursor:default"><div class="kpi-label">MRR</div><div class="kpi-value">${esc(o.mrr)}</div></div>
          <div class="kpi" style="cursor:default"><div class="kpi-label">Assentos</div><div class="kpi-value">${o.users}/${o.seats}</div></div>
        </div>
        ${chartCard("Consumo de tokens", "últimos 30 dias", area(D.series((o.name || "x").length + 3, 30, 100, 0.24, 0.35), "var(--accent)"))}`,
      seguranca: `
        <div class="grid2">
          <div class="card" style="margin-bottom:0"><div class="card-hd"><span class="card-title">Políticas</span>${editing ? '<span class="spacer"></span><span class="card-note">em edição</span>' : ""}</div>
            <div class="card-bd"><dl class="kv">
              <dt>SSO (SAML)</dt><dd>${chk("sso", o.sso, "Habilitado")}</dd>
              <dt>MFA obrigatório</dt><dd>${chk("mfaReq", o.mfaReq, "Exigido")}</dd>
              <dt>Allowlist de IP</dt><dd>${chk("ipAllow", o.ipAllow, "Ativa")}</dd>
              <dt>Retenção de dados</dt><dd>${sel("retention", o.retention, ["30 dias", "90 dias", "180 dias", "1 ano", "Indefinida"])}</dd>
            </dl></div></div>
          <div class="card" style="margin-bottom:0"><div class="card-hd"><span class="card-title">Ações</span></div>
            <div class="card-bd" style="display:flex;flex-direction:column;gap:8px">
              <button class="btn" data-org-act="rebill">Reprocessar cobrança</button>
              <button class="btn" data-org-act="revoke">Revogar todas as sessões</button>
              <button class="btn" data-org-act="invite">Reenviar convite ao owner</button>
              <button class="btn ${o.status === "canceled" ? "" : "danger"}" data-org-act="${o.status === "canceled" ? "reactivate" : "suspend"}">${o.status === "canceled" ? "Reativar organização" : "Suspender organização"}</button>
            </div></div>
        </div>`,
      auditoria: `
        <div class="card"><div class="card-hd"><span class="card-title">Log de auditoria</span><span class="spacer"></span><span class="card-note">${o.audit.length} eventos</span>
          <button class="btn sm" data-org-act="export" style="margin-left:10px">Exportar CSV</button></div>
          <table><thead><tr><th>Horário</th><th>Ator</th><th>Ação</th><th>IP</th></tr></thead>
          <tbody>${o.audit.length ? o.audit.map(r => `<tr><td class="mono" style="font-size:12px">${esc(r[0])}</td><td>${esc(r[1])}</td><td>${esc(r[2])}</td><td class="mono" style="font-size:12px;color:var(--muted)">${esc(r[3])}</td></tr>`).join("")
            : `<tr><td colspan="4" style="color:var(--muted)">Nenhum evento registrado.</td></tr>`}</tbody></table></div>`,
    };
    return {
      header: `
        <div style="flex:1;min-width:0">
        <div style="display:flex;align-items:center;gap:14px;margin-bottom:18px">
          <span class="u-avatar" style="width:52px;height:52px;border-radius:12px;display:grid;place-items:center;font-size:17px;font-weight:600;color:#fff;background:${color};flex-shrink:0">${isNew && !o.name ? "+" : initials}</span>
          <div style="min-width:0;overflow:hidden"><div class="view-hd-title" style="margin-bottom:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${isNew ? (o.name ? esc(o.name) : "Nova organização") : esc(o.name)}</div>
            <div class="view-hd-sub" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${isNew
              ? "Preencha os dados, o plano e as políticas de segurança"
              : `${o.plan} · ${o.cycle} · ${o.users} usuários · ${statusLabel(o.status)}`}</div></div>
          <div style="flex:1"></div>
          ${isNew
            ? `<button class="btn primary" style="flex-shrink:0;white-space:nowrap" data-create-org="1">Criar organização</button>
               <button class="btn" style="flex-shrink:0;white-space:nowrap" data-cancel-org="1">Cancelar</button>`
            : editing
              ? `<button class="btn primary" style="flex-shrink:0;white-space:nowrap" data-save-org="1">Salvar</button>
                 <button class="btn" style="flex-shrink:0;white-space:nowrap" data-cancel-org="1">Cancelar</button>`
              : `<button class="btn primary" style="flex-shrink:0;white-space:nowrap" data-edit-org="${esc(o.name)}">Editar organização</button>
                 <button class="btn" style="flex-shrink:0;white-space:nowrap" data-view="clientes">‹ Voltar</button>`}
        </div>
        <div class="view-tabs" style="flex-wrap:wrap">${tabs.map(t =>
          `<button class="view-tab ${t[0] === tab ? "is-active" : ""}" data-otab="${t[0]}">${t[1]}</button>`).join("")}</div>
        </div>`,
      body: panes[tab] || panes.dados,
    };
  }

  /* ── Configurações ──────────────────────────── */
  function configuracoes(scope) {
    const tabs = [["ia", "Modelos e IA"], ["video", "Vídeo e Stock"], ["sistema", "Navegador e Sistema"], ["voz", "Voz e Legendas"]];
    const tag = (ok, label) => ok
      ? `<span class="status-pill" style="color:var(--ok)"><span class="status-dot active"></span>${label}</span>`
      : `<span class="status-pill" style="color:var(--warn)"><span class="status-dot pending"></span>${label}</span>`;
    const field = (label, val, opts) => `<div><label class="dr-sec" style="margin:0 0 7px">${label}</label>${opts && opts.select
      ? `<select class="set-select" id="${opts.id}" style="width:100%">${opts.opts.map(([v, t]) => `<option value="${v}" ${v === val ? "selected" : ""}>${t}</option>`).join("")}</select>`
      : `<input class="np-input" id="${opts && opts.id || ""}" style="width:100%" type="${opts && opts.type || "text"}" value="${esc(val)}" /></div>`}</div>`;
    const prov = D.__providers;
    const panes = {
      ia: `
        <div class="dr-sec" style="margin-top:0">Provedor de IA</div>
        <div class="card-note" style="margin:6px 0 14px;font-family:inherit">Usado para roteiro, metadados, palavras-chave, estratégia e recomendações — inclusive o roteiro do Short, escrito pelo Turbo.</div>
        ${field("Provedor de IA", prov && prov.llm_provider || "moonshot", { select: true, id: "cfgLlmProvider", opts: [["moonshot", "Kimi (Moonshot)"], ["nvidia", "NVIDIA"]] })}
        <div class="dr-sec" style="margin-top:20px">Kimi (Moonshot)</div>
        <div class="grid2">
          <div><label class="dr-sec" style="margin:0 0 7px">Chave Moonshot/Kimi ${prov ? tag(!!prov.moonshot, prov.moonshot ? "Configurada" : "Não configurada") : ""}</label>
            <input class="np-input" id="cfgMoonshotKey" style="width:100%" type="password" placeholder="${prov && prov.moonshot ? "•••••••• (deixe em branco para manter)" : "sk-..."}" autocomplete="off" />
            <div class="card-note" id="cfgMoonshotKeyStatus" style="margin-top:5px;min-height:14px"></div></div>
          <div><label class="dr-sec" style="margin:0 0 7px">Modelo Moonshot/Kimi</label>
            <input class="np-input" id="cfgMoonshotModel" style="width:100%" value="${esc(prov && prov.moonshot_model || "")}" placeholder="kimi-k3 (padrão)" autocomplete="off" />
            <div class="card-note" style="margin-top:6px;font-family:inherit">Ex.: kimi-k3, kimi-k2.6 — a lista exata depende do plano da sua conta.</div></div>
        </div>
        <div class="dr-sec" style="margin-top:20px">NVIDIA</div>
        <div class="card-note" style="margin:6px 0 14px;font-family:inherit">O modelo de visão é usado em "Revisar imagens com IA" e o de imagem em "Gerar thumbnail com IA", ambos no painel de publicação.</div>
        <div class="grid2">
          <div><label class="dr-sec" style="margin:0 0 7px">Chave NVIDIA ${prov ? tag(!!prov.nvidia, prov.nvidia ? "Configurada" : "Não configurada") : ""}</label>
            <input class="np-input" id="cfgNvidiaKey" style="width:100%" type="password" placeholder="${prov && prov.nvidia ? "•••••••• (deixe em branco para manter)" : "nvapi-..."}" autocomplete="off" />
            <div class="card-note" id="cfgNvidiaKeyStatus" style="margin-top:5px;min-height:14px"></div></div>
          <div><label class="dr-sec" style="margin:0 0 7px">Modelo de texto</label>
            <input class="np-input" id="cfgNvidiaTextModel" style="width:100%" value="${esc(prov && prov.nvidia_text_model || "")}" placeholder="google/gemma-4-31b-it" autocomplete="off" /></div>
          <div><label class="dr-sec" style="margin:0 0 7px">Modelo de visão (revisão de imagens)</label>
            <input class="np-input" id="cfgNvidiaVisionModel" style="width:100%" value="${esc(prov && prov.nvidia_vision_model || "")}" placeholder="google/diffusiongemma-26b-a4b-it" autocomplete="off" /></div>
          <div><label class="dr-sec" style="margin:0 0 7px">Modelo de imagem (thumbnail)</label>
            <input class="np-input" id="cfgNvidiaImageModel" style="width:100%" value="${esc(prov && prov.nvidia_image_model || "")}" placeholder="black-forest-labs/flux.1-dev" autocomplete="off" /></div>
        </div>
        <div class="card-note" style="margin-top:10px;font-family:inherit">Salvar grava direto em <code>runtime/moneyprinter/config.toml</code>. Trocar o provedor de IA também reconfigura o roteiro do Short no Turbo. É preciso reiniciar o container para aplicar: <code>docker compose restart moneyprinterturbo</code>.</div>`,
      video: `
        <div class="dr-sec" style="margin-top:0">Filmagens e stock</div>
        <div class="card-note" style="margin:6px 0 14px;font-family:inherit">Fonte padrão usada para buscar as filmagens. Configure a chave de quem for usar antes de selecionar.</div>
        <div class="grid2" style="margin-top:10px">
          ${field("Fonte de vídeo padrão", "pexels", { select: true, id: "cfgVideoSource", opts: [["pexels", "Pexels"], ["pixabay", "Pixabay"], ["coverr", "Coverr"]] })}
          ${field("Duração padrão do clipe (s)", "5", { id: "cfgDuration", type: "number" })}
          ${field("Cenas por vídeo (padrão)", "5", { id: "cfgParagraphs", type: "number" })}
        </div>
        <div class="grid2" style="margin-top:14px">
          <div><label class="dr-sec" style="margin:0 0 7px">Chave Pexels ${prov ? tag(!!prov.pexels, prov.pexels ? "Configurada" : "Não configurada") : ""}</label>
            <input class="np-input" id="cfgPexelsKey" style="width:100%" type="password" placeholder="${prov && prov.pexels ? "•••••••• (deixe em branco para manter)" : "cole a chave aqui"}" autocomplete="off" />
            <div class="card-note" id="cfgPexelsKeyStatus" style="margin-top:5px;min-height:14px"></div></div>
          <div><label class="dr-sec" style="margin:0 0 7px">Chave Pixabay ${prov ? tag(!!prov.pixabay, prov.pixabay ? "Configurada" : "Não configurada") : ""}</label>
            <input class="np-input" id="cfgPixabayKey" style="width:100%" type="password" placeholder="${prov && prov.pixabay ? "•••••••• (deixe em branco para manter)" : "cole a chave aqui"}" autocomplete="off" />
            <div class="card-note" id="cfgPixabayKeyStatus" style="margin-top:5px;min-height:14px"></div></div>
          <div><label class="dr-sec" style="margin:0 0 7px">Chave Coverr ${prov ? tag(!!prov.coverr, prov.coverr ? "Configurada" : "Não configurada") : ""}</label>
            <input class="np-input" id="cfgCoverrKey" style="width:100%" type="password" placeholder="${prov && prov.coverr ? "•••••••• (deixe em branco para manter)" : "cole a chave aqui"}" autocomplete="off" />
            <div class="card-note" id="cfgCoverrKeyStatus" style="margin-top:5px;min-height:14px"></div></div>
        </div>
        <div class="dr-sec" style="margin-top:20px">Publicação (Upload-Post)</div>
        <div class="grid2" style="margin-top:10px">
          <div><label class="dr-sec" style="margin:0 0 7px">Chave Upload-Post ${prov ? tag(!!prov.upload_post, prov.upload_post ? "Configurada" : "Não configurada") : ""}</label>
            <input class="np-input" id="cfgUploadPostKey" style="width:100%" type="password" placeholder="${prov && prov.upload_post ? "•••••••• (deixe em branco para manter)" : "cole a chave aqui"}" autocomplete="off" />
            <div class="card-note" id="cfgUploadPostKeyStatus" style="margin-top:5px;min-height:14px"></div></div>
          <div><label class="dr-sec" style="margin:0 0 7px">Usuário Upload-Post</label>
            <input class="np-input" id="cfgUploadPostUser" style="width:100%" value="${esc(prov && prov.upload_post_username || "")}" placeholder="nome de usuário" autocomplete="off" /></div>
        </div>
        <div class="card-note" style="margin-top:10px;font-family:inherit">A chave Upload-Post é usada para conectar canais TikTok em <a href="#" data-view="clientes" style="color:var(--accent)">Canais</a>.</div>
        <div class="card-note" style="margin-top:10px;font-family:inherit">Salvar grava direto em <code>runtime/moneyprinter/config.toml</code>. É preciso reiniciar o container para aplicar: <code>docker compose restart moneyprinterturbo</code>.</div>`,
      sistema: `
        <div class="dr-sec" style="margin-top:0">Navegador e sistema</div>
        <div class="grid2" style="margin-top:10px">
          ${field("Idioma da interface", "Português (Brasil)", { select: true, opts: [["pt-BR", "Português (Brasil)"], ["en-US", "English (US)"], ["es-ES", "Español"]] })}
          ${field("Fuso horário", "América/São Paulo (GMT-3)", { select: true, opts: [["america_sp", "América/São Paulo (GMT-3)"], ["utc", "UTC"]] })}
          <div><label class="dr-sec" style="margin:0 0 7px">Tema</label><label style="display:flex;align-items:center;gap:7px;height:34px"><input type="checkbox" class="cbx" /> Modo escuro</label></div>
          ${field("Perfil do navegador (automação)", "Padrão do sistema")}
        </div>`,
      voz: `
        <div class="dr-sec" style="margin-top:0">Voz e legendas</div>
        <div class="card-note" style="margin:6px 0 14px;font-family:inherit">Edge TTS ${prov ? tag(!!prov.edge_tts, "Sempre disponível") : ""} — não exige chave.</div>
        <div class="grid2">
          ${field("Idioma da narração padrão", "pt-BR", { select: true, id: "cfgLanguage", opts: [["pt-BR", "Português (Brasil)"], ["en-US", "English (US)"], ["es-ES", "Español"]] })}
          ${field("Voz sintética padrão", "pt-BR-FranciscaNeural", { select: true, id: "cfgVoice", opts: [["pt-BR-FranciscaNeural", "Português (Brasil) — Feminina"], ["pt-BR-AntonioNeural", "Português (Brasil) — Masculina"]] })}
          ${field("Tamanho da legenda", "56", { id: "cfgFontSize", type: "number" })}
          ${field("Cor do texto", "#FFFFFF", { id: "cfgTextColor" })}
          ${field("Cor da borda", "#000000", { id: "cfgStrokeColor" })}
          <div><label class="dr-sec" style="margin:0 0 7px">Legendas</label><label style="display:flex;align-items:center;gap:7px;height:34px"><input type="checkbox" class="cbx" id="cfgSubtitleEnabled" checked /> Habilitadas por padrão</label></div>
        </div>`,
    };
    const cur = scope.cfgTab && panes[scope.cfgTab] ? scope.cfgTab : "ia";
    return `
      <div class="card" style="margin-bottom:0">
        <div class="card-hd"><span class="card-title">Configurações gerais</span><span class="spacer"></span>
          <button class="btn primary sm" id="cfgSaveBtn" type="button">Salvar alterações</button></div>
        <div class="view-tabs" id="cfgTabs" style="padding:0 18px;flex-wrap:wrap">${tabs.map(t =>
          `<button class="view-tab ${t[0] === cur ? "is-active" : ""}" data-cfgtab="${t[0]}">${t[1]}</button>`).join("")}</div>
        <div class="card-bd" id="cfgBody">${panes[cur]}</div>
      </div>`;
  }

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
   * - Configuração do tópico (com chips de sugestão rápida)
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
          <button class="btn gen-save-defaults" type="button" style="margin-top:10px;padding:11px" title="Salvar estas opções como padrão para novos vídeos">Salvar padrão</button>
        </div>
      </div>`;
  }
  function gerador(scope) {
    return screenTabs([
      ["gerar", "Gerador", studioCreative(scope, "long")],
      ["parametros", "Parâmetros de vídeo", studioParams("long")],
      ["prompt", "Prompt do sistema", systemPromptPane("long")],
      ["biblioteca", "Biblioteca", libraryCard("long")],
    ]);
  }
  function youtubeShorts(scope) {
    return screenTabs([
      ["gerar", "Gerador", studioCreative(scope, "short")],
      ["parametros", "Parâmetros de vídeo", studioParams("short")],
      ["prompt", "Prompt do sistema", systemPromptPane("short")],
      ["biblioteca", "Biblioteca", libraryCard("short")],
    ]);
  }
  /* One form, two formats: the engine (Turbo, approval, publishing) is shared; what differs is the
     aspect, the duration control, the script writer and which channels may receive the video.
     Split in two: studioCreative (topic/script/channels + the live pipeline/preview) sits on the
     "Gerador" tab; studioParams (voice/render/subtitle knobs + "Salvar padrão") gets its own tab. */
  function studioCreative(scope, format) {
    const isLong = format === "long";
    const chips = ["Psicologia", "Profundezas", "Hábitos Ricos", "Espaço", "História Oculta"];
    const steps = [["Roteiro IA", "doc"], ["Keywords", "search"], ["Voz e Legendas", "mic"], ["Filmagens Stock", "grid"], ["Render HD", "activity"]];
    return `
      <div class="grid-2-1">
        <div class="card" style="margin-bottom:0" data-format="${format}">
          <div class="card-hd"><span class="card-title">${isLong ? "Vídeo longo para YouTube" : "Short para YouTube e TikTok"}</span><span class="spacer"></span><span class="badge plan-pro">${isLong ? "16:9 · 5–15 min" : "9:16 · 60–90 s"}</span></div>
          <div class="card-bd" style="display:flex;flex-direction:column;gap:16px">
            <div>
              <label class="dr-sec" style="margin:0 0 7px">Tema / tópico do vídeo</label>
              <textarea class="np-input" id="genTopicInput" rows="2" style="width:100%;resize:vertical" placeholder="Ex: 5 fatos inacreditáveis sobre as profundezas do oceano"></textarea>
              <div style="display:flex;flex-wrap:wrap;gap:7px;margin-top:9px">
                ${chips.map(c => `<button class="btn sm" type="button" data-chip="${c}">${c}</button>`).join("")}
              </div>
              <div class="card-note" style="margin-top:8px">A IA gera o roteiro completo e as palavras-chave de filmagem a partir deste tema.</div>
            </div>
            <div>
              <div style="display:flex;justify-content:space-between;align-items:center;margin:0 0 7px"><label class="dr-sec" style="margin:0">Roteiro (opcional)</label>${isLong
                ? `<button class="btn sm" type="button" id="genLongScriptBtn">Escrever roteiro longo com IA</button>`
                : `<button class="btn sm" type="button" id="genScriptAiBtn">Gerar roteiro com IA</button>`}</div>
              <textarea class="np-input" id="genScriptInput" rows="${isLong ? 10 : 4}" style="width:100%;resize:vertical" placeholder="${isLong ? "Escreva por seções com a IA ou cole o roteiro completo" : "Deixe vazio para a IA escrever, ou gere/cole e edite aqui"}"></textarea>
              <div class="card-note" id="genScriptStats" style="margin-top:6px;font-family:inherit"></div>
            </div>
            <details>
              <summary class="dr-sec" style="margin:0;cursor:pointer">Configurações avançadas do roteiro</summary>
              <div style="display:flex;flex-direction:column;gap:12px;margin-top:10px">
                <div>
                  <label class="dr-sec" style="margin:0 0 7px">Requisitos personalizados do roteiro</label>
                  <textarea class="np-input" id="genScriptRequirements" rows="2" style="width:100%;resize:vertical" placeholder="Ex: use um tom mais leve, estilo TikTok, com uma abertura mais intrigante"></textarea>
                  <button class="btn sm gen-save-defaults" type="button" style="margin-top:6px" title="Salvar estas opções como padrão para novos vídeos">Salvar padrão</button>
                </div>
              </div>
            </details>
            <div>
              <div style="display:flex;justify-content:space-between;align-items:center;margin:0 0 7px"><label class="dr-sec" style="margin:0">Palavras-chave das filmagens (opcional)</label><button class="btn sm" type="button" id="genTermsAiBtn">Gerar palavras-chave</button></div>
              <input class="np-input" id="genTermsInput" style="width:100%" placeholder="deep ocean, ocean life — separadas por vírgula (em inglês o Pexels acha mais)" />
            </div>
            <div>
              <label class="dr-sec" style="margin:0 0 7px">${isLong ? "Selecione o canal" : "Publicar nos canais"}</label>
              ${isLong
                ? `<select class="set-select" id="genChannelSelect" style="width:100%"><option value="">Escolher depois</option>${D.CHANNELS.filter(c => c.status === "connected" && c.platform === "YouTube").map(c => `<option value="${esc(c.id)}">${esc(c.name)} · YouTube</option>`).join("")}</select>`
                : `<div id="genChannelList" style="display:flex;flex-direction:column;gap:6px"></div>`}
              <div class="card-note" style="margin-top:6px;font-family:inherit">Canais conectados em <a href="#" data-view="clientes" style="color:var(--accent)">Canais</a>.${isLong ? " Vídeos longos vão só para o YouTube." : " No máximo um canal por plataforma."}</div>
            </div>
            <button class="btn primary" id="genStartBtn" style="padding:11px">${isLong ? "Gerar vídeo longo" : "Gerar Short"}</button>
          </div>
        </div>
        <div style="display:flex;flex-direction:column;gap:20px">
          <div class="card" style="margin-bottom:0">
            <div class="card-hd"><span class="card-title">Pipeline em tempo real</span><span class="spacer"></span><span class="card-note" id="genPipelineNote">Pronto para gerar</span></div>
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
                <button class="btn" data-toast="Caminho copiado"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:15px;height:15px"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg> Copiar Caminho</button>
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
                    <label class="card-note" style="font-family:inherit;display:flex;align-items:center;gap:6px">Agendar para <input class="np-input" type="datetime-local" id="genPublishAt" style="padding:4px 6px" /></label>
                    <span class="card-note" id="genPublishStatus" role="status" style="font-family:inherit"></span>
                  </div>
                  <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:6px">
                    <button class="btn sm" id="genReviewBtn" type="button">Revisar imagens com IA</button>
                    <button class="btn sm" id="genThumbBtn" type="button">Gerar thumbnail com IA</button>
                  </div>
                  <div id="genReviewResult" style="margin-top:6px"></div>
                  <img id="genThumbImg" alt="Thumbnail gerada" style="margin-top:8px;width:100%;max-width:320px;border-radius:8px;display:block" hidden />
                </div>
              </div></div>
          </div>
        </div>
      </div>`;
  }
  /* Voice/render/subtitle knobs, moved off the "Gerador" tab into their own — same fields Turbo's
     VideoParams exposes, just laid out per format like studioCreative was. */
  function studioParams(format) {
    const isLong = format === "long";
    return `
      <div class="card" style="margin-bottom:0">
        <div class="card-hd"><span class="card-title">Parâmetros de vídeo</span></div>
        <div class="card-bd" style="display:flex;flex-direction:column;gap:16px">
          <div class="grid2" style="margin:0">
            <div><label class="dr-sec" style="margin:0 0 7px">Idioma da narração</label>
              <select class="set-select" id="genLanguageSelect" style="width:100%"><option value="pt-BR">Português (Brasil)</option><option value="en-US">English (US)</option><option value="es-ES">Español</option></select></div>
            <div><label class="dr-sec" style="margin:0 0 7px">Voz sintética</label>
              <select class="set-select" id="genVoiceSelect" style="width:100%">
                <optgroup label="Português (Brasil)"><option value="pt-BR-FranciscaNeural">Francisca — Feminina</option><option value="pt-BR-AntonioNeural">Antonio — Masculina</option></optgroup>
                <optgroup label="English (US)"><option value="en-US-JennyNeural">Jenny — Female</option><option value="en-US-GuyNeural">Guy — Male</option></optgroup>
                <optgroup label="Español"><option value="es-ES-ElviraNeural">Elvira — Femenina</option><option value="es-ES-AlvaroNeural">Álvaro — Masculino</option></optgroup>
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
              <div class="card-note" style="margin-top:6px">"Seguir o roteiro" busca uma filmagem para cada trecho narrado, na ordem. Gere as palavras-chave antes para ver quantos cortes vão ser usados.</div></div>
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
          <button class="btn gen-save-defaults" type="button" id="genSaveDefaultsBtn" style="padding:11px" title="Salvar estas opções como padrão para novos vídeos">Salvar padrão</button>
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

  /* ── Agentes ────────────────────────────────── */
  /* Panes are filled by MoneyPrinterRuntime.bindAgents when opened: they all need live Gateway data. */
  function agentes() {
    const tabs = [["estrategia", "Estratégia"], ["analytics", "Analytics"], ["agendamento", "Agendamento"], ["operador", "Operador automático"]];
    return `
      <div class="card" style="margin-bottom:0" id="agentsCard">
        <div class="view-tabs" id="agTabs" style="padding:0 18px;flex-wrap:wrap">${tabs.map((t, i) =>
          `<button class="view-tab ${i ? "" : "is-active"}" data-agtab="${t[0]}">${t[1]}</button>`).join("")}</div>
        <div class="card-bd">
          <div data-agpane="estrategia">
            <div class="card-note" style="font-family:inherit;margin-bottom:12px">Sugere temas a partir do nicho do canal, do que está em alta no YouTube e do que você já produziu.</div>
            <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end">
              <div class="md-field" style="min-width:220px"><label>Canal</label><select class="set-select" style="width:100%" id="agStrategyChannel"><option value="">Selecione um canal…</option></select></div>
              <div class="md-field"><label>Formato</label><select class="set-select" id="agStrategyFormat"><option value="short">Short</option><option value="long">Vídeo longo</option></select></div>
              <button class="btn primary" id="agStrategyBtn" type="button">Sugerir temas</button>
            </div>
            <div id="agStrategyList" style="display:flex;flex-direction:column;gap:10px;margin-top:16px"></div>
          </div>
          <div data-agpane="analytics" hidden>
            <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
              <span class="card-note" style="font-family:inherit;flex:1">Desempenho real dos vídeos publicados no YouTube. A nota (0–100) compara cada vídeo com a mediana do próprio canal.</span>
              <button class="btn sm" id="agAnalyticsBtn" type="button">Atualizar métricas</button>
            </div>
            <div id="agAnalyticsBody" style="margin-top:12px"></div>
          </div>
          <div data-agpane="agendamento" hidden><div id="agScheduleBody"></div></div>
          <div data-agpane="operador" hidden>
            <div class="card-note" style="font-family:inherit;margin-bottom:12px">Gera vídeos sozinho na frequência escolhida e prepara os metadados. Nada é publicado sem a sua aprovação.</div>
            <div id="agOperatorBody" style="display:flex;flex-direction:column;gap:12px"></div>
            <div class="dr-sec" style="margin-top:20px">Aguardando sua aprovação</div>
            <div id="agAwaitingBody"></div>
          </div>
        </div>
      </div>`;
  }

  /* ── Jobs e workers ─────────────────────────── */
  /* Filled by MoneyPrinterRuntime.bindJobsWorkers/renderPendingJobs from window.CK.PENDING_JOBS. */
  function jobsWorkers() {
    return `
      <div class="card" style="margin-top:16px">
        <div class="card-hd"><span class="card-title">Jobs em andamento / com falha</span></div>
        <div class="card-bd" id="jobsWorkersBody" style="padding:0 18px"></div>
      </div>`;
  }

  /* ── Notificações ───────────────────────────── */
  function notificacoes(scope) {
    const list = D.NOTIFICATIONS;
    const unread = list.filter(n => !n.read).length;
    return `
      <div class="view-filters">
        <span class="rows-count">${unread} não lida${unread === 1 ? "" : "s"} de ${list.length}</span>
        <div class="view-filters-spacer"></div>
        <button class="btn sm" id="notifReadAll">Marcar todas como lidas</button>
      </div>
      <div class="card" style="margin-top:16px"><div class="card-bd" id="notifBody" style="padding:0">${notifRows(list)}</div></div>`;
  }
  function notifRows(list) {
    if (!list.length) return `<div class="empty"><div class="empty-t">Nenhuma notificação</div><div class="empty-s">Você está em dia.</div></div>`;
    return list.map(n => `
      <div class="alert-row ${n.read ? "" : "is-unread"}" data-notif="${n.id}" data-goto="${n.view}" style="cursor:pointer">
        <span class="sev-dot ${n.sev === "ok" ? "info" : n.sev}" style="${n.sev === "ok" ? "background:var(--ok);box-shadow:0 0 0 3px var(--ok-soft, rgba(16,185,129,.18))" : ""}"></span>
        <span class="alert-main"><span class="alert-title">${esc(n.title)}${n.read ? "" : ' <span class="badge plan-pro" style="margin-left:6px;font-size:10px">nova</span>'}</span>
          <div class="alert-meta"><span>${esc(n.sub)}</span></div></span>
        <span class="alert-time">${n.time}</span>
      </div>`).join("");
  }

  return { overview, usuarios, logs, billing, clientes, custosIA, videoGrids, configuracoes, gerador, notificacoes,
           alertRows, userRows, logRows, orgRows, userDrawer, logDrawer, orgDrawer, perfilUsuario, filtersBar,
           organizacao, orgRec, newOrgRec, canais, canaisHead, canalRows, canalRec, newCanalRec, canal, videoList, youtubeShorts, agentes, jobsWorkers };
})();
