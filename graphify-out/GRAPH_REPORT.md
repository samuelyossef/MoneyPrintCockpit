# Graph Report - Moneyprint layout  (2026-09-14)

## Corpus Check
- 117 files · ~442,761 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 633 nodes · 971 edges · 83 communities (34 shown, 49 thin omitted)
- Extraction: 84% EXTRACTED · 16% INFERRED · 0% AMBIGUOUS · INFERRED: 157 edges (avg confidence: 0.85)
- Token cost: 252,219 input · 11,809 output

## Community Hubs (Navigation)
- DC Canvas Component Runtime
- Gateway API Backend
- Cockpit App Shell
- Cockpit Views
- Image Slot Web Component
- Lattice App Core
- Lattice App Core (uploads copy)
- Artifact Panel (Lattice)
- Lattice Views
- Artifact Panel (uploads copy)
- Lattice Views (uploads copy)
- Package Manifest & Repo Refs
- UI Primitives (Lattice)
- Tweaks Panel (Lattice)
- UI Primitives (uploads copy)
- Tweaks Panel (uploads copy)
- Moneyprinter Runtime Bridge
- Lattice Entry Points & Source Refs
- Account/Settings Modals (Lattice)
- Account/Settings Modals (uploads copy)
- Connectors Panel (Lattice)
- Static File Server
- Connectors Panel (uploads copy)
- Gateway Contract Tests
- Composer (Lattice)
- Composer (uploads copy)
- Message/Artifact Business Rules
- OpenRouter Integration Docs
- i18n (Lattice)
- Skills Panel (Lattice)
- i18n (uploads copy)
- Skills Panel (uploads copy)
- Docs & QA Screenshots
- Moneyprinter Concept Mockups
- Confirm Modal Business Rules
- Shared Shell/Icon
- Projects Domain Rules
- Docker Compose / Turbo Config
- Project Detail View Spec
- Project Memory Domain
- OpenRouter Activity Docs
- Views Screenshot Ref
- Pasted Screenshots
- Activity Page
- Artifact Business Rules
- Composer Business Rules
- Conversation Business Rules
- Knowledge Base Business Rules
- Task Business Rules
- Profile Page
- Connectors Source Ref
- i18n Source Ref
- Skills Source Ref
- Artifacts DB Schema
- Runs DB Schema
- OpenRouter Screenshot
- OpenRouter Screenshot
- OpenRouter Screenshot
- OpenRouter Screenshot
- OpenRouter Screenshot
- OpenRouter Screenshot
- Copylab Screenshot
- Uploads Documentation
- Pasted Screenshot
- Pasted Screenshot
- Pasted Screenshot
- Pasted Screenshot
- Pasted Screenshot
- Pasted Screenshot
- Pasted Screenshot
- Pasted Screenshot
- Pasted Screenshot
- Pasted Screenshot
- Pasted Screenshot
- Pasted Screenshot
- Pasted Screenshot
- Users Page

## God Nodes (most connected - your core abstractions)
1. `ImageSlot` - 27 edges
2. `createRuntime()` - 22 edges
3. `get()` - 21 edges
4. `database()` - 19 edges
5. `create_job()` - 12 edges
6. `renderView()` - 12 edges
7. `boot()` - 12 edges
8. `now()` - 11 edges
9. `afterRender()` - 11 edges
10. `reconcile_job()` - 10 edges

## Surprising Connections (you probably didn't know these)
- `Cockpit UI Screenshot` --semantically_similar_to--> `Cockpit SPA`  [EXTRACTED] [semantically similar]
  .qa-moneyprint-desktop.png → Cockpit.html
- `Lattice Home UI Screenshot` --references--> `App`  [INFERRED]
  screens/lattice-home.png → src/app.jsx
- `index.html` --references--> `App`  [EXTRACTED]
  Lattice.html → src/app.jsx
- `App` --conceptually_related_to--> `Conversation Rules`  [EXTRACTED]
  src/app.jsx → uploads/Documentation.md
- `Lattice Admin Activity Overview` --references--> `Views`  [INFERRED]
  scraps/01-act1.png → src/views.jsx

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Lattice Admin Suite** — activity_html, users_html, profile_html [EXTRACTED 0.90]
- **Lattice Core Architecture** — src_app, src_primitives, src_i18n, uploads_documentation_md [EXTRACTED 0.95]
- **Chat Interface Flow** — src_composer, src_artifact_panel, src_app [EXTRACTED 0.90]
- **Structured Chat Output Flow** — elements_app_message, elements_app_textblock, elements_app_toolcard, elements_app_artifactcard, elements_artifact_panel_artifactpanel [EXTRACTED 1.00]
- **Project Management System** — business_rn_pj, elements_views_projectdetailview, technical_db_projects, functional_projects_overview [INFERRED 0.90]
- **Destructive Action Safety Pattern** — business_rn_co, elements_primitives_useconfirm, elements_primitives_modal [EXTRACTED 1.00]
- **OpenRouter Observability Suite** — uploads_captura_de_tela_2_7_2026_181232_openrouter_ai_jpeg, uploads_captura_de_tela_2_7_2026_18126_openrouter_ai_jpeg, uploads_captura_de_tela_2_7_2026_181315_openrouter_ai_jpeg, uploads_captura_de_tela_2_7_2026_181332_openrouter_ai_jpeg, uploads_pasted_1783027881539_0_png [EXTRACTED 1.00]
- **MoneyPrinter UI Design System** — uploads_draw_2f7f6ae8_f104_4e59_822a_8cb858dda68e_png, uploads_draw_aa4f5de5_6435_4929_80c6_e4cfd06b3e14_png, uploads_draw_f065a5c3_89e8_4eb8_9c2d_a6a0f2522f2a_png, uploads_pasted_1789258829854_0_png [INFERRED 0.90]
- **MoneyPrinter V2 User Interface Flow** — uploads_pasted_1789271113409_0_png, uploads_pasted_1789275495991_0_png, uploads_pasted_1789259029316_0_png [EXTRACTED 0.95]

## Communities (83 total, 49 thin omitted)

### Community 0 - "DC Canvas Component Runtime"
Cohesion: 0.07
Nodes (65): boot(), collectProps(), compileAttr(), compileTemplate(), contentKey(), createComponentFactory(), getDC(), Dispatcher() (+57 more)

### Community 1 - "Gateway API Backend"
Cohesion: 0.11
Nodes (54): Any, BaseModel, Connection, delete, FastAPI, FileResponse, api_key(), artifact_or_404() (+46 more)

### Community 2 - "Cockpit App Shell"
Cohesion: 0.12
Nodes (32): addWorkspace(), afterRender(), bindTopbar(), channelAction(), closeDrawer(), closePops(), collectChannel(), collectOrg() (+24 more)

### Community 3 - "Cockpit Views"
Cohesion: 0.08
Nodes (23): area(), bars(), canais(), canal(), canalRows(), clientes(), custosIA(), gerador() (+15 more)

### Community 4 - "Image Slot Web Component"
Cohesion: 0.13
Nodes (7): flushNow(), getSlot(), ImageSlot, load(), save(), setSlot(), toDataUrl()

### Community 5 - "Lattice App Core"
Cohesion: 0.08
Nodes (12): ACCENTS, DEMO_BLOCKS, describeTask(), INITIAL_ACCOUNT, INITIAL_APP_SETTINGS, initialConvos, MODELS, RECENT_GROUPS (+4 more)

### Community 6 - "Lattice App Core (uploads copy)"
Cohesion: 0.08
Nodes (12): ACCENTS, DEMO_BLOCKS, describeTask(), INITIAL_ACCOUNT, INITIAL_APP_SETTINGS, initialConvos, MODELS, RECENT_GROUPS (+4 more)

### Community 7 - "Artifact Panel (Lattice)"
Cohesion: 0.21
Nodes (17): ArtifactCard(), ArtifactPanel(), ARTIFACTS_SAMPLE, artKindLabel(), artLineCount(), DocumentView(), escapeHtml(), extractTables() (+9 more)

### Community 8 - "Lattice Views"
Cohesion: 0.15
Nodes (11): FILE_ICON_COLOR, FILE_ICON_NAME, fileIcon(), FileMark(), FileViewerPanel(), knFormatSize(), knKindFromName(), KnowledgeInline() (+3 more)

### Community 9 - "Artifact Panel (uploads copy)"
Cohesion: 0.24
Nodes (15): ArtifactCard(), ArtifactPanel(), ARTIFACTS_SAMPLE, artKindLabel(), artLineCount(), escapeHtml(), extractTables(), highlightCode() (+7 more)

### Community 10 - "Lattice Views (uploads copy)"
Cohesion: 0.15
Nodes (11): FILE_ICON_COLOR, FILE_ICON_NAME, fileIcon(), FileMark(), FileViewerPanel(), knFormatSize(), knKindFromName(), KnowledgeInline() (+3 more)

### Community 11 - "Package Manifest & Repo Refs"
Cohesion: 0.12
Nodes (15): author, description, keywords, license, main, name, scripts, dev (+7 more)

### Community 12 - "UI Primitives (Lattice)"
Cohesion: 0.19
Nodes (9): Action(), Badge(), cn(), ConfirmContext, ConfirmProvider(), ICONS, InlineEditable(), Switch() (+1 more)

### Community 14 - "UI Primitives (uploads copy)"
Cohesion: 0.19
Nodes (9): Action(), Badge(), cn(), ConfirmContext, ConfirmProvider(), ICONS, InlineEditable(), Switch() (+1 more)

### Community 16 - "Moneyprinter Runtime Bridge"
Cohesion: 0.42
Nodes (10): bindGenerator(), integer(), projectFor(), request(), restoreDraft(), saveDraft(), setPipeline(), showArtifact() (+2 more)

### Community 17 - "Lattice Entry Points & Source Refs"
Cohesion: 0.22
Nodes (9): index.html, Artifact Rules, Conversation Rules, Lattice Home UI Screenshot, App, Artifact Panel, Composer, Primitives (+1 more)

### Community 20 - "Connectors Panel (Lattice)"
Cohesion: 0.29
Nodes (5): AUDIT_LOG, CONNECTORS, ConnectorsView(), INITIAL_CONNECTOR_STATE, statusPill()

### Community 21 - "Static File Server"
Cohesion: 0.25
Nodes (6): fs, http, MIME_TYPES, path, port, server

### Community 22 - "Connectors Panel (uploads copy)"
Cohesion: 0.29
Nodes (5): AUDIT_LOG, CONNECTORS, ConnectorsView(), INITIAL_CONNECTOR_STATE, statusPill()

### Community 24 - "Composer (Lattice)"
Cohesion: 0.38
Nodes (6): ATT_COLOR, AttachChip(), attKind(), Composer(), fmtBytes(), ROTATING_PROMPTS

### Community 25 - "Composer (uploads copy)"
Cohesion: 0.38
Nodes (6): ATT_COLOR, AttachChip(), attKind(), Composer(), fmtBytes(), ROTATING_PROMPTS

### Community 26 - "Message/Artifact Business Rules"
Cohesion: 0.33
Nodes (6): Chat Output Rules, ArtifactCard Component, Message Component, TextBlock Component, ToolCard Component, ArtifactPanel Component

### Community 27 - "OpenRouter Integration Docs"
Cohesion: 0.33
Nodes (6): MoneyPrinter V2, OpenRouter Integration, OpenRouter Logs - Generations View, Video Generator UI - Topic Selection, Logs and Traces UI, MoneyPrinter V2 Turbo UI

### Community 28 - "i18n (Lattice)"
Cohesion: 0.40
Nodes (3): LangContext, LANGUAGES, STRINGS

### Community 30 - "i18n (uploads copy)"
Cohesion: 0.40
Nodes (3): LangContext, LANGUAGES, STRINGS

### Community 32 - "Docs & QA Screenshots"
Cohesion: 0.50
Nodes (4): Cockpit SPA, Technical Documentation, Lattice Desktop App, Cockpit UI Screenshot

### Community 34 - "Moneyprinter Concept Mockups"
Cohesion: 0.50
Nodes (4): MoneyPrinter V2 System, Settings UI - AI Providers and General Configuration, UI Form - Add YouTube Shorts Account, YouTube Shorts Automator UI

### Community 36 - "Confirm Modal Business Rules"
Cohesion: 0.67
Nodes (3): Destructive Confirmations Rules, Modal Component, useConfirm Hook

## Knowledge Gaps
- **140 isolated node(s):** `MODELS`, `ACCENTS`, `INITIAL_APP_SETTINGS`, `INITIAL_ACCOUNT`, `DEMO_BLOCKS` (+135 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **49 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Are the 7 inferred relationships involving `createRuntime()` (e.g. with `adoptParsed()` and `dcUpdate()`) actually correct?**
  _`createRuntime()` has 7 INFERRED edges - model-reasoned connections that need verification._
- **What connects `MODELS`, `ACCENTS`, `INITIAL_APP_SETTINGS` to the rest of the system?**
  _140 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `DC Canvas Component Runtime` be split into smaller, more focused modules?**
  _Cohesion score 0.06698564593301436 - nodes in this community are weakly interconnected._
- **Should `Gateway API Backend` be split into smaller, more focused modules?**
  _Cohesion score 0.10707070707070707 - nodes in this community are weakly interconnected._
- **Should `Cockpit App Shell` be split into smaller, more focused modules?**
  _Cohesion score 0.11666666666666667 - nodes in this community are weakly interconnected._
- **Should `Cockpit Views` be split into smaller, more focused modules?**
  _Cohesion score 0.08408408408408409 - nodes in this community are weakly interconnected._
- **Should `Image Slot Web Component` be split into smaller, more focused modules?**
  _Cohesion score 0.1319073083778966 - nodes in this community are weakly interconnected._