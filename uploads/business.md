Business Rules
A specification of the rules governing how each Lattice feature is used. Every rule gets a traceable code (RN-XX-00) and describes its trigger, precondition, expected behavior and constraint. Use it alongside Documentation.md (architecture) and Referência de Elementos.md (per-component detail).

must
mandatory / system invariant
may
optional / at the user's discretion
RN-XX-00
traceable rule code
01
Conversations
RN-CV
Lifecycle of a conversation in the sidebar: creation, selection, organization (pin/archive) and deletion. The Pinned / Today / Earlier / Archived groups are derived automatically from state, not assigned manually.

ID	Rule
RN-CV-01	A new conversation is created via New chat (sidebar) or ⌘N. Every conversation is born unpinned and unarchived, with updatedAt = the moment of creation.
RN-CV-02	There is always at most one active conversation (activeId). Clicking a row makes it active; on mobile, selecting must close the sidebar.
RN-CV-03	The title is editable by double-click on the row or the F2 key. The edit saves on blur or Enter and is cancelled with Esc; a blank title keeps the previous value.
RN-CV-04	Pinning moves the conversation to the Pinned group and, by rule, unarchives it. Pin and Archive are therefore mutually exclusive.
RN-CV-05	Grouping is automatic: Today = updated within the last ~20h; Earlier = older; Archived = archived. The user does not pick the group directly.
RN-CV-06	Deleting a conversation must go through the unified confirmation modal (never a direct delete). If the deleted conversation was the active one, the system auto-selects the next available conversation.
RN-CV-07	Row actions (Rename, Pin/Unpin, Archive, Delete) are reachable via the ⋯ button or right-click, opening the context menu anchored to the row.
02
Chat output
RN-MS
An assistant reply is a script of typed blocks (text, tool, artifact) rendered in array order. Instead of one opaque text blob, every turn is a structured sequence in which each element plays a single role in the reading flow.

Output contract. blocks is the script (data), Message is the conductor (the single point that turns data into UI), and each *Block/*Card is one instrument with one role. To change what appears in the chat, edit the blocks; to change how a type appears, edit its renderer.

ID	Rule
RN-MS-01	A user message is always plain text in a right-aligned bubble. An assistant message is composed of an ordered sequence of blocks.
RN-MS-02	blocks[] is the turn's data contract: an ordered array where position in the array equals position on screen. The assistant interleaves prose, actions and deliverables by ordering this array — there is no separate layout step.
RN-MS-03	Message is the single dispatch point that maps a block to its renderer by kind: text → TextBlock (prose with inline markdown); tool → ToolCard (collapsible evidence); artifact → ArtifactCard (deliverable). No other component decides block rendering.
RN-MS-04	Each renderer has one role and stays in its lane: TextBlock = the voice/reasoning; ToolCard = verifiable evidence on demand; ArtifactCard = the consultable product, separated from the conversation thread.
RN-MS-05	An unknown kind falls back to a tool card, and a non-existent artifactId must be ignored (no broken card rendered) — an invalid block never crashes the turn.
RN-MS-06	Assistant prose is rendered with rich, Claude-style markdown. Block-level: H1–H4 headings, bulleted and numbered lists (with indentation-based nesting), blockquotes (>), horizontal rules (---), fenced code blocks ```lang and tables. Inline: bold, italic, bold-italic, code, ~~strikethrough~~ and links [text](url) — which must open in a new tab with rel="noopener noreferrer".
RN-MS-07	While streaming = true, only the last text block shows the blinking caret as a "sign of life". The caret disappears when generation finishes.
RN-MS-08	Tool cards start in the state defined by block.open and can be expanded/collapsed via the header. The body (output/diff) is only mounted when open, keeping the reading flow uncluttered.
RN-MS-09	Post-turn actions (Copy, Regenerate, Like, Dislike) act on the whole reply and only appear after generation ends (!streaming) and on hover. Like and Dislike are mutually exclusive.
RN-MS-10	Copy on a reply serializes every block to text (prose + [tag] title / body for tools), not just the visible excerpt.
RN-MS-11	The artifact boundary is preserved: substantial deliverables (large code, HTML, documents or tables) are still promoted to an artifact; only inline prose gets the rich formatting. A small code block becomes <pre>; a large one becomes an ArtifactCard. An unclosed fence during streaming renders as code until the end.
RN-MS-12	A single markdown renderer is shared between the chat (TextBlock) and the markdown artifact preview, guaranteeing consistent rendering in both places. The streaming caret (RN-MS-07) attaches to the end of the last block, preserving the sign of life.
03
Composer
RN-CP
The message input box, with voice, attachments, mode toggles and a model picker.

ID	Rule
RN-CP-01	Send is only enabled when there is non-empty text (after trim). An empty or whitespace-only message must not be sent.
RN-CP-02	Enter sends; Shift+Enter inserts a line break. After sending, the field is cleared and returns to minimum height.
RN-CP-03	The box auto-grows with the text, from 44px up to ~220px; beyond that it scrolls internally.
RN-CP-04	Voice dictation uses webkitSpeechRecognition and preserves the text already typed. When the API is unavailable, the feature degrades gracefully (no error).
RN-CP-05	Attachments show a preview per type (image / pdf / doc / file). Blob URLs must be released when the attachment is removed or unmounted (no memory leak).
RN-CP-06	Think and Deep Search are independent toggles — any combination (none, one or both) is valid.
RN-CP-07	The placeholder rotates in the empty state (no conversation) and is static ("Reply…") in an active conversation. The token counter reflects the current box content.
04
Background tasks
RN-TK
Side panel of asynchronous tasks. Starts closed by default; opens via the topbar toggle or ⌘J.

ID	Rule
RN-TK-01	Every task has exactly one status: running, queued, done or error. Tasks are grouped by status in the panel.
RN-TK-02	Inline actions depend on status: Stop only on running; Cancel only on queued; Retry only on error. Done tasks have no inline action.
RN-TK-03	Clicking the task (or Enter/Space) expands the detail (description + Tool / Status / Duration / Error). Inline buttons use stopPropagation so they do not trigger the expansion.
RN-TK-04	The panel width is adjustable via the left edge (drag); double-clicking the handle resets it. The chosen width must persist in localStorage.
RN-TK-05	The tasks panel and the artifact panel occupy the same side column; opening an artifact replaces the tasks view.
05
Artifacts & Preview
RN-AR
Deliverables (HTML, Markdown, code, table) opened in a side panel with dynamic tabs, copy, download and table extraction.

ID	Rule
RN-AR-01	Available tabs derive from kind + content: HTML/Markdown get Preview; any content with a detectable table gets Table; all have Code/Raw.
RN-AR-02	The initial tab follows precedence: table/csv → Table; HTML/MD → Preview; otherwise Table (if present) or Code.
RN-AR-03	HTML is rendered in a sandboxed iframe (execution isolation); Markdown as rich render; code with basic highlighting.
RN-AR-04	The Table tab detects tabular data from three sources — HTML <table>, Markdown table (| a | b |) and CSV — and offers Copy CSV and Download CSV. Numeric columns are right-aligned; multiple tables become selectable chips.
RN-AR-05	The panel closes via button or Esc. Width is resizable (380–1100px) and double-click resets to 580px; the value persists in localStorage.
RN-AR-06	Copy confirms visually (check) and the footer shows the character/line count of the content.
06
Projects
RN-PJ
Groupers for conversations and knowledge. Each project has its own name, description, instructions (system prompt) and settings.

ID	Rule
RN-PJ-01	Creating a project requires a Name (mandatory); Description is optional. The Create button stays disabled while the name is empty.
RN-PJ-02	On creation, the system generates a unique slug-format id, writes it to PROJECT_META, and navigates straight to the project detail.
RN-PJ-03	Name and description are inline-editable in the detail; they save on blur/Enter and reflect immediately across the other screens.
RN-PJ-04	Instructions (system prompt) auto-save and persist per project; they apply to every conversation scoped to it.
RN-PJ-05	+ New chat in {name} creates a conversation scoped to the project (linked via project.root) and opens it.
RN-PJ-06	Deleting a project must go through confirmation and, once confirmed, removes the project and all conversations linked to it.
RN-PJ-07	Project settings (default model, auto-archive, sharing) are applied by merging over the existing state, without overwriting fields that were not sent.
07
Knowledge
RN-KB
Files indexed per project, used as sources. Upload by drag-drop or picker; every file is indexed before becoming available.

ID	Rule
RN-KB-01	Accepted formats: PDF, DOCX, MD, TXT, CSV, PNG/JPG, with a limit of 25 MB per file. Upload by dragging onto the dashed zone or via the "select" button.
RN-KB-02	Every file enters with status indexing and only becomes indexed once indexing completes. Only indexed files count as usable sources.
RN-KB-03	Re-sync per file returns the status to indexing and re-indexes; on completion it returns to indexed with the date "just now".
RN-KB-04	Deleting a file must go through the confirmation modal, showing the file name in emphasis.
RN-KB-05	The file type is inferred from the extension (mapped to an icon); the drop zone gives visual feedback (is-over) during the drag.
RN-KB-06	The global Knowledge view aggregates files from all projects, with search by name and per-row deletion (also via confirmation).
08
File viewer
RN-FV
Side panel previewing a knowledge file, with download and re-indexing.

ID	Rule
RN-FV-01	Clicking a file (in Knowledge) opens the panel with name, size, date and inline status (indexed/indexing).
RN-FV-02	Download generates a real file (blob) containing the item's metadata.
RN-FV-03	Re-sync within the panel re-indexes the file, following the same status rule as Knowledge (RN-KB-03).
RN-FV-04	Closes via button or Esc. Width is resizable (380–1100px), double-click resets to 580px, and the value persists in localStorage.
09
History & Archive
RN-HS
A table of conversations with filters, multi-select and bulk actions. Archive is the same table restricted to archived conversations.

ID	Rule
RN-HS-01	The filters (text search, project, time range 24h/week/month and "with preview") are cumulative — they combine by intersection.
RN-HS-02	Multi-select enables bulk actions: bulk archive and bulk delete. Bulk delete must go through confirmation.
RN-HS-03	Per-row actions in History: Archive + Delete (with confirmation). In Archive: Restore + Delete.
RN-HS-04	The archivedOnly flag toggles between History and Archive; a conversation appears in only one of the two tables according to its archive state.
10
Connectors
RN-CN
External integrations (Drive, Gmail, GitHub, Slack, etc.) with an OAuth authorization flow and permission management.

ID	Rule
RN-CN-01	Every connector has a status: connected, expired or disconnected, shown as a pill on the card.
RN-CN-02	Connecting follows the flow idle → consent → connecting → success. The user must pass through the consent screen (scopes) before connecting.
RN-CN-03	An already-connected connector shows the granted permissions (checkboxes) and the Disconnect action; disconnecting returns the status to disconnected.
RN-CN-04	Each connector must be consistent across the three sources (metadata, logo and initial state); a missing one is treated as a registration error.
RN-CN-05	Connect/disconnect events are recorded in the Audit log (in Settings).
11
Skills
RN-SK
Toggleable assistant capabilities (PDF Reader, Web Search, Code Review, etc.), filterable by category.

ID	Rule
RN-SK-01	Each skill is turned on/off by an individual switch. The header reflects the count X of N enabled in real time.
RN-SK-02	Skills carry a core or beta badge; the badge is informational and does not block activation.
RN-SK-03	The search and category filters (All, Core, Documents, Web, Engineering, …) are combinable and affect only the listing, not the activation state.
RN-SK-04	Each card offers Configure and Docs; the enabled/disabled state persists independently per skill.
12
Settings
RN-CF
Global preferences split across General, Model, Appearance, Audit log, Shortcuts and About.

ID	Rule
RN-CF-01	Settings changes apply immediately to the global state (appSettings), with no separate "save" step.
RN-CF-02	General controls language, sounds, notifications and auto-save; Model controls default model, creativity, max tokens and tool-call display.
RN-CF-03	Appearance (dark mode, density, accent) must sync bidirectionally with the Tweaks panel — changing it in one place reflects in the other.
RN-CF-04	The Audit log lives inside Settings (not Connectors) and is read-only.
RN-CF-05	Settings is available both as a full-screen view and as a modal; both share the same body and state.
13
My account
RN-AC
User profile, plan, usage and billing.

ID	Rule
RN-AC-01	Name, email, workspace, time zone and default model are inline-editable (save on blur/Enter).
RN-AC-02	The usage meter shows tokens consumed against the plan's limit (cap).
RN-AC-03	The Manage billing and Sign out actions are always available; the sign-out item is highlighted red in the dropdown.
RN-AC-04	The account dropdown auto-repositions upward when there is no room below the trigger.
14
Search (⌘K)
RN-BS
Command palette for locating conversations and triggering commands.

ID	Rule
RN-BS-01	The palette opens via ⌘K / Ctrl+K as an overlay over the interface; it searches conversations and commands.
RN-BS-02	It closes on Esc or click-outside; selecting a result executes the corresponding action and closes the palette.
15
Destructive confirmations
RN-CO
Cross-cutting rule: every irreversible action goes through a single shared modal.

Invariant. No deletion uses the browser's native confirm(). Every destructive action — delete chat, project, file, or bulk delete — must open the same confirmation modal.

ID	Rule
RN-CO-01	The modal takes a title, a body (text or JSX with the target emphasized), labels and the onConfirm callback; on confirm it runs and closes itself.
RN-CO-02	When danger: true, the confirm button turns red. Cancel or Esc aborts with no side effect.
RN-CO-03	There is always at most one active confirmation dialog at a time.
16
Languages (i18n)
RN-LN
Cross-cutting rule: every string in the main interface goes through the translation dictionary.

ID	Rule
RN-LN-01	Supported languages: en, pt, es, fr. en is the mandatory fallback when a key is missing in the current language.
RN-LN-02	Changing the language (Settings → General → Language, or the "Language" dropdown item) must re-render the whole interface translated immediately, with no reload.
RN-LN-03	New main-interface text must be added via a translation key, never hard-coded into the component.
17
Appearance & Theme
RN-AP
Cross-cutting rule: theme, accent color and density are persisted global preferences.

ID	Rule
RN-AP-01	Light/dark mode switches the whole theme; the preference must persist across sessions.
RN-AP-02	The accent has 6 options (blue, green, purple, red, teal, orange); only one active at a time, applied across the whole interface.
RN-AP-03	Density (compact / regular / comfy) adjusts global spacing consistently across all screens.
RN-AP-04	The Tweaks panel controls and Settings → Appearance must stay in sync (see RN-CF-03).
18
Keyboard shortcuts
RN-AT
Cross-cutting rule: shortcuts respond globally, except when focus is in a text field and the key has an editing meaning.

Shortcut	Action
⌘K	Open the search palette
⌘N	New conversation
⌘,	Open Settings
⌘B	Toggle the sidebar
⌘J	Toggle the tasks panel
↵	Send message (⇧↵ = new line)
Esc	Close modal, palette or artifact panel
F2	Rename the selected conversation
19
Responsiveness & Accessibility
RN-RA
Cross-cutting rule: the interface adapts density and panels to screen size and stays operable by keyboard.

ID	Rule
RN-RA-01	On screens ≤900px, the sidebar and tasks panel become overlaid drawers with a backdrop; the topbar gains the hamburger button.
RN-RA-02	On project pages on narrow screens, the tabs scroll horizontally and the chat snippet is hidden to prioritize the essentials.
RN-RA-03	Every icon button must have an accessible label (sr-only); the focus ring appears only on keyboard navigation (focus-visible).
RN-RA-04	Escape closes any open overlay (modal, palette, dropdown, panel). Toggles expose role="switch" and aria-checked.
RN-RA-05	Features that depend on browser APIs (e.g. voice) must degrade gracefully when unavailable, without breaking the flow.