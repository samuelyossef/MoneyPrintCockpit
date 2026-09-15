/* ============================================================
   modals.jsx — MyAccount (Body/View/Modal), Settings (Body/View/Modal)
   ============================================================ */
const { useState: useStateM } = React;

const SETTINGS_TABS = [
  { id: "general", tk: "set.general" },
  { id: "model", tk: "set.model" },
  { id: "appearance", tk: "set.appearance" },
  { id: "audit", tk: "set.audit" },
  { id: "shortcuts", tk: "set.shortcuts" },
  { id: "about", tk: "set.about" },
];

const SHORTCUTS = [
  { k: "⌘K", a: "Open search palette" },
  { k: "⌘N", a: "New conversation" },
  { k: "⌘,", a: "Open Settings" },
  { k: "⌘B", a: "Toggle sidebar" },
  { k: "⌘J", a: "Toggle tasks panel" },
  { k: "↵", a: "Send message" },
  { k: "⇧↵", a: "New line" },
  { k: "Esc", a: "Close overlay" },
  { k: "F2", a: "Rename conversation" },
];

/* ---------------- My account ---------------- */
function MyAccountBody({ account, onChange, onSignOut }) {
  const usagePct = Math.min(100, Math.round((account.used / account.cap) * 100));
  return (
    <div className="acc-view-shell">
      <div className="acc-head">
        <div className="acc-avatar">{account.name.split(" ").map((s) => s[0]).slice(0, 2).join("")}</div>
        <div>
          <div className="acc-name">
            <InlineEditable value={account.name} onChange={(v) => onChange({ name: v })} />
          </div>
          <div className="acc-plan">
            <Badge>{account.plan}</Badge>
            <span className="muted">{account.email}</span>
          </div>
        </div>
      </div>

      <div className="acc-rows">
        <div className="acc-row"><span className="acc-label">Email</span>
          <InlineEditable value={account.email} onChange={(v) => onChange({ email: v })} /></div>
        <div className="acc-row"><span className="acc-label">Workspace</span>
          <InlineEditable value={account.workspace} onChange={(v) => onChange({ workspace: v })} /></div>
        <div className="acc-row"><span className="acc-label">Time zone</span>
          <InlineEditable value={account.tz} onChange={(v) => onChange({ tz: v })} /></div>
        <div className="acc-row"><span className="acc-label">Default model</span>
          <InlineEditable value={account.model} onChange={(v) => onChange({ model: v })} /></div>
      </div>

      <div className="acc-meter">
        <div className="acc-meter-hd">
          <span>Token usage this cycle</span>
          <span className="mono muted">{account.used.toLocaleString()} / {account.cap.toLocaleString()}</span>
        </div>
        <div className="acc-meter-bar"><div className="acc-meter-fill" style={{ width: usagePct + "%" }} /></div>
      </div>

      <div className="acc-actions">
        <button className="btn"><Icon name="card" />Manage billing</button>
        <button className="btn danger" onClick={onSignOut}><Icon name="signout" />Sign out</button>
      </div>
    </div>
  );
}
function MyAccountModal({ open, onClose, account, onChange, onSignOut }) {
  const t = useT();
  return (
    <Modal open={open} onClose={onClose} title={t("view.account.title")} width={560}>
      <MyAccountBody account={account} onChange={onChange} onSignOut={onSignOut} />
    </Modal>
  );
}
function MyAccountView({ account, onChange, onSignOut }) {
  const t = useT();
  return (
    <div className="view">
      <ViewHeader icon="user" title={t("view.account.title")} subtitle={account.email} />
      <div className="view-body view-body-scroll">
        <MyAccountBody account={account} onChange={onChange} onSignOut={onSignOut} />
      </div>
    </div>
  );
}

/* ---------------- Settings ---------------- */
function SettingsBody({ settings, onChange, tab, setTab }) {
  const t = useT();
  const ACCENTS = window.ACCENTS || [];
  return (
    <div className="set-shell">
      <nav className="set-nav">
        {SETTINGS_TABS.map((st) => (
          <button key={st.id} className={cn("set-nav-item", tab === st.id && "is-active")} onClick={() => setTab(st.id)}>
            {t(st.tk)}
          </button>
        ))}
      </nav>
      <div className="set-pane">
        {tab === "general" && (
          <>
            <div className="set-row">
              <div><div className="set-label">Language</div><div className="set-hint">Re-renders the whole interface instantly.</div></div>
              <select className="set-select" value={settings.language} onChange={(e) => onChange({ language: e.target.value })}>
                {window.LANGUAGES.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}
              </select>
            </div>
            <div className="set-row"><div className="set-label">Sound effects</div>
              <Switch on={settings.soundEnabled} onChange={(v) => onChange({ soundEnabled: v })} label="Sound effects" /></div>
            <div className="set-row"><div className="set-label">Notifications</div>
              <Switch on={settings.notificationsEnabled} onChange={(v) => onChange({ notificationsEnabled: v })} label="Notifications" /></div>
            <div className="set-row"><div className="set-label">Auto-save drafts</div>
              <Switch on={settings.autoSave} onChange={(v) => onChange({ autoSave: v })} label="Auto-save" /></div>
          </>
        )}
        {tab === "model" && (
          <>
            <div className="set-row"><div className="set-label">Default model</div>
              <select className="set-select" value={settings.model} onChange={(e) => onChange({ model: e.target.value })}>
                {window.MODELS.map((m) => <option key={m} value={m}>{m}</option>)}
              </select></div>
            <div className="set-row"><div><div className="set-label">Creativity</div><div className="set-hint">Temperature for generation.</div></div>
              <div className="set-slider">
                <input type="range" min="0" max="100" value={settings.creativity}
                       onChange={(e) => onChange({ creativity: +e.target.value })} />
                <span className="mono muted">{(settings.creativity / 100).toFixed(2)}</span>
              </div></div>
            <div className="set-row"><div className="set-label">Max tokens</div>
              <div className="set-slider">
                <input type="range" min="1000" max="200000" step="1000" value={settings.maxTokens}
                       onChange={(e) => onChange({ maxTokens: +e.target.value })} />
                <span className="mono muted">{(settings.maxTokens / 1000).toFixed(0)}k</span>
              </div></div>
            <div className="set-row"><div className="set-label">Show tool calls</div>
              <Switch on={settings.showTools} onChange={(v) => onChange({ showTools: v })} label="Show tool calls" /></div>
          </>
        )}
        {tab === "appearance" && (
          <>
            <div className="set-row"><div className="set-label">Dark mode</div>
              <Switch on={settings.dark} onChange={(v) => onChange({ dark: v })} label="Dark mode" /></div>
            <div className="set-row"><div className="set-label">Density</div>
              <select className="set-select" value={settings.density} onChange={(e) => onChange({ density: e.target.value })}>
                <option value="compact">Compact</option>
                <option value="regular">Regular</option>
                <option value="comfy">Comfy</option>
              </select></div>
            <div className="set-row"><div><div className="set-label">Accent</div><div className="set-hint">Applied across the whole interface.</div></div>
              <div className="swatch-row">
                {ACCENTS.map((a) => (
                  <button key={a.id} className={cn("swatch", settings.accent === a.id && "is-active")}
                          style={{ background: a.accent }} aria-label={a.id}
                          onClick={() => onChange({ accent: a.id })} />
                ))}
              </div></div>
          </>
        )}
        {tab === "audit" && (
          <table className="audit-table">
            <thead><tr><th>Event</th><th>Target</th><th>Actor</th><th>When</th></tr></thead>
            <tbody>
              {(window.AUDIT_LOG || []).map((e, i) => (
                <tr key={i}><td>{e.event}</td><td className="mono">{e.target}</td><td>{e.actor}</td><td className="muted">{e.when}</td></tr>
              ))}
            </tbody>
          </table>
        )}
        {tab === "shortcuts" && (
          <div className="kbd-grid">
            {SHORTCUTS.map((s, i) => (
              <div key={i} className="kbd-row"><span>{s.a}</span><span className="kbd">{s.k}</span></div>
            ))}
          </div>
        )}
        {tab === "about" && (
          <div className="about">
            <div className="about-brand">
              <span className="brand-mark">L</span>
              <div><div style={{ fontWeight: 600, fontSize: 15 }}>Lattice</div><div className="mono muted" style={{ fontSize: 12 }}>v2.4.0 · build 20260611</div></div>
            </div>
            <p className="about-copy">A desktop AI workspace with a minimal, high-density interface. Zero build step — React + Babel in the browser.</p>
            <div className="about-links">
              <button className="btn btn-sm"><Icon name="book" />Documentation</button>
              <button className="btn btn-sm"><Icon name="globe" />Changelog</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
function SettingsModal({ open, onClose, settings, onChange }) {
  const t = useT();
  const [tab, setTab] = useStateM("general");
  return (
    <Modal open={open} onClose={onClose} title={t("set.title")} width={640}>
      <SettingsBody settings={settings} onChange={onChange} tab={tab} setTab={setTab} />
    </Modal>
  );
}
function SettingsView({ settings, onChange }) {
  const t = useT();
  const [tab, setTab] = useStateM("general");
  return (
    <div className="view">
      <ViewHeader icon="gear" title={t("set.title")} subtitle="Global preferences" />
      <div className="view-body set-view-body">
        <SettingsBody settings={settings} onChange={onChange} tab={tab} setTab={setTab} />
      </div>
    </div>
  );
}

Object.assign(window, { SETTINGS_TABS, SHORTCUTS, MyAccountBody, MyAccountModal, MyAccountView, SettingsBody, SettingsModal, SettingsView });
