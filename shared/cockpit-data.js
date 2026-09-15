/**
 * ============================================================================
 * Lattice Cockpit — Fonte de Dados (Mocks & Formatação)
 * ============================================================================
 * Centraliza o estado simulado da aplicação: canais de vídeo (YouTube Shorts,
 * TikTok), histórico de vídeos gerados, notificações do sistema, métricas de
 * analytics, faturas e logs de eventos.
 *
 * NOTA DE INTEGRAÇÃO:
 * Para conectar esta interface a um backend real (ex: Python/FastAPI ou Node.js),
 * substitua estes arrays estáticos por chamadas assíncronas `fetch('/api/...')`.
 * ============================================================================
 */
window.CK = (function () {

  /**
   * Lista de organizações cadastradas no ecossistema
   * O campo 'f' é um fator multiplicador usado para simular métricas proporcionais
   */
  const ORGS = [
    { id: "all",   name: "Todas as organizações", short: "TD", color: "#6b7280", plan: "—",          f: 1 },
    { id: "copy",  name: "Copyxyz",               short: "CX", color: "#2563eb", plan: "Enterprise", f: 0.42 },
    { id: "acme",  name: "Acme Corp",             short: "AC", color: "#7c3aed", plan: "Enterprise", f: 0.31 },
    { id: "north", name: "Northwind",             short: "NW", color: "#0d9488", plan: "Pro",        f: 0.18 },
    { id: "demo",  name: "Demo Organization",     short: "DO", color: "#ea580c", plan: "Free",       f: 0.09 },
  ];

  /** Espaços de trabalho disponíveis */
  const WORKSPACES = ["AI Platform", "Legal", "Finance", "Marketing"];

  /**
   * Notificações do sistema
   * Severidades: 'ok' (sucesso), 'warning' (alerta), 'info' (informativo), 'critical' (erro crítico)
   */
  const NOTIFICATIONS = [
    { id: 1, sev: "ok",       title: "Vídeo renderizado com sucesso",     sub: "\u201c3 fatos de psicologia\u201d · 9:16 · 42s",     time: "há 6 min",  read: false, view: "overview" },
    { id: 2, sev: "warning",  title: "Provedor de IA com poucos créditos", sub: "OpenAI — restam 12% da cota mensal",               time: "há 38 min", read: false, view: "config" },
    { id: 3, sev: "info",     title: "Novo modelo disponível",             sub: "Gemini 2.5 Flash adicionado à lista de modelos",   time: "há 2h",     read: false, view: "config" },
    { id: 4, sev: "critical", title: "Falha ao gerar legendas",            sub: "STT AssemblyAI retornou erro de autenticação",     time: "há 3h",     read: true,  view: "config" },
    { id: 5, sev: "ok",       title: "Vídeo renderizado com sucesso",     sub: "\u201cCuriosidades do espaço #12\u201d · 9:16 · 51s", time: "há 5h",   read: true,  view: "overview" },
    { id: 6, sev: "info",     title: "Chave de API atualizada",            sub: "Anthropic — nova chave validada e ativa",          time: "ontem",     read: true,  view: "config" },
  ];

  /**
   * Catálogo de vídeos gerados divididos por proporção:
   * - vert: Verticais (9:16) para TikTok, Instagram Reels e YouTube Shorts
   * - horiz: Horizontais (16:9) para YouTube tradicional
   * Formato de cada item: [proporção, plataforma, título, canal_vinculado]
   */
  const VIDEOS = {
    vert: [
      ["9:16", "TikTok", "Curiosidades do espaço #12", "Clipes Virais"],
      ["9:16", "Shorts", "3 fatos de psicologia", "Canal Principal"],
      ["9:16", "TikTok", "História oculta: piratas", "Histórias Rápidas"],
      ["9:16", "Shorts", "Finanças em 60 segundos", "Shorts Diários"],
      ["9:16", "TikTok", "Mitos da nutrição", "Clipes Virais"],
    ],
    horiz: [
      ["16:9", "YouTube", "Como a IA muda o trabalho", "Canal Principal"],
      ["16:9", "YouTube", "Resumo semanal de notícias", "Canal Principal"],
    ],
  };

  /** Filtros de períodos temporais para métricas e gráficos */
  const PERIODS = [
    { id: "7d",  label: "Últimos 7 dias",   f: 0.24, n: 7,  step: "diário" },
    { id: "30d", label: "Últimos 30 dias",  f: 1,    n: 30, step: "diário" },
    { id: "90d", label: "Últimos 90 dias",  f: 3.05, n: 12, step: "semanal" },
  ];

  /** Ambientes de execução */
  const ENVS = ["Produção", "Staging", "Todos os ambientes"];

  // ==========================================================================
  // Funções Auxiliares de Formatação Numérica e Monetária
  // ==========================================================================
  const fmtBRL = n => "R$ " + Math.round(n).toLocaleString("pt-BR");
  const fmtUSD = n => "US$ " + Math.round(n).toLocaleString("pt-BR");
  const fmtInt = n => Math.round(n).toLocaleString("pt-BR");
  const fmtPct = n => n.toFixed(1).replace(".", ",") + "%";

  /**
   * Gerador de séries numéricas pseudo-aleatórias consistentes
   * Garante que os gráficos mantenham a mesma curva visual ao navegar entre telas
   */
  function series(seed, n, base, drift, noise) {
    const out = []; let s = seed;
    for (let i = 0; i < n; i++) {
      s = (s * 9301 + 49297) % 233280;
      const r = s / 233280;
      out.push(Math.max(0, base * (1 + drift * (i / n)) * (1 + noise * (r - 0.5))));
    }
    return out;
  }

  /**
   * Recalcula os Indicadores Chave de Performance (KPIs) com base no escopo ativo
   * @param {Object} scope Objeto contendo { org, period }
   * @returns {Array} Lista de cartões de KPI
   */
  function kpis(scope) {
    const org = ORGS.find(o => o.id === scope.org) || ORGS[0];
    const per = PERIODS.find(p => p.id === scope.period) || PERIODS[1];
    const stock = org.f;              // Métricas estáticas baseadas no tamanho da organização
    const flow = org.f * per.f;       // Métricas de fluxo que variam com o período
    const dScale = per.f < 1 ? 0.34 : per.f > 2 ? 2.3 : 1;

    return [
      { id: "mrr",     view: "billing",  label: "MRR",              value: fmtBRL(124800 * stock),  delta: +8.2 * (per.f < 1 ? 0.3 : 1), suffix: "%", seed: 7,  color: "var(--ok)" },
      { id: "custs",   view: "canais",   label: "Clientes",         value: fmtInt(184 * stock),      delta: +12 * dScale,                 suffix: "",  seed: 31, color: "var(--ok)", raw: true },
      { id: "actives", view: "usuarios", label: "Usuários ativos",  value: fmtInt(4821 * stock),     delta: +14 * (per.f < 1 ? 0.5 : 1),  suffix: "%", seed: 53, color: "var(--ok)" },
      { id: "churn",   view: "billing",  label: "Churn",            value: fmtPct(2.1 + (org.id === "demo" ? 3.4 : 0)), delta: -0.3, suffix: " p.p.", seed: 91, color: "var(--err)", invert: true },
      { id: "aispend", view: "logs",     label: "Gasto com IA",     value: fmtUSD(4218 * flow),      delta: +6.4 * dScale,                suffix: "%", seed: 17, color: "var(--warn)" },
    ];
  }

  /** Ações rápidas pendentes de atenção no painel */
  const ACTIONS = [
    { n: 3, sev: "crit", title: "Aprovações pendentes de alto risco", sub: "Research Agent · Billing Bot · Ops Runner", view: "logs", cta: "Revisar" },
    { n: 2, sev: "crit", title: "Pagamentos recusados",               sub: "Acme Corp · Northwind — cobrança reprocessa em 6h", view: "billing", cta: "Abrir faturas" },
    { n: 1, sev: "warn", title: "Integração expirada",                sub: "Google Workspace — token OAuth vencido há 2 dias", view: "config", cta: "Reconectar" },
    { n: 4, sev: "warn", title: "Convites aguardando aceite",         sub: "Enviados há mais de 7 dias", view: "usuarios", cta: "Reenviar" },
    { n: 2, sev: "info", title: "Jobs falharam sem retry",            sub: "Fila document-ingest · worker w-04", view: "logs", cta: "Ver jobs" },
  ];

  /** Feed de alertas de segurança e infraestrutura */
  const ALERTS = [
    { sev: "critical", title: "Taxa de erro da OpenAI acima de 8%", org: "Plataforma", meta: "gpt-4o · 412 requisições afetadas", time: "há 4 min", view: "logs" },
    { sev: "critical", title: "Falha no pagamento da fatura INV-2091", org: "Acme Corp", meta: "R$ 8.500 · cartão recusado", time: "há 22 min", view: "billing" },
    { sev: "warning",  title: "Acme atingiu 91% do limite de tokens", org: "Acme Corp", meta: "18,2M / 20M tokens no ciclo", time: "há 1 h", view: "billing" },
    { sev: "warning",  title: "Fila de workers acima do limite",      org: "Plataforma", meta: "queue depth 1.284 · limite 800", time: "há 1 h", view: "logs" },
    { sev: "warning",  title: "6 chaves de API sem uso há 90 dias",   org: "Copyxyz", meta: "rotação recomendada", time: "há 3 h", view: "config" },
    { sev: "warning",  title: "Latência p95 do RAG em 2,4 s",         org: "AI Platform", meta: "reranking responde por 1,1 s", time: "há 5 h", view: "logs" },
    { sev: "info",     title: "Adoção de MFA chegou a 78%",           org: "Plataforma", meta: "+6 p.p. na semana", time: "ontem", view: "usuarios" },
    { sev: "info",     title: "Novo plano Business publicado",        org: "Plataforma", meta: "3 organizações migradas", time: "ontem", view: "billing" },
    { sev: "info",     title: "Northwind convidou 12 usuários",       org: "Northwind", meta: "11 aceitos", time: "há 2 d", view: "usuarios" },
  ];

  /** Registro de membros da equipe com papéis de controle de acesso (RBAC) */
  const USERS = [
    { name: "Samuel Yossef",   email: "samuel@copyxyz.com",       role: "Admin",      org: "Copyxyz",   ws: "AI Platform", status: "active",   last: "há 2 min",  mfa: true,  color: "#2563eb", title: "AI Architect" },
    { name: "Avery Chen",      email: "avery@copyxyz.com",        role: "Owner",      org: "Copyxyz",   ws: "AI Platform", status: "active",   last: "há 18 min", mfa: true,  color: "#7c3aed", title: "Head of Platform" },
    { name: "Noah Kim",        email: "noah.kim@acme.com",        role: "Admin",      org: "Acme Corp", ws: "Legal",       status: "active",   last: "há 1 h",    mfa: true,  color: "#ea580c", title: "Legal Ops Lead" },
    { name: "Maya Reyes",      email: "maya@northwind.io",        role: "Membro",     org: "Northwind", ws: "Finance",     status: "active",   last: "há 3 h",    mfa: false, color: "#0d9488", title: "Analista" },
    { name: "Camille Laurent", email: "camille@acme.com",         role: "Membro",     org: "Acme Corp", ws: "Marketing",   status: "active",   last: "há 6 h",    mfa: true,  color: "#db2777", title: "Content Manager" },
    { name: "Diego Fontes",    email: "diego@copyxyz.com",        role: "Suporte",    org: "Copyxyz",   ws: "AI Platform", status: "active",   last: "há 9 h",    mfa: false, color: "#16a34a", title: "Support Engineer" },
    { name: "Sofia Marchetti", email: "sofia@northwind.io",       role: "Membro",     org: "Northwind", ws: "Legal",       status: "pending",  last: "convite enviado", mfa: false, color: "#b45309", title: "Paralegal" },
    { name: "Ravi Anand",      email: "ravi@acme.com",            role: "Somente ver",org: "Acme Corp", ws: "Finance",     status: "active",   last: "há 1 d",    mfa: true,  color: "#0284c7", title: "Auditor" },
    { name: "Lena Brandt",     email: "lena@demo.lattice.app",    role: "Membro",     org: "Demo",      ws: "Marketing",   status: "canceled", last: "há 34 d",   mfa: false, color: "#6b7280", title: "—" },
    { name: "Tomás Ferreira",  email: "tomas@copyxyz.com",        role: "Admin",      org: "Copyxyz",   ws: "Finance",     status: "active",   last: "há 2 d",    mfa: true,  color: "#9333ea", title: "Finance Lead" },
    { name: "Ana Duarte",      email: "ana@northwind.io",         role: "Membro",     org: "Northwind", ws: "AI Platform", status: "pending",  last: "convite enviado", mfa: false, color: "#e11d48", title: "Data Scientist" },
    { name: "Iris Nakamura",   email: "iris@acme.com",            role: "Owner",      org: "Acme Corp", ws: "AI Platform", status: "active",   last: "há 5 h",    mfa: true,  color: "#0f766e", title: "CTO" },
  ];

  /** Matriz de permissões por categoria e papel */
  const PERMS = [
    ["Documentos",  1, 1, 0, 0],
    ["Agentes",     1, 1, 1, 0],
    ["Usuários",    1, 1, 0, 1],
    ["Billing",     1, 0, 0, 0],
    ["Chaves de API", 1, 1, 1, 1],
    ["Logs e traces", 1, 0, 0, 0],
  ];

  /**
   * Logs operacionais e traces de requisições de IA
   * Inclui tempo, nível de criticidade, microsserviço e trace ID
   */
  const LOGS = [
    { ts: "14:02:41", lvl: "CRITICAL", svc: "ai-gateway",  msg: "provider timeout após 30s — openai/gpt-4o", org: "Acme Corp", trace: "tr_9f2b41c8", user: "samuel@copyxyz.com", model: "gpt-4o", retries: 3 },
    { ts: "14:02:38", lvl: "ERROR",    svc: "ai-gateway",  msg: "rate limit 429 recebido do provedor", org: "Acme Corp", trace: "tr_9f2b41c8", user: "samuel@copyxyz.com", model: "gpt-4o", retries: 2 },
    { ts: "14:01:12", lvl: "WARN",     svc: "rag-indexer", msg: "chunk excedeu 8k tokens, truncado", org: "Copyxyz", trace: "tr_71ad0e33", user: "avery@copyxyz.com", model: "text-embedding-3", retries: 0 },
    { ts: "13:58:04", lvl: "INFO",     svc: "auth",        msg: "sessão criada via SSO (okta)", org: "Copyxyz", trace: "tr_44cc9012", user: "diego@copyxyz.com", model: "—", retries: 0 },
    { ts: "13:57:51", lvl: "ERROR",    svc: "billing",     msg: "cobrança recusada: card_declined", org: "Northwind", trace: "tr_2f80ba55", user: "maya@northwind.io", model: "—", retries: 1 },
    { ts: "13:55:20", lvl: "WARN",     svc: "worker",      msg: "queue depth 1284 acima do limite 800", org: "Plataforma", trace: "tr_10ee7742", user: "—", model: "—", retries: 0 },
    { ts: "13:52:09", lvl: "INFO",     svc: "agents",      msg: "execução concluída em 4,2s — Research Agent", org: "Copyxyz", trace: "tr_88bd1290", user: "samuel@copyxyz.com", model: "claude-sonnet", retries: 0 },
    { ts: "13:49:47", lvl: "ERROR",    svc: "rag-indexer", msg: "OCR falhou no documento contrato-2091.pdf", org: "Acme Corp", trace: "tr_5510cc7f", user: "noah.kim@acme.com", model: "—", retries: 2 },
    { ts: "13:44:31", lvl: "INFO",     svc: "api",         msg: "chave de API criada — scope: read", org: "Copyxyz", trace: "tr_9012ffab", user: "tomas@copyxyz.com", model: "—", retries: 0 },
    { ts: "13:41:02", lvl: "WARN",     svc: "ai-gateway",  msg: "latência p95 em 2412ms para rag/query", org: "AI Platform", trace: "tr_71ad0e33", user: "—", model: "gpt-4o-mini", retries: 0 },
    { ts: "13:38:55", lvl: "INFO",     svc: "auth",        msg: "MFA habilitado pelo usuário", org: "Northwind", trace: "tr_6621aa03", user: "ana@northwind.io", model: "—", retries: 0 },
    { ts: "13:31:18", lvl: "ERROR",    svc: "agents",      msg: "ferramenta send_email negada por política", org: "Copyxyz", trace: "tr_88bd1290", user: "samuel@copyxyz.com", model: "claude-sonnet", retries: 0 },
    { ts: "13:22:40", lvl: "INFO",     svc: "billing",     msg: "fatura INV-2093 emitida — R$ 1.240", org: "Northwind", trace: "tr_2f80ba55", user: "—", model: "—", retries: 0 },
    { ts: "13:18:07", lvl: "WARN",     svc: "auth",        msg: "5 tentativas de login falharam", org: "Demo", trace: "tr_3390de11", user: "lena@demo.lattice.app", model: "—", retries: 0 },
  ];

  /**
   * Pipeline de geração de vídeo do Moneyprint
   * Descreve as etapas de execução e tempos típicos de cada microsserviço
   */
  const TRACE = [
    { name: "Request",         ms: 1 },
    { name: "Roteiro IA",       ms: 620 },
    { name: "Keywords",        ms: 140 },
    { name: "Voz e Legendas",  ms: 480 },
    { name: "Filmagens Stock", ms: 310 },
    { name: "Render HD",       ms: 1800, slow: true },
    { name: "Response",        ms: 60 },
  ];

  /** Solicitações de aprovação com níveis de risco associados */
  const APPROVALS = [
    { agent: "Research Agent", action: "Enviar e-mail para lista externa (48 destinatários)", by: "Samuel Yossef", risk: "med",  cost: "US$ 0,04", when: "há 8 min" },
    { agent: "Billing Bot",    action: "Emitir crédito de R$ 2.400 para Acme Corp",           by: "Tomás Ferreira", risk: "high", cost: "US$ 0,01", when: "há 25 min" },
    { agent: "Ops Runner",     action: "Reiniciar workers da fila document-ingest",           by: "Diego Fontes",   risk: "high", cost: "—",        when: "há 40 min" },
    { agent: "Legal Reviewer", action: "Publicar resumo de contrato no workspace Legal",      by: "Noah Kim",       risk: "low",  cost: "US$ 0,12", when: "há 2 h" },
  ];

  /** Catálogo de planos de assinatura da plataforma */
  const PLANS = [
    { name: "Free",       price: "R$ 0",     users: "3",         tokens: "100k/mês",  projects: "1",         api: "1k/mês",   orgs: 62, current: false },
    { name: "Starter",    price: "R$ 149",   users: "10",        tokens: "2M/mês",    projects: "5",         api: "50k/mês",  orgs: 48, current: false },
    { name: "Pro",        price: "R$ 590",   users: "50",        tokens: "10M/mês",   projects: "25",        api: "500k/mês", orgs: 41, current: true },
    { name: "Business",   price: "R$ 1.890", users: "200",       tokens: "40M/mês",   projects: "Ilimitado", api: "2M/mês",   orgs: 21, current: false },
    { name: "Enterprise", price: "Sob consulta", users: "Ilimitado", tokens: "Negociado", projects: "Ilimitado", api: "Negociado", orgs: 12, current: false },
  ];

  /** Faturas emitidas e histórico de cobrança */
  const INVOICES = [
    { id: "INV-2091", org: "Acme Corp", amount: "R$ 8.500", status: "overdue",  due: "01/09/2026", plan: "Enterprise" },
    { id: "INV-2092", org: "Copyxyz",   amount: "R$ 11.240", status: "paid",    due: "01/09/2026", plan: "Enterprise" },
    { id: "INV-2093", org: "Northwind", amount: "R$ 1.240", status: "overdue",  due: "02/09/2026", plan: "Pro" },
    { id: "INV-2094", org: "Demo",      amount: "R$ 0",     status: "paid",     due: "01/09/2026", plan: "Free" },
    { id: "INV-2095", org: "Copyxyz",   amount: "R$ 640",   status: "pending",  due: "10/09/2026", plan: "Add-on tokens" },
  ];

  /** Indicadores de segurança e auditoria */
  const SECURITY = [
    { label: "Sessões ativas",        value: "1.284", delta: "+34 hoje",       tone: "" },
    { label: "Logins falhados (24h)", value: "37",    delta: "5 contas",       tone: "warn" },
    { label: "Adoção de MFA",         value: "78%",   delta: "+6 p.p.",        tone: "ok" },
    { label: "Atividade suspeita",    value: "2",     delta: "IPs bloqueados", tone: "err" },
    { label: "Chaves expiradas",      value: "6",     delta: "rotação devida", tone: "warn" },
  ];

  /**
   * Canais de Redes Sociais Conectados (YouTube, YouTube Shorts, TikTok)
   * Base de dados utilizada para agendamento e publicação automática de vídeos
   */
  const CHANNELS = []; // preenchido por moneyprinter-runtime.js a partir de GET /api/channels

  /** Clientes corporativos e contratos */
  const CUSTOMERS = [
    ["Copyxyz", "Enterprise", 124, "R$ 11.240", 71, 92, "active"],
    ["Acme Corp", "Enterprise", 98, "R$ 8.500", 91, 64, "past_due"],
    ["Northwind", "Pro", 24, "R$ 1.240", 43, 78, "active"],
    ["Duval Groupe", "Business", 41, "R$ 1.890", 58, 85, "active"],
    ["Marchetti Design", "Starter", 8, "R$ 149", 22, 71, "trialing"],
    ["Demo Organization", "Free", 3, "R$ 0", 7, 38, "canceled"],
  ];

  /** Monitoramento de saúde dos componentes de RAG e vetores */
  const RAG = [
    { n: "Parsing",   v: "12.482 docs", s: "healthy" },
    { n: "OCR",       v: "1.204 págs",  s: "warning" },
    { n: "Chunking",  v: "208k chunks", s: "healthy" },
    { n: "Embedding", v: "208k vetores",s: "healthy" },
    { n: "Índice",    v: "4 shards",    s: "healthy" },
    { n: "Retrieval", v: "p95 480ms",   s: "healthy" },
    { n: "Reranking", v: "p95 1,1s",    s: "error" },
  ];

  /** Registro de atividades recentes dos operadores da plataforma */
  const ACTIVITY = [
    { who: "Samuel Yossef", what: "criou o agente Contract Extractor", when: "há 6 min", color: "#2563eb" },
    { who: "Tomás Ferreira", what: "alterou o plano da Northwind para Pro", when: "há 40 min", color: "#9333ea" },
    { who: "Sistema", what: "bloqueou 2 IPs por tentativas repetidas de login", when: "há 1 h", color: "#ef4444" },
    { who: "Iris Nakamura", what: "aprovou 3 solicitações do Legal Reviewer", when: "há 2 h", color: "#0f766e" },
    { who: "Diego Fontes", what: "reprocessou 128 jobs da fila document-ingest", when: "há 4 h", color: "#16a34a" },
    { who: "Avery Chen", what: "publicou o plano Business", when: "ontem", color: "#7c3aed" },
  ];

  // Exportação pública para o escopo global (window.CK)
  return {
    ORGS, WORKSPACES, VIDEOS, NOTIFICATIONS, PERIODS, ENVS, kpis, series,
    ACTIONS, ALERTS, USERS, PERMS, LOGS, TRACE, APPROVALS, PLANS,
    INVOICES, SECURITY, RAG, ACTIVITY, CUSTOMERS, CHANNELS,
    fmtBRL, fmtUSD, fmtInt, fmtPct
  };
})();
