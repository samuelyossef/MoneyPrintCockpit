/* Runtime state for the local MoneyPrinter cockpit. Data-bearing collections start
   empty and are hydrated by same-origin Gateway endpoints. */
window.CK = (function () {
  const VIDEOS = { vert: [], horiz: [] };
  const CHANNELS = [];
  const NOTIFICATIONS = [];
  const LOGS = [];
  const ORGS = [{ id: "local", name: "Local", short: "LC", color: "#2563eb", f: 1 }];
  const PERIODS = [{ id: "30d", label: "Últimos 30 dias", f: 1, n: 30 }];
  const ENVS = ["Local"];
  const empty = [];
  const fmtInt = value => Number(value || 0).toLocaleString("pt-BR");
  const fmtBRL = value => `R$ ${fmtInt(value)}`;
  const fmtUSD = value => `US$ ${fmtInt(value)}`;
  const fmtPct = value => `${Number(value || 0).toFixed(1).replace(".", ",")}%`;
  const series = (_seed, count) => Array.from({ length: count }, () => 0);
  const kpis = () => [];
  return {
    VIDEOS, CHANNELS, NOTIFICATIONS, LOGS, ORGS, PERIODS, ENVS, series, kpis,
    ALERTS: empty, USERS: empty, PERMS: empty, TRACE: empty, APPROVALS: empty,
    PLANS: empty, INVOICES: empty, SECURITY: empty, RAG: empty, ACTIVITY: empty,
    CUSTOMERS: empty, WORKSPACES: empty, ACTIONS: empty,
    fmtBRL, fmtUSD, fmtInt, fmtPct,
  };
}());
