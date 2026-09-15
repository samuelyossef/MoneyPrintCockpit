/* ============================================================
   connectors.jsx — ConnectorsView, ConnectorModal, CONNECTORS,
   ConnLogo, INITIAL_CONNECTOR_STATE, AUDIT_LOG
   ============================================================ */
const { useState: useStateCN } = React;

const CONNECTORS = [
  { id: "gdrive", name: "Google Drive", color: "#1FA463", blurb: "Index and search files from Drive.", scopes: ["Read files & folders", "View file metadata"] },
  { id: "gmail", name: "Gmail", color: "#EA4335", blurb: "Read and draft email threads.", scopes: ["Read messages", "Compose drafts"] },
  { id: "github", name: "GitHub", color: "#24292F", blurb: "Browse repos, issues and PRs.", scopes: ["Read repositories", "Read issues & PRs", "Read code"] },
  { id: "slack", name: "Slack", color: "#4A154B", blurb: "Search messages and post updates.", scopes: ["Read channels", "Post messages"] },
  { id: "notion", name: "Notion", color: "#111111", blurb: "Sync pages and databases.", scopes: ["Read pages", "Read databases"] },
  { id: "linear", name: "Linear", color: "#5E6AD2", blurb: "Track issues and cycles.", scopes: ["Read issues", "Update issues"] },
  { id: "jira", name: "Jira", color: "#2684FF", blurb: "Sync tickets and sprints.", scopes: ["Read issues", "Transition issues"] },
  { id: "figma", name: "Figma", color: "#F24E1E", blurb: "Pull frames and design specs.", scopes: ["Read files", "Read comments"] },
  { id: "gcal", name: "Google Calendar", color: "#4285F4", blurb: "Read events and availability.", scopes: ["Read events", "Read free/busy"] },
  { id: "dropbox", name: "Dropbox", color: "#0061FF", blurb: "Index documents in Dropbox.", scopes: ["Read files"] },
  { id: "confluence", name: "Confluence", color: "#172B4D", blurb: "Search spaces and pages.", scopes: ["Read spaces", "Read pages"] },
  { id: "sentry", name: "Sentry", color: "#362D59", blurb: "Pull errors and traces.", scopes: ["Read issues", "Read events"] },
  { id: "stripe", name: "Stripe", color: "#635BFF", blurb: "Read billing and customers.", scopes: ["Read customers", "Read invoices"] },
  { id: "pagerduty", name: "PagerDuty", color: "#06AC38", blurb: "Read incidents and on-call.", scopes: ["Read incidents", "Read schedules"] },
];

const INITIAL_CONNECTOR_STATE = {
  github: { status: "connected", account: "lattice-eng", lastSync: "2m ago" },
  slack: { status: "connected", account: "acme.slack.com", lastSync: "10m ago" },
  linear: { status: "connected", account: "ACME", lastSync: "1h ago" },
  gdrive: { status: "expired", account: "ops@acme.co", lastSync: "3d ago" },
  sentry: { status: "connected", account: "acme", lastSync: "5m ago" },
};

const AUDIT_LOG = [
  { event: "connector.connected", target: "github", actor: "you", when: "2m ago" },
  { event: "settings.changed", target: "appearance.dark", actor: "you", when: "12m ago" },
  { event: "connector.token_expired", target: "gdrive", actor: "system", when: "3d ago" },
  { event: "project.created", target: "verification-gate", actor: "you", when: "5d ago" },
  { event: "connector.connected", target: "slack", actor: "you", when: "1w ago" },
];

function ConnLogo({ id, size = 38 }) {
  const c = CONNECTORS.find((x) => x.id === id);
  const letter = c ? c.name[0] : "?";
  return (
    <span style={{
      width: size, height: size, borderRadius: 10, flex: "0 0 auto",
      background: c ? c.color : "#888", color: "#fff", display: "grid", placeItems: "center",
      fontWeight: 700, fontSize: size * 0.42, fontFamily: "Inter, sans-serif",
    }}>{letter}</span>
  );
}

function statusPill(status) {
  if (status === "connected") return <span className="conn-pill conn-pill-ok">connected</span>;
  if (status === "expired") return <span className="conn-pill conn-pill-warn">expired</span>;
  return <span className="conn-pill conn-pill-off">disconnected</span>;
}

function ConnectorModal({ open, connector, state, onClose, onConnect, onDisconnect }) {
  const [phase, setPhase] = useStateCN("idle");
  React.useEffect(() => {
    if (open) setPhase(state && state.status === "connected" ? "connected" : "idle");
  }, [open, connector && connector.id]);
  if (!connector) return null;

  const startConnect = () => setPhase("consent");
  const grant = () => {
    setPhase("connecting");
    setTimeout(() => { setPhase("success"); onConnect(connector.id); }, 1300);
  };

  return (
    <Modal open={open} onClose={onClose} title={connector.name} width={460}>
      {(phase === "idle") && (
        <>
          <div className="conn-modal-hd">
            <ConnLogo id={connector.id} />
            <div><div className="conn-modal-account">{connector.name}</div><div className="conn-modal-sub">Not connected</div></div>
          </div>
          <p className="conn-modal-help">{connector.blurb} Connect your account to let Lattice use it as a tool and source.</p>
          <div className="modal-foot">
            <button className="btn" onClick={onClose}>Cancel</button>
            <button className="btn primary" onClick={startConnect}>Connect {connector.name}</button>
          </div>
        </>
      )}
      {phase === "consent" && (
        <div className="conn-consent">
          <ConnLogo id={connector.id} size={48} />
          <h3>Authorize Lattice</h3>
          <p>Lattice is requesting access to your {connector.name} account:</p>
          <ul className="conn-consent-list">
            {connector.scopes.map((s, i) => <li key={i}><Icon name="check" />{s}</li>)}
          </ul>
          <div className="modal-foot">
            <button className="btn" onClick={() => setPhase("idle")}>Back</button>
            <button className="btn primary" onClick={grant}>Allow access</button>
          </div>
        </div>
      )}
      {phase === "connecting" && (
        <div className="conn-loading"><div className="conn-spinner" />Connecting to {connector.name}…</div>
      )}
      {phase === "success" && (
        <div className="conn-success">
          <div className="conn-check"><Icon name="check" /></div>
          <h3>Connected</h3>
          <p>{connector.name} is now available to Lattice.</p>
          <div className="modal-foot" style={{ justifyContent: "center" }}>
            <button className="btn primary" onClick={onClose}>Done</button>
          </div>
        </div>
      )}
      {phase === "connected" && (
        <>
          <div className="conn-modal-hd">
            <ConnLogo id={connector.id} />
            <div><div className="conn-modal-account">{(state && state.account) || connector.name}</div><div className="conn-modal-sub">Connected · synced {(state && state.lastSync) || "just now"}</div></div>
          </div>
          <div className="conn-scopes">
            <div className="conn-scopes-label">Granted permissions</div>
            {connector.scopes.map((s, i) => (
              <label key={i} className="conn-scope"><input type="checkbox" defaultChecked />{s}</label>
            ))}
          </div>
          <div className="modal-foot">
            <button className="btn danger" onClick={() => { onDisconnect(connector.id); onClose(); }}>Disconnect</button>
            <button className="btn" onClick={onClose}>Close</button>
          </div>
        </>
      )}
    </Modal>
  );
}

function ConnectorsView() {
  const t = useT();
  const [state, setState] = useStateCN(() => ({ ...INITIAL_CONNECTOR_STATE }));
  const [active, setActive] = useStateCN(null);

  const connect = (id) => {
    setState((s) => ({ ...s, [id]: { status: "connected", account: (s[id] && s[id].account) || "you@acme.co", lastSync: "just now" } }));
    AUDIT_LOG.unshift({ event: "connector.connected", target: id, actor: "you", when: "just now" });
  };
  const disconnect = (id) => {
    setState((s) => { const n = { ...s }; n[id] = { status: "disconnected" }; return n; });
    AUDIT_LOG.unshift({ event: "connector.disconnected", target: id, actor: "you", when: "just now" });
  };

  return (
    <div className="view">
      <ViewHeader icon="plug" title={t("view.connectors.title")} subtitle={t("view.connectors.sub")} />
      <div className="view-body view-body-scroll">
        <div className="conn-grid">
          {CONNECTORS.map((c) => {
            const st = state[c.id] || { status: "disconnected" };
            return (
              <button key={c.id} className="conn-card" onClick={() => setActive(c)}>
                <div className="conn-card-hd"><ConnLogo id={c.id} />{statusPill(st.status)}</div>
                <div className="conn-card-meta">
                  <div className="conn-card-name">{c.name}</div>
                  <div className="conn-card-blurb">{c.blurb}</div>
                </div>
                <div className="conn-card-foot">
                  {st.status !== "disconnected"
                    ? <><span>{st.account}</span><span className="artifact-sep">·</span><span className="muted">synced {st.lastSync}</span></>
                    : <span className="muted">Not connected</span>}
                </div>
              </button>
            );
          })}
        </div>
      </div>
      <ConnectorModal open={!!active} connector={active} state={active ? state[active.id] : null}
                      onClose={() => setActive(null)} onConnect={connect} onDisconnect={disconnect} />
    </div>
  );
}

window.AUDIT_LOG = AUDIT_LOG;
Object.assign(window, { CONNECTORS, ConnLogo, INITIAL_CONNECTOR_STATE, ConnectorModal, ConnectorsView });
