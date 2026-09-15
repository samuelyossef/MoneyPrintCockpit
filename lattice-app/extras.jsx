/* ============================================================
   extras.jsx — Menu (context dropdown), SearchPalette (⌘K), MsgActions
   ============================================================ */
const { useState: useStateE, useEffect: useEffectE, useRef: useRefE, useLayoutEffect: useLayoutEffectE } = React;

/* ---------- Menu ---------- */
function Menu({ open, onClose, anchorRect, items, minWidth = 200 }) {
  const ref = useRefE(null);
  const [pos, setPos] = useStateE({ left: -9999, top: -9999 });

  useLayoutEffectE(() => {
    if (!open || !anchorRect || !ref.current) return;
    const m = ref.current.getBoundingClientRect();
    const vw = window.innerWidth, vh = window.innerHeight;
    let left = anchorRect.left;
    let top = anchorRect.bottom + 4;
    if (left + m.width > vw - 8) left = Math.max(8, anchorRect.right - m.width);
    if (top + m.height > vh - 8) top = Math.max(8, anchorRect.top - m.height - 4); // flip up
    setPos({ left, top });
  }, [open, anchorRect]);

  if (!open) return null;
  return ReactDOM.createPortal(
    <div className="menu-layer" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }} onContextMenu={(e) => { e.preventDefault(); onClose(); }}>
      <div className="menu" ref={ref} style={{ left: pos.left, top: pos.top, minWidth }} role="menu">
        {items.map((it, i) => {
          if (it.divider) return <div key={i} className="menu-divider" />;
          if (it.section) return <div key={i} className="menu-section">{it.section}</div>;
          return (
            <button key={i} className={cn("menu-item", it.danger && "is-danger", it.active && "is-active")}
                    role="menuitem"
                    onClick={() => { onClose(); it.onClick && it.onClick(); }}>
              {it.icon && <span className="menu-icon"><Icon name={it.icon} /></span>}
              <span className="menu-label">{it.label}</span>
              {it.check && <span className="menu-check"><Icon name="check" /></span>}
              {it.kbd && <span className="menu-kbd">{it.kbd}</span>}
            </button>
          );
        })}
      </div>
    </div>,
    document.body
  );
}

/* ---------- SearchPalette ---------- */
function SearchPalette({ open, onClose, convos, commands, onPickConvo, onRunCommand }) {
  const [q, setQ] = useStateE("");
  const inputRef = useRefE(null);
  useEffectE(() => {
    if (open) { setQ(""); setTimeout(() => inputRef.current && inputRef.current.focus(), 30); }
  }, [open]);
  useEffectE(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [open, onClose]);
  if (!open) return null;

  const ql = q.trim().toLowerCase();
  const matchedConvos = convos.filter((c) => !ql || c.title.toLowerCase().includes(ql)).slice(0, 6);
  const matchedCmds = commands.filter((c) => !ql || c.label.toLowerCase().includes(ql));
  const empty = matchedConvos.length === 0 && matchedCmds.length === 0;

  return ReactDOM.createPortal(
    <div className="palette-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="palette" role="dialog" aria-modal="true" aria-label="Search">
        <div className="palette-input">
          <Icon name="search" className="palette-search-icon" />
          <input ref={inputRef} value={q} placeholder="Search conversations and commands…"
                 onChange={(e) => setQ(e.target.value)} />
          <span className="palette-esc">esc</span>
        </div>
        <div className="palette-results">
          {empty && <div className="palette-empty">No results for “{q}”</div>}
          {matchedCmds.length > 0 && (
            <>
              <div className="palette-section">Commands</div>
              {matchedCmds.map((c) => (
                <button key={c.id} className="palette-item" onClick={() => { onClose(); onRunCommand(c); }}>
                  <span className="palette-icon"><Icon name={c.icon} /></span>
                  <span className="palette-meta"><span className="palette-title">{c.label}</span></span>
                  {c.kbd && <span className="palette-kbd">{c.kbd}</span>}
                </button>
              ))}
            </>
          )}
          {matchedConvos.length > 0 && (
            <>
              <div className="palette-section">Conversations</div>
              {matchedConvos.map((c) => (
                <button key={c.id} className="palette-item" onClick={() => { onClose(); onPickConvo(c); }}>
                  <span className="palette-icon"><Icon name="search" /></span>
                  <span className="palette-meta">
                    <span className="palette-title">{c.title}</span>
                    {c.project && <span className="palette-sub">{c.project}</span>}
                  </span>
                  <span className="palette-tag">chat</span>
                </button>
              ))}
            </>
          )}
        </div>
        <div className="palette-foot">
          <span><kbd>↑↓</kbd> navigate</span>
          <span><kbd>↵</kbd> open</span>
          <span><kbd>esc</kbd> close</span>
        </div>
      </div>
    </div>,
    document.body
  );
}

/* ---------- MsgActions ---------- */
function MsgActions({ onCopy, onRegen, liked, disliked, onLike, onDislike }) {
  const [copied, setCopied] = useStateE(false);
  const copy = () => { onCopy && onCopy(); setCopied(true); setTimeout(() => setCopied(false), 1400); };
  return (
    <div className="msg-actions">
      <Action icon={copied ? "check" : "copy"} label="Copy" onClick={copy} />
      <Action icon="regen" label="Regenerate" onClick={onRegen} />
      <Action icon="like" label="Good response" active={liked} onClick={onLike} />
      <Action icon="dislike" label="Bad response" active={disliked} onClick={onDislike} />
    </div>
  );
}

Object.assign(window, { Menu, SearchPalette, MsgActions });
