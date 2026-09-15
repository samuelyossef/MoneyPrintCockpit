/**
 * ============================================================================
 * Lattice Cockpit — Controlador Principal da Aplicação SPA
 * ============================================================================
 * Responsável por:
 * 1. Gerenciar o estado global da sessão e preferências (tema claro/escuro)
 * 2. Roteamento de telas (Visão Geral, Canais, Gerador de Vídeo, YouTube Shorts, etc.)
 * 3. Renderização dinâmica da Sidebar e Topbar
 * 4. Controle do painel deslizante (Drawer) para inspeção de logs e detalhes
 * 5. Sistema de notificações Toast e busca global
 * ============================================================================
 */
(function () {
  // Referências aos módulos de dados (CK) e templates visuais (CKV)
  const D = window.CK, V = window.CKV;
  const LS = "lattice-cockpit-v1"; // Chave de persistência no localStorage

  /**
   * Mapeamento de todas as telas (Views) do painel
   * Cada rota possui:
   * - title: Título principal exibido no cabeçalho
   * - sub: Descrição ou subtítulo contextual
   * - render: Função de template em `cockpit-views.js` responsável pelo HTML
   */
  const VIEWS = {
    overview:  { title: "Visão geral", sub: "Sexta, 4 de setembro · estado da plataforma em tempo real", render: V.overview },
    clientes:  { title: "Canais", sub: "Contas conectadas, plataformas e métricas de publicação", render: V.canais },
    usuarios:  { title: "Usuários", sub: "Acesso, papéis e permissões em todas as organizações", render: V.usuarios },
    logs:      { title: "Logs e traces", sub: "Fluxo de eventos, erros e latência por requisição", render: V.logs },
    notificacoes: { title: "Notificações", sub: "Alertas de geração, provedores e sistema", render: V.notificacoes },
    billing:   { title: "Receita", sub: "MRR, faturas e planos", render: V.billing },
    custos:    { title: "Analytics", sub: "Desempenho da plataforma e dos canais conectados", render: V.custosIA },
    config:    { title: "Configurações", sub: "Preferências da conta, notificações e integrações", render: V.configuracoes },
    gerador:   { title: "Gerador de Vídeo", sub: "Vídeos longos 16:9 para YouTube", render: V.gerador },
    youtube:   { title: "Shorts", sub: "Vídeos verticais para YouTube Shorts e TikTok", render: V.youtubeShorts },
    agentes:   { title: "Agentes", sub: "Estratégia, analytics, agendamento e operador automático", render: V.agentes },
  };

  const NAV = {
    super: [
      { section: "Plataforma", items: [
        { v: "overview", label: "Visão geral", icon: "grid" },
        { v: "clientes", label: "Canais", icon: "spark", get count() { return D.CHANNELS.length ? String(D.CHANNELS.length) : ""; } },
      ]},
      { section: "Pessoas", items: [
        { v: "notificacoes", label: "Notificações", icon: "bell", count: "4" },
      ]},
      { section: "Studio", items: [
        { v: "gerador", label: "Gerador de Vídeo", icon: "spark" },
        { v: "youtube", label: "Shorts", icon: "yt" },
        { v: "agentes", label: "Agentes", icon: "spark" },
        { v: "custos", label: "Analytics", icon: "activity" },
      ]},
      { section: "Observabilidade", items: [
        { v: "logs", label: "Logs e traces", icon: "logs" },
        { v: "soon:Jobs e workers", label: "Jobs e workers", icon: "activity", count: "2" },
      ]},
      { section: "Conta", items: [
        { v: "soon:Integrações", label: "Integrações", icon: "grid" },
        { v: "config", label: "Configurações", icon: "settings" },
      ]},
    ],
    org: [
      { section: "Workspace", items: [
        { v: "overview", label: "Visão geral", icon: "grid" },
        { v: "soon:Projetos", label: "Projetos", icon: "building", count: "18" },
        { v: "soon:Uso", label: "Uso", icon: "activity" },
      ]},
      { section: "Pessoas", items: [
        { v: "notificacoes", label: "Notificações", icon: "bell", count: "4" },
      ]},
      { section: "Conta", items: [
        { v: "logs", label: "Logs", icon: "logs" },
        { v: "config", label: "Configurações", icon: "settings" },
      ]},
    ],
  };

  const ICONS = {
    grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    building: '<path d="M4 21V6a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v15"/><path d="M14 10h4a2 2 0 0 1 2 2v9"/><path d="M8 8h2M8 12h2M8 16h2"/>',
    users: '<circle cx="12" cy="8" r="4"/><path d="M4.5 21a7.5 7.5 0 0 1 15 0"/>',
    card: '<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M2 10h20"/>',
    bell: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.9 1.9 0 0 0 3.4 0"/>',
    logs: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M3 15h18M12 4v16"/>',
    activity: '<path d="M3 12h4l3 8 4-16 3 8h4"/>',
    shield: '<path d="M12 2 3 6v6c0 5 3.8 8.5 9 10 5.2-1.5 9-5 9-10V6Z"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
    spark: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6.3 6.3l2.8 2.8M14.9 14.9l2.8 2.8M17.7 6.3l-2.8 2.8M9.1 14.9l-2.8 2.8"/>',
    yt: '<rect x="2" y="5" width="20" height="14" rx="4"/><path d="M10 9.5v5l4.5-2.5Z" fill="currentColor" stroke="none"/>',
    chev: '<path d="m6 9 6 6 6-6"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5 19 19M19 5l-1.5 1.5M6.5 17.5 5 19"/>',
    moon: '<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5Z"/>',
  };
  const ic = n => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICONS[n] || ""}</svg>`;

  /* ── estado ─────────────────────────────────── */
  const saved = (() => { try { return JSON.parse(localStorage.getItem(LS)) || {}; } catch (e) { return {}; } })();
  const S = Object.assign({
    view: "overview", persona: "super", org: "all", ws: "AI Platform",
    period: "30d", env: "Produção", theme: "light", soon: null,
  }, saved);
  const save = () => { try { localStorage.setItem(LS, JSON.stringify(S)); } catch (e) {} };
  const scope = () => ({ org: S.org, period: S.period, env: S.env, cfgTab: S.cfgTab });

  document.documentElement.setAttribute("data-theme", S.theme);

  /* ── sidebar ────────────────────────────────── */
  function renderSidebar() {
    const groups = NAV[S.persona];
    const who = S.persona === "super" ? ["Avery Chen", "Super Admin · Plataforma", "AC"] : ["Samuel Yossef", "Org Admin · Copyxyz", "SY"];
    document.getElementById("sidebar-root").innerHTML = `
      <div class="sb-brand"><span class="brand-label"></span>
        <button class="icon-btn sb-collapse" id="sbCollapse" aria-label="Recolher menu">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/></svg></button>
      </div>
      <div class="sb-scroll">
        ${groups.map(g => `
          <div class="sb-section"><span class="sb-label">${g.section}</span></div>
          <div class="sb-nav">${g.items.map(it => `
            <button class="sb-item ${it.v === S.view || (S.view === "soon" && it.v === "soon:" + S.soon) ? "is-active" : ""}" data-view="${it.v}" title="${it.label}">
              ${ic(it.icon)}<span class="sb-label">${it.label}</span>${it.v === "notificacoes" ? (D.NOTIFICATIONS.filter(n => !n.read).length ? `<span class="count">${D.NOTIFICATIONS.filter(n => !n.read).length}</span>` : "") : (it.count ? `<span class="count">${it.count}</span>` : "")}
            </button>`).join("")}</div>`).join("")}
        </div>
      </div>
      <div class="sb-foot"><span class="avatar">${who[2]}</span>
        <div class="sb-label"><div class="who">${who[0]}</div><div class="plan">${who[1]}</div></div></div>`;
  }

  /* ── topbar ─────────────────────────────────── */
  function renderTopbar() {
    const org = D.ORGS.find(o => o.id === S.org);
    document.getElementById("topbar-root").innerHTML = `
      <button class="icon-btn menu-toggle" id="menuToggle" aria-label="Menu"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><path d="M3 6h18M3 12h18M3 18h18"/></svg></button>
      <div class="ctx" hidden>
        <button class="ctx-btn" data-pop="orgPop"><span class="org-dot" style="background:${org.color}">${org.short}</span>${org.id === "all" ? "Todas as orgs" : org.name}${ic("chev")}</button>
        <div class="pop" id="orgPop"><div class="pop-label">Organização</div>
          ${D.ORGS.map(o => `<button class="pop-item ${o.id === S.org ? "is-on" : ""}" data-set="org" data-val="${o.id}">
            <span class="org-dot" style="background:${o.color}">${o.short}</span>
            <span>${o.id === "all" ? "Todas as organizações" : o.name}<div class="sub">${o.plan}</div></span><span class="tick">✓</span></button>`).join("")}</div>
      </div>
      <span class="ctx-sep" hidden>/</span>
      <div class="ctx" hidden>
        <button class="ctx-btn" data-pop="wsPop">${S.ws}${ic("chev")}</button>
        <div class="pop" id="wsPop"><div class="pop-label">Workspace</div>
          ${D.WORKSPACES.map(w => `<button class="pop-item ${w === S.ws ? "is-on" : ""}" data-set="ws" data-val="${w}">${w}<span class="tick">✓</span></button>`).join("")}</div>
      </div>
      <div class="top-right">
        <span class="top-pill"><span class="dot"></span> ${S.env.toLowerCase()}</span>
        ${D.__health ? `<span class="top-pill"><span class="dot" style="background:${D.__health.status === "ok" ? "var(--ok)" : D.__health.status === "degraded" ? "var(--warn)" : "var(--err)"}"></span> Gateway: ${D.__health.status === "ok" ? "online" : D.__health.status === "degraded" ? "parcial" : "offline"}</span>` : ""}
        <span class="top-pill">LLM: <b style="font-weight:600">Claude Sonnet 4</b></span>
        <span class="top-pill"><span class="dot" style="background:var(--ok)"></span> Tarefa: Concluída</span>
        <button class="icon-btn" id="themeBtn" aria-label="Alternar tema">${ic(S.theme === "dark" ? "sun" : "moon")}</button>
        <div class="ctx">
          <button class="icon-btn" data-pop="notifPop" aria-label="Notificações">${ic("bell")}</button>
          <div class="pop to-right" id="notifPop" style="min-width:320px">
            <div class="pop-label">Notificações</div>
            ${D.ALERTS.slice(0, 5).map(a => `<button class="pop-item" data-goto="${a.view}">
              <span class="sev-dot ${a.sev}" style="margin-top:5px"></span>
              <span><span style="color:var(--ink)">${a.title}</span><div class="sub">${a.org} · ${a.time}</div></span></button>`).join("")}
          </div>
        </div>
      </div>`;
  }

  /* ── main ───────────────────────────────────── */
  function renderView() {
    const head = document.getElementById("viewHead");
    if (S.view === "perfil") {
      const u = D.USERS.find(x => x.email === S.profileUser) || D.USERS[0];
      const p = V.perfilUsuario(u, S.profileTab, !!S.editing);
      head.innerHTML = p.header;
      document.getElementById("viewBody").innerHTML = p.body;
      afterRender(); return;
    }
    if (S.view === "org" || S.view === "orgnew") {
      const isNew = S.view === "orgnew";
      if (isNew && !draft) draft = V.newOrgRec();
      const rec = isNew ? draft : V.orgRec(S.orgName || D.CUSTOMERS[0][0]);
      const p = V.organizacao(rec, S.orgTab, isNew || !!S.orgEditing, isNew);
      head.innerHTML = p.header;
      document.getElementById("viewBody").innerHTML = p.body;
      afterRender(); return;
    }
    if (S.view === "canal" || S.view === "canalnovo") {
      const isNew = S.view === "canalnovo";
      if (isNew && !draftCh) draftCh = V.newCanalRec();
      const rec = isNew ? draftCh : V.canalRec(S.channelId);
      if (!rec) { S.view = "clientes"; save(); renderView(); return; } // disconnected, or list not loaded yet after a reload
      const p = V.canal(rec, S.chTab, isNew || !!S.chEditing, isNew);
      head.innerHTML = p.header;
      document.getElementById("viewBody").innerHTML = p.body;
      afterRender(); return;
    }
    if (S.view === "soon") {
      head.innerHTML = `<div><div class="view-hd-title">${S.soon}</div><div class="view-hd-sub">Área mapeada na arquitetura, ainda não construída neste protótipo.</div></div>`;
      document.getElementById("viewBody").innerHTML = `
        <div class="card" style="margin-top:20px"><div class="empty">
          <div class="empty-t">${S.soon} entra aqui</div>
          <div class="empty-s">O shell, o escopo global e os componentes já estão prontos — esta tela usa a mesma estrutura das demais.</div>
          <button class="btn" data-view="overview">Voltar para a visão geral</button>
        </div></div>`;
      afterRender(); return;
    }
    const v = VIEWS[S.view] || VIEWS.overview;
    head.innerHTML = S.view === "clientes" ? V.canaisHead() : `<div><div class="view-hd-title">${v.title}</div><div class="view-hd-sub">${v.sub}</div></div>`;
    document.getElementById("viewBody").innerHTML = v.render(scope());
    afterRender();
  }

  function go(view) {
    if (view.startsWith("soon:")) { S.soon = view.slice(5); S.view = "soon"; }
    else { S.view = view; }
    save(); closePops(); renderSidebar(); renderView();
    document.body.classList.remove("sidebar-open");
    document.querySelector(".view-body-scroll").scrollTop = 0;
  }
  function refresh() { save(); renderTopbar(); renderView(); bindTopbar(); }
  D.__refresh = refresh; // lets moneyprinter-runtime.js repaint after syncing real video data
  D.__go = go; // lets moneyprinter-runtime.js jump to Visão geral once a video finishes
  D.__refreshTopbar = () => { renderTopbar(); bindTopbar(); }; // topbar-only repaint, keeps form fields intact

  /* ── popovers ───────────────────────────────── */
  function closePops() { document.querySelectorAll(".pop.is-open").forEach(p => p.classList.remove("is-open")); }
  document.addEventListener("click", e => {
    const trig = e.target.closest("[data-pop]");
    if (trig) {
      const el = document.getElementById(trig.dataset.pop);
      const open = el.classList.contains("is-open");
      closePops(); if (!open) el.classList.add("is-open");
      e.stopPropagation(); return;
    }
    if (!e.target.closest(".pop") && !e.target.closest(".gsearch")) { closePops(); hideRes(); }
  });

  /* ── delegação global de ações ──────────────── */
  document.addEventListener("click", e => {
    const t = e.target;
    const set = t.closest("[data-set]");
    if (set) { S[set.dataset.set] = set.dataset.val; closePops(); refresh(); return; }
    if (t.closest("[data-reset]")) { S.org = "all"; S.period = "30d"; S.env = "Produção"; refresh(); return; }
    const per = t.closest("[data-persona]");
    if (per) { S.persona = per.dataset.persona; S.view = "overview"; closePops(); save(); renderSidebar(); renderView(); return; }
    const nav = t.closest("[data-view]"); if (nav) { go(nav.dataset.view); return; }
    const goto = t.closest("[data-goto]"); if (goto) { go(goto.dataset.goto); return; }
    const copy = t.closest("[data-copy]");
    if (copy) {
      closePops();
      navigator.clipboard.writeText(location.origin + copy.dataset.copy).then(() => showToast("Link do vídeo copiado"), () => showToast("Não foi possível copiar o link"));
      return;
    }
    const toast = t.closest("[data-toast]"); if (toast) { showToast(toast.dataset.toast); return; }
    if (t.closest("#themeBtn")) {
      S.theme = S.theme === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", S.theme);
      save(); renderTopbar(); bindTopbar(); return;
    }
    if (t.closest("#sbCollapse")) { document.body.classList.toggle("sidebar-collapsed"); return; }
    if (t.closest("#menuToggle")) { document.body.classList.toggle("sidebar-open"); return; }
    if (t.closest(".sb-backdrop")) { document.body.classList.remove("sidebar-open"); return; }
    if (t.closest(".dr-backdrop") || t.closest("#drClose")) { closeDrawer(); return; }
    if (t.closest("#newOrgBtn")) { draft = V.newOrgRec(); S.orgTab = "dados"; go("orgnew"); return; }
    if (t.closest("#newChBtn")) { draftCh = V.newCanalRec(); S.chTab = "dados"; go("canalnovo"); return; }
    const oo = t.closest("[data-open-org]");
    if (oo) { e.preventDefault(); openOrgPage(oo.dataset.openOrg); return; }
    const ot = t.closest("[data-otab]");
    if (ot) { collectOrg(currentOrg()); S.orgTab = ot.dataset.otab; save(); renderView(); return; }
    const eo = t.closest("[data-edit-org]");
    if (eo) { S.orgName = eo.dataset.editOrg; S.orgEditing = 1; snapshotOrg(); save(); renderView(); return; }
    if (t.closest("[data-save-org]")) { saveOrg(); return; }
    if (t.closest("[data-cancel-org]")) {
      if (S.view === "orgnew") { draft = null; go("clientes"); return; }
      restoreOrg(); S.orgEditing = 0; save(); renderView(); return;
    }
    if (t.closest("[data-create-org]")) { createOrg(); return; }
    const oact = t.closest("[data-org-act]"); if (oact) { orgAction(oact.dataset.orgAct); return; }
    const chr = t.closest("[data-ch-row]"); if (chr) { openChannelPage(chr.dataset.chRow); return; }
    const cht = t.closest("[data-chtab]");
    if (cht) { if (S.chEditing) collectChannel(currentChannel()); S.chTab = cht.dataset.chtab; save(); renderView(); return; }
    const ech = t.closest("[data-edit-ch]");
    if (ech) { S.chEditing = 1; snapshotChannel(); save(); renderView(); return; }
    if (t.closest("[data-save-ch]")) { saveChannel(); return; }
    if (t.closest("[data-cancel-ch]")) {
      if (S.view === "canalnovo") { draftCh = null; go("clientes"); return; }
      restoreChannel(); S.chEditing = 0; save(); renderView(); return;
    }
    if (t.closest("[data-create-ch]")) { createChannel(); return; }
    const chact = t.closest("[data-ch-act]"); if (chact) { channelAction(chact.dataset.chAct); return; }
    if (t.closest("[data-test-prov]")) { testProvider(); return; }
    if (t.closest("[data-clear-key]")) {
      const r = V.orgRec(S.orgName); r.providerKey = ""; r.providerTest = "";
      logAudit(r, "removeu a chave de API do provedor"); save(); renderView(); showToast("Chave removida"); return;
    }
    if (t.closest("[data-ws-add]")) { const r = document.getElementById("wsNewRow"); if (r) { r.hidden = false; const e = document.getElementById("wsEmpty"); if (e) e.hidden = true; document.getElementById("wsNewName").focus(); } return; }
    if (t.closest("[data-ws-cancel]")) { const r = document.getElementById("wsNewRow"); if (r) { r.hidden = true; const e = document.getElementById("wsEmpty"); if (e) e.hidden = false; } return; }
    if (t.closest("[data-ws-save]")) { addWorkspace(); return; }
    const wsd = t.closest("[data-ws-del]");
    if (wsd) {
      const r = V.orgRec(S.orgName), i = +wsd.dataset.wsDel, w = r.workspaces[i];
      r.workspaces.splice(i, 1); logAudit(r, `excluiu o workspace ${w.name}`); save(); renderView(); showToast(`Workspace "${w.name}" excluído`); return;
    }
    const wsr = t.closest("[data-ws-rename]");
    if (wsr) {
      const r = V.orgRec(S.orgName), i = +wsr.dataset.wsRename;
      const name = prompt("Novo nome do workspace:", r.workspaces[i].name);
      if (name && name.trim()) { logAudit(r, `renomeou ${r.workspaces[i].name} para ${name.trim()}`); r.workspaces[i].name = name.trim(); save(); renderView(); showToast("Workspace renomeado"); }
      return;
    }
    const ut = t.closest("[data-user-toggle]");
    if (ut) {
      e.stopPropagation();
      const u = D.USERS.find(x => x.email === ut.dataset.userToggle);
      u.status = u.status === "canceled" ? "active" : "canceled";
      const r = V.orgRec(S.orgName); logAudit(r, `${u.status === "canceled" ? "desativou" : "reativou"} ${u.name}`);
      save(); renderView(); showToast(`${u.name} ${u.status === "canceled" ? "desativado" : "reativado"}`); return;
    }
    const oi = t.closest("[data-open-invoice]");
    if (oi) { showToast(`Fatura ${oi.dataset.openInvoice} — PDF gerado`); return; }
    const op = t.closest("[data-open-profile]");
    if (op) { closeDrawer(); S.profileUser = op.dataset.openProfile; S.profileTab = "perfil"; S.view = "perfil"; S.editing = 0; save(); renderSidebar(); renderView(); return; }
    const pt = t.closest("[data-ptab]");
    if (pt) { if (S.editing) collectProfile(); S.profileTab = pt.dataset.ptab; save(); renderView(); return; }
    const ep = t.closest("[data-edit-profile]");
    if (ep) { S.editing = 1; S.profileUser = ep.dataset.editProfile; snapshotUser(); save(); renderView(); return; }
    if (t.closest("[data-save-profile]")) { collectProfile(); S.editing = 0; save(); renderView(); showToast("Perfil e permissões salvos"); return; }
    if (t.closest("[data-cancel-profile]")) { restoreUser(); S.editing = 0; save(); renderView(); return; }
  });

  /* ── drawer ─────────────────────────────────── */
  function openDrawer(cfg) {
    document.getElementById("drHead").innerHTML =
      `<div style="flex:1;min-width:0"><div class="dr-title">${cfg.title}</div><div class="dr-sub">${cfg.sub}</div></div>
       <button class="icon-btn" id="drClose" aria-label="Fechar"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg></button>`;
    document.getElementById("drBody").innerHTML = cfg.body;
    document.getElementById("drFoot").innerHTML = cfg.footer || "";
    document.body.classList.add("drawer-open");
  }
  function closeDrawer() { document.body.classList.remove("drawer-open"); }

  /* ── organização: página, edição e criação ──── */
  let draft = null, orgSnapshot = null, draftCh = null, chSnapshot = null;
  function currentOrg() {
    if (S.view === "orgnew") return draft;
    if (S.view === "org" && S.orgEditing) return V.orgRec(S.orgName);
    return null;
  }
  function openOrgPage(name) {
    closeDrawer(); S.orgName = name; S.orgTab = "dados"; S.orgEditing = 0; go("org");
  }
  function openChannelPage(id) {
    S.channelId = id; S.chTab = "dados"; S.chEditing = 0; go("canal");
  }
  function collectChannel(rec) {
    if (!rec) return;
    document.querySelectorAll("#viewBody [data-cfield]").forEach(el => {
      const f = el.dataset.cfield;
      rec[f] = el.type === "checkbox" ? (el.checked ? 1 : 0) : el.value.trim();
    });
  }
  function currentChannel() {
    if (S.view === "canalnovo") return draftCh;
    if (S.view === "canal" && S.chEditing) return V.canalRec(S.channelId);
    return null;
  }
  function snapshotChannel() { chSnapshot = Object.assign({}, V.canalRec(S.channelId)); }
  function restoreChannel() { const r = V.canalRec(S.channelId); if (r && chSnapshot) Object.assign(r, chSnapshot); }
  function saveChannel() {
    const rec = V.canalRec(S.channelId);
    if (!rec) return;
    collectChannel(rec);
    window.MoneyPrinterRuntime.saveChannel(rec.id, { niche: rec.niche, language: rec.language })
      .then(() => { S.chEditing = 0; save(); renderView(); showToast("Canal salvo"); })
      .catch(error => showToast(error.message));
  }
  function createChannel() {
    collectChannel(draftCh);
    // Opened synchronously inside the click, or the popup blocker eats the login tab.
    const win = window.open("", "_blank");
    window.MoneyPrinterRuntime.connectChannel(draftCh, win)
      .then(() => { draftCh = null; go("clientes"); showToast("Conclua o login na nova aba. O canal aparece aqui quando você voltar."); })
      .catch(error => { if (win) win.close(); showToast(error.message); });
  }
  function channelAction(act) {
    const rec = V.canalRec(S.channelId);
    if (!rec) return;
    if (act === "disconnect" && !confirm(`Desconectar o canal "${rec.name}"? Vídeos já publicados continuam na plataforma.`)) return;
    const win = act === "reconnect" ? window.open("", "_blank") : null;
    window.MoneyPrinterRuntime.channelAction(rec, act, win)
      .then(() => {
        if (act === "disconnect") { go("clientes"); showToast("Canal desconectado"); return; }
        if (act === "reconnect") { showToast("Conclua o login na nova aba."); return; }
        const current = V.canalRec(S.channelId);
        renderView(); showToast(current && current.status === "connected" ? "Canal conectado" : "Canal ainda não está conectado");
      })
      .catch(error => { if (win) win.close(); showToast(error.message); });
  }
  function collectOrg(rec) {
    if (!rec) return;
    document.querySelectorAll("#viewBody [data-ofield]").forEach(el => {
      const f = el.dataset.ofield;
      if (el.type === "radio") { if (el.checked) rec[f] = el.value; return; }
      rec[f] = el.type === "checkbox" ? (el.checked ? 1 : 0) : el.value.trim();
    });
  }
  function snapshotOrg() { const r = V.orgRec(S.orgName); orgSnapshot = Object.assign({}, r); }
  function restoreOrg() { const r = V.orgRec(S.orgName); if (orgSnapshot) Object.assign(r, orgSnapshot); }
  function syncOrgRow(prevName, rec) {
    const row = D.CUSTOMERS.find(c => c[0] === prevName);
    if (row) { row[0] = rec.name; row[1] = rec.plan; row[3] = rec.mrr; row[6] = rec.status; }
    if (prevName !== rec.name && D.ORGREC) { delete D.ORGREC[prevName]; D.ORGREC[rec.name] = rec; }
  }
  function saveOrg() {
    const prev = S.orgName, rec = V.orgRec(prev);
    collectOrg(rec);
    if (!rec.name) { rec.name = prev; }
    syncOrgRow(prev, rec);
    S.orgName = rec.name; S.orgEditing = 0; save(); renderView();
    showToast("Organização salva");
  }
  function createOrg() {
    collectOrg(draft);
    if (!draft.name) {
      S.orgTab = "dados"; renderView();
      const f = document.querySelector('#viewBody [data-ofield="name"]'); if (f) f.focus();
      showToast("Informe o nome da organização"); return;
    }
    const rec = draft;
    D.CUSTOMERS.unshift([rec.name, rec.plan, Number(rec.users) || 1, rec.mrr || "R$ 0", Number(rec.usage) || 0, Number(rec.health) || 100, rec.status || "trialing"]);
    D.ORGREC = D.ORGREC || {}; D.ORGREC[rec.name] = rec;
    D.ORGS.push({ id: rec.slug || rec.name.toLowerCase(), name: rec.name, short: rec.name.slice(0, 2).toUpperCase(), plan: rec.plan, color: "#0f766e" });
    draft = null;
    S.orgName = rec.name; S.orgTab = "dados"; S.orgEditing = 0; go("org");
    showToast(`Organização "${rec.name}" criada`);
  }
  let snapshot = null;
  function stamp() {
    const d = new Date();
    const p = n => String(n).padStart(2, "0");
    return `${p(d.getDate())}/${p(d.getMonth() + 1)} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }
  function logAudit(rec, action) {
    if (!rec) return;
    rec.audit = rec.audit || [];
    rec.audit.unshift([stamp(), "Você (admin)", action, "191.34.20.11"]);
  }
  function addWorkspace() {
    const rec = V.orgRec(S.orgName);
    const name = (document.getElementById("wsNewName").value || "").trim();
    if (!name) { document.getElementById("wsNewName").focus(); showToast("Informe o nome do workspace"); return; }
    rec.workspaces = rec.workspaces || [];
    rec.workspaces.push({ name, owner: rec.owner || "—", users: 0, projects: 0, env: document.getElementById("wsNewEnv").value });
    logAudit(rec, `criou o workspace ${name}`);
    save(); renderView(); showToast(`Workspace "${name}" criado`);
  }
  function testProvider() {
    const rec = V.orgRec(S.orgName);
    if (!rec.providerKey) {
      rec.providerTest = "Falha: nenhuma chave de API configurada.";
      save(); renderView(); showToast("Configure a chave de API primeiro"); return;
    }
    rec.providerTest = `Conexão com ${rec.provider} verificada às ${stamp()} · latência 412 ms`;
    logAudit(rec, `testou a conexão com ${rec.provider}`);
    save(); renderView(); showToast(`Conexão com ${rec.provider} OK`);
  }
  function orgAction(act) {
    const rec = V.orgRec(S.orgName);
    if (act === "rebill") { logAudit(rec, "reprocessou a cobrança do ciclo"); showToast("Reprocessamento de cobrança enviado"); }
    else if (act === "revoke") { logAudit(rec, "revogou todas as sessões da organização"); showToast("Sessões da organização revogadas"); }
    else if (act === "invite") { logAudit(rec, `reenviou convite para ${rec.ownerEmail || "o owner"}`); showToast("Convite reenviado ao owner"); }
    else if (act === "suspend") { rec.status = "canceled"; syncOrgRow(rec.name, rec); logAudit(rec, "suspendeu a organização"); showToast("Organização suspensa"); }
    else if (act === "reactivate") { rec.status = "active"; syncOrgRow(rec.name, rec); logAudit(rec, "reativou a organização"); showToast("Organização reativada"); }
    else if (act === "export") { showToast(`${(rec.audit || []).length} eventos exportados em CSV`); return; }
    save(); renderView();
  }
  function currentUser() { return D.USERS.find(x => x.email === S.profileUser); }
  function snapshotUser() {
    const u = currentUser(); if (!u) return;
    snapshot = Object.assign({}, u, { perms: (u.perms || D.PERMS).map(p => p.slice()) });
  }
  function restoreUser() {
    const u = currentUser(); if (!u || !snapshot) return;
    Object.assign(u, snapshot);
    u.perms = snapshot.perms.map(p => p.slice());
  }
  function collectProfile() {
    const u = currentUser(); if (!u) return;
    document.querySelectorAll("#viewBody [data-field]").forEach(el => {
      const f = el.dataset.field;
      u[f] = el.type === "checkbox" ? el.checked : el.value.trim() || u[f];
    });
    document.querySelectorAll("#viewBody [data-perm]").forEach(el => {
      const [ri, ci] = el.dataset.perm.split("-").map(Number);
      if (u.perms && u.perms[ri]) u.perms[ri][ci + 1] = el.checked ? 1 : 0;
    });
  }
  document.addEventListener("keydown", e => {
    if (e.key === "Escape") { closeDrawer(); closePops(); hideRes(); }
  });

  /* ── toast ──────────────────────────────────── */
  function showToast(msg) {
    const wrap = document.getElementById("toasts");
    const el = document.createElement("div");
    el.className = "toast"; el.innerHTML = `<span class="t-dot"></span>${msg}`;
    wrap.appendChild(el);
    setTimeout(() => { el.style.transition = "opacity .2s"; el.style.opacity = "0"; setTimeout(() => el.remove(), 220); }, 2600);
  }

  /* ── busca global ───────────────────────────── */
  function hideRes() { const r = document.getElementById("gres"); if (r) r.classList.remove("is-open"); }
  function bindTopbar() {
    const q = document.getElementById("gq"), res = document.getElementById("gres");
    if (!q) return;
    const run = () => {
      const s = q.value.trim().toLowerCase();
      if (!s) { hideRes(); return; }
      const users = D.USERS.filter(u => (u.name + u.email + u.role).toLowerCase().includes(s)).slice(0, 4);
      const orgs = D.ORGS.filter(o => o.id !== "all" && o.name.toLowerCase().includes(s)).slice(0, 3);
      const invs = D.INVOICES.filter(i => (i.id + i.org).toLowerCase().includes(s)).slice(0, 3);
      const logs = D.LOGS.filter(l => (l.msg + l.svc + l.trace).toLowerCase().includes(s)).slice(0, 4);
      let html = "";
      if (users.length) html += `<div class="gres-group">Usuários</div>` + users.map(u =>
        `<div class="gres-item" data-search-user="${u.email}"><span class="u-avatar" style="width:24px;height:24px;border-radius:50%;display:grid;place-items:center;font-size:10px;font-weight:600;color:#fff;background:${u.color}">${u.name.split(" ").map(w => w[0]).slice(0, 2).join("")}</span>
          <span><span class="gr-name">${u.name}</span><div class="gr-sub">${u.email} · ${u.org}</div></span><span class="gr-kind">usuário</span></div>`).join("");
      if (orgs.length) html += `<div class="gres-group">Organizações</div>` + orgs.map(o =>
        `<div class="gres-item" data-search-org="${o.name}"><span class="org-dot" style="background:${o.color}">${o.short}</span>
          <span><span class="gr-name">${o.name}</span><div class="gr-sub">${o.plan}</div></span><span class="gr-kind">org</span></div>`).join("");
      if (invs.length) html += `<div class="gres-group">Faturas</div>` + invs.map(i =>
        `<div class="gres-item" data-goto="billing"><span><span class="gr-name">${i.id}</span><div class="gr-sub">${i.org} · ${i.amount}</div></span><span class="gr-kind">fatura</span></div>`).join("");
      if (logs.length) html += `<div class="gres-group">Logs</div>` + logs.map(l =>
        `<div class="gres-item" data-goto="logs"><span><span class="gr-name">${l.msg}</span><div class="gr-sub">${l.svc} · ${l.trace}</div></span><span class="gr-kind">${l.lvl.toLowerCase()}</span></div>`).join("");
      res.innerHTML = html || `<div class="gres-empty">Nada encontrado para “${q.value}”</div>`;
      res.classList.add("is-open");
    };
    q.addEventListener("input", run);
    q.addEventListener("focus", run);
    res.addEventListener("click", e => {
      const u = e.target.closest("[data-search-user]");
      if (u) { hideRes(); q.value = ""; openDrawer(V.userDrawer(D.USERS.find(x => x.email === u.dataset.searchUser))); return; }
      const o = e.target.closest("[data-search-org]");
      if (o) { hideRes(); q.value = ""; openOrgPage(o.dataset.searchOrg); return; }
      hideRes(); q.value = "";
    });
  }

  /* ── comportamentos por view ────────────────── */
  function afterRender() {
    const body = document.getElementById("viewBody");
    body.querySelectorAll("[data-ws-env]").forEach(s => s.addEventListener("change", () => {
      const r = V.orgRec(S.orgName), i = +s.dataset.wsEnv;
      r.workspaces[i].env = s.value; logAudit(r, `alterou o ambiente de ${r.workspaces[i].name} para ${s.value}`);
      save(); showToast("Ambiente atualizado");
    }));
    body.querySelectorAll('[data-ofield="provider"]').forEach(r => r.addEventListener("change", () => {
      const rec = currentOrg(); if (!rec) return;
      collectOrg(rec); rec.provider = r.value; rec.providerModel = ""; rec.providerFallback = "Nenhum"; rec.providerBase = "";
      renderView();
    }));

    /* alertas: filtro por severidade */
    const tabs = body.querySelector("#sevTabs");
    if (tabs) {
      const org = D.ORGS.find(o => o.id === S.org);
      const list = D.ALERTS.filter(a => S.org === "all" || a.org === org.name || a.org === "Plataforma");
      tabs.addEventListener("click", e => {
        const b = e.target.closest(".sev-tab"); if (!b) return;
        tabs.querySelectorAll(".sev-tab").forEach(x => x.classList.remove("is-on"));
        b.classList.add("is-on");
        body.querySelector("#alertList").innerHTML = V.alertRows(list, b.dataset.sev);
        e.stopPropagation();
      }, true);
    }

    /* configurações: abas */
    const cfgTabs = body.querySelector("#cfgTabs");
    if (cfgTabs) {
      cfgTabs.addEventListener("click", e => {
        const b = e.target.closest("[data-cfgtab]"); if (!b) return;
        S.cfgTab = b.dataset.cfgtab; save(); renderView();
      });
    }
    /* notificações: marcar como lida / marcar todas */
    const notifBody = body.querySelector("#notifBody");
    if (notifBody) {
      notifBody.addEventListener("click", e => {
        const row = e.target.closest("[data-notif]"); if (!row) return;
        const n = D.NOTIFICATIONS.find(x => x.id === +row.dataset.notif);
        if (n && !n.read) { n.read = true; renderSidebar(); renderView(); }
      });
      const allBtn = body.querySelector("#notifReadAll");
      if (allBtn) allBtn.addEventListener("click", e => {
        e.stopPropagation();
        D.NOTIFICATIONS.forEach(n => n.read = true);
        renderSidebar(); renderView(); showToast("Todas as notificações marcadas como lidas");
      });
    }

    /* Gerador de vídeo real, via Gateway same-origin. */
    const chipBtns = body.querySelectorAll("[data-chip]");
    if (chipBtns.length) {
      const topicInput = body.querySelector("#genTopicInput");
      chipBtns.forEach(b => b.addEventListener("click", () => {
        chipBtns.forEach(x => x.classList.remove("primary"));
        b.classList.add("primary");
        if (topicInput) topicInput.value = b.dataset.chip;
      }));
    }
    if (window.MoneyPrinterRuntime) window.MoneyPrinterRuntime.bindGenerator(body, showToast);
    if (window.MoneyPrinterRuntime) window.MoneyPrinterRuntime.bindConfigSettings(body, showToast);
    if (window.MoneyPrinterRuntime) window.MoneyPrinterRuntime.bindYoutubeLibraryPublish(body, showToast);
    if (window.MoneyPrinterRuntime) window.MoneyPrinterRuntime.bindAgents(body, showToast);

    /* vídeos recentes: filtro por formato + modo de visualização + filtro avançado */
    const vidTabs = body.querySelector("#vidTabs"), vidView = body.querySelector("#vidView");
    if (vidTabs) {
      const curFmt = () => vidTabs.querySelector(".sev-tab.is-on").dataset.vidfmt;
      const curView = () => vidView ? vidView.querySelector(".sev-tab.is-on").dataset.vidview : "grid";
      const chSel = body.querySelector("#vidFilterChannel"), plSel = body.querySelector("#vidFilterPlatform"), tiSel = body.querySelector("#vidFilterTitle");
      const curFilt = () => ({ channel: chSel ? chSel.value : "", platform: plSel ? plSel.value : "", title: tiSel ? tiSel.value : "" });
      const rerender = () => { body.querySelector("#vidBody").innerHTML = V.videoGrids(curFmt(), curView(), curFilt()); };
      vidTabs.addEventListener("click", e => {
        const b = e.target.closest(".sev-tab"); if (!b) return;
        vidTabs.querySelectorAll(".sev-tab").forEach(x => x.classList.remove("is-on"));
        b.classList.add("is-on");
        rerender();
        e.stopPropagation();
      }, true);
      if (vidView) vidView.addEventListener("click", e => {
        const b = e.target.closest(".sev-tab"); if (!b) return;
        vidView.querySelectorAll(".sev-tab").forEach(x => x.classList.remove("is-on"));
        b.classList.add("is-on");
        rerender();
        e.stopPropagation();
      }, true);
      const advBtn = body.querySelector("#vidAdvBtn"), advPanel = body.querySelector("#vidAdvPanel");
      if (advBtn) advBtn.addEventListener("click", () => {
        advPanel.style.display = advPanel.style.display === "none" ? "flex" : "none";
        advBtn.classList.toggle("primary", advPanel.style.display !== "none");
      });
      if (chSel) chSel.addEventListener("change", rerender);
      if (plSel) plSel.addEventListener("change", rerender);
      if (tiSel) tiSel.addEventListener("input", rerender);
      const clearBtn = body.querySelector("#vidFilterClear");
      if (clearBtn) clearBtn.addEventListener("click", () => { if (chSel) chSel.value = ""; if (plSel) plSel.value = ""; if (tiSel) tiSel.value = ""; rerender(); });

      const vidBody = body.querySelector("#vidBody"), bulkBar = body.querySelector("#vidBulkBar");
      function syncVidBulk() {
        const boxes = [...vidBody.querySelectorAll(".vid-cbx")];
        const n = boxes.filter(b => b.checked).length;
        bulkBar.classList.toggle("is-on", n > 0);
        const cnt = body.querySelector("#vidBbCount"); if (cnt) cnt.textContent = n + (n === 1 ? " selecionado" : " selecionados");
        const selAll = vidBody.querySelector("#vidSelAll"); if (selAll) selAll.checked = n > 0 && n === boxes.length;
      }
      vidBody.addEventListener("change", e => {
        if (e.target.id === "vidSelAll") { vidBody.querySelectorAll(".vid-cbx").forEach(b => { b.checked = e.target.checked; }); syncVidBulk(); return; }
        if (e.target.classList.contains("vid-cbx")) syncVidBulk();
      });
      vidBody.addEventListener("click", async e => {
        const del = e.target.closest("[data-vid-del-kind]");
        if (del) {
          const entry = D.VIDEOS[del.dataset.vidDelKind][+del.dataset.vidDelIdx];
          const jobId = entry && entry[4];
          if (jobId && window.MoneyPrinterRuntime) {
            try { await window.MoneyPrinterRuntime.deleteJob(jobId); showToast("Vídeo excluído"); }
            catch (error) { showToast(error.message); }
          } else {
            D.VIDEOS[del.dataset.vidDelKind].splice(+del.dataset.vidDelIdx, 1);
            rerender();
            showToast("Vídeo excluído");
          }
        }
      });
      const delBtn = body.querySelector("#vidDeleteBtn");
      if (delBtn) delBtn.addEventListener("click", async () => {
        const boxes = [...vidBody.querySelectorAll(".vid-cbx:checked")];
        if (!boxes.length) return;
        const jobIds = boxes.map(b => { const e = D.VIDEOS[b.dataset.vidKind][+b.dataset.vidIdx]; return e && e[4]; }).filter(Boolean);
        if (jobIds.length && window.MoneyPrinterRuntime) {
          try { await Promise.all(jobIds.map(id => window.MoneyPrinterRuntime.deleteJob(id))); showToast(jobIds.length + " vídeo(s) excluído(s)"); }
          catch (error) { showToast(error.message); }
          return;
        }
        const byKind = { vert: [], horiz: [] };
        boxes.forEach(b => byKind[b.dataset.vidKind].push(+b.dataset.vidIdx));
        Object.keys(byKind).forEach(k => byKind[k].sort((a, b) => b - a).forEach(i => D.VIDEOS[k].splice(i, 1)));
        const n = boxes.length;
        rerender();
        showToast(`${n} ${n === 1 ? "vídeo excluído" : "vídeos excluídos"}`);
      });
    }
    const uBody = body.querySelector("#uBody");
    if (uBody) {
      const org = D.ORGS.find(o => o.id === S.org);
      const base = D.USERS.filter(u => S.org === "all" || u.org === org.name.replace(" Organization", ""));
      const q = body.querySelector("#uSearch"), rl = body.querySelector("#uRole"), st = body.querySelector("#uStatus");
      const apply = () => {
        const s = (q.value || "").toLowerCase();
        const rows = base.filter(u =>
          (!s || (u.name + u.email + u.role).toLowerCase().includes(s)) &&
          (rl.value.startsWith("Todos") || u.role === rl.value) &&
          (st.value.startsWith("Todos") || u.status === st.value));
        uBody.innerHTML = V.userRows(rows);
        body.querySelector("#uCount").textContent = rows.length + (rows.length === 1 ? " usuário" : " usuários");
        syncBulk();
      };
      [q, rl, st].forEach(el => el.addEventListener("input", apply));
      const selAll = body.querySelector("#selAll");
      function syncBulk() {
        const boxes = [...uBody.querySelectorAll(".row-cbx")];
        const n = boxes.filter(b => b.checked).length;
        boxes.forEach(b => b.closest("tr").classList.toggle("is-sel", b.checked));
        const bar = body.querySelector("#bulkbar");
        bar.classList.toggle("is-on", n > 0);
        body.querySelector("#bbCount").textContent = n + (n === 1 ? " usuário selecionado" : " usuários selecionados");
        selAll.checked = n > 0 && n === boxes.length;
      }
      selAll.addEventListener("change", () => {
        uBody.querySelectorAll(".row-cbx").forEach(b => { b.checked = selAll.checked; });
        syncBulk();
      });
      uBody.addEventListener("click", e => {
        if (e.target.classList.contains("row-cbx")) { syncBulk(); return; }
        const tr = e.target.closest("[data-user]"); if (!tr) return;
        openDrawer(V.userDrawer(D.USERS.find(u => u.email === tr.dataset.user)));
      });
      body.querySelector("#bulkbar").addEventListener("click", e => {
        const b = e.target.closest("[data-bulk]"); if (!b) return;
        const n = uBody.querySelectorAll(".row-cbx:checked").length;
        showToast(`${b.dataset.bulk} — ${n} ${n === 1 ? "usuário" : "usuários"}`);
        uBody.querySelectorAll(".row-cbx").forEach(x => { x.checked = false; });
        syncBulk();
      });
    }

    /* logs: filtros e drawer */
    const logBody = body.querySelector("#logBody");
    if (logBody) {
      const q = body.querySelector("#lSearch"), lv = body.querySelector("#lLevel"), sv = body.querySelector("#lSvc");
      const apply = () => {
        const s = (q.value || "").toLowerCase();
        const rows = D.LOGS.filter(l =>
          (!s || (l.msg + l.svc + l.trace + l.org).toLowerCase().includes(s)) &&
          (lv.value.startsWith("Todos") || l.lvl === lv.value) &&
          (sv.value.startsWith("Todos") || l.svc === sv.value));
        logBody.innerHTML = V.logRows(rows);
        logBody.dataset.map = rows.map(r => D.LOGS.indexOf(r)).join(",");
        body.querySelector("#lCount").textContent = rows.length + (rows.length === 1 ? " linha" : " linhas");
      };
      [q, lv, sv].forEach(el => el.addEventListener("input", apply));
      logBody.addEventListener("click", e => {
        const r = e.target.closest("[data-log]"); if (!r) return;
        const map = logBody.dataset.map ? logBody.dataset.map.split(",").map(Number) : null;
        const idx = map ? map[+r.dataset.log] : +r.dataset.log;
        openDrawer(V.logDrawer(D.LOGS[idx]));
      });
    }

    /* clientes: drawer por organização */
    const orgRows = body.querySelectorAll("[data-org-row]");
    orgRows.forEach(tr => tr.addEventListener("click", () => openOrgPage(tr.dataset.orgRow)));

    /* aprovações */
    const grid = body.querySelector("#aprGrid");
    if (grid) {
      grid.addEventListener("click", e => {
        const a = e.target.closest("[data-approve]"), r = e.target.closest("[data-reject]"), rv = e.target.closest("[data-review]");
        const card = e.target.closest(".apr"); if (!card) return;
        if (rv) { const d = D.APPROVALS[+card.dataset.apr]; openDrawer({
          title: d.agent, sub: d.action,
          body: `<div class="dr-sec">Solicitação</div><dl class="kv">
              <dt>Solicitado por</dt><dd>${d.by}</dd><dt>Risco</dt><dd>${d.risk === "low" ? "Baixo" : d.risk === "med" ? "Médio" : "Alto"}</dd>
              <dt>Custo estimado</dt><dd class="mono">${d.cost}</dd><dt>Idade</dt><dd>${d.when}</dd></dl>
            <div class="dr-sec">Ferramentas envolvidas</div><div class="scope-tags"><span class="tag is-on">send_email</span><span class="tag">read_documents</span><span class="tag">search_web</span></div>
            <div class="dr-sec">Política aplicada</div><div style="font-size:13px;color:var(--ink-2)">Ações externas exigem aprovação humana quando o risco é médio ou alto, ou quando o custo estimado passa de US$ 1,00.</div>`,
          footer: `<button class="btn primary" data-toast="Solicitação aprovada">Aprovar</button><button class="btn danger" data-toast="Solicitação rejeitada">Rejeitar</button>`,
        }); return; }
        if (a || r) {
          card.classList.add("is-done");
          card.querySelector(".apr-actions").innerHTML = `<span class="card-note">${a ? "Aprovado" : "Rejeitado"} · há instantes</span>`;
          showToast(a ? "Solicitação aprovada — agente notificado" : "Solicitação rejeitada");
          const left = grid.querySelectorAll(".apr:not(.is-done)").length;
          body.querySelector("#aprCount").textContent = left + " pendentes";
        }
      });
    }
  }

  /* ── boot ───────────────────────────────────── */
  renderSidebar(); renderTopbar(); bindTopbar(); renderView();
})();
