/**
 * ============================================================================
 * Lattice Admin — Renderizador Compartilhado de Shell (Sidebar + Topbar)
 * ============================================================================
 * Utilizado pelas páginas administrativas (Activity.html, Users.html, Logs.html, etc.)
 * para manter a navegação e layout uniformes.
 *
 * Exemplo de uso em cada página:
 *   renderShell({ active: 'users', crumb: 'Users' });
 * ============================================================================
 */
(function () {
  /**
   * Catálogo de ícones SVG em linha (evita requisições adicionais de fontes de ícones)
   */
  const ICONS = {
    activity: '<path d="M3 12h4l3 8 4-16 3 8h4"/>',
    logs: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M3 15h18M12 4v16"/>',
    users: '<circle cx="12" cy="8" r="4"/><path d="M4.5 21a7.5 7.5 0 0 1 15 0"/>',
    card: '<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M2 10h20"/>',
    bell: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.9 1.9 0 0 0 3.4 0"/>',
    invoice: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 17h6"/>',
    gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 13.5a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-2.88 1.2V21a2 2 0 1 1-4 0v-.09A1.7 1.7 0 0 0 7 19.4a1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 2.6 14H2.5a2 2 0 1 1 0-4h.09A1.7 1.7 0 0 0 4 7a1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.7 1.7 0 0 0 8.5 2.6h.09A1.7 1.7 0 0 0 10 1.5V1.5a2 2 0 1 1 4 0v.09A1.7 1.7 0 0 0 16.87 4l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.7 1.7 0 0 0 21.4 9.5h.1a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.01 0Z"/>',
    chev: '<path d="m6 9 6 6 6-6"/>',
  };

  /**
   * Helper para renderizar tag SVG com o caminho do ícone desejado
   */
  function icon(name) {
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] || ""}</svg>`;
  }

  /**
   * Estrutura de navegação da barra lateral do Admin
   * Dividida em seções: Overview, People, Billing, Account
   */
  const NAV = [
    { section: "Overview", items: [
      { id: "activity", label: "Activity", href: "Activity.html", icon: "activity" },
      { id: "logs", label: "Logs", href: "Logs.html", icon: "logs" },
    ]},
    { section: "People", items: [
      { id: "users", label: "Users", href: "Users.html", icon: "users", count: "1,284" },
      { id: "subscribers", label: "Subscribers", href: "Subscribers.html", icon: "card", count: "412" },
      { id: "invitations", label: "Invitations", href: "Invitations.html", icon: "bell" },
    ]},
    { section: "Billing", items: [
      { id: "plans", label: "Plans", href: "Plans.html", icon: "card" },
      { id: "invoices", label: "Invoices", href: "Invoices.html", icon: "invoice" },
    ]},
    { section: "Account", items: [
      { id: "settings", label: "Settings", href: "Settings.html", icon: "gear" },
    ]},
  ];

  /**
   * Renderiza os elementos visuais da casca (Shell): Sidebar e Topbar
   * @param {Object} opts { active: 'id_da_tela', crumb: 'Nome para o Breadcrumb' }
   */
  function renderShell(opts) {
    const { active, crumb } = opts;

    // 1. Template da Barra Lateral (Sidebar)
    const sidebarHtml = `
      <div class="sb-brand">
        <span class="brand-mark">L</span>
        <span class="brand-label">Lattice Admin</span>
        <button class="icon-btn sb-collapse" id="sbCollapse" aria-label="Recolher menu lateral"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/></svg></button>
      </div>
      <div class="sb-ws">
        <span class="sb-label">Default Workspace</span>
        ${icon("chev")}
      </div>
      <div class="sb-scroll">
        ${NAV.map(group => `
          <div class="sb-section"><span class="sb-label">${group.section}</span></div>
          <div class="sb-nav">
            ${group.items.map(it => `
              <a class="sb-item ${it.id === active ? "is-active" : ""}" href="${it.href}" title="${it.label}">
                ${icon(it.icon)}
                <span class="sb-label">${it.label}</span>
                ${it.count ? `<span class="count">${it.count}</span>` : ""}
              </a>
            `).join("")}
          </div>
        `).join("")}
      </div>
      <div class="sb-foot">
        <span class="avatar">AC</span>
        <div class="sb-label">
          <div class="who">Avery Chen</div>
          <div class="plan">Owner · Enterprise</div>
        </div>
      </div>
    `;

    // 2. Template da Barra Superior (Topbar)
    const topbarHtml = `
      <button class="icon-btn menu-toggle" aria-label="Abrir Menu" id="menuToggle"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><path d="M3 6h18M3 12h18M3 18h18"/></svg></button>
      <span class="crumbs">Lattice Admin / ${crumb}</span>
      <div class="top-right">
        <span class="top-pill"><span class="dot"></span> conectado</span>
        <button class="icon-btn" aria-label="Notificações">${icon("bell")}</button>
      </div>
    `;

    // Injeção do HTML no DOM
    document.getElementById("sidebar-root").innerHTML = sidebarHtml;
    document.getElementById("topbar-root").innerHTML = topbarHtml;

    // 3. Comportamento Mobile: Menu Off-Canvas com Backdrop
    let backdrop = document.querySelector(".sb-backdrop");
    if (!backdrop) {
      backdrop = document.createElement("div");
      backdrop.className = "sb-backdrop";
      document.body.appendChild(backdrop);
    }
    const close = () => document.body.classList.remove("sidebar-open");
    document.getElementById("menuToggle").addEventListener("click", (e) => {
      e.stopPropagation();
      document.body.classList.toggle("sidebar-open");
    });
    backdrop.addEventListener("click", close);
    document.querySelectorAll("#sidebar-root .sb-item").forEach(a => a.addEventListener("click", close));

    // 4. Comportamento Desktop: Recolhimento da Barra Lateral com Persistência
    if (localStorage.getItem("sbCollapsed") === "1") document.body.classList.add("sidebar-collapsed");
    document.getElementById("sbCollapse").addEventListener("click", () => {
      const on = document.body.classList.toggle("sidebar-collapsed");
      localStorage.setItem("sbCollapsed", on ? "1" : "0");
    });
    document.querySelector("#sidebar-root .brand-mark").addEventListener("click", () => {
      if (document.body.classList.contains("sidebar-collapsed")) {
        document.body.classList.remove("sidebar-collapsed");
        localStorage.setItem("sbCollapsed", "0");
      }
    });
  }

  // Torna a função globalmente acessível
  window.renderShell = renderShell;
})();
