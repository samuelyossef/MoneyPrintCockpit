/* ============================================================
   primitives.jsx — shared building blocks (exported on window)
   cn, ICONS/Icon, Tooltip, Action, Switch, Badge, Modal,
   InlineEditable, ConfirmContext + ConfirmProvider + useConfirm,
   useMediaQuery, useClickOutside
   ============================================================ */
const { useState, useEffect, useRef, useLayoutEffect, useContext, createContext, useCallback } = React;

/* ---- cn: conditional className join ---- */
function cn(...args) {
  return args.filter(Boolean).join(" ");
}

/* ---- Icon registry (lucide-style inner markup) ---- */
const ICONS = {
  search: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
  pin: '<path d="M12 17v5"/><path d="M9 3h6l-1 7 3 3H7l3-3-1-7Z"/>',
  archive: '<rect x="3" y="4" width="18" height="4" rx="1"/><path d="M5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8"/><path d="M10 12h4"/>',
  trash: '<path d="M3 6h18"/><path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"/><path d="M19 6l-1 14a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1L5 6"/>',
  more: '<circle cx="5" cy="12" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="19" cy="12" r="1.4"/>',
  chevRight: '<path d="m9 6 6 6-6 6"/>',
  chevDown: '<path d="m6 9 6 6 6-6"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 18v3"/>',
  attach: '<path d="M21 11.5 12.5 20a5 5 0 0 1-7-7l8-8a3.3 3.3 0 0 1 4.7 4.7l-8 8a1.7 1.7 0 0 1-2.4-2.4l7.3-7.3"/>',
  arrowUp: '<path d="M12 19V5"/><path d="m5 12 7-7 7 7"/>',
  spark: '<path d="M12 3l1.5 5.5L19 10l-5.5 1.5L12 17l-1.5-5.5L5 10l5.5-1.5Z"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3a15 15 0 0 1 0 18 15 15 0 0 1 0-18Z"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 13.5a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-2.88 1.2V21a2 2 0 1 1-4 0v-.09A1.7 1.7 0 0 0 7 19.4a1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 2.6 14H2.5a2 2 0 1 1 0-4h.09A1.7 1.7 0 0 0 4 7a1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.7 1.7 0 0 0 8.5 2.6h.09A1.7 1.7 0 0 0 10 1.5V1.5a2 2 0 1 1 4 0v.09A1.7 1.7 0 0 0 16.87 4l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.7 1.7 0 0 0 21.4 9.5h.1a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.01 0Z"/>',
  clock: '<path d="M3 12a9 9 0 1 0 2.6-6.4L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l3 2"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
  book: '<path d="M4 19.5V6a2 2 0 0 1 2-2h13v15H6a2 2 0 0 0-2 2Z"/><path d="M19 19H6a2 2 0 0 0-2 2"/>',
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z"/><path d="M14 3v5h5"/>',
  like: '<path d="M7 21V10"/><path d="M7 10l4-7a2 2 0 0 1 3 2.2L13 9h5.5a2 2 0 0 1 2 2.4l-1.3 6.5A2 2 0 0 1 17.2 21H7"/>',
  dislike: '<path d="M17 3v11"/><path d="M17 14l-4 7a2 2 0 0 1-3-2.2L11 15H5.5a2 2 0 0 1-2-2.4l1.3-6.5A2 2 0 0 1 6.8 3H17"/>',
  regen: '<path d="M21 12a9 9 0 1 1-2.6-6.4"/><path d="M21 3v5h-5"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4.5 21a7.5 7.5 0 0 1 15 0"/>',
  card: '<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M2 10h20"/>',
  signout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5"/><path d="M21 12H9"/>',
  bell: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.9 1.9 0 0 0 3.4 0"/>',
  panelLeft: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
  panelRight: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>',
  menu: '<path d="M3 6h18M3 12h18M3 18h18"/>',
  download: '<path d="M12 3v12"/><path d="m7 12 5 5 5-5"/><path d="M5 21h14"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
  activity: '<path d="M3 12h4l3 8 4-16 3 8h4"/>',
  plug: '<path d="M9 2v6M15 2v6"/><path d="M7 8h10v3a5 5 0 0 1-10 0Z"/><path d="M12 16v6"/>',
  puzzle: '<path d="M9.5 2.5a2 2 0 0 1 4 0c0 .6.4 1 1 1H17a1 1 0 0 1 1 1v2.5c0 .6.4 1 1 1a2 2 0 0 1 0 4c-.6 0-1 .4-1 1V19a1 1 0 0 1-1 1h-2.5c-.6 0-1 .4-1 1a2 2 0 0 1-4 0c0-.6-.4-1-1-1H5a1 1 0 0 1-1-1v-2.5c0-.6-.4-1-1-1a2 2 0 0 1 0-4c.6 0 1-.4 1-1V5a1 1 0 0 1 1-1h3.5c.6 0 1-.4 1-1Z"/>',
  sliders: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3"/><path d="M2 14h4M10 8h4M18 16h4"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"/>',
  code: '<path d="m16 18 6-6-6-6"/><path d="m8 6-6 6 6 6"/>',
  window: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18"/>',
  table: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M3 15h18M12 4v16"/>',
  cornerUpRight: '<path d="M15 14l5-5-5-5"/><path d="M20 9H9a4 4 0 0 0-4 4v7"/>',
  expand:  '<path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/>',
  shrink:  '<path d="M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7"/>',
  bash: '<path d="m4 17 6-6-6-6"/><path d="M12 19h8"/>',
  read: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 17h4"/>',
  grep: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/><path d="M9 11h4"/>',
  send: '<path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4Z"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-2.6-6.4"/><path d="M21 3v5h-5"/>',
  back: '<path d="M19 12H5"/><path d="m12 19-7-7 7-7"/>',
  upload: '<path d="M12 17V5"/><path d="m7 10 5-5 5 5"/><path d="M5 19h14"/>',
  filePdf: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z"/><path d="M14 3v5h5"/><path d="M8.5 13.5h1a1 1 0 0 1 0 2h-1Zm0 0v3"/>',
  fileCsv: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z"/><path d="M14 3v5h5"/><path d="M9 14h6M9 17h6"/>',
  fileImg: '<rect x="4" y="4" width="16" height="16" rx="2"/><circle cx="9" cy="9" r="1.5"/><path d="m4 17 5-5 5 5 6-6"/>',
  fileTxt: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z"/><path d="M14 3v5h5"/><path d="M8 13h8M8 16h6"/>',
  fileDoc: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z"/><path d="M14 3v5h5"/><path d="M8 13h8M8 16h8M8 10h3"/>',
  lock: '<rect x="4" y="11" width="16" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  eye: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
};

function Icon({ name, className, style }) {
  const inner = ICONS[name] || ICONS.file;
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
         strokeLinecap="round" strokeLinejoin="round" className={className} style={style}
         aria-hidden="true" dangerouslySetInnerHTML={{ __html: inner }} />
  );
}

/* ---- Tooltip ---- */
function Tooltip({ label, side = "top", delay = 350, children }) {
  const ref = useRef(null);
  const [pos, setPos] = useState(null);
  const timer = useRef(null);

  const show = () => {
    timer.current = setTimeout(() => {
      const el = ref.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      let x = r.left + r.width / 2, y = r.top;
      if (side === "bottom") y = r.bottom + 6;
      else if (side === "top") y = r.top - 6;
      else if (side === "left") { x = r.left - 6; y = r.top + r.height / 2; }
      else if (side === "right") { x = r.right + 6; y = r.top + r.height / 2; }
      setPos({ x, y });
    }, delay);
  };
  const hide = () => { clearTimeout(timer.current); setPos(null); };
  useEffect(() => () => clearTimeout(timer.current), []);

  const child = React.cloneElement(React.Children.only(children), {
    ref: (node) => { ref.current = node; },
    onMouseEnter: show, onMouseLeave: hide, onFocus: show, onBlur: hide,
  });

  return (
    <>
      {child}
      {pos && label && ReactDOM.createPortal(
        <div className={cn("tt", "tt-" + side)} style={{ left: pos.x, top: pos.y }}>{label}</div>,
        document.body
      )}
    </>
  );
}

/* ---- Action: accessible icon button ---- */
function Action({ icon, label, onClick, className, active, danger, tip, tipSide = "bottom", ...rest }) {
  const btn = (
    <button type="button"
            className={cn("icon-btn", active && "is-active", danger && "is-danger", className)}
            onClick={onClick} {...rest}>
      {typeof icon === "string" ? <Icon name={icon} /> : icon}
      <span className="sr-only">{label}</span>
    </button>
  );
  return tip === false ? btn : <Tooltip label={label} side={tipSide}>{btn}</Tooltip>;
}

/* ---- Switch (role=switch) ---- */
function Switch({ on, onChange, label }) {
  return (
    <button type="button" role="switch" aria-checked={!!on} aria-label={label}
            className={cn("switch", on && "on")} onClick={() => onChange(!on)}>
      <span className="thumb" />
    </button>
  );
}

/* ---- Badge ---- */
function Badge({ children, className }) {
  return <span className={cn("badge", className)}>{children}</span>;
}

/* ---- useClickOutside ---- */
function useClickOutside(ref, handler) {
  useEffect(() => {
    const onDown = (e) => {
      const el = ref.current;
      if (el && !el.contains(e.target)) handler(e);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [handler]);
}

/* ---- useMediaQuery ---- */
function useMediaQuery(query) {
  const [match, setMatch] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setMatch(mq.matches);
    on();
    mq.addEventListener ? mq.addEventListener("change", on) : mq.addListener(on);
    return () => { mq.removeEventListener ? mq.removeEventListener("change", on) : mq.removeListener(on); };
  }, [query]);
  return match;
}

/* ---- Modal ---- */
function Modal({ open, onClose, title, children, width = 560 }) {
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); onClose && onClose(); } };
    document.addEventListener("keydown", onKey, true);
    const t = setTimeout(() => { ref.current && ref.current.focus(); }, 20);
    return () => { document.removeEventListener("keydown", onKey, true); clearTimeout(t); };
  }, [open]);
  if (!open) return null;
  return ReactDOM.createPortal(
    <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose && onClose(); }}>
      <div className="modal" style={{ maxWidth: width }} ref={ref} tabIndex={-1}
           role="dialog" aria-modal="true" aria-label={title || "Dialog"}>
        {title && (
          <div className="modal-hd">
            <h2>{title}</h2>
            <Action icon="x" label="Close" onClick={onClose} tip={false} />
          </div>
        )}
        <div className="modal-body">{children}</div>
      </div>
    </div>,
    document.body
  );
}

/* ---- InlineEditable ---- */
function InlineEditable({ value, onChange, placeholder = "—", type = "text", className, multiline }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const inputRef = useRef(null);
  useEffect(() => { setDraft(value); }, [value]);
  useEffect(() => { if (editing && inputRef.current) { inputRef.current.focus(); inputRef.current.select && inputRef.current.select(); } }, [editing]);

  const commit = () => {
    setEditing(false);
    const v = (draft ?? "").toString();
    if (v !== (value ?? "")) onChange && onChange(v);
  };
  const cancel = () => { setDraft(value); setEditing(false); };

  if (editing) {
    return (
      <input ref={inputRef} type={type} className={cn("inline-edit", className)} value={draft ?? ""}
             onChange={(e) => setDraft(e.target.value)} onBlur={commit}
             onKeyDown={(e) => {
               if (e.key === "Enter" && !multiline) { e.preventDefault(); commit(); }
               else if (e.key === "Escape") { e.preventDefault(); cancel(); }
             }} />
    );
  }
  return (
    <button type="button" className={cn("inline-display", className)} onClick={() => setEditing(true)}>
      <span>{value || <span className="muted">{placeholder}</span>}</span>
      <Icon name="edit" />
    </button>
  );
}

/* ---- Confirm system ---- */
const ConfirmContext = createContext(() => {});
function ConfirmProvider({ children }) {
  const [dialog, setDialog] = useState(null);
  const openConfirm = useCallback((opts) => setDialog(opts), []);
  const close = () => setDialog(null);
  return (
    <ConfirmContext.Provider value={openConfirm}>
      {children}
      <Modal open={!!dialog} onClose={close} title={dialog?.title} width={dialog?.width || 460}>
        {dialog && (
          <>
            <div className="confirm-copy">{dialog.body}</div>
            <div className="modal-foot">
              <button className="btn" onClick={close}>{dialog.cancelLabel || "Cancel"}</button>
              <button className={cn("btn", dialog.danger ? "danger" : "primary")}
                      onClick={() => { close(); dialog.onConfirm && dialog.onConfirm(); }}>
                {dialog.confirmLabel || "Confirm"}
              </button>
            </div>
          </>
        )}
      </Modal>
    </ConfirmContext.Provider>
  );
}
function useConfirm() { return useContext(ConfirmContext); }

Object.assign(window, {
  cn, ICONS, Icon, Tooltip, Action, Switch, Badge,
  useClickOutside, useMediaQuery, Modal, InlineEditable,
  ConfirmContext, ConfirmProvider, useConfirm,
});
