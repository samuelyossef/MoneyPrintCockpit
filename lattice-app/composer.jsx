/* ============================================================
   composer.jsx — Composer (input box) + AttachChip
   voice, attachments, Think / Deep Search toggles, model + tokens
   ============================================================ */
const { useState: useStateC, useRef: useRefC, useEffect: useEffectC } = React;

const ROTATING_PROMPTS = [
  "Ask Lattice anything…",
  "Confirm the embedded PG harness applies our migrations…",
  "Draft the Q3 OKRs for onboarding…",
  "Summarize the verification-gate test matrix…",
  "Review this diff for race conditions…",
];

function attKind(name, type) {
  const ext = (name.split(".").pop() || "").toLowerCase();
  if ((type && type.startsWith("image/")) || ["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(ext)) return "image";
  if (ext === "pdf") return "pdf";
  if (["doc", "docx", "md", "txt", "rtf"].includes(ext)) return "doc";
  return "file";
}
function fmtBytes(b) {
  if (b < 1024) return b + " B";
  if (b < 1024 * 1024) return (b / 1024).toFixed(0) + " KB";
  return (b / 1024 / 1024).toFixed(1) + " MB";
}
const ATT_COLOR = { image: "#2563eb", pdf: "#ef4444", doc: "#2EA47A", file: "#8B5CF6" };

function AttachChip({ att, onRemove }) {
  return (
    <div className="att-chip" style={{ "--att": ATT_COLOR[att.kind] }}>
      {att.kind === "image" && att.url
        ? <img className="att-thumb" src={att.url} alt="" />
        : <span className="att-icon"><Icon name={att.kind === "pdf" ? "filePdf" : att.kind === "doc" ? "fileDoc" : "file"} /></span>}
      <span className="att-meta">
        <span className="att-name">{att.name}</span>
        <span className="att-sub">{fmtBytes(att.size)}</span>
      </span>
      <button className="att-x" onClick={onRemove} aria-label="Remove attachment"><Icon name="x" /></button>
    </div>
  );
}

function Composer({ value, onChange, onSend, disabled, tokens, model, onPickModel,
                    rotatePlaceholder = false, staticPlaceholder }) {
  const t = useT();
  const taRef = useRefC(null);
  const [think, setThink] = useStateC(false);
  const [deep, setDeep] = useStateC(false);
  const [listening, setListening] = useStateC(false);
  const [attachments, setAttachments] = useStateC([]);
  const [rotIdx, setRotIdx] = useStateC(0);
  const recRef = useRefC(null);
  const fileRef = useRefC(null);
  const baseTextRef = useRefC("");

  // autosize
  useEffectC(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, rotatePlaceholder ? 220 : 200) + "px";
  }, [value]);

  // rotating placeholder
  useEffectC(() => {
    if (!rotatePlaceholder) return;
    const id = setInterval(() => setRotIdx((i) => (i + 1) % ROTATING_PROMPTS.length), 3800);
    return () => clearInterval(id);
  }, [rotatePlaceholder]);

  // blob cleanup
  useEffectC(() => () => { attachments.forEach((a) => a.url && URL.revokeObjectURL(a.url)); }, []);

  const placeholder = rotatePlaceholder ? ROTATING_PROMPTS[rotIdx] : (staticPlaceholder || t("composer.placeholder.reply"));
  const canSend = !disabled && (value.trim().length > 0 || attachments.length > 0);

  const doSend = () => {
    if (!canSend) return;
    attachments.forEach((a) => a.url && URL.revokeObjectURL(a.url));
    setAttachments([]);
    onSend(value.trim(), { think, deep, attachments });
  };

  const onKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); doSend(); }
  };

  const pickFiles = (e) => {
    const files = Array.from(e.target.files || []);
    const next = files.map((f) => {
      const kind = attKind(f.name, f.type);
      return { id: Math.random().toString(36).slice(2), name: f.name, size: f.size, kind,
               url: kind === "image" ? URL.createObjectURL(f) : null };
    });
    setAttachments((cur) => [...cur, ...next]);
    e.target.value = "";
  };
  const removeAtt = (id) => setAttachments((cur) => {
    const found = cur.find((a) => a.id === id);
    if (found && found.url) URL.revokeObjectURL(found.url);
    return cur.filter((a) => a.id !== id);
  });

  const toggleMic = () => {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) { return; } // graceful degrade
    if (listening) { recRef.current && recRef.current.stop(); setListening(false); return; }
    const rec = new SR();
    rec.continuous = true; rec.interimResults = true;
    baseTextRef.current = value ? value + " " : "";
    rec.onresult = (ev) => {
      let txt = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) txt += ev.results[i][0].transcript;
      onChange(baseTextRef.current + txt);
    };
    rec.onend = () => setListening(false);
    rec.onerror = () => setListening(false);
    recRef.current = rec;
    try { rec.start(); setListening(true); } catch (_) { setListening(false); }
  };
  const micSupported = !!(window.SpeechRecognition || window.webkitSpeechRecognition);

  return (
    <div className={cn("composer", listening && "is-recording")}>
      {attachments.length > 0 && (
        <div className="comp-attach">
          {attachments.map((a) => <AttachChip key={a.id} att={a} onRemove={() => removeAtt(a.id)} />)}
        </div>
      )}
      <textarea ref={taRef} rows={1} value={value} placeholder={placeholder}
                onChange={(e) => onChange(e.target.value)} onKeyDown={onKey} disabled={disabled} />
      <div className="comp-foot">
        <input ref={fileRef} type="file" multiple hidden onChange={pickFiles} />
        <button className="comp-chip" onClick={() => fileRef.current && fileRef.current.click()}>
          <Icon name="attach" /><span>{t("composer.attach")}</span>
        </button>
        <button className={cn("comp-chip", "toggle", think && "is-on")} onClick={() => setThink((v) => !v)}>
          <Icon name="spark" /><span>{t("composer.think")}</span>
        </button>
        <button className={cn("comp-chip", "toggle", deep && "is-on")} onClick={() => setDeep((v) => !v)}>
          <Icon name="globe" /><span>{t("composer.deepSearch")}</span>
        </button>
        <span className="comp-spacer" />
        <span className="comp-tokens">{tokens} tok</span>
        {micSupported && (
          <Action icon="mic" label="Voice input" className={cn("mic", listening && "is-listening")}
                  onClick={toggleMic} tip={false} />
        )}
        <button className="comp-chip model"
                onClick={(e) => onPickModel && onPickModel(e.currentTarget.getBoundingClientRect())}>
          {model}<Icon name="chevDown" />
        </button>
        <button className={cn("send", !canSend && "disabled")} onClick={doSend} aria-label="Send message">
          <Icon name="arrowUp" />
        </button>
      </div>
    </div>
  );
}

Object.assign(window, { Composer, AttachChip });
