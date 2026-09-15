/* ============================================================
   artifact-panel.jsx — deliverables: ArtifactCard, ArtifactPanel,
   TableView + table helpers, renderMarkdown, highlightCode,
   parseArtifacts, ARTIFACTS_SAMPLE
   ============================================================ */
const { useState: useStateA, useEffect: useEffectA, useRef: useRefA, useMemo: useMemoA } = React;

/* ---------- sample artifacts ---------- */
const ARTIFACTS_SAMPLE = {
  "art-landing": {
    id: "art-landing", kind: "html", title: "Landing hero",
    content: `<!doctype html><html><head><meta charset="utf8"><style>
  body{margin:0;font-family:Inter,system-ui,sans-serif;background:#0b1020;color:#e5e9f5}
  .hero{min-height:100vh;display:grid;place-items:center;text-align:center;padding:40px}
  h1{font-size:52px;letter-spacing:-.03em;margin:0 0 12px;background:linear-gradient(90deg,#7aa2ff,#c6a2ff);-webkit-background-clip:text;color:transparent}
  p{font-size:18px;color:#9aa4c4;max-width:520px;margin:0 auto 28px}
  a{display:inline-block;background:#5b8cff;color:#0b1020;font-weight:600;padding:12px 22px;border-radius:10px;text-decoration:none}
</style></head><body><div class="hero"><div>
  <h1>Ship verification, not guesswork</h1>
  <p>Lattice gates every change behind a deterministic harness so your team moves fast with proof.</p>
  <a href="#">Start free</a>
</div></div></body></html>`,
  },
  "art-okr": {
    id: "art-okr", kind: "markdown", title: "Q3 OKR draft",
    content: `# Q3 Objectives

Three objectives focused on reducing time-to-value for new teams.

## O1 — Halve median time-to-first-output
**KR1.** New signups produce a useful agent output within 8 minutes (p50).

**KR2.** First-session completion rate above 62% (currently 41%).

**KR3.** Onboarding drop-off below 15% at the "first prompt" step.

## O2 — Day-30 power-user parity
**KR1.** Day-30 actives use Projects and Background tasks at parity with 90-day actives.

**KR2.** Median tasks-per-session ≥ 3 by day 14.

> Notes: assume TTV measured in days from signup to first useful output.`,
  },
  "art-throttle": {
    id: "art-throttle", kind: "code", language: "javascript", title: "Throttled counter",
    content: `// Tiny throttled counter — leading + trailing edge.
function throttle(fn, wait) {
  let last = 0, t = null, lastArgs;
  return function (...args) {
    const now = Date.now();
    const remaining = wait - (now - last);
    lastArgs = args;
    if (remaining <= 0) {
      if (t) { clearTimeout(t); t = null; }
      last = now;
      fn.apply(this, args);
    } else if (!t) {
      t = setTimeout(() => {
        last = Date.now();
        t = null;
        fn.apply(this, lastArgs);
      }, remaining);
    }
  };
}

const log = throttle((n) => console.log('tick', n), 250);
let n = 0; setInterval(() => log(++n), 40);`,
  },
  "art-testmatrix": {
    id: "art-testmatrix", kind: "table", title: "Verification matrix",
    content: `suite,cases,passing,failing,coverage,duration
work_products,42,42,0,94%,0.8s
issue_approvals,31,31,0,91%,0.6s
tool_governance,57,55,2,88%,1.2s
audit_trail,19,19,0,97%,0.3s
permission_gate,64,64,0,93%,1.1s`,
  },
  "art-brief": {
    id: "art-brief", kind: "document", title: "Technical Brief.pdf",
    content: `# Technical Brief\n**Project:** Lattice Platform v4 · **Date:** June 2026\n\n## Executive Summary\n\nThis document outlines the technical architecture of Lattice Platform v4, focusing on the new artifact rendering pipeline and multi-modal document processing capabilities.\n\nThe platform processes documents from multiple formats — PDF, DOCX, XLSX, HTML — extracting structured content while preserving the original layout and semantic meaning of the source.\n\n## Scope\n\nThis brief covers the following components:\n- Document ingestion and classification\n- Artifact extraction pipeline\n- Structured content rendering\n- Multi-page pagination system\f# Architecture Overview\n\n## Ingestion Pipeline\n\nDocuments enter the system through the Upload API, where they are classified by MIME type and routed to the appropriate extractor.\n\n| Format | Extractor | Output Kind |\n|--------|-----------|-------------|\n| PDF | PDFBox | document |\n| DOCX | Docx4j | document |\n| XLSX | Apache POI | table |\n| HTML | Readability | html |\n\n## Extraction Layer\n\nThe extraction layer preserves semantic structure — headings, paragraphs, lists, tables — converting them to a normalized markdown representation that downstream renderers consume consistently.\n\n> The key invariant: structure is never lost, only transformed.\f# Implementation Notes\n\n## Renderer\n\nThe document renderer uses a virtual pagination model. Content is split into logical pages at natural boundaries — section breaks, form feeds, explicit page markers — rather than fixed line counts.\n\nThis preserves the author's intended structure while adapting to any viewport width.\n\n## Pagination State\n\nPage position is persisted to **localStorage** per artifact ID so users who refresh return to where they left off.\n\n## Keyboard Navigation\n\n- **← / →** — previous / next page\n- **Home / End** — first / last page\n- **Esc** — exit expanded view\n\n## Status\n\nShipped in v4.1. Tracked in issue #2847.`,
  },
  "art-adoption": {
    id: "art-adoption", kind: "markdown", title: "Functions & elements — detail",
    content: `## Chat output inventory

A function-by-function breakdown of who renders each block type.

| Element | Role | blocks |
|---|---|---|
| TextBlock | Prose / reasoning | text |
| .msg-table | Inline comparable data | table |
| ToolCard | Evidence on demand | tool |
| ArtifactCard | Browsable deliverable | artifact |

Each renderer stays in its lane: prose explains, tables compare, tool cards prove,
and artifacts hold the product.`,
  },
};

/* ---------- Document page parser ---------- */
function parseDocPages(content) {
  if (!content) return [''];
  // Support: \f (form feed), --- PAGE BREAK ---, \n===\n, explicit <!-- page -->
  const parts = content.split(/\f|(?:^|\n)---\s*PAGE\s*(?:BREAK)?\s*---(?:\n|$)|(?:^|\n)-{4,}\s*page\s*\d*\s*-{4,}(?:\n|$)|<!--\s*page\s*-->/);
  const pages = parts.map(p => p.trim()).filter(Boolean);
  return pages.length ? pages : [content];
}

/* ---------- DocumentView ---------- */
function DocumentView({ artifact }) {
  const { useState: useStateD, useEffect: useEffectD, useMemo: useMemoD, useRef: useRefD } = React;
  const pages = useMemoD(() => parseDocPages(artifact.content), [artifact.content]);
  const storageKey = 'lattice.doc.page.' + artifact.id;
  const [page, setPage] = useStateD(() => {
    try { const n = parseInt(localStorage.getItem(storageKey)); return isNaN(n) ? 0 : Math.min(n, pages.length - 1); } catch { return 0; }
  });
  const scrollRef = useRefD(null);
  const total = pages.length;

  const goTo = (n) => {
    const idx = Math.max(0, Math.min(total - 1, n));
    setPage(idx);
    try { localStorage.setItem(storageKey, idx); } catch {}
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
  };

  useEffectD(() => {
    const onKey = (e) => {
      const tag = document.activeElement && document.activeElement.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA') return;
      if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') { e.preventDefault(); goTo(page - 1); }
      else if (e.key === 'ArrowRight' || e.key === 'ArrowDown') { e.preventDefault(); goTo(page + 1); }
      else if (e.key === 'Home') { e.preventDefault(); goTo(0); }
      else if (e.key === 'End') { e.preventDefault(); goTo(total - 1); }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [page, total]);

  const html = renderMarkdown(pages[page] || '');

  return (
    <div className="art-docview">
      <div className="art-doc-scroll" ref={scrollRef}>
        <div className="art-doc-page">
          <div className="art-doc-body" dangerouslySetInnerHTML={{ __html: html }} />
          {total > 1 && <div className="art-doc-pagenum">— {page + 1} —</div>}
        </div>
      </div>
      {total > 1 && (
        <div className="art-doc-pagination">
          <button className={cn('art-doc-nav', page === 0 && 'is-disabled')} onClick={() => goTo(page - 1)} disabled={page === 0}>
            <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"><path d="M10 13L5 8l5-5"/></svg>
          </button>
          <div className="art-doc-pages">
            {Array.from({ length: total }, (_, i) => (
              <button key={i} className={cn('art-doc-pip', i === page && 'is-active')} onClick={() => goTo(i)} title={'Page ' + (i + 1)} />
            ))}
          </div>
          <span className="art-doc-counter">{page + 1} / {total}</span>
          <button className={cn('art-doc-nav', page === total - 1 && 'is-disabled')} onClick={() => goTo(page + 1)} disabled={page === total - 1}>
            <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"><path d="M6 3l5 5-5 5"/></svg>
          </button>
        </div>
      )}
    </div>
  );
}

/* ---------- CSV / table helpers ---------- */
function splitCsvLine(line) {
  const out = []; let cur = "", q = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (q) {
      if (c === '"' && line[i + 1] === '"') { cur += '"'; i++; }
      else if (c === '"') q = false;
      else cur += c;
    } else if (c === '"') q = true;
    else if (c === ",") { out.push(cur); cur = ""; }
    else cur += c;
  }
  out.push(cur);
  return out.map((s) => s.trim());
}
function splitMdRow(line) {
  return line.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map((s) => s.trim());
}
function isNumericColumn(rows, idx) {
  let n = 0, total = 0;
  for (const r of rows) {
    const v = (r[idx] || "").replace(/[%$,\sx]/g, "");
    if (v === "") continue;
    total++;
    if (!isNaN(parseFloat(v)) && isFinite(v)) n++;
  }
  return total > 0 && n / total >= 0.7;
}
function tableToCsv(tbl) {
  const esc = (s) => /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  return [tbl.headers, ...tbl.rows].map((r) => r.map((c) => esc(String(c ?? ""))).join(",")).join("\n");
}
function extractTables(content, kind, language) {
  const tables = [];
  if (!content) return tables;
  if (kind === "table" || kind === "csv" || language === "csv") {
    const lines = content.trim().split(/\r?\n/).filter(Boolean);
    if (lines.length) {
      const headers = splitCsvLine(lines[0]);
      const rows = lines.slice(1).map(splitCsvLine);
      tables.push({ name: "Sheet 1", headers, rows });
    }
    return tables;
  }
  if (kind === "html") {
    try {
      const doc = new DOMParser().parseFromString(content, "text/html");
      doc.querySelectorAll("table").forEach((tb, i) => {
        const trs = Array.from(tb.querySelectorAll("tr"));
        if (!trs.length) return;
        let headers, bodyRows;
        const thead = tb.querySelector("thead");
        if (thead) {
          headers = Array.from(thead.querySelectorAll("th,td")).map((c) => c.textContent.trim());
          bodyRows = trs.filter((tr) => !thead.contains(tr));
        } else {
          headers = Array.from(trs[0].querySelectorAll("th,td")).map((c) => c.textContent.trim());
          bodyRows = trs.slice(1);
        }
        const rows = bodyRows.map((tr) => Array.from(tr.querySelectorAll("td,th")).map((c) => c.textContent.trim()));
        tables.push({ name: "Table " + (i + 1), headers, rows });
      });
    } catch (_) {}
    return tables;
  }
  // markdown
  const lines = content.split(/\r?\n/);
  let i = 0, tnum = 0;
  while (i < lines.length) {
    if (/\|/.test(lines[i]) && i + 1 < lines.length && /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1]) && /-/.test(lines[i + 1])) {
      const headers = splitMdRow(lines[i]);
      const rows = [];
      i += 2;
      while (i < lines.length && /\|/.test(lines[i]) && lines[i].trim()) { rows.push(splitMdRow(lines[i])); i++; }
      tnum++;
      tables.push({ name: "Table " + tnum, headers, rows });
    } else i++;
  }
  return tables;
}

/* ---------- markdown + code highlight ---------- */
function escapeHtml(s) { return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
function mdInline(s) {
  s = escapeHtml(s);
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  s = s.replace(/\*\*\*([^*]+)\*\*\*/g, "<strong><em>$1</em></strong>");
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
  s = s.replace(/~~([^~]+)~~/g, "<del>$1</del>");
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  return s;
}
function renderMarkdown(text) {
  if (!text) return "";
  const lines = text.split(/\r?\n/);
  let html = "", i = 0;
  const flushList = (buf, ordered) => buf.length ? `<${ordered ? "ol" : "ul"}>${buf.map((x) => "<li>" + mdInline(x) + "</li>").join("")}</${ordered ? "ol" : "ul"}>` : "";
  while (i < lines.length) {
    const line = lines[i];
    if (/^```/.test(line)) {
      let code = ""; i++;
      while (i < lines.length && !/^```/.test(lines[i])) { code += lines[i] + "\n"; i++; }
      i++;
      html += `<pre class="art-md-pre"><code>${escapeHtml(code.replace(/\n$/, ""))}</code></pre>`;
      continue;
    }
    // table
    if (/\|/.test(line) && i + 1 < lines.length && /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1]) && /-/.test(lines[i + 1])) {
      const headers = splitMdRow(line); const rows = [];
      i += 2;
      while (i < lines.length && /\|/.test(lines[i]) && lines[i].trim()) { rows.push(splitMdRow(lines[i])); i++; }
      html += `<table class="art-md-table"><thead><tr>${headers.map((h) => "<th>" + mdInline(h) + "</th>").join("")}</tr></thead><tbody>${rows.map((r) => "<tr>" + r.map((c) => "<td>" + mdInline(c) + "</td>").join("") + "</tr>").join("")}</tbody></table>`;
      continue;
    }
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) { html += `<h${h[1].length}>${mdInline(h[2])}</h${h[1].length}>`; i++; continue; }
    if (/^\s*>\s?/.test(line)) { html += `<blockquote>${mdInline(line.replace(/^\s*>\s?/, ""))}</blockquote>`; i++; continue; }
    if (/^\s*(---|\*\*\*|___)\s*$/.test(line)) { html += "<hr/>"; i++; continue; }
    if (/^\s*[-*+]\s+/.test(line)) {
      const buf = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) { buf.push(lines[i].replace(/^\s*[-*+]\s+/, "")); i++; }
      html += flushList(buf, false); continue;
    }
    if (/^\s*\d+\.\s+/.test(line)) {
      const buf = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) { buf.push(lines[i].replace(/^\s*\d+\.\s+/, "")); i++; }
      html += flushList(buf, true); continue;
    }
    if (line.trim() === "") { i++; continue; }
    let para = line; i++;
    while (i < lines.length && lines[i].trim() !== "" && !/^(#{1,4}\s|\s*[-*+]\s|\s*\d+\.\s|>|```|\|)/.test(lines[i])) { para += " " + lines[i]; i++; }
    html += `<p>${mdInline(para)}</p>`;
  }
  return html;
}
function highlightCode(code, lang) {
  let h = escapeHtml(code);
  h = h.replace(/(\/\/[^\n]*)/g, '<span class="tk-comment">$1</span>');
  h = h.replace(/('[^']*'|"[^"]*"|`[^`]*`)/g, '<span class="tk-string">$1</span>');
  h = h.replace(/\b(function|const|let|var|return|if|else|for|while|new|class|import|export|from|await|async|this|setTimeout|setInterval|clearTimeout|true|false|null|undefined)\b/g, '<span class="tk-kw">$1</span>');
  h = h.replace(/\b(\d+(?:\.\d+)?)\b/g, '<span class="tk-num">$1</span>');
  return h;
}

/* ---------- parseArtifacts (custom <artifact> tags in stream) ---------- */
function parseArtifacts(text) {
  const blocks = [];
  const re = /<artifact\s+([^>]*?)>([\s\S]*?)(<\/artifact>|$)/g;
  let last = 0, m;
  const attr = (s, k) => { const mm = s.match(new RegExp(k + '="([^"]*)"')); return mm ? mm[1] : ""; };
  while ((m = re.exec(text))) {
    if (m.index > last) { const pre = text.slice(last, m.index).trim(); if (pre) blocks.push({ kind: "text", text: pre }); }
    const id = attr(m[1], "id") || "art-" + Math.random().toString(36).slice(2);
    const kind = attr(m[1], "kind") || attr(m[1], "type") || "markdown";
    const title = attr(m[1], "title") || "Artifact";
    const content = m[2].trim();
    window.ARTIFACTS_SAMPLE[id] = { id, kind, title, content, language: attr(m[1], "language") };
    blocks.push({ kind: "artifact", artifactId: id });
    last = re.lastIndex;
  }
  if (last < text.length) { const tail = text.slice(last).trim(); if (tail) blocks.push({ kind: "text", text: tail }); }
  return { blocks };
}

/* ---------- ArtifactCard ---------- */
function artLineCount(a) { return (a.content || "").split(/\r?\n/).length; }
function artKindLabel(a) {
  return ({ html: "Web preview", markdown: "Markdown", code: a.language || "Code", table: "csv", csv: "csv", document: "Document" })[a.kind] || a.kind;
}
function ArtifactCard({ artifact, onOpen }) {
  if (!artifact) return null;
  const iconName = { html: "window", markdown: "fileDoc", code: "code", table: "table", csv: "table", document: "fileDoc" }[artifact.kind] || "file";
  const thumbKind = artifact.kind === "csv" || artifact.kind === "document" ? (artifact.kind === "document" ? "markdown" : "table") : artifact.kind;
  return (
    <button className="artifact-card" onClick={() => onOpen && onOpen(artifact)}>
      <span className="artifact-thumb" data-kind={thumbKind}><Icon name={iconName} /></span>
      <span className="artifact-meta">
        <span className="artifact-title">{artifact.title}</span>
        <span className="artifact-sub">
          <span className="artifact-kind">{artKindLabel(artifact)}</span>
          <span className="artifact-sep">·</span>
          <span>{artLineCount(artifact)} lines</span>
        </span>
      </span>
      <span className="artifact-open"><Icon name="cornerUpRight" /></span>
    </button>
  );
}

/* ---------- TableView ---------- */
function TableView({ tables }) {
  const [active, setActive] = useStateA(0);
  const [copied, setCopied] = useStateA(false);
  const tbl = tables[active] || tables[0];
  if (!tbl) return null;
  const numericCols = tbl.headers.map((_, i) => isNumericColumn(tbl.rows, i));
  const copyCsv = () => {
    navigator.clipboard && navigator.clipboard.writeText(tableToCsv(tbl));
    setCopied(true); setTimeout(() => setCopied(false), 1400);
  };
  const downloadCsv = () => {
    const blob = new Blob([tableToCsv(tbl)], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a"); a.href = url; a.download = (tbl.name || "table") + ".csv"; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return (
    <div className="art-tableview">
      <div className="art-table-toolbar">
        {tables.length > 1 && (
          <div className="art-table-switcher">
            {tables.map((tt, i) => (
              <button key={i} className={cn("art-table-chip", i === active && "is-active")} onClick={() => setActive(i)}>{tt.name}</button>
            ))}
          </div>
        )}
        <span className="art-table-spacer" />
        <button className="btn btn-sm" onClick={copyCsv}><Icon name={copied ? "check" : "copy"} />{copied ? "Copied" : "Copy CSV"}</button>
        <button className="btn btn-sm" onClick={downloadCsv}><Icon name="download" />Download CSV</button>
      </div>
      <div className="art-table-scroll">
        <table className="art-table">
          <thead>
            <tr>
              <th className="art-table-rownum">#</th>
              {tbl.headers.map((h, i) => <th key={i} className={numericCols[i] ? "num" : ""}>{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {tbl.rows.map((r, ri) => (
              <tr key={ri}>
                <td className="art-table-rownum">{ri + 1}</td>
                {tbl.headers.map((_, ci) => <td key={ci} className={numericCols[ci] ? "num" : ""}>{r[ci] ?? ""}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="art-foot"><span>{tbl.rows.length} rows · {tbl.headers.length} cols</span><span className="mono">csv</span></div>
    </div>
  );
}

/* ---------- ArtifactPanel ---------- */
function ArtifactPanel({ artifact, onClose }) {
  const tables = useMemoA(() => artifact ? extractTables(artifact.content, artifact.kind, artifact.language) : [], [artifact]);
  const availableTabs = useMemoA(() => {
    const tabs = [];
    if (artifact && artifact.kind === "document") tabs.push("Document");
    if (artifact && (artifact.kind === "html" || artifact.kind === "markdown")) tabs.push("Preview");
    if (tables.length) tabs.push("Table");
    tabs.push(artifact && (artifact.kind === "code") ? "Code" : "Raw");
    return tabs;
  }, [artifact, tables]);
  const initialTab = useMemoA(() => {
    if (!artifact) return "Raw";
    if (artifact.kind === "document") return "Document";
    if (artifact.kind === "table" || artifact.kind === "csv") return "Table";
    if (artifact.kind === "html" || artifact.kind === "markdown") return "Preview";
    return tables.length ? "Table" : availableTabs[availableTabs.length - 1];
  }, [artifact]);
  const [tab, setTab] = useStateA(initialTab);
  const [copied, setCopied] = useStateA(false);
  const [isExpanded, setIsExpanded] = useStateA(false);
  useEffectA(() => { setTab(initialTab); setIsExpanded(false); }, [artifact && artifact.id]);

  // resize
  const panelRef = useRefA(null);
  useEffectA(() => {
    const onKey = (e) => {
      if (e.key === "Escape") {
        if (isExpanded) setIsExpanded(false);
        else onClose && onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose, isExpanded]);

  const startResize = (e) => {
    e.preventDefault();
    const app = document.querySelector(".app");
    const startX = e.clientX;
    const startW = parseInt(getComputedStyle(app).getPropertyValue("--art-w")) || 580;
    const move = (ev) => {
      let w = startW - (ev.clientX - startX);
      w = Math.max(380, Math.min(1100, w));
      app.style.setProperty("--art-w", w + "px");
    };
    const up = () => {
      document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up);
      const w = parseInt(getComputedStyle(app).getPropertyValue("--art-w")) || 580;
      localStorage.setItem("lattice.art.w", w);
    };
    document.addEventListener("mousemove", move); document.addEventListener("mouseup", up);
  };
  const resetWidth = () => {
    const app = document.querySelector(".app");
    app.style.setProperty("--art-w", "580px");
    localStorage.setItem("lattice.art.w", 580);
  };

  if (!artifact) return null;
  const copyContent = () => {
    navigator.clipboard && navigator.clipboard.writeText(artifact.content);
    setCopied(true); setTimeout(() => setCopied(false), 1400);
  };
  const downloadContent = () => {
    const ext = { html: "html", markdown: "md", code: artifact.language === "javascript" ? "js" : "txt", table: "csv", csv: "csv" }[artifact.kind] || "txt";
    const blob = new Blob([artifact.content], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a"); a.href = url; a.download = artifact.title.replace(/\s+/g, "-").toLowerCase() + "." + ext; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const chars = (artifact.content || "").length, linesN = artLineCount(artifact);

  return (
    <aside className={isExpanded ? "art-panel art-panel--expanded" : "art-panel"} ref={panelRef}>
      <div className="art-resize" onMouseDown={startResize} onDoubleClick={resetWidth} />
      <div className="art-hd">
        <span className="artifact-thumb" data-kind={artifact.kind === "csv" ? "table" : artifact.kind} style={{ width: 30, height: 30 }}>
          <Icon name={{ html: "window", markdown: "fileDoc", code: "code", table: "table", csv: "table" }[artifact.kind] || "file"} />
        </span>
        <div className="art-hd-meta">
          <div className="art-hd-title">{artifact.title}</div>
          <div className="art-hd-sub">{artKindLabel(artifact)}</div>
        </div>
        <div className="art-hd-actions">
          <div className="art-tabs">
            {availableTabs.map((tb) => (
              <button key={tb} className={cn("art-tab", tab === tb && "is-active")} onClick={() => setTab(tb)}>{tb}</button>
            ))}
          </div>
          <Action icon={copied ? "check" : "copy"} label="Copy" onClick={copyContent} tip={false} />
          <Action icon="download" label="Download" onClick={downloadContent} tip={false} />
          <Action icon={isExpanded ? "shrink" : "expand"} label={isExpanded ? "Exit fullscreen" : "Expand"} onClick={() => setIsExpanded(v => !v)} tip={false} />
          <Action icon="x" label="Close" onClick={onClose} tip={false} />
        </div>
      </div>
      <div className="art-body">
        {tab === "Document" && <DocumentView artifact={artifact} />}
        {tab === "Preview" && artifact.kind === "html" && (
          <iframe className="art-iframe" sandbox="allow-scripts" srcDoc={artifact.content} title={artifact.title} />
        )}
        {tab === "Preview" && artifact.kind === "markdown" && (
          <div className="art-md" dangerouslySetInnerHTML={{ __html: renderMarkdown(artifact.content) }} />
        )}
        {tab === "Table" && <TableView tables={tables} />}
        {(tab === "Code" || tab === "Raw") && (
          <pre className="art-code" dangerouslySetInnerHTML={{ __html: highlightCode(artifact.content, artifact.language) }} />
        )}
      </div>
      <div className="art-foot"><span>{chars} chars · {linesN} lines</span><span className="mono">{artifact.language || artifact.kind}</span></div>
    </aside>
  );
}

window.ARTIFACTS_SAMPLE = ARTIFACTS_SAMPLE;
Object.assign(window, {
  ArtifactCard, ArtifactPanel, TableView, DocumentView,
  extractTables, isNumericColumn, tableToCsv, splitCsvLine, splitMdRow,
  renderMarkdown, highlightCode, parseArtifacts, parseDocPages,
});
