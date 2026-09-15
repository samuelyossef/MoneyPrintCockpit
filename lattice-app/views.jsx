/* ============================================================
   views.jsx — ViewHeader, ViewTabs, Projects, ProjectDetail,
   Knowledge (inline + global), History, FileViewerPanel,
   PROJECT_META, PROJECT_KNOWLEDGE, file helpers
   ============================================================ */
const { useState: useStateV, useEffect: useEffectV, useMemo: useMemoV, useRef: useRefV } = React;

/* ---------- project data (mutable) ---------- */
const PROJECT_META = {
  "verification-gate": { name: "Verification Gate", desc: "Deterministic test harness gating every change.", color: "#2563eb", icon: "L", instructions: "You are the verification-gate engineer. Enforce the migration + test contract before any change is approved. Prefer evidence (test runs) over assertions.", defaultModel: "lattice-4.1 · pro", autoArchive: "30d", shared: true },
  "onboarding-ttv": { name: "Onboarding TTV", desc: "Halve median time-to-first-output for new teams.", color: "#1F8A5B", icon: "O", instructions: "Focus on reducing time-to-value. Quantify every recommendation with the relevant funnel metric.", defaultModel: "lattice-4.1", autoArchive: "never", shared: false },
  "design-system": { name: "Design System", desc: "Tokens, components and usage docs.", color: "#8B5CF6", icon: "D", instructions: "", defaultModel: "lattice-4.1", autoArchive: "90d", shared: true },
};

const PROJECT_KNOWLEDGE = {
  "verification-gate": [
    { id: "k1", name: "harness-architecture.md", kind: "md", size: 18422, updated: "2d ago", status: "indexed" },
    { id: "k2", name: "migration-list.csv", kind: "csv", size: 4310, updated: "2d ago", status: "indexed" },
    { id: "k3", name: "gate-spec.pdf", kind: "pdf", size: 920331, updated: "5d ago", status: "indexed" },
  ],
  "onboarding-ttv": [
    { id: "k4", name: "funnel-q2.csv", kind: "csv", size: 8211, updated: "1d ago", status: "indexed" },
    { id: "k5", name: "user-interviews.docx", kind: "docx", size: 44120, updated: "4d ago", status: "indexing" },
  ],
  "design-system": [
    { id: "k6", name: "tokens.json.txt", kind: "txt", size: 6120, updated: "1w ago", status: "indexed" },
  ],
};

function knKindFromName(name) {
  const ext = (name.split(".").pop() || "").toLowerCase();
  if (ext === "pdf") return "pdf";
  if (["doc", "docx"].includes(ext)) return "docx";
  if (ext === "md") return "md";
  if (ext === "csv") return "csv";
  if (["png", "jpg", "jpeg", "gif", "webp"].includes(ext)) return "img";
  return "txt";
}
function knFormatSize(b) {
  if (b < 1024) return b + " B";
  if (b < 1024 * 1024) return (b / 1024).toFixed(0) + " KB";
  return (b / 1024 / 1024).toFixed(1) + " MB";
}
const FILE_ICON_COLOR = { pdf: "#ef4444", docx: "#2563eb", md: "#2EA47A", csv: "#16a34a", img: "#8B5CF6", txt: "#6b7280" };
const FILE_ICON_NAME = { pdf: "filePdf", docx: "fileDoc", md: "fileDoc", csv: "fileCsv", img: "fileImg", txt: "fileTxt" };
function fileIcon(kind) { return FILE_ICON_NAME[kind] || "file"; }
function FileMark({ kind }) {
  return <span className="file-icon" style={{ color: FILE_ICON_COLOR[kind] || "#6b7280" }}><Icon name={fileIcon(kind)} /></span>;
}

/* ---------- ViewHeader / ViewTabs ---------- */
function ViewHeader({ icon, title, subtitle, actions, children }) {
  return (
    <div className="view-hd">
      <div className="view-hd-main">
        {icon && <span className="view-hd-icon"><span className="proj-icon"><Icon name={icon} /></span></span>}
        <div className="view-hd-meta">
          <div className="view-hd-title">{title}</div>
          {subtitle && <div className="view-hd-sub">{subtitle}</div>}
        </div>
      </div>
      {actions && <div className="view-hd-actions">{actions}</div>}
      {children}
    </div>
  );
}
function ViewTabs({ tabs, active, onChange }) {
  return (
    <div className="view-tabs">
      {tabs.map((tb) => (
        <button key={tb.id} className={cn("view-tab", active === tb.id && "is-active")} onClick={() => onChange(tb.id)}>
          {tb.label}{tb.count != null && <span className="view-tab-count">{tb.count}</span>}
        </button>
      ))}
    </div>
  );
}

/* ---------- ProjectsView ---------- */
function ProjectsView({ convos, onOpenProject, onNewProject, version }) {
  const t = useT();
  const ids = Object.keys(PROJECT_META);
  return (
    <div className="view">
      <ViewHeader icon="folder" title={t("view.projects.title")} subtitle={t("view.projects.sub")}
        actions={<button className="btn primary btn-sm" onClick={onNewProject}><Icon name="plus" />{t("np.title")}</button>} />
      <div className="view-body view-body-scroll">
        <div className="proj-grid">
          {ids.map((id) => {
            const p = PROJECT_META[id];
            const chats = convos.filter((c) => c.project === id).length;
            const files = (PROJECT_KNOWLEDGE[id] || []).length;
            return (
              <button key={id} className="proj-card" onClick={() => onOpenProject(id)}>
                <span className="proj-card-mark" style={{ background: p.color }}>{p.icon}</span>
                <div className="proj-card-meta">
                  <div className="proj-card-name">{p.name}</div>
                  <div className="proj-card-desc">{p.desc}</div>
                </div>
                <div className="proj-card-stats">
                  <span>{chats} chats</span><span className="artifact-sep">·</span><span>{files} files</span>
                </div>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/* ---------- KnowledgeInline ---------- */
function KnowledgeInline({ projectId, onOpenFile, allowUpload = true }) {
  const confirm = useConfirm();
  const [files, setFiles] = useStateV(() => (PROJECT_KNOWLEDGE[projectId] || []).map((f) => ({ ...f })));
  const [over, setOver] = useStateV(false);
  const fileRef = useRefV(null);
  useEffectV(() => { PROJECT_KNOWLEDGE[projectId] = files; }, [files, projectId]);
  useEffectV(() => { setFiles((PROJECT_KNOWLEDGE[projectId] || []).map((f) => ({ ...f }))); }, [projectId]);

  const addFiles = (list) => {
    const added = Array.from(list).map((f) => ({
      id: "k" + Math.random().toString(36).slice(2), name: f.name, kind: knKindFromName(f.name),
      size: f.size || 12000, updated: "now", status: "indexing",
    }));
    setFiles((cur) => [...added, ...cur]);
    added.forEach((a) => setTimeout(() => {
      setFiles((cur) => cur.map((x) => x.id === a.id ? { ...x, status: "indexed", updated: "just now" } : x));
    }, 1600 + Math.random() * 1200));
  };
  const resync = (id) => {
    setFiles((cur) => cur.map((x) => x.id === id ? { ...x, status: "indexing" } : x));
    setTimeout(() => setFiles((cur) => cur.map((x) => x.id === id ? { ...x, status: "indexed", updated: "just now" } : x)), 1500);
  };
  const remove = (file) => confirm({
    title: "Delete file", danger: true, confirmLabel: "Delete",
    body: <>Delete <span className="confirm-target">“{file.name}”</span>? This removes it as a source.</>,
    onConfirm: () => setFiles((cur) => cur.filter((x) => x.id !== file.id)),
  });

  return (
    <div className="kn-pane">
      {allowUpload && (
        <div className={cn("kn-drop", over && "is-over")}
             onDragOver={(e) => { e.preventDefault(); setOver(true); }}
             onDragLeave={() => setOver(false)}
             onDrop={(e) => { e.preventDefault(); setOver(false); addFiles(e.dataTransfer.files); }}>
          <Icon name="upload" />
          <div>
            <div className="kn-drop-title">Drag files here to index</div>
            <div className="kn-drop-sub">PDF, DOCX, MD, TXT, CSV, PNG/JPG · up to 25 MB · or <button className="link" onClick={() => fileRef.current && fileRef.current.click()}>browse</button></div>
          </div>
          <input ref={fileRef} type="file" multiple hidden onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }} />
        </div>
      )}
      <ul className="kn-files">
        {files.map((f) => (
          <li key={f.id}>
            <button className="kn-file" onClick={() => onOpenFile && onOpenFile({ ...f, projectId })}>
              <FileMark kind={f.kind} />
              <span className="kn-file-meta">
                <span className="kn-file-name">{f.name}</span>
                <span className="kn-file-sub">{knFormatSize(f.size)} · {f.updated}</span>
              </span>
              <span className={cn("kn-status", f.status)}>{f.status}</span>
              <span className="kn-file-actions" onClick={(e) => e.stopPropagation()}>
                <Action icon="refresh" label="Re-sync" onClick={() => resync(f.id)} tip={false} />
                <Action icon="trash" label="Delete" className="kn-delete" onClick={() => remove(f)} tip={false} />
              </span>
            </button>
          </li>
        ))}
        {files.length === 0 && <div className="view-empty"><h3>No files yet</h3><p>Upload documents to use them as sources in this project.</p></div>}
      </ul>
    </div>
  );
}

/* ---------- ProjectDetailView ---------- */
function ProjectDetailView({ projectId, convos, onBack, onOpenChat, onOpenFile, onRename, onUpdateDesc,
                             onNewChat, onSaveInstructions, onUpdateSettings, onDelete }) {
  const t = useT();
  const p = PROJECT_META[projectId];
  const [tab, setTab] = useStateV("chats");
  const [instr, setInstr] = useStateV(p.instructions || "");
  const projConvos = convos.filter((c) => c.project === projectId);
  const files = PROJECT_KNOWLEDGE[projectId] || [];

  useEffectV(() => { setTab("chats"); setInstr(PROJECT_META[projectId].instructions || ""); }, [projectId]);

  const tabs = [
    { id: "chats", label: t("proj.tab.chats"), count: projConvos.length },
    { id: "knowledge", label: t("proj.tab.knowledge"), count: files.length },
    { id: "instructions", label: t("proj.tab.instructions") },
    { id: "settings", label: t("proj.tab.settings") },
  ];

  return (
    <div className="view">
      <ViewHeader
        icon={null}
        title={<InlineEditable value={p.name} onChange={(v) => onRename(projectId, v)} />}
        subtitle={<InlineEditable value={p.desc} placeholder="Add a description…" onChange={(v) => onUpdateDesc(projectId, v)} />}
        actions={<button className="btn btn-sm" onClick={onBack}><Icon name="back" />{t("proj.back")}</button>}>
        <div className="view-hd-main" style={{ display: "none" }} />
        <ViewTabs tabs={tabs} active={tab} onChange={setTab} />
      </ViewHeader>
      <div className="view-body view-body-scroll">
        {tab === "chats" && (
          projConvos.length ? (
            <ul className="proj-chat-list">
              {projConvos.map((c) => (
                <li key={c.id}>
                  <button className="proj-chat-row" onClick={() => onOpenChat(c.id)}>
                    <span className="proj-chat-title">{c.title}</span>
                    <span className="proj-chat-snip">{c.snippet || "—"}</span>
                    <span className="proj-chat-time">{c.meta}</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <div className="view-empty">
              <h3>No chats yet</h3><p>Start a conversation scoped to {p.name}.</p>
              <button className="btn primary" onClick={() => onNewChat(projectId)}><Icon name="plus" />{t("proj.newChatIn")} {p.name}</button>
            </div>
          )
        )}
        {tab === "chats" && projConvos.length > 0 && (
          <div style={{ marginTop: 14 }}>
            <button className="btn btn-sm" onClick={() => onNewChat(projectId)}><Icon name="plus" />{t("proj.newChatIn")} {p.name}</button>
          </div>
        )}
        {tab === "knowledge" && <KnowledgeInline projectId={projectId} onOpenFile={onOpenFile} />}
        {tab === "instructions" && (
          <div className="proj-instructions">
            <p className="proj-instructions-help">System prompt injected into every conversation in this project. Saves automatically.</p>
            <textarea className="proj-instructions-input" value={instr}
                      placeholder="e.g. You are a senior backend engineer. Prefer evidence over assertion…"
                      onChange={(e) => { setInstr(e.target.value); onSaveInstructions(projectId, e.target.value); }} />
            <div className="proj-instructions-foot">Saved · applies to {projConvos.length} conversation{projConvos.length === 1 ? "" : "s"}</div>
          </div>
        )}
        {tab === "settings" && (
          <div className="proj-settings">
            <div className="set-row"><div className="set-label">Default model</div>
              <select className="set-select" value={p.defaultModel} onChange={(e) => onUpdateSettings(projectId, { defaultModel: e.target.value })}>
                {window.MODELS.map((m) => <option key={m} value={m}>{m}</option>)}
              </select></div>
            <div className="set-row"><div className="set-label">Auto-archive</div>
              <select className="set-select" value={p.autoArchive} onChange={(e) => onUpdateSettings(projectId, { autoArchive: e.target.value })}>
                <option value="never">Never</option><option value="30d">After 30 days</option><option value="90d">After 90 days</option>
              </select></div>
            <div className="set-row"><div className="set-label">Shared with workspace</div>
              <Switch on={!!p.shared} onChange={(v) => onUpdateSettings(projectId, { shared: v })} label="Shared" /></div>
            <div className="set-row danger-row">
              <div><div className="set-label">Delete project</div><div className="set-hint">Removes the project and all {projConvos.length} linked conversations.</div></div>
              <button className="btn danger" onClick={() => onDelete(projectId)}><Icon name="trash" />Delete project</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------- KnowledgeView (global) ---------- */
function KnowledgeView({ onOpenFile }) {
  const t = useT();
  const confirm = useConfirm();
  const [q, setQ] = useStateV("");
  const [version, setVersion] = useStateV(0);
  const all = useMemoV(() => {
    const out = [];
    Object.keys(PROJECT_KNOWLEDGE).forEach((pid) => (PROJECT_KNOWLEDGE[pid] || []).forEach((f) => out.push({ ...f, projectId: pid, projectName: PROJECT_META[pid].name })));
    return out;
  }, [version]);
  const ql = q.trim().toLowerCase();
  const filtered = all.filter((f) => !ql || f.name.toLowerCase().includes(ql));
  const remove = (file) => confirm({
    title: "Delete file", danger: true, confirmLabel: "Delete",
    body: <>Delete <span className="confirm-target">“{file.name}”</span>?</>,
    onConfirm: () => { PROJECT_KNOWLEDGE[file.projectId] = (PROJECT_KNOWLEDGE[file.projectId] || []).filter((x) => x.id !== file.id); setVersion((v) => v + 1); },
  });

  return (
    <div className="view">
      <ViewHeader icon="book" title={t("view.knowledge.title")} subtitle={t("view.knowledge.sub")} />
      <div className="view-body view-body-scroll">
        <div className="view-filters">
          <span className="view-search"><Icon name="search" /><input placeholder="Search files…" value={q} onChange={(e) => setQ(e.target.value)} /></span>
        </div>
        <ul className="kn-files">
          {filtered.map((f) => (
            <li key={f.id}>
              <button className="kn-file" onClick={() => onOpenFile(f)}>
                <FileMark kind={f.kind} />
                <span className="kn-file-meta">
                  <span className="kn-file-name">{f.name}</span>
                  <span className="kn-file-sub">{f.projectName} · {knFormatSize(f.size)} · {f.updated}</span>
                </span>
                <span className={cn("kn-status", f.status)}>{f.status}</span>
                <span className="kn-file-actions" onClick={(e) => e.stopPropagation()}>
                  <Action icon="trash" label="Delete" className="kn-delete" onClick={() => remove(f)} tip={false} />
                </span>
              </button>
            </li>
          ))}
          {filtered.length === 0 && <div className="view-empty"><h3>No files</h3><p>Nothing matches your search.</p></div>}
        </ul>
      </div>
    </div>
  );
}

/* ---------- HistoryView ---------- */
function HistoryView({ convos, onOpenChat, onArchive, onDelete, archivedOnly }) {
  const t = useT();
  const confirm = useConfirm();
  const [q, setQ] = useStateV("");
  const [proj, setProj] = useStateV("all");
  const [range, setRange] = useStateV("all");
  const [withPreview, setWithPreview] = useStateV(false);
  const [sel, setSel] = useStateV(() => new Set());

  const now = Date.now();
  const rangeMs = { "24h": 864e5, week: 6048e5, month: 2592e6 };
  const list = convos.filter((c) => {
    if (archivedOnly ? !c.archived : c.archived) return false;
    if (q && !c.title.toLowerCase().includes(q.toLowerCase())) return false;
    if (proj !== "all" && c.project !== proj) return false;
    if (range !== "all" && now - c.updatedAt > rangeMs[range]) return false;
    if (withPreview && !c.hasPreview) return false;
    return true;
  });

  const toggleSel = (id) => setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const clearSel = () => setSel(new Set());
  const bulkDelete = () => confirm({
    title: "Delete conversations", danger: true, confirmLabel: `Delete ${sel.size}`,
    body: <>Delete <span className="confirm-target">{sel.size} conversation{sel.size === 1 ? "" : "s"}</span>? This cannot be undone.</>,
    onConfirm: () => { sel.forEach((id) => onDelete(id, true)); clearSel(); },
  });
  const bulkArchive = () => { sel.forEach((id) => onArchive(id, !archivedOnly)); clearSel(); };

  return (
    <div className="view">
      <ViewHeader icon={archivedOnly ? "archive" : "clock"}
        title={archivedOnly ? t("view.archive.title") : t("view.history.title")}
        subtitle={archivedOnly ? t("view.archive.sub") : t("view.history.sub")} />
      {sel.size > 0 && (
        <div className="bulk-bar">
          <span>{sel.size} selected</span>
          <span className="comp-spacer" style={{ flex: 1 }} />
          <button className="btn btn-sm" onClick={bulkArchive}><Icon name={archivedOnly ? "back" : "archive"} />{archivedOnly ? t("common.restore") : t("common.archive")}</button>
          <button className="btn btn-sm danger" onClick={bulkDelete}><Icon name="trash" />{t("common.delete")}</button>
          <button className="btn btn-sm" onClick={clearSel}>{t("common.cancel")}</button>
        </div>
      )}
      <div className="view-body view-body-scroll">
        <div className="view-filters">
          <span className="view-search"><Icon name="search" /><input placeholder="Search conversations…" value={q} onChange={(e) => setQ(e.target.value)} /></span>
          <select className="set-select" value={proj} onChange={(e) => setProj(e.target.value)}>
            <option value="all">All projects</option>
            {Object.keys(PROJECT_META).map((id) => <option key={id} value={id}>{PROJECT_META[id].name}</option>)}
          </select>
          <select className="set-select" value={range} onChange={(e) => setRange(e.target.value)}>
            <option value="all">Any time</option><option value="24h">Last 24h</option><option value="week">Last week</option><option value="month">Last month</option>
          </select>
          <label className="view-checkbox"><input type="checkbox" checked={withPreview} onChange={(e) => setWithPreview(e.target.checked)} />With preview</label>
        </div>
        <table className="hist-table">
          <thead>
            <tr><th className="hist-check"></th><th>Title</th><th className="hist-project">Project</th><th className="hist-flags">Flags</th><th className="hist-time">Updated</th><th className="hist-actions"></th></tr>
          </thead>
          <tbody>
            {list.map((c) => (
              <tr key={c.id} className={sel.has(c.id) ? "is-selected" : ""}>
                <td className="hist-check"><input type="checkbox" checked={sel.has(c.id)} onChange={() => toggleSel(c.id)} /></td>
                <td className="hist-title" onClick={() => onOpenChat(c.id)}>
                  <span className="hist-title-text">{c.title}</span>
                  {c.pinned && <span className="hist-pin"><Icon name="pin" style={{ width: 11, height: 11 }} /></span>}
                </td>
                <td className="hist-project">{c.project ? <span className="hist-project-pill">{PROJECT_META[c.project].name}</span> : <span className="muted">—</span>}</td>
                <td className="hist-flags">{c.hasPreview && <span className="flag" title="Has artifact"><Icon name="window" style={{ width: 13, height: 13 }} /></span>}</td>
                <td className="hist-time">{c.meta}</td>
                <td className="hist-actions">
                  {archivedOnly
                    ? <button className="btn btn-sm" onClick={() => onArchive(c.id, false)}>{t("common.restore")}</button>
                    : <button className="btn btn-sm" onClick={() => onArchive(c.id, true)}>{t("common.archive")}</button>}
                  <button className="btn btn-sm danger" onClick={() => confirm({ title: t("confirm.deleteChat.title"), danger: true, confirmLabel: t("common.delete"), body: <>{t("confirm.deleteChat.body")} <span className="confirm-target">“{c.title}”</span>?</>, onConfirm: () => onDelete(c.id, true) })}>{t("common.delete")}</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {list.length === 0 && <div className="view-empty"><h3>Nothing here</h3><p>No conversations match these filters.</p></div>}
      </div>
    </div>
  );
}

/* ---------- FileViewerPanel ---------- */
function FileViewerPanel({ file, onClose }) {
  const [status, setStatus] = useStateV(file ? file.status : "indexed");
  useEffectV(() => { setStatus(file ? file.status : "indexed"); }, [file && file.id]);
  useEffectV(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const startResize = (e) => {
    e.preventDefault();
    const app = document.querySelector(".app");
    const startX = e.clientX;
    const startW = parseInt(getComputedStyle(app).getPropertyValue("--art-w")) || 580;
    const move = (ev) => { let w = Math.max(380, Math.min(1100, startW - (ev.clientX - startX))); app.style.setProperty("--art-w", w + "px"); };
    const up = () => { document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up); localStorage.setItem("lattice.art.w", parseInt(getComputedStyle(app).getPropertyValue("--art-w")) || 580); };
    document.addEventListener("mousemove", move); document.addEventListener("mouseup", up);
  };

  if (!file) return null;
  const resync = () => { setStatus("indexing"); setTimeout(() => setStatus("indexed"), 1500); };
  const download = () => {
    const meta = `File: ${file.name}\nKind: ${file.kind}\nSize: ${knFormatSize(file.size)}\nUpdated: ${file.updated}\nStatus: ${status}\n`;
    const blob = new Blob([meta], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a"); a.href = url; a.download = file.name + ".meta.txt"; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  return (
    <aside className="art-panel file-viewer">
      <div className="art-resize" onMouseDown={startResize} onDoubleClick={() => { const app = document.querySelector(".app"); app.style.setProperty("--art-w", "580px"); localStorage.setItem("lattice.art.w", 580); }} />
      <div className="art-hd">
        <FileMark kind={file.kind} />
        <div className="art-hd-meta">
          <div className="art-hd-title">{file.name}</div>
          <div className="art-hd-sub">{knFormatSize(file.size)} · {file.updated} · <span className={cn("kn-status-inline", status)}>{status}</span></div>
        </div>
        <div className="art-hd-actions">
          <Action icon="refresh" label="Re-sync" onClick={resync} tip={false} />
          <Action icon="download" label="Download" onClick={download} tip={false} />
          <Action icon="x" label="Close" onClick={onClose} tip={false} />
        </div>
      </div>
      <div className="art-body">
        <div className="fv-placeholder">
          {file.kind === "img" ? (
            <div className="fv-image"><div className="fv-image-placeholder" style={{ background: "linear-gradient(135deg,#6366f1,#8b5cf6)" }}>{file.name}</div></div>
          ) : file.kind === "csv" ? (
            <div className="fv-page">
              <div className="fv-page-h1">{file.name}</div>
              <div className="fv-page-table">
                {Array.from({ length: 6 }).map((_, r) => (
                  <div key={r} className="fv-page-row">{Array.from({ length: 4 }).map((_, c) => <div key={c} className="fv-page-cell" />)}</div>
                ))}
              </div>
            </div>
          ) : (
            <div className="fv-page">
              <div className="fv-page-h1">{file.name}</div>
              <div className="fv-page-line" style={{ width: "92%" }} />
              <div className="fv-page-line" style={{ width: "100%" }} />
              <div className="fv-page-line" style={{ width: "85%" }} />
              <div className="fv-page-h2">Section</div>
              <div className="fv-page-line" style={{ width: "96%" }} />
              <div className="fv-page-line" style={{ width: "78%" }} />
            </div>
          )}
        </div>
      </div>
    </aside>
  );
}

Object.assign(window, {
  PROJECT_META, PROJECT_KNOWLEDGE, knKindFromName, knFormatSize, fileIcon, FileMark,
  ViewHeader, ViewTabs, ProjectsView, KnowledgeInline, ProjectDetailView,
  KnowledgeView, HistoryView, FileViewerPanel,
});
