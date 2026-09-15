/* ============================================================
   skills.jsx — SkillsView, SKILL_LIST, SkillIcon
   ============================================================ */
const { useState: useStateSK, useMemo: useMemoSK } = React;

const SKILL_LIST = [
  { id: "pdf", name: "PDF Reader", cat: "Documents", badge: "core", icon: "filePdf", desc: "Extract text, tables and structure from PDF files.", on: true },
  { id: "web", name: "Web Search", cat: "Web", badge: "core", icon: "globe", desc: "Search the open web and cite live sources.", on: true },
  { id: "code-review", name: "Code Review", cat: "Engineering", badge: "core", icon: "code", desc: "Review diffs for bugs, style and security issues.", on: true },
  { id: "data-table", name: "Data Tables", cat: "Data", badge: "core", icon: "table", desc: "Parse CSV/Excel and produce comparable tables.", on: true },
  { id: "bash", name: "Shell", cat: "Engineering", badge: "core", icon: "bash", desc: "Run sandboxed shell commands and read output.", on: true },
  { id: "docx", name: "Doc Writer", cat: "Documents", badge: "beta", icon: "fileDoc", desc: "Draft and format long-form documents.", on: false },
  { id: "image", name: "Image Analysis", cat: "Documents", badge: "beta", icon: "fileImg", desc: "Describe and extract text from images.", on: false },
  { id: "calendar", name: "Scheduling", cat: "Productivity", badge: "beta", icon: "clock", desc: "Read calendars and propose meeting times.", on: false },
  { id: "sql", name: "SQL Runner", cat: "Data", badge: "beta", icon: "table", desc: "Query connected databases read-only.", on: false },
  { id: "translate", name: "Translation", cat: "Web", badge: "core", icon: "globe", desc: "Translate between 30+ languages in context.", on: true },
  { id: "summarize", name: "Summarizer", cat: "Documents", badge: "core", icon: "book", desc: "Condense long threads and files into briefs.", on: true },
  { id: "deploy", name: "Deploy Hooks", cat: "Engineering", badge: "beta", icon: "plug", desc: "Trigger CI/CD pipelines from a run.", on: false },
];

const SKILL_CATS = ["All", "Core", "Documents", "Web", "Engineering", "Data", "Productivity"];

function SkillIcon({ id }) {
  const s = SKILL_LIST.find((x) => x.id === id);
  return <Icon name={s ? s.icon : "puzzle"} />;
}

function SkillsView() {
  const t = useT();
  const [skills, setSkills] = useStateSK(() => SKILL_LIST.map((s) => ({ ...s })));
  const [q, setQ] = useStateSK("");
  const [cat, setCat] = useStateSK("All");

  const toggle = (id) => setSkills((arr) => arr.map((s) => s.id === id ? { ...s, on: !s.on } : s));
  const enabled = skills.filter((s) => s.on).length;

  const filtered = useMemoSK(() => {
    const ql = q.trim().toLowerCase();
    return skills.filter((s) => {
      const catOk = cat === "All" || (cat === "Core" ? s.badge === "core" : s.cat === cat);
      const qOk = !ql || s.name.toLowerCase().includes(ql) || s.desc.toLowerCase().includes(ql);
      return catOk && qOk;
    });
  }, [skills, q, cat]);

  return (
    <div className="view">
      <ViewHeader icon="puzzle" title={t("view.skills.title")} subtitle={`${enabled} of ${skills.length} enabled`} />
      <div className="view-body view-body-scroll">
        <div className="view-filters">
          <span className="view-search">
            <Icon name="search" />
            <input placeholder="Search skills…" value={q} onChange={(e) => setQ(e.target.value)} />
          </span>
        </div>
        <div className="skills-cats" style={{ marginBottom: 16 }}>
          {SKILL_CATS.map((c) => (
            <button key={c} className={cn("skills-cat", cat === c && "is-active")} onClick={() => setCat(c)}>{c}</button>
          ))}
        </div>
        <div className="skills-grid">
          {filtered.map((s) => (
            <div key={s.id} className={cn("skill-card", s.on && "is-on")}>
              <div className="skill-hd">
                <span className="skill-icon"><SkillIcon id={s.id} /></span>
                <div className="skill-meta">
                  <div className="skill-name">
                    {s.name}
                    <span className={cn("skill-badge", s.badge)}>{s.badge}</span>
                  </div>
                  <div className="skill-cat">{s.cat}</div>
                </div>
                <Switch on={s.on} onChange={() => toggle(s.id)} label={"Toggle " + s.name} />
              </div>
              <div className="skill-desc">{s.desc}</div>
              <div className="skill-foot">
                <button className="btn btn-sm"><Icon name="gear" />{t("common.configure")}</button>
                <button className="btn btn-sm"><Icon name="book" />{t("common.docs")}</button>
              </div>
            </div>
          ))}
        </div>
        {filtered.length === 0 && <div className="view-empty"><h3>No skills found</h3><p>Try a different search or category.</p></div>}
      </div>
    </div>
  );
}

Object.assign(window, { SKILL_LIST, SKILL_CATS, SkillIcon, SkillsView });
