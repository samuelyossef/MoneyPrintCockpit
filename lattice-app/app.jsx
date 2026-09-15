/* ============================================================
   app.jsx — Root, App, Sidebar, ConvoRow, Breadcrumb, Thread,
   Message/TextBlock/ToolCard, TasksPanel, EmptyHome, seeds
   ============================================================ */
const { useState: U, useEffect: E, useRef: R, useMemo: M, useCallback: CB } = React;

/* ---------------- constants ---------------- */
const MODELS = ["lattice-4.1", "lattice-4.1 · pro", "lattice-4.1 · max", "lattice-3.5 · fast"];
const ACCENTS = [
  { id: "blue", accent: "#2563eb", ink: "#1d4ed8" },
  { id: "green", accent: "#16a34a", ink: "#15803d" },
  { id: "purple", accent: "#8b5cf6", ink: "#7c3aed" },
  { id: "red", accent: "#ef4444", ink: "#dc2626" },
  { id: "teal", accent: "#0d9488", ink: "#0f766e" },
  { id: "orange", accent: "#ea580c", ink: "#c2410c" },
];
window.MODELS = MODELS; window.ACCENTS = ACCENTS;

const INITIAL_APP_SETTINGS = {
  language: "en", soundEnabled: true, notificationsEnabled: true, autoSave: true,
  model: "lattice-4.1", creativity: 35, maxTokens: 64000, showTools: true,
  dark: false, density: "regular", accent: "blue",
};
const INITIAL_ACCOUNT = {
  name: "Avery Chen", email: "avery@acme.co", workspace: "Acme Engineering",
  tz: "America/Sao_Paulo", plan: "Pro", model: "lattice-4.1 · pro",
  used: 184230, cap: 500000,
};

const hrAgo = (n) => Date.now() - n * 3600 * 1000;
const minAgo = (n) => Date.now() - n * 60 * 1000;

/* ---------------- seed conversations ---------------- */
const DEMO_BLOCKS = [
  { kind: "text", text: "Looking at the harness setup — it boots Postgres through `applyPendingMigrations`, which runs the full migration list. That means **work_products** and **issue_approvals** are already visible to the gate." },
  { kind: "tool", tag: "Bash", title: "Run shared verification-gate tests", meta: "0.8s · 19/19 ✓", open: false,
    body: "$ pnpm test packages/server/verification-gate\n\n  ✓ blocks low-confidence advisory (84ms)\n  ✓ allows passthrough on bypass-by-board (61ms)\n  ✓ rejects mismatched actor on update (44ms)\n\n  19 passed (19)" },
  { kind: "text", text: "Here's the current verification matrix across the gate's suites:" },
  { kind: "artifact", artifactId: "art-testmatrix" },
  { kind: "tool", tag: "Edit", title: "verification-gate.integration.test.ts", meta: "+84 −0", open: false,
    diff: [
      { t: "add", n: 1, s: "import { describe, it, expect } from 'vitest';" },
      { t: "add", n: 2, s: "import { applyPendingMigrations } from '../harness/pg';" },
      { t: "add", n: 3, s: "import { evaluateGate } from '../verification-gate';" },
      { t: "add", n: 4, s: "" },
      { t: "add", n: 5, s: "describe('verification-gate enforcement', () => {" },
    ] },
  { kind: "text", text: "Stage 21 is closed. Moving to stage 12 — the envelope is declared in the prompt but the real enforcement lives in `toolGovernanceService.evaluate`." },
];

const initialConvos = [
  { id: "c1", title: "Verification gate rollout", project: "verification-gate", pinned: true, archived: false,
    updatedAt: minAgo(8), meta: "2h", snippet: "Confirm the embedded PG harness applies our migrations…", hasPreview: true,
    messages: [
      { id: "m1", role: "user", text: "Confirm the embedded PG harness applies our migrations. I need the issue_work_products columns visible to the gate." },
      { id: "m2", role: "assistant", blocks: DEMO_BLOCKS },
    ] },
  { id: "c2", title: "Embedded PG harness migrations", project: "verification-gate", pinned: false, archived: false,
    updatedAt: minAgo(40), meta: "now", snippet: "Migration ordering for the embedded harness.", hasPreview: false, messages: [] },
  { id: "c3", title: "Q3 OKR draft", project: "onboarding-ttv", pinned: false, archived: false,
    updatedAt: hrAgo(26), meta: "1d", snippet: "Three objectives for time-to-value.", hasPreview: true,
    messages: [
      { id: "m3", role: "user", text: "Draft the Q3 OKRs for onboarding time-to-value." },
      { id: "m4", role: "assistant", blocks: [
        { kind: "text", text: "Here's a first pass focused on halving time-to-first-output. I split it into two objectives:" },
        { kind: "artifact", artifactId: "art-okr" },
      ] },
    ] },
  { id: "c4", title: "TypeBox codegen migration", project: "verification-gate", pinned: false, archived: false,
    updatedAt: hrAgo(72), meta: "3d", snippet: "@lattice/types must stay structurally identical.", hasPreview: false, messages: [] },
  { id: "c5", title: "Throttle helper review", project: "design-system", pinned: false, archived: false,
    updatedAt: hrAgo(50), meta: "2d", snippet: "Leading + trailing edge throttle.", hasPreview: true,
    messages: [
      { id: "m5", role: "user", text: "Review this throttled counter for correctness." },
      { id: "m6", role: "assistant", blocks: [
        { kind: "text", text: "The leading + trailing edges look correct. One note: `lastArgs` is captured on every call, so the trailing invocation uses the most recent arguments — which is usually what you want." },
        { kind: "artifact", artifactId: "art-throttle" },
      ] },
    ] },
  { id: "c6", title: "Landing hero copy", project: "design-system", pinned: false, archived: false,
    updatedAt: hrAgo(30), meta: "1d", snippet: "Hero section for the marketing site.", hasPreview: true,
    messages: [
      { id: "m7", role: "user", text: "Give me a hero section for the landing page." },
      { id: "m8", role: "assistant", blocks: [
        { kind: "text", text: "Here's a focused hero with a single call to action:" },
        { kind: "artifact", artifactId: "art-landing" },
      ] },
    ] },
  { id: "c7", title: "Chat output inventory", project: null, pinned: false, archived: false,
    updatedAt: hrAgo(4), meta: "4h", snippet: "Who renders each block type.", hasPreview: true,
    messages: [
      { id: "m9", role: "user", text: "Give me an inventory of the chat output elements and who renders each one." },
      { id: "m10", role: "assistant", blocks: [
        { kind: "text", text: "Following the rule: the short comparison goes in the inline table; the full breakdown lives in the artifact below." },
        { kind: "artifact", artifactId: "art-adoption" },
      ] },
    ] },
  { id: "c8", title: "Planner unit tests", project: null, pinned: false, archived: false,
    updatedAt: hrAgo(120), meta: "5d", snippet: "Flaky planner test triage.", hasPreview: false, messages: [] },
  { id: "c9", title: "Sprint retro notes", project: "onboarding-ttv", pinned: false, archived: true,
    updatedAt: hrAgo(200), meta: "1w", snippet: "Archived retro from last cycle.", hasPreview: false, messages: [] },
  { id: "c10", title: "Old API deprecation", project: null, pinned: false, archived: true,
    updatedAt: hrAgo(400), meta: "3w", snippet: "Deprecated v1 endpoints.", hasPreview: false, messages: [] },
];

const RECENT_GROUPS = [
  { key: "pinned", match: (c) => c.pinned && !c.archived },
  { key: "today", match: (c) => !c.pinned && !c.archived && c.updatedAt > hrAgo(20) },
  { key: "earlier", match: (c) => !c.pinned && !c.archived && c.updatedAt <= hrAgo(20) },
  { key: "archived", match: (c) => c.archived },
];

const TASKS_SEED = [
  { id: "t1", name: "Run intake classifier and model tests", tool: "Bash", status: "running", duration: "running…", detail: "Executing the intake classifier suite against the staging fixtures." },
  { id: "t2", name: "Run planner unit tests", tool: "Bash", status: "queued", duration: "queued", detail: "Queued behind the classifier run." },
  { id: "t3", name: "Typecheck server package", tool: "Bash", status: "error", duration: "exit 2", detail: "tsc reported 2 errors in toolGovernanceService.ts.", error: "TS2345: Argument of type 'Envelope' is not assignable to 'GateInput'." },
  { id: "t4", name: "Run verification-gate enforcement integration", tool: "Bash", status: "done", duration: "1.2s", detail: "All 19 enforcement cases passed." },
  { id: "t5", name: "Run issues-service integration (embedded PG)", tool: "Bash", status: "done", duration: "2.8s", detail: "Embedded Postgres booted and migrations applied." },
  { id: "t6", name: "Run shared verification-gate tests", tool: "Bash", status: "done", duration: "0.8s", detail: "Shared suite green." },
  { id: "t7", name: "Check skills / global existence and references", tool: "Grep", status: "done", duration: "0.2s", detail: "No dangling references found." },
];

/* ---------------- inline markdown for chat prose ---------------- */
function renderInline(text) {
  const nodes = [];
  const re = /(\*\*\*[^*]+\*\*\*|\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g;
  let last = 0, m, key = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("***")) nodes.push(<strong key={key++}><em>{tok.slice(3, -3)}</em></strong>);
    else if (tok.startsWith("**")) nodes.push(<strong key={key++}>{tok.slice(2, -2)}</strong>);
    else if (tok.startsWith("`")) nodes.push(<code key={key++}>{tok.slice(1, -1)}</code>);
    else if (tok.startsWith("*")) nodes.push(<em key={key++}>{tok.slice(1, -1)}</em>);
    else if (tok.startsWith("[")) {
      const mm = tok.match(/\[([^\]]+)\]\(([^)]+)\)/);
      nodes.push(<a key={key++} href={mm[2]} target="_blank" rel="noopener noreferrer">{mm[1]}</a>);
    }
    last = re.lastIndex;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

function TextBlock({ text, streaming }) {
  const paras = text.split(/\n\n+/);
  return (
    <div className="msg-text">
      {paras.map((p, i) => (
        <p key={i}>
          {p.split("\n").map((line, j) => (
            <React.Fragment key={j}>{j > 0 && <br />}{renderInline(line)}</React.Fragment>
          ))}
          {streaming && i === paras.length - 1 && <span className="caret" />}
        </p>
      ))}
    </div>
  );
}

function ToolCard({ block }) {
  const [open, setOpen] = U(!!block.open);
  const tagClass = (block.tag || "tool").toLowerCase();
  return (
    <div className={cn("tool", open && "open")}>
      <div className="tool-hd" onClick={() => setOpen((o) => !o)}>
        <span className={cn("tool-tag", tagClass)}>{block.tag || "tool"}</span>
        <span className="tool-title">{block.title}</span>
        {block.meta && <span className="tool-meta">{block.meta}</span>}
        <span className="tool-chev"><Icon name="chevRight" /></span>
      </div>
      {open && (
        <div className="tool-body">
          {block.diff
            ? block.diff.map((d, i) => (
                <div key={i}>
                  <span className="gutter">{d.n}</span>
                  <span className={d.t === "add" ? "added" : d.t === "rem" ? "removed" : ""}>{d.t === "add" ? "+ " : d.t === "rem" ? "- " : "  "}{d.s}</span>
                </div>
              ))
            : block.body}
        </div>
      )}
    </div>
  );
}

function Message({ m, streaming, onCopy, onRegen, onOpenArtifact }) {
  const [liked, setLiked] = U(false), [disliked, setDisliked] = U(false);
  if (m.role === "user") return <div className="msg-user">{m.text}</div>;
  const blocks = m.blocks || [];
  const lastTextIdx = blocks.map((b) => b.kind).lastIndexOf("text");
  const serialize = () => blocks.map((b) => {
    if (b.kind === "text") return b.text;
    if (b.kind === "artifact") { const a = window.ARTIFACTS_SAMPLE[b.artifactId]; return a ? `[artifact] ${a.title}\n${a.content}` : ""; }
    return `[${b.tag}] ${b.title}\n${b.body || (b.diff ? b.diff.map((d) => (d.t === "add" ? "+" : d.t === "rem" ? "-" : " ") + d.s).join("\n") : "")}`;
  }).filter(Boolean).join("\n\n");

  return (
    <div className="msg-assist">
      {blocks.map((b, i) => {
        if (b.kind === "text") return <TextBlock key={i} text={b.text} streaming={streaming && i === lastTextIdx} />;
        if (b.kind === "artifact") {
          const a = window.ARTIFACTS_SAMPLE[b.artifactId];
          if (!a) return null;
          return <ArtifactCard key={i} artifact={a} onOpen={onOpenArtifact} />;
        }
        return <ToolCard key={i} block={b} />;
      })}
      {!streaming && blocks.length > 0 && (
        <MsgActions onCopy={() => onCopy(serialize())} onRegen={onRegen}
          liked={liked} disliked={disliked}
          onLike={() => { setLiked((v) => !v); setDisliked(false); }}
          onDislike={() => { setDisliked((v) => !v); setLiked(false); }} />
      )}
    </div>
  );
}

/* ---------------- ConvoRow ---------------- */
function ConvoRow({ c, active, editing, onSelect, onStartEdit, onCommitEdit, onMenu }) {
  const inputRef = R(null);
  E(() => { if (editing && inputRef.current) { inputRef.current.focus(); inputRef.current.select(); } }, [editing]);
  return (
    <div className={cn("convo", active && "active", c.pinned && "pinned", c.archived && "archived")}
         onClick={() => onSelect(c.id)}
         onDoubleClick={(e) => { e.preventDefault(); onStartEdit(c.id); }}
         onContextMenu={(e) => { e.preventDefault(); onMenu(e.currentTarget.getBoundingClientRect(), c.id); }}>
      {editing ? (
        <input ref={inputRef} className="convo-edit" defaultValue={c.title}
               onClick={(e) => e.stopPropagation()}
               onBlur={(e) => onCommitEdit(c.id, e.target.value)}
               onKeyDown={(e) => { if (e.key === "Enter") onCommitEdit(c.id, e.target.value); else if (e.key === "Escape") onCommitEdit(c.id, c.title); }} />
      ) : (
        <>
          <span className="convo-title">{c.title}</span>
          <span className="convo-meta">{c.meta}</span>
          <button className="convo-more" onClick={(e) => { e.stopPropagation(); onMenu(e.currentTarget.getBoundingClientRect(), c.id); }} aria-label="Conversation actions">
            <Icon name="more" />
          </button>
        </>
      )}
    </div>
  );
}

/* ---------------- Sidebar ---------------- */
function Sidebar({ collapsed, onToggle, convos, activeId, editingId, view,
                  onNewChat, onSearch, onNav, onSelectConvo, onStartEdit, onCommitEdit, onConvoMenu, account, onAccount }) {
  const t = useT();
  const NAV = [
    { id: "projects", label: t("sb.projects"), icon: "folder", view: "projects" },
    { id: "history", label: t("sb.history"), icon: "clock", view: "history" },
    { id: "knowledge", label: t("sb.knowledge"), icon: "book", view: "knowledge" },
    { id: "connectors", label: t("sb.connectors"), icon: "plug", view: "connectors" },
    { id: "skills", label: t("sb.skills"), icon: "puzzle", view: "skills" },
  ];
  return (
    <aside className={cn("sidebar", collapsed && "is-collapsed")}>
      <div className="sb-top">
        <Action icon="panelLeft" label={collapsed ? t("sb.expand") : t("sb.collapse")} className="sb-toggle" onClick={onToggle} tip={false} />
      </div>
      <div className="sb-actions">
        <button className="sb-act primary" onClick={() => onNewChat()}>
          <Icon name="plus" /><span className="sb-label">{t("sb.newChat")}</span><span className="kbd">⌘N</span>
        </button>
        <button className="sb-act" onClick={onSearch}>
          <Icon name="search" /><span className="sb-label">{t("sb.search")}</span><span className="kbd">⌘K</span>
        </button>
        {NAV.map((n) => (
          <button key={n.id} className={cn("sb-act", view.kind === n.view && "is-active")} onClick={() => onNav(n.view)}>
            <Icon name={n.icon} /><span className="sb-label">{n.label}</span>
          </button>
        ))}
      </div>

      <div className="sb-section"><span>{t("recent.today")}</span></div>
      <div className="sb-list">
        {RECENT_GROUPS.map((g) => {
          const items = convos.filter(g.match).sort((a, b) => b.updatedAt - a.updatedAt);
          if (!items.length) return null;
          return (
            <React.Fragment key={g.key}>
              <div className="sb-section" style={{ paddingLeft: 8 }}>
                <span>{t("recent." + g.key)}</span><span className="count">{items.length}</span>
              </div>
              {items.map((c) => (
                <ConvoRow key={c.id} c={c} active={c.id === activeId && view.kind === "chat"} editing={editingId === c.id}
                  onSelect={onSelectConvo} onStartEdit={onStartEdit} onCommitEdit={onCommitEdit} onMenu={onConvoMenu} />
              ))}
            </React.Fragment>
          );
        })}
      </div>

      <div className="sb-foot" onClick={(e) => onAccount(e.currentTarget.getBoundingClientRect())} style={{ cursor: "default" }}>
        <span className="avatar">{account.name.split(" ").map((s) => s[0]).slice(0, 2).join("")}</span>
        <span className="sb-who">
          <span className="who">{account.name}</span>
          <span className="plan">{account.plan}</span>
        </span>
      </div>
    </aside>
  );
}

/* ---------------- Breadcrumb ---------------- */
function Breadcrumb({ view, convo, onToggleSidebar, onToggleTasks, onHome, tasksOpen, artOpen }) {
  const t = useT();
  let trail;
  if (view.kind === "chat") {
    trail = convo
      ? <><button className="crumbs-btn root" onClick={onHome}>Lattice</button><span className="sep">/</span><span className="leaf">{convo.title}</span></>
      : <button className="crumbs-btn root" onClick={onHome}>Lattice</button>;
  } else if (view.kind === "project") {
    const p = PROJECT_META[view.projectId];
    trail = <><button className="crumbs-btn root" onClick={() => onHome()}>Lattice</button><span className="sep">/</span><span className="leaf">{p ? p.name : "Project"}</span></>;
  } else {
    const titles = { projects: t("sb.projects"), history: t("sb.history"), archive: t("menu.archive"), knowledge: t("sb.knowledge"), connectors: t("sb.connectors"), skills: t("sb.skills"), settings: t("set.title"), account: t("view.account.title") };
    trail = <><button className="crumbs-btn root" onClick={onHome}>Lattice</button><span className="sep">/</span><span className="leaf">{titles[view.kind] || view.kind}</span></>;
  }
  return (
    <div className="topbar">
      <Action icon="menu" label="Menu" className="topbar-menu" onClick={onToggleSidebar} tip={false} />
      <div className="crumbs">{trail}</div>
      <div className="top-right">
        <span className="top-pill"><span className="dot" />{t("top.connected")}</span>
        <Action icon="bell" label="Notifications" tipSide="bottom" />
        <Action icon="panelRight" label={tasksOpen ? "Hide tasks" : "Show tasks"} active={tasksOpen && !artOpen} onClick={onToggleTasks} tipSide="bottom" />
      </div>
    </div>
  );
}

/* ---------------- TasksPanel ---------------- */
function describeTask(tk) { return tk.detail || `${tk.tool} task — ${tk.status}`; }
function TaskItem({ tk, onStop, onCancel, onRetry }) {
  const [open, setOpen] = U(false);
  return (
    <div className={cn("task", open && "is-open")} data-status={tk.status}
         tabIndex={0} role="button" onClick={() => setOpen((o) => !o)}
         onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setOpen((o) => !o); } }}>
      <div className="task-head">
        <span className="task-dot" />
        <div className="task-main">
          <div className="task-name">{tk.name}</div>
          <div className="task-sub">
            <span className="task-tool">{tk.tool}</span>
            <span>{tk.status === "running" ? "running…" : tk.status === "queued" ? "queued" : tk.status === "error" ? "error · " + tk.duration : "done · " + tk.duration}</span>
            {tk.status === "running" && <button className="task-stop" onClick={(e) => { e.stopPropagation(); onStop(tk.id); }}><Icon name="stop" />stop</button>}
            {tk.status === "queued" && <button className="task-stop" onClick={(e) => { e.stopPropagation(); onCancel(tk.id); }}>cancel</button>}
            {tk.status === "error" && <button className="retry" onClick={(e) => { e.stopPropagation(); onRetry(tk.id); }}>retry</button>}
          </div>
        </div>
        <span className="task-chev"><Icon name="chevRight" /></span>
      </div>
      {open && (
        <div className="task-detail">
          <p className="task-desc">{describeTask(tk)}</p>
          <dl className="task-meta">
            <div><dt>Tool</dt><dd>{tk.tool}</dd></div>
            <div><dt>Status</dt><dd>{tk.status}</dd></div>
            <div><dt>Duration</dt><dd>{tk.duration}</dd></div>
            {tk.error && <div><dt>Error</dt><dd className="mono">{tk.error}</dd></div>}
          </dl>
        </div>
      )}
    </div>
  );
}
function TasksPanel({ tasks, onClear, onStop, onCancel, onRetry }) {
  const t = useT();
  const groups = [
    { key: "running", label: t("tasks.running") },
    { key: "queued", label: t("tasks.queued") },
    { key: "error", label: t("tasks.errors") },
    { key: "done", label: t("tasks.done") },
  ];
  const startResize = (e) => {
    e.preventDefault();
    const app = document.querySelector(".app");
    const startX = e.clientX;
    const startW = parseInt(getComputedStyle(app).getPropertyValue("--tasks-w")) || 340;
    const move = (ev) => { let w = Math.max(280, Math.min(520, startW - (ev.clientX - startX))); app.style.setProperty("--tasks-w", w + "px"); };
    const up = () => { document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up); localStorage.setItem("lattice.tasks.w", parseInt(getComputedStyle(app).getPropertyValue("--tasks-w")) || 340); };
    document.addEventListener("mousemove", move); document.addEventListener("mouseup", up);
  };
  const resetW = () => { const app = document.querySelector(".app"); app.style.setProperty("--tasks-w", "340px"); localStorage.setItem("lattice.tasks.w", 340); };
  return (
    <aside className="tasks">
      <div className="tasks-resize" onMouseDown={startResize} onDoubleClick={resetW} />
      <div className="tasks-hd">
        <h3>{t("tasks.title")}</h3>
        <button className="clear" onClick={onClear}>{t("tasks.clear")}</button>
      </div>
      <div className="tasks-list">
        {tasks.length === 0 && <div className="view-empty" style={{ padding: "40px 16px" }}><p>{t("tasks.empty")}</p></div>}
        {groups.map((g) => {
          const items = tasks.filter((tk) => tk.status === g.key);
          if (!items.length) return null;
          return (
            <React.Fragment key={g.key}>
              <div className="tasks-section"><span>{g.label}</span><span className="line" /><span className="count mono">{String(items.length).padStart(2, "0")}</span></div>
              {items.map((tk) => <TaskItem key={tk.id} tk={tk} onStop={onStop} onCancel={onCancel} onRetry={onRetry} />)}
            </React.Fragment>
          );
        })}
      </div>
    </aside>
  );
}

/* ---------------- EmptyHome ---------------- */
function Starburst() {
  return (
    <span className="greet-icon"><svg className="starburst" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
      <path d="M12 2v5M12 17v5M2 12h5M17 12h5M5 5l3.5 3.5M15.5 15.5 19 19M19 5l-3.5 3.5M8.5 15.5 5 19" />
    </svg></span>
  );
}
function EmptyHome({ account, draft, onDraft, onSend, model, onPickModel, tokens }) {
  const greeting = (() => { const h = new Date().getHours(); return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening"; })();
  const chips = [
    { icon: "spark", label: "Summarize the test matrix" },
    { icon: "code", label: "Review a diff" },
    { icon: "book", label: "Draft Q3 OKRs" },
    { icon: "table", label: "Build a comparison table" },
  ];
  return (
    <div className="empty-shell">
      <div className="empty-home">
        <div className="greet"><Starburst /><h1 className="greet-text">{greeting}, {account.name.split(" ")[0]}</h1></div>
        <div className="composer-wrap">
          <Composer value={draft} onChange={onDraft} onSend={onSend} tokens={tokens}
                    model={model} onPickModel={onPickModel} rotatePlaceholder />
        </div>
        <div className="quick-chips">
          {chips.map((c, i) => (
            <button key={i} className="quick-chip" onClick={() => onDraft(c.label)}>
              <span className="quick-chip-icon"><Icon name={c.icon} /></span>{c.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ============================================================
   App
   ============================================================ */
function App({ appSettings, setAppSettings, account, setAccount }) {
  const t = useT();
  const confirm = useConfirm();
  const isMobile = useMediaQuery("(max-width: 900px)");

  const [convos, setConvos] = U(initialConvos);
  const [activeId, setActiveId] = U("c1");
  const [view, setView] = U({ kind: "chat" });
  const [draft, setDraft] = U("");
  const [streaming, setStreaming] = U(false);
  const [sidebarOpen, setSidebarOpen] = U(!isMobile);
  const [tasksOpen, setTasksOpen] = U(false);
  const [artifact, setArtifact] = U(null);
  const [filePreview, setFilePreview] = U(null);
  const [tasks, setTasks] = U(TASKS_SEED);
  const [newProjectOpen, setNewProjectOpen] = U(false);
  const [settingsOpen, setSettingsOpen] = U(false);
  const [accountOpen, setAccountOpen] = U(false);
  const [searchOpen, setSearchOpen] = U(false);
  const [editingId, setEditingId] = U(null);
  const [convoMenu, setConvoMenu] = U(null);     // {rect, id}
  const [accountMenuRect, setAccountMenuRect] = U(null);
  const [modelMenuRect, setModelMenuRect] = U(null);
  const [langMenuRect, setLangMenuRect] = U(null);
  const [pjVersion, setPjVersion] = U(0);
  const streamRef = R(null);

  const activeConvo = convos.find((c) => c.id === activeId);
  const tokens = Math.max(0, Math.ceil(draft.trim().length / 4));

  /* appearance handled in Root; restore panel widths */
  E(() => {
    const app = document.querySelector(".app");
    if (!app) return;
    const aw = localStorage.getItem("lattice.art.w"); if (aw) app.style.setProperty("--art-w", aw + "px");
    const tw = localStorage.getItem("lattice.tasks.w"); if (tw) app.style.setProperty("--tasks-w", tw + "px");
  }, []);

  E(() => { setSidebarOpen(!isMobile); }, [isMobile]);

  /* keyboard shortcuts */
  E(() => {
    const onKey = (e) => {
      const meta = e.metaKey || e.ctrlKey;
      const tag = (e.target.tagName || "").toLowerCase();
      const typing = tag === "input" || tag === "textarea" || e.target.isContentEditable;
      if (meta && e.key.toLowerCase() === "k") { e.preventDefault(); setSearchOpen(true); }
      else if (meta && e.key.toLowerCase() === "n") { e.preventDefault(); newChat(); }
      else if (meta && e.key === ",") { e.preventDefault(); setSettingsOpen(true); }
      else if (meta && e.key.toLowerCase() === "b") { e.preventDefault(); setSidebarOpen((o) => !o); }
      else if (meta && e.key.toLowerCase() === "j") { e.preventDefault(); setTasksOpen((o) => !o); }
      else if (e.key === "F2" && !typing && activeConvo) { e.preventDefault(); setView({ kind: "chat" }); setEditingId(activeId); }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  });

  /* ---- conversation ops ---- */
  const goChat = (id) => { setActiveId(id); setView({ kind: "chat" }); setArtifact(null); setFilePreview(null); if (isMobile) setSidebarOpen(false); };
  const newChat = (projectId = null) => {
    const id = "c" + Math.random().toString(36).slice(2, 8);
    const c = { id, title: "New conversation", project: projectId, pinned: false, archived: false, updatedAt: Date.now(), meta: "now", snippet: "", hasPreview: false, messages: [] };
    setConvos((cur) => [c, ...cur]);
    goChat(id);
  };
  const togglePin = (id) => setConvos((cur) => cur.map((c) => c.id === id ? { ...c, pinned: !c.pinned, archived: c.pinned ? c.archived : false } : c));
  const archiveConvo = (id, val) => setConvos((cur) => cur.map((c) => c.id === id ? { ...c, archived: val != null ? val : !c.archived, pinned: false } : c));
  const deleteConvo = (id, silent) => {
    const run = () => setConvos((cur) => {
      const next = cur.filter((c) => c.id !== id);
      if (id === activeId) { const n = next[0]; setActiveId(n ? n.id : null); }
      return next;
    });
    if (silent) run();
    else confirm({ title: t("confirm.deleteChat.title"), danger: true, confirmLabel: t("common.delete"),
      body: <>{t("confirm.deleteChat.body")} <span className="confirm-target">“{(convos.find((c) => c.id === id) || {}).title}”</span>?</>, onConfirm: run });
  };
  const commitEdit = (id, val) => {
    const v = (val || "").trim();
    setConvos((cur) => cur.map((c) => c.id === id ? { ...c, title: v || c.title } : c));
    setEditingId(null);
  };

  /* ---- send / stream ---- */
  const send = (text) => {
    if (!text) return;
    const uid = "u" + Math.random().toString(36).slice(2);
    const aid = "a" + Math.random().toString(36).slice(2);
    const wantsTable = /table|matrix|test|csv|compare/i.test(text);
    const reply = wantsTable
      ? "Here's what I found — the gate's suites are green except `tool_governance`, which has 2 known failures. The full matrix is attached below."
      : "Got it. " + "I'll work through this step by step and surface any blockers as I go. The enforcement path runs through `toolGovernanceService.evaluate`, so that's where I'll start.";
    setConvos((cur) => cur.map((c) => c.id === activeId ? {
      ...c, updatedAt: Date.now(), meta: "now", snippet: text,
      messages: [...c.messages, { id: uid, role: "user", text }, { id: aid, role: "assistant", blocks: [{ kind: "text", text: "" }] }],
    } : c));
    setDraft("");
    setStreaming(true);
    let i = 0;
    clearInterval(streamRef.current);
    streamRef.current = setInterval(() => {
      i += Math.max(2, Math.round(reply.length / 36));
      const shown = reply.slice(0, i);
      setConvos((cur) => cur.map((c) => {
        if (c.id !== activeId) return c;
        const msgs = c.messages.slice();
        const a = msgs[msgs.length - 1];
        msgs[msgs.length - 1] = { ...a, blocks: [{ kind: "text", text: shown }] };
        return { ...c, messages: msgs };
      }));
      if (i >= reply.length) {
        clearInterval(streamRef.current);
        setStreaming(false);
        if (wantsTable) {
          setConvos((cur) => cur.map((c) => {
            if (c.id !== activeId) return c;
            const msgs = c.messages.slice();
            const a = msgs[msgs.length - 1];
            msgs[msgs.length - 1] = { ...a, blocks: [{ kind: "text", text: reply }, { kind: "artifact", artifactId: "art-testmatrix" }] };
            return { ...c, messages: msgs, hasPreview: true };
          }));
        }
      }
    }, 60);
  };
  E(() => () => clearInterval(streamRef.current), []);

  const copyText = (txt) => navigator.clipboard && navigator.clipboard.writeText(txt);

  /* ---- project ops ---- */
  const bump = () => setPjVersion((v) => v + 1);
  const createProject = ({ name, desc }) => {
    const base = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "project";
    let id = base, n = 1; while (PROJECT_META[id]) id = base + "-" + (++n);
    PROJECT_META[id] = { name, desc, color: ACCENTS[(Object.keys(PROJECT_META).length) % ACCENTS.length].accent, icon: name[0].toUpperCase(), instructions: "", defaultModel: "lattice-4.1", autoArchive: "never", shared: false };
    PROJECT_KNOWLEDGE[id] = [];
    bump(); setNewProjectOpen(false); setView({ kind: "project", projectId: id }); setArtifact(null); setFilePreview(null);
  };
  const renameProject = (id, name) => { PROJECT_META[id].name = name; bump(); };
  const updateProjectDesc = (id, desc) => { PROJECT_META[id].desc = desc; bump(); };
  const saveInstructions = (id, val) => { PROJECT_META[id].instructions = val; };
  const updateProjectSettings = (id, patch) => { PROJECT_META[id] = { ...PROJECT_META[id], ...patch }; bump(); };
  const requestDeleteProject = (id) => confirm({
    title: t("proj.delete.title"), danger: true, confirmLabel: t("common.delete"),
    body: <>{t("proj.delete.body")} <span className="confirm-target">“{PROJECT_META[id].name}”</span> and all its conversations?</>,
    onConfirm: () => {
      setConvos((cur) => cur.filter((c) => c.project !== id));
      delete PROJECT_META[id]; delete PROJECT_KNOWLEDGE[id];
      bump(); setView({ kind: "projects" });
    },
  });

  const openArtifact = (a) => { setFilePreview(null); setArtifact(a); setTasksOpen(false); };
  const openFile = (f) => { setArtifact(null); setFilePreview(f); };

  /* ---- nav helpers ---- */
  const nav = (kind) => { setView({ kind }); setArtifact(null); setFilePreview(null); if (isMobile) setSidebarOpen(false); };
  const goHome = () => { setView({ kind: "chat" }); setArtifact(null); setFilePreview(null); };

  /* ---- menus ---- */
  const menuConvo = convoMenu ? convos.find((c) => c.id === convoMenu.id) : null;
  const convoMenuItems = menuConvo ? [
    { label: t("convo.rename"), icon: "edit", onClick: () => { setView({ kind: "chat" }); setEditingId(menuConvo.id); } },
    { label: menuConvo.pinned ? t("convo.unpin") : t("convo.pin"), icon: "pin", onClick: () => togglePin(menuConvo.id) },
    { label: menuConvo.archived ? t("convo.unarchive") : t("convo.archive"), icon: "archive", onClick: () => archiveConvo(menuConvo.id) },
    { divider: true },
    { label: t("convo.delete"), icon: "trash", danger: true, onClick: () => deleteConvo(menuConvo.id) },
  ] : [];

  const accountMenuItems = [
    { section: account.email },
    { label: t("menu.account"), icon: "user", onClick: () => setAccountOpen(true) },
    { label: t("menu.settings"), icon: "gear", kbd: "⌘,", onClick: () => setSettingsOpen(true) },
    { label: t("menu.language"), icon: "globe", onClick: () => { setLangMenuRect(accountMenuRect); } },
    { label: appSettings.dark ? "Light mode" : "Dark mode", icon: appSettings.dark ? "sun" : "moon", onClick: () => setAppSettings({ dark: !appSettings.dark }) },
    { label: t("menu.archive"), icon: "archive", onClick: () => nav("archive") },
    { divider: true },
    { label: t("menu.signout"), icon: "signout", danger: true, onClick: () => {} },
  ];
  const langItems = window.LANGUAGES.map((l) => ({ label: l.label, check: appSettings.language === l.code, onClick: () => setAppSettings({ language: l.code }) }));
  const modelItems = MODELS.map((m) => ({ label: m, check: appSettings.model === m, onClick: () => setAppSettings({ model: m }) }));

  const paletteCommands = [
    { id: "new", label: t("sb.newChat"), icon: "plus", kbd: "⌘N", run: () => newChat() },
    { id: "projects", label: t("sb.projects"), icon: "folder", run: () => nav("projects") },
    { id: "history", label: t("sb.history"), icon: "clock", run: () => nav("history") },
    { id: "knowledge", label: t("sb.knowledge"), icon: "book", run: () => nav("knowledge") },
    { id: "connectors", label: t("sb.connectors"), icon: "plug", run: () => nav("connectors") },
    { id: "skills", label: t("sb.skills"), icon: "puzzle", run: () => nav("skills") },
    { id: "settings", label: t("set.title"), icon: "gear", kbd: "⌘,", run: () => setSettingsOpen(true) },
  ];

  const artOpen = !!(artifact || filePreview);
  const appData = {
    "data-sidebar": (isMobile ? (sidebarOpen ? "open" : "closed") : (sidebarOpen ? "open" : "closed")),
    "data-tasks": (tasksOpen && !artOpen ? "open" : "closed"),
    "data-art": (artOpen ? "open" : "closed"),
  };

  const showComposer = view.kind === "chat" && activeConvo;

  return (
    <div className="app" {...appData}>
      <Sidebar collapsed={!sidebarOpen && !isMobile} onToggle={() => setSidebarOpen((o) => !o)}
        convos={convos} activeId={activeId} editingId={editingId} view={view}
        onNewChat={newChat} onSearch={() => setSearchOpen(true)} onNav={nav}
        onSelectConvo={goChat} onStartEdit={setEditingId} onCommitEdit={commitEdit}
        onConvoMenu={(rect, id) => setConvoMenu({ rect, id })}
        account={account} onAccount={(rect) => setAccountMenuRect(rect)} />

      <main className="main">
        <Breadcrumb view={view} convo={activeConvo} tasksOpen={tasksOpen} artOpen={artOpen}
          onToggleSidebar={() => setSidebarOpen((o) => !o)} onToggleTasks={() => { setArtifact(null); setFilePreview(null); setTasksOpen((o) => !o); }} onHome={goHome} />

        {view.kind === "chat" && (activeConvo ? (
          activeConvo.messages.length === 0 ? (
            <EmptyHome account={account} draft={draft} onDraft={setDraft} onSend={send}
              model={appSettings.model} onPickModel={(rect) => setModelMenuRect(rect)} tokens={tokens} />
          ) : (
            <div className="thread">
              <div className="thread-inner">
                {activeConvo.messages.map((m, i) => (
                  <Message key={m.id} m={m} streaming={streaming && i === activeConvo.messages.length - 1}
                    onCopy={copyText} onRegen={() => {}} onOpenArtifact={openArtifact} />
                ))}
              </div>
            </div>
          )
        ) : (
          <EmptyHome account={account} draft={draft} onDraft={setDraft} onSend={(txt) => { newChat(); setTimeout(() => send(txt), 30); }}
            model={appSettings.model} onPickModel={(rect) => setModelMenuRect(rect)} tokens={tokens} />
        ))}

        {view.kind === "projects" && <ProjectsView convos={convos} version={pjVersion} onOpenProject={(id) => setView({ kind: "project", projectId: id })} onNewProject={() => setNewProjectOpen(true)} />}
        {view.kind === "project" && PROJECT_META[view.projectId] && (
          <ProjectDetailView projectId={view.projectId} convos={convos}
            onBack={() => setView({ kind: "projects" })} onOpenChat={goChat} onOpenFile={openFile}
            onRename={renameProject} onUpdateDesc={updateProjectDesc} onNewChat={(id) => newChat(id)}
            onSaveInstructions={saveInstructions} onUpdateSettings={updateProjectSettings} onDelete={requestDeleteProject} />
        )}
        {view.kind === "history" && <HistoryView convos={convos} onOpenChat={goChat} onArchive={archiveConvo} onDelete={deleteConvo} archivedOnly={false} />}
        {view.kind === "archive" && <HistoryView convos={convos} onOpenChat={goChat} onArchive={archiveConvo} onDelete={deleteConvo} archivedOnly={true} />}
        {view.kind === "knowledge" && <KnowledgeView onOpenFile={openFile} />}
        {view.kind === "connectors" && <ConnectorsView />}
        {view.kind === "skills" && <SkillsView />}
        {view.kind === "settings" && <SettingsView settings={appSettings} onChange={setAppSettings} />}
        {view.kind === "account" && <MyAccountView account={account} onChange={setAccount} onSignOut={() => {}} />}

        {showComposer && (
          <div className="composer-wrap">
            <Composer value={draft} onChange={setDraft} onSend={send} disabled={streaming} tokens={tokens}
              model={appSettings.model} onPickModel={(rect) => setModelMenuRect(rect)} />
          </div>
        )}
      </main>

      {artifact ? <ArtifactPanel artifact={artifact} onClose={() => setArtifact(null)} />
        : filePreview ? <FileViewerPanel file={filePreview} onClose={() => setFilePreview(null)} />
          : <TasksPanel tasks={tasks} onClear={() => setTasks([])}
              onStop={(id) => setTasks((cur) => cur.map((tk) => tk.id === id ? { ...tk, status: "done", duration: "stopped" } : tk))}
              onCancel={(id) => setTasks((cur) => cur.filter((tk) => tk.id !== id))}
              onRetry={(id) => setTasks((cur) => cur.map((tk) => tk.id === id ? { ...tk, status: "running", duration: "running…" } : tk))} />}

      {isMobile && <div className="mobile-backdrop" onClick={() => { setSidebarOpen(false); setTasksOpen(false); setArtifact(null); setFilePreview(null); }} />}

      {/* modals + overlays */}
      <NewProjectModal open={newProjectOpen} onClose={() => setNewProjectOpen(false)} onCreate={createProject} t={t} />
      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} settings={appSettings} onChange={setAppSettings} />
      <MyAccountModal open={accountOpen} onClose={() => setAccountOpen(false)} account={account} onChange={setAccount} onSignOut={() => {}} />
      <SearchPalette open={searchOpen} onClose={() => setSearchOpen(false)} convos={convos.map((c) => ({ ...c, project: c.project ? PROJECT_META[c.project] && PROJECT_META[c.project].name : null }))}
        commands={paletteCommands} onPickConvo={(c) => goChat(c.id)} onRunCommand={(c) => c.run()} />

      <Menu open={!!convoMenu} onClose={() => setConvoMenu(null)} anchorRect={convoMenu && convoMenu.rect} items={convoMenuItems} />
      <Menu open={!!accountMenuRect && !langMenuRect} onClose={() => setAccountMenuRect(null)} anchorRect={accountMenuRect} items={accountMenuItems} minWidth={220} />
      <Menu open={!!langMenuRect} onClose={() => { setLangMenuRect(null); setAccountMenuRect(null); }} anchorRect={langMenuRect} items={langItems} minWidth={160} />
      <Menu open={!!modelMenuRect} onClose={() => setModelMenuRect(null)} anchorRect={modelMenuRect} items={modelItems} minWidth={180} />
    </div>
  );
}

/* ---------------- NewProjectModal ---------------- */
function NewProjectModal({ open, onClose, onCreate, t }) {
  const [name, setName] = U("");
  const [desc, setDesc] = U("");
  E(() => { if (open) { setName(""); setDesc(""); } }, [open]);
  const submit = () => { if (name.trim()) onCreate({ name: name.trim(), desc: desc.trim() }); };
  return (
    <Modal open={open} onClose={onClose} title={t("np.title")} width={520}>
      <p className="np-subtitle">{t("np.subtitle")}</p>
      <div className="np-form" onKeyDown={(e) => { if (e.key === "Enter") submit(); }}>
        <div className="np-field">
          <label className="np-label">{t("np.name")}</label>
          <input className="np-input" autoFocus value={name} placeholder={t("np.namePh")} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="np-field">
          <label className="np-label">{t("np.desc")}</label>
          <input className="np-input" value={desc} placeholder={t("np.descPh")} onChange={(e) => setDesc(e.target.value)} />
        </div>
      </div>
      <div className="modal-foot">
        <button className="btn" onClick={onClose}>{t("common.cancel")}</button>
        <button className="btn primary" disabled={!name.trim()} onClick={submit}>{t("np.create")}</button>
      </div>
    </Modal>
  );
}

/* ---------------- Root (providers + appearance + tweaks) ---------------- */
function Root() {
  const [appSettings, setAppSettings] = U(INITIAL_APP_SETTINGS);
  const [account, setAccount] = U(INITIAL_ACCOUNT);
  const patchSettings = (patch) => setAppSettings((s) => ({ ...s, ...patch }));
  const patchAccount = (patch) => setAccount((a) => ({ ...a, ...patch }));

  E(() => {
    const root = document.documentElement;
    root.setAttribute("data-theme", appSettings.dark ? "dark" : "light");
    const acc = ACCENTS.find((a) => a.id === appSettings.accent) || ACCENTS[0];
    root.style.setProperty("--accent", acc.accent);
    root.style.setProperty("--accent-ink", acc.ink);
    document.body.style.fontSize = ({ compact: 13, regular: 14, comfy: 15 }[appSettings.density] || 14) + "px";
  }, [appSettings.dark, appSettings.accent, appSettings.density]);

  const accentHex = (ACCENTS.find((a) => a.id === appSettings.accent) || ACCENTS[0]).accent;

  return (
    <ConfirmProvider>
      <LangContext.Provider value={appSettings.language}>
        <App appSettings={appSettings} setAppSettings={patchSettings} account={account} setAccount={patchAccount} />
        <TweaksPanel title="Tweaks">
          <TweakSection label="Appearance" />
          <TweakToggle label="Dark mode" value={appSettings.dark} onChange={(v) => patchSettings({ dark: v })} />
          <TweakRadio label="Density" value={appSettings.density} options={["compact", "regular", "comfy"]} onChange={(v) => patchSettings({ density: v })} />
          <TweakColor label="Accent" value={accentHex} options={ACCENTS.map((a) => a.accent)}
            onChange={(hex) => { const a = ACCENTS.find((x) => x.accent === hex); if (a) patchSettings({ accent: a.id }); }} />
          <TweakSection label="Workspace" />
          <TweakSelect label="Language" value={appSettings.language} options={window.LANGUAGES.map((l) => l.code)} onChange={(v) => patchSettings({ language: v })} />
          <TweakSelect label="Model" value={appSettings.model} options={MODELS} onChange={(v) => patchSettings({ model: v })} />
        </TweaksPanel>
      </LangContext.Provider>
    </ConfirmProvider>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<Root />);
