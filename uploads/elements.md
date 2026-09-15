Elements & Functions Reference
Complete glossary of every component, function, hook and prop in the Lattice prototype. Use it alongside Documentation.md (which covers architecture and the big picture); this document focuses on the detail of each piece.

Component
Function / helper
Hook
Data / constant
CSS group
01
primitives.jsx
Reusable building blocks exposed on window for the other Babel files.

fn
cn(...args)
Conditionally joins class names. Accepts strings and falsy values (which are ignored).

cn("btn", isActive && "is-active", danger && "danger")
comp
Tooltip({ label, side, delay, children })
Hover/focus tooltip.

label — text shown
side — "top" | "bottom" | "left" | "right" (position)
delay — ms before it appears (e.g. 250 collapsed, 600 expanded)
children — the element that triggers the tooltip
comp
Action({ icon, label, onClick, ... })
Accessible icon button with a built-in sr-only label. The base of every icon button.

comp
Modal({ open, onClose, title, children, width })
Modal shell with backdrop, close on Esc, initial focus and centering.

open — boolean controlling visibility
onClose — called on backdrop, Esc or the close button
title — header
width — width in px (default 560)
children — body; use .modal-foot for the button bar
Buttons & badge · .modal-foot
Preview
HTML
Cancel
Save changes
Delete
Pro
comp
InlineEditable({ value, onChange, placeholder, type })
Text that becomes an input on click; saves on blur or Enter, cancels on Esc.

value — current value
onChange(newValue) — called only if it changed
placeholder — shown when empty
Used in the project title/description and the My account fields
data
hook
ConfirmContext + useConfirm()
Global destructive-confirmation system. Replaces the native confirm().

useConfirm() returns an openConfirm(opts) function
opts: { title, body, danger, confirmLabel, cancelLabel, onConfirm }
body can be a string or JSX (e.g. a highlighted name)
danger: true → the confirm button turns red
onConfirm() → runs on confirm; the modal closes itself
The provider lives in App; a single <Modal> renders the active dialog
const confirm = useConfirm();
confirm({
  title: "Delete file",
  body: <>Delete <span className="confirm-target">"{name}"</span>?</>,
  danger: true,
  confirmLabel: "Delete",
  onConfirm: () => removeFile(id),
});
hook
useMediaQuery(query)
Returns a reactive boolean for a media query (e.g. useMediaQuery("(max-width: 900px)") to detect mobile).

hook
useClickOutside(ref, handler)
Calls handler on a click outside the ref element. Used in menus and dropdowns.

02
i18n.jsx
data
STRINGS
Dictionary nested by language: STRINGS[lang][key]. Languages: en, pt, es, fr. en is the fallback.

Keys grouped by prefix:

Prefix	Coverage
sb.*	Sidebar (newChat, search, projects, history, expand, collapse)
menu.*	Account dropdown (account, settings, language, archive, …)
recent.*	Conversation groups (pinned, today, earlier, archived)
convo.*	Context menu (rename, pin, unpin, unarchive)
common.*	Generic buttons (cancel, delete, archive, save, …)
tasks.*	Tasks panel (title, clear, running, queued, errors, done)
top.*	Breadcrumb (connected)
composer.*	Composer placeholder
confirm.*	Chat deletion modal
np.*	New project modal (title, subtitle, name, desc, create)
proj.*	Project detail (newChatIn, back, delete.title, delete.body)
set.* / view.*	Settings and view headers
hook
useT()
Reads the language from LangContext and returns t(key). Missing key → en fallback → the key itself.

const t = useT();
<button>{t('sb.newChat')}</button>
data
LangContext
Context carrying the current language code. App provides it with appSettings.language.

03
app.jsx
Root file: global state, layout, routing and the chat components.

comp
App()
Root component. Holds all high-level state and builds the grid layout (sidebar | main | tasks/artifact). Wraps the tree in LangContext.Provider and ConfirmContext.Provider.

comp
Sidebar({ ... })
Collapsible sidebar.

Toggle aligned right (open) / centered (collapsed)
Actions: New chat, Search, Projects, History (with tooltips and shortcuts that appear on hover)
Conversation list grouped by RECENT_GROUPS
Footer with the account → opens the dropdown
data
RECENT_GROUPS
Defines the sidebar groups and their filters:

[
  { key: "pinned",   match: c => c.pinned && !c.archived },
  { key: "today",    match: c => !c.pinned && !c.archived && c.updatedAt > hr(20) },
  { key: "earlier",  match: c => !c.pinned && !c.archived && c.updatedAt <= hr(20) },
  { key: "archived", match: c => c.archived },
]
The label is translated by t('recent.' + key).

comp
ConvoRow({ c, active, editing, onSelect, onMenu, ... })
A conversation row. Click selects; double-click renames; ⋯/right-click opens the context menu.

ConvoRow · click to select
Preview
HTML
Verification gate rollout
2h
Embedded PG harness migrations
now
Q3 OKR draft
1d
TypeBox codegen migration
3d
comp
Breadcrumb({ convo, onToggleSidebar, onToggleTasks, ... })
Topbar: hamburger (mobile), project/chat trail, "connected" pill and the tasks-panel toggle.

data
Structured chat output — overview
The central conversation area (.thread → .thread-inner) is where information reaches the user in a structured way: instead of a single text block, each assistant turn is an ordered sequence of typed blocks that Message walks in order. Each element has a specific role in the "reading flow":

Element (role)	Role in the output
.thread / .thread-inner	Reading container. Scrolls vertically, centers the column (max-width: 760px) and sets the rhythm between turns (gap). Layout only — it doesn't decide content.
Message	Turn conductor. Decides the format by role and, for the assistant, dispatches each block to the right renderer. The single point that turns data (blocks) into UI.
blocks[]	Turn script. Ordered array describing the response as data ({ kind: "text" | "tool" | "artifact", … }). Array order = screen order: the assistant interleaves prose, actions and deliverables.
TextBlock	Voice/reasoning. Renders the prose (paragraphs, breaks, inline markdown). The "narrator" that explains what happens between actions.
ToolCard	Work/evidence. Shows a tool call as a collapsible card — tag, title, metric and body (output/diff). Role: give proof and detail on demand, without cluttering the read.
ArtifactCard	Deliverable. Represents the browsable result (HTML, Markdown, table, code); clicking opens the side ArtifactPanel. Role: separate the "product" from the conversation.
.caret	Sign of life. Blinking cursor at the end of the last TextBlock while streaming — signals "still generating".
MsgActions	Post-turn. Copy/Regenerate/Like/Dislike bar that appears on hover, after the turn ends (!streaming). Role: act on the whole response.
.msg-table	Comparable data. Table rendered directly in the turn (not in an artifact) when the response needs to align values side by side. Role: structure short comparisons inline, without pulling the user out of the conversation thread.
Contract in one line: blocks is the script, Message is the conductor, and each *Block/*Card is an instrument with a single role. To change what appears in the chat, edit the blocks (data); to change how a type appears, edit its renderer.

real elements · one full turn
Preview
HTML
Confirm the embedded PG harness applies our migrations. I need the issue_work_products columns visible to the gate.
Looking at the harness setup — it boots Postgres through applyPendingMigrations, which runs the full migration list. That means work_products and issue_approvals are already visible to the gate.

Bash
Run shared verification-gate tests
0.8s · 19/19 ✓
$ pnpm test packages/server/verification-gate

  ✓ blocks low-confidence advisory (84ms)
  ✓ allows passthrough on bypass-by-board (61ms)
  ✓ rejects mismatched actor on update (44ms)

  19 passed (19)

Verification matrix
Table
·
7 lines
Stage 21 is closed. Moving to stage 12 — the envelope is declared in the prompt but the real enforcement lives in toolGovernanceService.evaluate

rule
Structured-output rule
rule
Every assistant response is an ordered sequence of typed blocks — never a dump of running text. Use prose (TextBlock) only for reasoning and transitions; comparable data goes in an inline .msg-table; verifiable work becomes a ToolCard; and any browsable deliverable becomes an ArtifactCard.

Quick test: if the information compares across rows and columns → table; if it's a product (doc, code, spreadsheet) → artifact; if it's how you got there → tool card; everything else is prose.

Below, a turn that follows the rule: a sentence of context, the inline table with the data, and the deliverable split into an artifact — whose Preview details each function and its elements.

Message that follows the rule · text → table → artifact
Preview
HTML
Give me an inventory of the chat output elements and who renders each one.
Following the rule: the short comparison goes in the inline table; the full, function-by-function breakdown lives in the artifact below.

Element	Role	blocks
TextBlock	Prose / reasoning	text
.msg-table	Inline comparable data	table
ToolCard	Evidence on demand	tool
ArtifactCard	Browsable deliverable	artifact

Functions & elements — detail
Markdown
·
6 functions


comp
Message({ m, streaming, onCopy, onRegen, onOpenArtifact })
Renders one turn and is the single place mapping block type → component.

m.role === "user" → a simple right-aligned bubble (.msg-user), text only.
m.role === "assistant" → .msg-assist that runs m.blocks.map(...) dispatching by kind:
text → TextBlock (the last block gets streaming to show the caret)
artifact → resolves ARTIFACTS_SAMPLE[block.artifactId] and builds an ArtifactCard (returns null if the id doesn't exist)
any other (tool) → ToolCard
When the turn ends (!streaming) and there are blocks, it appends MsgActions; onCopy serializes every block to text (prose + the tools' [tag] title / body).
comp
TextBlock({ text, streaming })
The assistant's "voice". Splits text into paragraphs on blank lines (\n\n+), then into lines on \n (becomes <br>), and runs each line through renderInline. If streaming, it pins the .caret at the end of the last paragraph.

TextBlock · markdown inline + caret
Preview
HTML
Three-phase plan. The hard constraint: @lattice/types must stay structurally identical at the type level, even as the generator changes underneath.

fn
renderInline(text)
Inline mini-markdown for chat output: recognizes **bold**, *italic* and `code` via a single regex and returns an array of React nodes. Keeps text readable without a full parser.

comp
ToolCard({ block })
The turn's "evidence" — a collapsible tool-call card.

Header: tag (Bash/Read/Edit/Grep…, becomes a lowercase CSS class), title, meta (e.g. 0.8s · 19/19 ✓) and a rotating chevron.
Local open state (starts from block.open); clicking the header toggles it.
Body (.tool-body) only mounts when open: renders block.body in monospace with scrolling — terminal output or a diff (added/removed lines).
Role: offer verifiable detail on demand, without interrupting the reading of the prose.
ToolCard · collapsed and open
Preview
HTML
Read
issue_work_products.ts
143 lines · 1 file
Edit
verification-gate.integration.test.ts
+84 −0
 1  + import { describe, it, expect } from 'vitest';
 2  + import { applyPendingMigrations } from '../harness/pg';
 3  + import { evaluateGate } from '../verification-gate';
 4  +
 5  + describe('verification-gate enforcement', () => {
comp
ArtifactCard({ artifact, onOpen })
The "deliverable" embedded in the turn. A clickable card that calls onOpen(artifact) to open the side ArtifactPanel (Preview/Table/Code). Separates the final product (document, table, code, HTML) from the conversation thread — see details in artifact-panel.jsx.

ArtifactCard · by type (html / markdown / code)
Preview
HTML

Landing hero
Web preview
·
38 lines

Q3 OKR draft
Markdown
·
24 lines

Throttled counter
Code
·
12 lines
comp
TasksPanel({ tasks, onClear, onRetry, onStop, tasksOpen })
Background tasks drawer. Groups by status (running/queued/errors/done), with inline actions and a resize handle that persists the width.

Clicking a task toggles a detail block (description + Tool/Status/Duration/Error); the chevron rotates; Enter/Space toggle too
describeTask(t) — uses t.detail or generates a description from tool + status
The inline buttons (stop/cancel/retry) call stopPropagation so they don't trigger the expansion
TasksPanel · click a task to expand
Preview
HTML
Run shared verification-gate tests
Bash
0.8s
Run intake classifier and model tests
Bash
—
Typecheck server package
Bash
exit 2
Run planner unit tests
Bash
queued
Background tasks · full panel (click a task)
Preview
HTML
Background tasks
Clear
Running
01
Run intake classifier and model tests
Bash
running…
stop
Queued
01
Run planner unit tests
Bash
queued
cancel
Errors
01
Typecheck server package
Bash
error · exit 2
retry
Done
05
Run enforcement test from server dir
Bash
done · 0.6s
Run verification-gate enforcement integration
Bash
done · 1.2s
Run issues-service integration (embedded PG)
Bash
done · 2.8s
Run shared verification-gate tests
Bash
done · 0.8s
Check skills / global existence and references
Grep
done · 0.2s
comp
EmptyHome(...)
Initial state when there's no active conversation: greeting + centered composer + suggestion chips.

comp
NewProjectModal({ open, onClose, onCreate, t })
Project creation modal.

Title np.title, subtitle np.subtitle
Fields: Name (required) and Description
Enter in any field submits; the Create button is disabled without a name
onCreate({ name, desc }) → creates it in PROJECT_META
fn
Project functions (inside App)
Function	What it does
createProject({name,desc})	Generates a unique slug id, writes to PROJECT_META, navigates to the detail
renameProject(id, name)	Updates PROJECT_META[id].name
updateProjectDesc(id, desc)	Updates the description
saveProjectInstructions(id, val)	Persists the project's system prompt
updateProjectSettings(id, patch)	Merges settings (model, auto-archive, share)
newChatInProject(id)	Creates a chat scoped to the project and opens it
requestDeleteProject(id)	Opens the confirm; on confirm calls deleteProjectConfirmed
deleteProjectConfirmed(id)	Removes the project + all its chats
fn
Conversation functions (inside App)
Function	What it does
togglePin(id)	Pin/unpin (and unarchive)
archiveConvo(id)	Archive/unarchive
deleteConvo(id)	Removes; if it was active, selects the next one
requestDeleteConvo(id)	Opens the chat-deletion confirm
data
INITIAL_APP_SETTINGS
Defaults: language, soundEnabled, notificationsEnabled, autoSave, model, creativity, maxTokens, showTools, dark, compact, accent.

data
initialConvos
Seed conversations that populate the sidebar and the projects.

04
composer.jsx
comp
Composer({ value, onChange, onSend, disabled, tokens, model, onPickModel, rotatePlaceholder, staticPlaceholder })
Main input box.

Autosize — grows from 44px to 220px with the text
Placeholder — staticPlaceholder (default = t('composer.placeholder.reply')); rotatePlaceholder enables rotating suggestions in the empty state
Mic — uses webkitSpeechRecognition, preserving the already-typed text; degrades gracefully if the API is missing
Attachments — file picker with per-type preview (image/pdf/doc/file) and blob-URL cleanup
Toggles — Think and Deep Search (independent)
Model picker + token counter
onSend() on Enter; Shift+Enter adds a newline
Composer · type to enable send
Preview
HTML
Ask Lattice anything…
Attach
Think
0 tok
lattice-4.1

05
views.jsx
Full-screen views mounted in <main> based on view.kind.

comp
ViewHeader({ icon, title, subtitle, actions, children })
Standard view header. actions is pinned to the right (e.g. a Back button). children is usually ViewTabs.

comp
ViewTabs({ tabs, active, onChange })
Horizontal tabs with an optional counter (tab.count). They scroll on narrow screens.

ViewTabs · click to switch
Preview
HTML
Chats
12
Knowledge
5
Instructions
Settings
comp
ProjectsView({ convos, onOpenProject, onNewProject })
Grid of project cards (name + description + stats). "New project" button in the header.

comp
ProjectDetailView({ projectId, convos, onBack, onOpenChat, onOpenFile, onRename, onUpdateDesc, onNewChat, onSaveInstructions, onUpdateSettings, onDelete })
Project detail with tabs:

Chats — list filtered by project.root; empty state with "+ New chat in {name}"
Knowledge — renders KnowledgeInline
Instructions — textarea with auto-save (reads PROJECT_META[id].instructions or default)
Settings — model/auto-archive selects, a share switch, Delete project
Title and description use InlineEditable
comp
KnowledgeInline({ projectId, onOpenFile, allowUpload })
Project file management.

Upload: drag-drop onto the dashed zone or the "browse" button (file picker)
Each file enters as indexing → indexed (simulated timeout)
Re-sync per file (goes back to indexing and re-indexes)
Delete per file via useConfirm() (unified modal)
helpers: knKindFromName(name) (maps extension → type), knFormatSize(bytes) (B/KB/MB)
comp
KnowledgeView({ onOpenFile })
Global view: every file across all projects, with search and per-row delete (also via useConfirm()).

comp
HistoryView({ convos, onOpenChat, onArchive, onDelete, archivedOnly })
History/archive table.

Filters: search, project, time range
Multi-select with bulk archive/delete (delete goes through confirm)
Per-row actions: Archive/Restore + Delete (confirm)
archivedOnly switches between History and Archive
comp
FileViewerPanel({ file, onClose })
Side panel for file preview.

Header with name, size, date and inline status (indexed/indexing)
Download — generates a real file (blob) with the metadata
Re-sync — re-indexes the file
Close — button or Esc
Resize handle on the left edge (380–1100px, double-click resets to 580px; persisted in localStorage)
data
PROJECT_META
Map id → { name, desc, color, icon, instructions?, defaultModel?, autoArchive?, shared? }. Source of truth for projects.

data
PROJECT_KNOWLEDGE
Map projectId → [ { id, name, kind, size, updated, status } ]. Files per project.

fn
fileIcon(kind)
Returns the icon SVG for the given type (pdf, docx, md, txt, csv, img).

06
modals.jsx
comp
MyAccountView / MyAccountModal
Account: avatar, editable name, plan badge, inline fields (email, workspace, timezone, model), usage meter, billing and sign out. View and Modal share MyAccountBody.

comp
SettingsView / SettingsModal
Settings with a side nav + pane. Both use SettingsBody.

comp
SettingsBody({ settings, onChange, tab, setTab })
Settings content, split by SETTINGS_TABS:

Tab	Controls
General	Language (en/pt/es/fr), sounds, notifications, auto-save
Model	default model, creativity, max tokens, show tool calls
Appearance	dark mode, compact density, accent
Audit log	event table
Shortcuts	shortcut list
About	version/info
Changing Language calls onChange({ language }), which updates appSettings and re-renders the whole app translated.

Switch · click to toggle
Preview
HTML
Notifications

Compact mode

Show tool calls

data
SETTINGS_TABS
List of { id, tk } where tk is the label's i18n key.

07
artifact-panel.jsx
comp
ArtifactCard({ artifact, onOpen })
Clickable card inside a message; opens the panel.

comp
ArtifactPanel({ artifact, onClose })
Side panel with Preview / Table / Code tabs (dynamic):

HTML → iframe sandbox
Markdown → render rico (renderMarkdown)
Code → basic highlight (highlightCode)
Table → appears when there's tabular data; renders TableView
Copy (with check), Download, Close (Esc), persistent resize handle
Footer with char/line count
The initial tab is chosen by kind: table/csv → Table; HTML/MD → Preview; otherwise Table (if any) or Code
rule
Structured data output goes to the right-hand panel, not the chat body. Browsable results open in a dedicated ArtifactPanel — pick the tab by format: Web preview for renderable HTML, Markdown for documents, Table for CSV/tabular data (with Copy/Download CSV), Code for source. The process (tasks, commands) goes to Background tasks.

ArtifactPanel · Web preview (html)
Preview
HTML
Landing hero
html
Preview
Code




612 chars · 26 lines
html
ArtifactPanel · Markdown
Preview
HTML
Q3 OKR draft
markdown
Preview
Code



Q3 Objectives
Three objectives focused on reducing time-to-value for new teams.

O1 — Halve median time-to-first-output
KR1. New signups produce a useful agent output within 8 minutes (p50).

KR2. First-session completion rate above 62% (currently 41%).

KR3. Onboarding drop-off below 15% at the "first prompt" step.

O2 — Day-30 power-user parity
KR1. Day-30 actives use Projects and Background tasks at parity with 90-day actives.

KR2. Median tasks-per-session ≥ 3 by day 14.

Notes: assume TTV measured in days from signup to first useful output.
968 chars · 21 lines
markdown
ArtifactPanel · Table (csv)
Preview
HTML
Verification matrix
csv
Table
Raw


5 rows · 6 cols
Copy CSV
Download CSV
suite	cases	passing	failing	coverage	duration
work_products	42	42	0	94%	0.8s
issue_approvals	31	31	0	91%	0.6s
tool_governance	57	55	2	88%	1.2s
audit_trail	19	19	0	97%	0.3s
permission_gate	64	64	0	93%	1.1s
5 rows · 6 cols
csv
ArtifactPanel · Code (javascript)
Preview
HTML
Throttled counter
javascript



// Tiny throttled counter — leading + trailing edge.
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
let n = 0; setInterval(() => log(++n), 40);
622 chars · 23 lines
javascript
comp
TableView({ tables })
Renders extracted tables + export.

tables — array of { name, headers, rows }
Row numbering, sticky header, numeric columns right-aligned
Copy CSV / Download CSV in the toolbar
Multiple tables become selectable chips
fn
extractTables(content, kind, language)
Detects and extracts tables from three sources:

HTML → DOMParser reads each <table> (uses <thead>/<th> or the 1st row as header)
Markdown → | a | b | blocks with a |---|---| separator
CSV / kind: "table" → parse with escaped quotes and commas
Returns [{ name, headers, rows }].

fn
isNumericColumn / tableToCsv / splitCsvLine / splitMdRow
Table helpers: numeric-column detection, CSV serialization (escapes , " \n) and CSV/Markdown row parsers.

fn
renderMarkdown(text) / highlightCode(code, lang)
Rendering helpers. renderMarkdown covers headings, lists, code, bold/italic, links. highlightCode applies simple token highlighting.

fn
parseArtifacts(text)
Parses custom `<artifact>` tags (both complete and streaming) from the assistant text stream. Automatically saves extracted artifacts to `window.ARTIFACTS_SAMPLE` and returns `{ blocks }` containing text and artifact kind blocks.

data
ARTIFACTS_SAMPLE
Sample artifacts, referenced by { kind: "artifact", artifactId } in messages. Includes table demos: art-testmatrix (CSV) and art-adoption (Markdown with a table).

08
extras.jsx
comp
Menu({ open, onClose, anchorRect, items })
Context dropdown positioned by anchorRect. items accepts { label, icon, kbd, onClick, danger, divider, section }. Auto-flips upward when it doesn't fit below.

comp
SearchPalette({ open, onClose, ... })
Search overlay (⌘K) over conversations and commands, with backdrop blur.

comp
MsgActions(...)
Action bar that appears on hover of an assistant message: Copy, Regenerate, Like, Dislike.

MsgActions · post-turn bar
Preview
HTML




ProjectsModal / KnowledgeModal here are legacy — the app uses the versions in views.jsx.

09
connectors.jsx
comp
ConnectorsView
Grid of 14 connectors with logo, status pill, account and last sync.

comp
ConnectorModal
OAuth mock: idle → consent → connecting (spinner) → success. When connected, shows permissions (checkboxes) + Disconnect.

data
CONNECTORS / ConnLogo / INITIAL_CONNECTOR_STATE
All three must stay consistent (id present in all three). CONNECTORS = metadata, ConnLogo = SVGs, INITIAL_CONNECTOR_STATE = initial state.

data
AUDIT_LOG
Events shown in the Audit log tab of Settings.

10
skills.jsx
comp
SkillsView
12 skills with icon, badge (core/beta), switch, description, Configure + Docs. Filters by search and category; header with X of N enabled.

data
SKILL_LIST / SkillIcon
Skill registry and icon map (SVG path by id).

11
Root state map
Main useState in App:

State	Purpose
convos / activeId	Conversation list and the active one
view	Current view ({ kind, projectId? })
draft	Composer text
streaming	Response in progress
appSettings	Global settings (includes language, accent, dark, …)
sidebarOpen / tasksOpen	Panel visibility
artifactId / filePreview	Side panel (artifact or file)
confirmDialog	Active confirmation dialog (via ConfirmContext)
newProjectOpen	New project modal
settingsOpen / accountOpen / searchOpen	Modals/overlays
convoMenu / accountMenuRect / modelMenuRect	Context-menu positions
editingId	Conversation being renamed inline
projectsVersion	Counter to force a re-render when PROJECT_META/PROJECT_KNOWLEDGE change
PROJECT_META and PROJECT_KNOWLEDGE are mutable objects outside React. To reflect changes, mutate them and increment projectsVersion (or a local version in the component).

12
User flows
Create a project
Projects → New project → fill in Name/Description → Create → writes to PROJECT_META, navigates to the detail.

Add knowledge
Project detail → Knowledge → drag files or "browse" → they enter as indexing → indexed. Re-sync and Delete per row.

Chat inside the project
Project detail → Chats → + New chat in {name} → creates a scoped chat and opens it.

Switch language
Settings → General → Language → the whole UI re-renders translated instantly.

Delete anything (any Delete)
Triggers useConfirm() → the same modal (title + body + red Cancel/Delete) → onConfirm runs.

Preview a file
Click a file (Knowledge) → side FileViewerPanel → Download / Re-sync / resize / Close (Esc).

Extract / view a table
Open an artifact with tabular data (HTML <table>, Markdown or CSV) → Table tab → rendered table → Copy CSV or Download CSV.