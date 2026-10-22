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
      ${v[4] ? `<button class="pop-item" type="button" data-open-library="${esc(v[4])}" data-library-kind="${kind}"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 21V9M7 14l5-5 5 5M5 3h14"/></svg><span>Preparar publicação</span></button>` : ""}
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
        <select class="set-select" id="lSvc"><option>Todos os serviços</option></select>
        <div class="view-filters-spacer"></div>
        <button class="btn sm" type="button" id="lRefresh">Atualizar</button>
        <span class="rows-count" id="lCount">Carregando…</span>
      </div>
      <div class="console" id="logBody"><div class="empty"><div class="empty-t">Carregando eventos reais…</div><div class="empty-s">Consultando o Gateway local.</div></div></div>
      <div class="grid2" style="margin-top:20px">
        <div class="card" id="logTraceCard"><div class="card-hd"><span class="card-title">Trace mais recente</span></div><div class="card-bd" id="logTraceBody"><div class="card-note">Aguardando eventos.</div></div></div>
        <div class="card" id="logSummaryCard"><div class="card-hd"><span class="card-title">Resumo dos eventos</span></div><div class="card-bd" id="logSummaryBody"><div class="card-note">Aguardando eventos.</div></div></div>
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
  /* Real data only: skeleton filled by MoneyPrinterRuntime.bindAnalytics
     from GET /api/analytics/overview. No mocked totals or D.series() charts. */
  function custosIA(scope) {
    return `
      <div id="analyticsBody">
        <div class="card" style="margin-bottom:0"><div class="empty">
          <div class="empty-t">Carregando métricas reais…</div>
          <div class="empty-s">Canais, jobs e YouTube Analytics do gateway.</div>
        </div></div>
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
    const spans = Array.isArray(l.spans) ? l.spans : [];
    const maxDuration = Math.max(1, ...spans.map(span => Number(span.duration_ms) || 0), Number(l.duration_ms) || 0);
    const traceHtml = spans.length ? spans.map(span => `
          <div class="trace-row"><span class="trace-name">${esc(span.name || span.service || "etapa")}</span>
            <span class="trace-track"><span class="trace-fill ${(Number(span.status_code) || 0) >= 400 ? "slow" : ""}" style="width:${Math.max(2, (Number(span.duration_ms) || 0) / maxDuration * 100).toFixed(1)}%"></span></span>
            <span class="trace-ms">${Number(span.duration_ms || 0).toLocaleString("pt-BR")}ms</span></div>`).join("")
      : `<div class="card-note">Esta requisição não chamou serviços externos.</div>`;
    const timestamp = l.timestamp ? new Date(l.timestamp).toLocaleString("pt-BR") : "—";
    return {
      title: l.lvl === "INFO" ? "Evento de sistema" : "Falha em " + l.svc,
      sub: esc(l.msg),
      body: `
        <div class="dr-sec">Contexto</div>
        <dl class="kv">
          <dt>Nível</dt><dd><span class="log-lvl ${l.lvl}">${l.lvl}</span></dd>
          <dt>Serviço</dt><dd class="mono">${esc(l.svc)}</dd>
          <dt>Horário</dt><dd class="mono">${esc(timestamp)}</dd>
          <dt>Método</dt><dd class="mono">${esc(l.method || "—")}</dd>
          <dt>Rota</dt><dd class="mono">${esc(l.path || "—")}</dd>
          <dt>Status HTTP</dt><dd class="mono">${esc(l.status_code ?? "—")}</dd>
          <dt>Duração</dt><dd class="mono">${Number(l.duration_ms || 0).toLocaleString("pt-BR")} ms</dd>
          <dt>Trace ID</dt><dd class="mono">${esc(l.trace || "—")}</dd>
        </dl>
        <div class="dr-sec">Trace</div>
        <div class="trace">${traceHtml}</div>`,
      footer: "",
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
    const tabs = [["ia", "Modelos e IA"], ["video", "Vídeo e Stock"], ["sistema", "Navegador e Sistema"]];
    const tag = (ok, label) => ok
      ? `<span class="status-pill" style="color:var(--ok)"><span class="status-dot active"></span>${label}</span>`
      : `<span class="status-pill" style="color:var(--warn)"><span class="status-dot pending"></span>${label}</span>`;
    const field = (label, val, opts) => `<div><label class="dr-sec" style="margin:0 0 7px">${label}</label>${opts && opts.select
      ? `<select class="set-select" id="${opts.id}" style="width:100%">${opts.opts.map(([v, t]) => `<option value="${v}" ${v === val ? "selected" : ""}>${t}</option>`).join("")}</select>`
      : `<input class="np-input" id="${opts && opts.id || ""}" style="width:100%" type="${opts && opts.type || "text"}" value="${esc(val)}" /></div>`}</div>`;
    const prov = D.__providers;
    const nvidiaCatalog = D.__nvidiaModels || { models: [
      { id: "moonshotai/kimi-k3", label: "Kimi K3", provider: "Moonshot AI", description: "Raciocínio avançado e contexto longo.", capabilities: { text: true, image: true, reasoning: true, streaming: true }, available: null },
      { id: "z-ai/glm-5.3", label: "GLM 5.3", provider: "Z.ai", description: "Equilibrado para roteiros, JSON e tarefas gerais.", capabilities: { text: true, image: false, reasoning: true, streaming: true }, available: null },
      { id: "z-ai/glm-5.3-flash", label: "GLM 5.3 Flash", provider: "Z.ai", description: "Variante rápida para tarefas frequentes.", capabilities: { text: true, image: false, reasoning: true, streaming: true }, available: null },
      { id: "nvidia/nemotron-3.5-lightning-30b-a3b", label: "Nemotron 3.5 Lightning", provider: "NVIDIA", description: "Modelo rápido para tarefas agentivas com thinking.", capabilities: { text: true, image: false, reasoning: true, streaming: true }, available: null },
      { id: "meta/muse-glimmer-30b", label: "Muse Glimmer 30B", provider: "Meta", description: "Raciocínio multimodal com ferramentas nativas.", capabilities: { text: true, image: true, reasoning: true, streaming: true }, available: null },
    ] };
    const nvidiaModels = nvidiaCatalog.models || [];
    const nvidiaSelected = prov && prov.nvidia_text_model || "moonshotai/kimi-k3";
    const selectedNvidia = nvidiaModels.find(model => model.id === nvidiaSelected) || nvidiaModels[0];
    const nvidiaCapabilities = selectedNvidia ? ["Texto", selectedNvidia.capabilities && selectedNvidia.capabilities.image ? "Imagem" : "", selectedNvidia.capabilities && selectedNvidia.capabilities.reasoning ? "Raciocínio" : "", selectedNvidia.capabilities && selectedNvidia.capabilities.streaming ? "Streaming" : ""].filter(Boolean).join(" · ") : "";
    const nvidiaCatalogNote = nvidiaCatalog.catalog_status === "available" ? "Catálogo NVIDIA atualizado." : nvidiaCatalog.catalog_status === "unauthorized" ? "A NVIDIA recusou a chave configurada." : nvidiaCatalog.catalog_status === "not_configured" ? "Configure a chave NVIDIA para consultar a disponibilidade." : "Disponibilidade não pôde ser consultada agora.";
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
        <div class="grid2">
          <div><label class="dr-sec" style="margin:0 0 7px">Chave NVIDIA ${prov ? tag(!!prov.nvidia, prov.nvidia ? "Configurada" : "Não configurada") : ""}</label>
            <input class="np-input" id="cfgNvidiaKey" style="width:100%" type="password" placeholder="${prov && prov.nvidia ? "•••••••• (deixe em branco para manter)" : "nvapi-..."}" autocomplete="off" />
            <div class="card-note" id="cfgNvidiaKeyStatus" style="margin-top:5px;min-height:14px"></div></div>
          <div><label class="dr-sec" style="margin:0 0 7px">Modelo de texto</label>
            <select class="set-select" id="cfgNvidiaTextModel" style="width:100%">${nvidiaModels.map(model => `<option value="${esc(model.id)}" ${model.id === nvidiaSelected ? "selected" : ""} ${model.available === false && model.id !== nvidiaSelected ? "disabled" : ""}>${esc(model.label)} — ${esc(model.provider)}${model.available === false ? " (indisponível)" : ""}</option>`).join("")}</select>
            <div class="card-note" id="cfgNvidiaModelInfo" style="margin-top:6px;font-family:inherit">${esc(selectedNvidia && selectedNvidia.description || "")}${nvidiaCapabilities ? ` ${esc(nvidiaCapabilities)}.` : ""}</div>
            <div class="card-note" id="cfgNvidiaCatalogStatus" style="margin-top:4px;font-family:inherit">${esc(nvidiaCatalogNote)}</div>
            <button class="btn sm" id="cfgNvidiaTestBtn" type="button" style="margin-top:9px">Testar modelo</button>
            <div class="card-note" id="cfgNvidiaTestStatus" style="margin-top:6px;min-height:16px"></div></div>
        </div>
        <div class="card-note" style="margin-top:10px;font-family:inherit">Salvar aplica o modelo imediatamente ao Gateway. O roteiro do Short no Turbo exige reinício após salvar: <code>docker compose restart moneyprinterturbo</code>.</div>`,
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
          ${field("Idioma da interface", "Português (Brasil)", { select: true, opts: [["pt-BR", "Português (Brasil)"], ["en-US", "English (US)"], ["es-ES", "Español"], ["de-DE", "Deutsch"]] })}
          ${field("Fuso horário", "América/São Paulo (GMT-3)", { select: true, opts: [["america_sp", "América/São Paulo (GMT-3)"], ["utc", "UTC"]] })}
          <div><label class="dr-sec" style="margin:0 0 7px">Tema</label><label style="display:flex;align-items:center;gap:7px;height:34px"><input type="checkbox" class="cbx" /> Modo escuro</label></div>
          ${field("Perfil do navegador (automação)", "Padrão do sistema")}
        </div>`,
    };
    const cur = scope.cfgTab && panes[scope.cfgTab] ? scope.cfgTab : "ia";
    return `
      <div class="card" style="margin-bottom:0">
        <div class="card-hd"><span class="card-title">Configurações gerais</span><span class="spacer"></span>
          <button class="btn sm" type="button" data-view="logs">Logs e traces</button>
          <button class="btn primary sm" id="cfgSaveBtn" type="button">Salvar alterações</button></div>
        <div class="view-tabs" id="cfgTabs" style="padding:0 18px;flex-wrap:wrap">${tabs.map(t =>
          `<button class="view-tab ${t[0] === cur ? "is-active" : ""}" data-cfgtab="${t[0]}">${t[1]}</button>`).join("")}</div>
        <div class="card-bd" id="cfgBody">${panes[cur]}</div>
      </div>`;
  }


  /* ── Agentes ────────────────────────────────── */
  /* Panes are filled by MoneyPrinterRuntime.bindAgents when opened: they all need live Gateway data. */
  function agentes(visible) {
    const tabs = [["estrategia", "Estratégia"], ["analytics", "Analytics"], ["agendamento", "Agendamento"], ["operador", "Operador automático"], ["revisoes", "Revisões de vídeo"]];
    const shown = Array.isArray(visible) && visible.length ? visible : tabs.map(tab => tab[0]);
    const initial = shown[0];
    return `
      <div class="card" style="margin-bottom:0" id="agentsCard" data-initial-tab="${initial}">
        <div class="view-tabs" id="agTabs" style="padding:0 18px;flex-wrap:wrap">${tabs.map(t =>
          `<button class="view-tab ${t[0] === initial ? "is-active" : ""}" data-agtab="${t[0]}" ${shown.includes(t[0]) ? "" : "hidden"}>${t[1]}</button>`).join("")}</div>
        <div class="card-bd">
          <div data-agpane="estrategia" ${initial === "estrategia" ? "" : "hidden"}>
            <div class="card-note" style="font-family:inherit;margin-bottom:12px">Sugere temas a partir do nicho do canal, do que está em alta no YouTube e do que você já produziu.</div>
            <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end">
              <div class="md-field" style="min-width:220px"><label>Canal</label><select class="set-select" style="width:100%" id="agStrategyChannel"><option value="">Selecione um canal…</option></select></div>
              <div class="md-field"><label>Formato</label><select class="set-select" id="agStrategyFormat"><option value="short">Short</option><option value="long">Vídeo longo</option></select></div>
              <button class="btn primary" id="agStrategyBtn" type="button">Sugerir temas</button>
            </div>
            <div id="agStrategyList" style="display:flex;flex-direction:column;gap:10px;margin-top:16px"></div>
          </div>
          <div data-agpane="analytics" ${initial === "analytics" ? "" : "hidden"}>
            <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
              <span class="card-note" style="font-family:inherit;flex:1">Desempenho real dos vídeos publicados no YouTube. A nota (0–100) compara cada vídeo com a mediana do próprio canal.</span>
              <button class="btn sm" id="agAnalyticsBtn" type="button">Atualizar métricas</button>
            </div>
            <div id="agAnalyticsBody" style="margin-top:12px"></div>
          </div>
          <div data-agpane="agendamento" ${initial === "agendamento" ? "" : "hidden"}><div id="agScheduleBody"></div></div>
          <div data-agpane="operador" ${initial === "operador" ? "" : "hidden"}>
            <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:12px">
              <span class="card-note" style="font-family:inherit;flex:1">Gera vídeos sozinho na frequência escolhida e prepara os metadados. Nada é publicado sem a sua aprovação.</span>
              <button class="btn sm" id="agOperatorRefreshBtn" type="button">Atualizar trabalhos</button>
            </div>
            <div id="agOperatorBody" style="display:flex;flex-direction:column;gap:12px"></div>
            <div class="dr-sec" style="margin-top:20px">Trabalhos do operador</div>
            <div id="agInProgressBody"></div>
            <div class="dr-sec" style="margin-top:20px">Aguardando sua aprovação</div>
            <div id="agAwaitingBody"></div>
          </div>
          <div data-agpane="revisoes" ${initial === "revisoes" ? "" : "hidden"}><div class="card-note" style="margin-bottom:12px">Revisão e correção automáticas por canal. Abra a geração para acompanhar as tentativas ou ajustar manualmente o que não puder ser corrigido.</div><div style="display:flex;flex-wrap:wrap;gap:8px"><button class="btn sm" id="agReviewsRefresh" type="button">Atualizar revisões</button><button class="btn sm" id="agReviewsClear" type="button">Limpar concluídas</button></div><div id="agReviewsBody" style="margin-top:12px"></div></div>
        </div>
      </div>`;
  }


  /* ── Assistente conversacional ───────────────── */
  function assistantChat(prefix) {
    const channelOptions = D.CHANNELS.map(channel =>
      "<option value=\"" + esc(channel.id) + "\">" + esc(channel.name) + " · " + esc(channel.platform) + "</option>"
    ).join("");
    const markup = "<div class=\"assistant-chat-shell\" id=\"assistantChatRoot\">" +
      "<div class=\"assistant-chat-intro\"><div><div class=\"card-title\">O que você quer fazer?</div>" +
      "<div class=\"card-note\">Peça ajuda sobre o sistema, Growth OS, canais, vídeos e trabalhos.</div></div>" +
      "<button class=\"btn sm\" type=\"button\" id=\"assistantNewChat\">Nova conversa</button></div>" +
      "<div class=\"assistant-chat-feed\" id=\"assistantChatFeed\" role=\"log\" aria-live=\"polite\"></div>" +
      "<div class=\"assistant-chat-compose\">" +
      "<div class=\"assistant-chat-suggestions\">" +
      "<button class=\"btn sm\" type=\"button\" data-chat-suggestion>Calcule meu gap para o YPP</button>" +
      "<button class=\"btn sm\" type=\"button\" data-chat-suggestion>Avalie uma ideia de vídeo</button>" +
      "<button class=\"btn sm\" type=\"button\" data-chat-suggestion>Crie um roteiro de Short</button>" +
      "<button class=\"btn sm\" type=\"button\" data-chat-suggestion>Quais trabalhos estão em andamento?</button></div>" +
      "<label class=\"assistant-chat-channel\" for=\"assistantChatChannel\">Canal de contexto" +
      "<select id=\"assistantChatChannel\" class=\"set-select\"><option value=\"\">Escolha quando necessário</option>" + channelOptions + "</select></label>" +
      "<form id=\"assistantChatForm\" class=\"assistant-chat-form\">" +
      "<textarea id=\"assistantChatInput\" rows=\"2\" maxlength=\"3000\" placeholder=\"Escreva sua mensagem…\" aria-label=\"Mensagem para o assistente\"></textarea>" +
      "<button class=\"btn primary\" id=\"assistantChatSend\" type=\"submit\">Enviar</button></form>" +
      "<div class=\"assistant-chat-footnote\">Ações de publicação continuam sujeitas à revisão humana.</div></div></div>";
    return prefix ? markup.split("assistantChat").join(prefix) : markup;
  }

  /* ── Growth OS ──────────────────────────────── */
  function growth() {
    const channelOptions = D.CHANNELS.map(channel => `<option value="${esc(channel.id)}">${esc(channel.name)} · ${esc(channel.platform)}</option>`).join("");
    const metric = (id, label, example) => `<div class="md-field"><label for="${id}">${label}</label><input class="np-input" id="${id}" type="number" min="0" step="any" placeholder="Exemplo: ${example}" style="width:100%" /></div>`;
    const dimension = (id, label, example) => `<div class="md-field"><label for="${id}">${label}</label><input class="np-input" id="${id}" type="number" min="0" max="100" placeholder="Exemplo: ${example}" style="width:100%" /></div>`;
    return `
      <div id="growthRoot">
        <div class="card" style="margin-bottom:16px">
          <div class="card-hd"><span class="card-title">Contexto do plano</span><span class="spacer"></span><span class="badge plan-pro">P0</span></div>
          <div class="card-bd">
            <div class="grid2" style="margin:0">
              <div class="md-field"><label for="growthChannel">Canal</label><select class="set-select" id="growthChannel" style="width:100%"><option value="">Selecione…</option>${channelOptions}</select></div>
              <div class="md-field"><label for="growthTopic">Tema</label><input class="np-input" id="growthTopic" style="width:100%" placeholder="Ex: Como agentes de IA estão substituindo workflows SaaS" /></div>
            </div>
          </div>
        </div>

        <div class="grid2" style="align-items:start">
          <div class="card" style="margin:0">
            <div class="card-hd"><span class="card-title">Meta YPP qualificada</span></div>
            <div class="card-bd">
              <div class="card-note" style="margin-bottom:10px">Preencha com métricas reais do canal antes de calcular. Campos vazios não são tratados como resultados medidos.</div><div class="grid2" style="margin:0">
                ${metric("growthSubscribers", "Inscritos atuais", 742)}
                ${metric("growthSubscriberTarget", "Meta de inscritos", 1000)}
                ${metric("growthWatchHours", "Horas qualificadas", 2860)}
                ${metric("growthWatchTarget", "Meta de horas", 4000)}
                ${metric("growthShortViews", "Views qualificadas de Shorts", 3200000)}
                ${metric("growthShortTarget", "Meta de Shorts", 10000000)}
                ${metric("growthDays", "Dias restantes", 4)}
                ${metric("growthAvd", "AVD em minutos", 6.8)}
              </div>
              <div style="display:flex;gap:8px;margin-top:14px"><button class="btn" id="growthEvaluate" type="button">Calcular gap</button></div>
              <div id="growthGoalResult" style="margin-top:14px"></div>
            </div>
          </div>

          <div class="card" style="margin:0">
            <div class="card-hd"><span class="card-title">Opportunity Score</span><span class="spacer"></span><span class="card-note">0–100</span></div>
            <div class="card-bd">
              <div class="grid2" style="margin:0">
                ${dimension("growthDemand", "Demanda", 80)}
                ${dimension("growthAudience", "Audience fit", 80)}
                ${dimension("growthRetention", "Retenção histórica", 70)}
                ${dimension("growthConversion", "Conversão em inscritos", 70)}
                ${dimension("growthExpectedAvd", "AVD esperado", 75)}
                ${dimension("growthEvergreen", "Evergreen", 65)}
                ${dimension("growthVelocity", "Velocidade da tendência", 80)}
                ${dimension("growthMonetization", "Monetização", 60)}
                ${dimension("growthStock", "Disponibilidade de stock", 80)}
                ${dimension("growthCompetition", "Competição", 50)}
                ${dimension("growthCost", "Custo de produção", 30)}
                ${dimension("growthSaturation", "Saturação", 40)}
              </div>
              <div style="display:flex;gap:8px;margin-top:14px;flex-wrap:wrap">
                <button class="btn" id="growthScore" type="button">Calcular score</button>
                <button class="btn primary" id="growthPlan" type="button">Criar plano durável</button>
              </div>
              <div id="growthOpportunityResult" style="margin-top:14px"></div>
            </div>
          </div>
        </div>

        <div class="card" style="margin-top:16px;margin-bottom:0">
          <div class="card-hd"><span class="card-title">Workflow Temporal</span><span class="spacer"></span><span class="card-note">publicação em revisão por padrão</span></div>
          <div class="card-bd" id="growthWorkflowResult"><div class="card-note">Nenhum plano iniciado nesta sessão.</div></div>
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
      <div class="alert-row ${n.read ? "" : "is-unread"}" data-notif="${n.id}" data-goto="${n.view}" ${n.job_id ? `data-job-id="${esc(n.job_id)}"` : ""} style="cursor:pointer">
        <span class="sev-dot ${n.sev === "ok" ? "info" : n.sev}" style="${n.sev === "ok" ? "background:var(--ok);box-shadow:0 0 0 3px var(--ok-soft, rgba(16,185,129,.18))" : ""}"></span>
        <span class="alert-main"><span class="alert-title">${esc(n.title)}${n.read ? "" : ' <span class="badge plan-pro" style="margin-left:6px;font-size:10px">nova</span>'}</span>
          <div class="alert-meta"><span>${esc(n.sub)}</span></div></span>
        <span class="alert-time">${n.time}</span>
      </div>`).join("");
  }

  const studioViews = window.CKVStudio({ D, esc });
  const channelViews = window.CKVChannels({ D, esc });
  const { gerador, youtubeShorts, libraryCard } = studioViews;
  const { canais, canaisHead, canalRows, canalRec, newCanalRec, canal } = channelViews;

  return { overview, usuarios, logs, billing, clientes, custosIA, videoGrids, configuracoes, gerador, notificacoes, libraryCard,
           alertRows, userRows, logRows, orgRows, userDrawer, logDrawer, orgDrawer, perfilUsuario, filtersBar,
           organizacao, orgRec, newOrgRec, canais, canaisHead, canalRows, canalRec, newCanalRec, canal, videoList, youtubeShorts, agentes, assistantChat, growth, jobsWorkers };
})();
