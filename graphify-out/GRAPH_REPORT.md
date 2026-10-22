# Graph Report - Moneyprint layout  (2026-09-18)

## Corpus Check
- 47 files · ~463,820 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 901 nodes · 1759 edges · 89 communities (50 shown, 39 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 167 edges (avg confidence: 0.85)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `ac2044f9`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- support.js
- main.py
- cockpit-app.js
- cockpit-views.js
- ImageSlot
- lattice-app/app.jsx
- uploads/app.jsx
- lattice-app/artifact-panel.jsx
- lattice-app/views.jsx
- uploads/artifact-panel.jsx
- uploads/views.jsx
- package.json
- lattice-app/primitives.jsx
- lattice-app/tweaks-panel.jsx
- uploads/primitives.jsx
- uploads/tweaks-panel.jsx
- moneyprinter-runtime.js
- Artifact Rules
- lattice-app/modals.jsx
- uploads/modals.jsx
- lattice-app/connectors.jsx
- server.js
- uploads/connectors.jsx
- GatewayContractTests
- lattice-app/composer.jsx
- uploads/composer.jsx
- review_job
- MoneyPrinter V2
- lattice-app/i18n.jsx
- lattice-app/skills.jsx
- uploads/i18n.jsx
- uploads/skills.jsx
- Cockpit SPA
- MoneyPrinter V2 System
- create_job
- shell.js
- database
- Docker Compose
- Any
- row_as_job
- refresh_channel
- Lattice Admin Activity Overview
- UI Buttons - Video Actions
- Activity Page
- get
- parse_json_object
- 2. Project Details Tabs
- 2. Database Models (SQLite)
- User Profile
- Documentação Funcional — Lattice Cockpit
- 💻 Como Executar
- OpenRouter Logs - Generations View (Past Month)
- OpenRouter Activity Dashboard
- OpenRouter Logs - Upstream Requests
- OpenRouter Logs - Sessions View
- OpenRouter Logs - Jobs View
- OpenRouter Credits Page
- Copy Lab Logo
- Lattice Documentation
- AI Platform Dashboard - Visão Geral
- AI Platform Dashboard - Visão Geral (Variant)
- AI Platform Dashboard - Visão Geral (Variant 2)
- Recent Videos Grid UI
- Solid Purple Background
- AI Chat Interface
- Marketing/Landing Page Component
- OpenRouter Profile & API Keys
- Usage Metrics Widgets
- UI Snippet - TTS and STT Provider Selection
- Dashboard - Analytics Charts
- Dashboard - Account Counters
- UI Buttons - Video Actions (Duplicate)
- Users Management
- patch
- no_cache_for_static_app
- Conversation Rules
- Lattice Home UI Screenshot

## God Nodes (most connected - your core abstractions)
1. `GatewayContractTests` - 74 edges
2. `database()` - 51 edges
3. `now()` - 27 edges
4. `ImageSlot` - 27 edges
5. `createRuntime()` - 22 edges
6. `create_job()` - 21 edges
7. `get()` - 21 edges
8. `request()` - 19 edges
9. `row_as_job()` - 17 edges
10. `review_job()` - 16 edges

## Surprising Connections (you probably didn't know these)
- `Cockpit UI Screenshot` --semantically_similar_to--> `Cockpit SPA`  [EXTRACTED] [semantically similar]
  .qa-moneyprint-desktop.png → Cockpit.html
- `MoneyPrinter V2 Turbo UI` --semantically_similar_to--> `Video Generator UI - Topic Selection`  [INFERRED] [semantically similar]
  uploads/pasted-1789258829854-0.png → uploads/draw-f065a5c3-89e8-4eb8-9c2d-a6a0f2522f2a.png
- `Technical Documentation` --cites--> `Cockpit SPA`  [EXTRACTED]
  DOCUMENTACAO.md → Cockpit.html
- `Technical Documentation` --cites--> `Lattice Desktop App`  [EXTRACTED]
  DOCUMENTACAO.md → lattice-app/Lattice.html
- `MoneyPrinter V2` --conceptually_related_to--> `OpenRouter Integration`  [INFERRED]
  uploads/pasted-1789258829854-0.png → uploads/Captura de tela_2-7-2026_181232_openrouter.ai.jpeg

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Lattice Admin Suite** — activity_html, users_html, profile_html [EXTRACTED 0.90]
- **MoneyPrinter V2 User Interface Flow** — uploads_pasted_1789271113409_0_png, uploads_pasted_1789275495991_0_png, uploads_pasted_1789259029316_0_png [EXTRACTED 0.95]
- **OpenRouter Observability Suite** — uploads_captura_de_tela_2_7_2026_181232_openrouter_ai_jpeg, uploads_captura_de_tela_2_7_2026_18126_openrouter_ai_jpeg, uploads_captura_de_tela_2_7_2026_181315_openrouter_ai_jpeg, uploads_captura_de_tela_2_7_2026_181332_openrouter_ai_jpeg, uploads_pasted_1783027881539_0_png [EXTRACTED 1.00]
- **MoneyPrinter UI Design System** — uploads_draw_2f7f6ae8_f104_4e59_822a_8cb858dda68e_png, uploads_draw_aa4f5de5_6435_4929_80c6_e4cfd06b3e14_png, uploads_draw_f065a5c3_89e8_4eb8_9c2d_a6a0f2522f2a_png, uploads_pasted_1789258829854_0_png [INFERRED 0.90]

## Communities (89 total, 39 thin omitted)

### Community 0 - "support.js"
Cohesion: 0.07
Nodes (65): boot(), collectProps(), compileAttr(), compileTemplate(), contentKey(), createComponentFactory(), getDC(), Dispatcher() (+57 more)

### Community 1 - "main.py"
Cohesion: 0.10
Nodes (35): AsyncClient, BaseModel, datetime, FastAPI, agent_loop(), as_utc(), _config_string(), default_generation_settings() (+27 more)

### Community 2 - "cockpit-app.js"
Cohesion: 0.11
Nodes (32): addWorkspace(), afterRender(), bindTopbar(), channelAction(), closeDrawer(), closePops(), collectChannel(), collectOrg() (+24 more)

### Community 3 - "cockpit-views.js"
Cohesion: 0.07
Nodes (30): area(), bars(), canais(), canal(), canalRows(), chStatus(), clientes(), custosIA() (+22 more)

### Community 4 - "ImageSlot"
Cohesion: 0.13
Nodes (7): flushNow(), getSlot(), ImageSlot, load(), save(), setSlot(), toDataUrl()

### Community 5 - "lattice-app/app.jsx"
Cohesion: 0.08
Nodes (12): ACCENTS, DEMO_BLOCKS, describeTask(), INITIAL_ACCOUNT, INITIAL_APP_SETTINGS, initialConvos, MODELS, RECENT_GROUPS (+4 more)

### Community 6 - "uploads/app.jsx"
Cohesion: 0.08
Nodes (12): ACCENTS, DEMO_BLOCKS, describeTask(), INITIAL_ACCOUNT, INITIAL_APP_SETTINGS, initialConvos, MODELS, RECENT_GROUPS (+4 more)

### Community 7 - "lattice-app/artifact-panel.jsx"
Cohesion: 0.21
Nodes (17): ArtifactCard(), ArtifactPanel(), ARTIFACTS_SAMPLE, artKindLabel(), artLineCount(), DocumentView(), escapeHtml(), extractTables() (+9 more)

### Community 8 - "lattice-app/views.jsx"
Cohesion: 0.15
Nodes (11): FILE_ICON_COLOR, FILE_ICON_NAME, fileIcon(), FileMark(), FileViewerPanel(), knFormatSize(), knKindFromName(), KnowledgeInline() (+3 more)

### Community 9 - "uploads/artifact-panel.jsx"
Cohesion: 0.24
Nodes (15): ArtifactCard(), ArtifactPanel(), ARTIFACTS_SAMPLE, artKindLabel(), artLineCount(), escapeHtml(), extractTables(), highlightCode() (+7 more)

### Community 10 - "uploads/views.jsx"
Cohesion: 0.15
Nodes (11): FILE_ICON_COLOR, FILE_ICON_NAME, fileIcon(), FileMark(), FileViewerPanel(), knFormatSize(), knKindFromName(), KnowledgeInline() (+3 more)

### Community 11 - "package.json"
Cohesion: 0.12
Nodes (15): author, description, keywords, license, main, name, scripts, start (+7 more)

### Community 12 - "lattice-app/primitives.jsx"
Cohesion: 0.19
Nodes (9): Action(), Badge(), cn(), ConfirmContext, ConfirmProvider(), ICONS, InlineEditable(), Switch() (+1 more)

### Community 14 - "uploads/primitives.jsx"
Cohesion: 0.19
Nodes (9): Action(), Badge(), cn(), ConfirmContext, ConfirmProvider(), ICONS, InlineEditable(), Switch() (+1 more)

### Community 16 - "moneyprinter-runtime.js"
Cohesion: 0.14
Nodes (42): applyConfigDefaults(), applyDefaults(), bindAgents(), bindAiHelpers(), bindConfigSettings(), bindGenerationDefaults(), bindGenerator(), bindJobsWorkers() (+34 more)

### Community 20 - "lattice-app/connectors.jsx"
Cohesion: 0.29
Nodes (5): AUDIT_LOG, CONNECTORS, ConnectorsView(), INITIAL_CONNECTOR_STATE, statusPill()

### Community 21 - "server.js"
Cohesion: 0.25
Nodes (6): fs, http, MIME_TYPES, path, port, server

### Community 22 - "uploads/connectors.jsx"
Cohesion: 0.29
Nodes (5): AUDIT_LOG, CONNECTORS, ConnectorsView(), INITIAL_CONNECTOR_STATE, statusPill()

### Community 24 - "lattice-app/composer.jsx"
Cohesion: 0.38
Nodes (6): ATT_COLOR, AttachChip(), attKind(), Composer(), fmtBytes(), ROTATING_PROMPTS

### Community 25 - "uploads/composer.jsx"
Cohesion: 0.38
Nodes (6): ATT_COLOR, AttachChip(), attKind(), Composer(), fmtBytes(), ROTATING_PROMPTS

### Community 26 - "review_job"
Cohesion: 0.10
Nodes (29): Exception, api_key(), config_value(), create_thumbnail(), download_turbo_file(), extract_review_frames(), generate_image(), health() (+21 more)

### Community 27 - "MoneyPrinter V2"
Cohesion: 0.33
Nodes (6): MoneyPrinter V2, OpenRouter Integration, OpenRouter Logs - Generations View, Video Generator UI - Topic Selection, Logs and Traces UI, MoneyPrinter V2 Turbo UI

### Community 28 - "lattice-app/i18n.jsx"
Cohesion: 0.40
Nodes (3): LangContext, LANGUAGES, STRINGS

### Community 30 - "uploads/i18n.jsx"
Cohesion: 0.40
Nodes (3): LangContext, LANGUAGES, STRINGS

### Community 32 - "Cockpit SPA"
Cohesion: 0.50
Nodes (4): Cockpit SPA, Technical Documentation, Lattice Desktop App, Cockpit UI Screenshot

### Community 34 - "MoneyPrinter V2 System"
Cohesion: 0.50
Nodes (4): MoneyPrinter V2 System, Settings UI - AI Providers and General Configuration, UI Form - Add YouTube Shorts Account, YouTube Shorts Automator UI

### Community 36 - "create_job"
Cohesion: 0.13
Nodes (23): build_turbo_payload(), clean_narration(), create_job(), job_max_words(), JobCreate, long_script(), LongScriptInput, Caps an AI-written script at max_words, backing up to the end of the last full… (+15 more)

### Community 39 - "database"
Cohesion: 0.18
Nodes (22): delete, agent_tick(), cancel_schedule(), connect(), database(), delete_channel(), delete_job(), delete_project() (+14 more)

### Community 41 - "Any"
Cohesion: 0.18
Nodes (19): Any, analytics_overview(), analytics_score(), create_project(), get_or_create_project(), get_project(), get_project_or_404(), list_projects() (+11 more)

### Community 42 - "row_as_job"
Cohesion: 0.23
Nodes (18): Connection, approve_job(), channel_row_or_404(), generate_metadata(), job_language(), job_row_or_404(), normalize_youtube_metadata(), publish_job() (+10 more)

### Community 43 - "refresh_channel"
Cohesion: 0.15
Nodes (18): ChannelConnect, ChannelPatch, clean_language(), list_channels(), patch_channel(), TikTok goes through one Upload-Post profile per Cockpit channel; the user links…, Titles trending on YouTube in the channel's region. Only a hint for the…, refresh_channel() (+10 more)

### Community 48 - "get"
Cohesion: 0.15
Nodes (17): FileResponse, artifact_or_404(), artifact_proxy(), get_thumbnail(), job_events(), list_schedule(), Upserts by YouTube channel id, so reconnecting a channel refreshes it (keeping…, root() (+9 more)

### Community 49 - "parse_json_object"
Cohesion: 0.19
Nodes (13): ai_image_materials(), _fill_visual_terms(), narration_seconds(), parse_json_object(), Kimi sometimes wraps the JSON in prose or a code fence; take the outermost…, Splits narration into `parts` contiguous, roughly-equal chunks by word count,…, Turbo drops a term's candidates once its URLs are already claimed by an earlier…, One stock-footage search term per script chunk, in narration order. Combined… (+5 more)

### Community 51 - "2. Project Details Tabs"
Cohesion: 0.15
Nodes (12): 1. Overview, 1. Projects Dashboard, 2. Chats, 2. Project Details Tabs, 3. Knowledge, 4. Runs, 5. Artifacts, 6. Memory (+4 more)

### Community 52 - "2. Database Models (SQLite)"
Cohesion: 0.15
Nodes (12): 1. Tenant & Resource Hierarchy, 2. Database Models (SQLite), 3. Permissions & Role Mapping (RBAC), 4. Run Summarization & Memory Generation, `artifacts`, `audit_events`, Constraints & Security Boundaries:, `project_contexts` (+4 more)

### Community 56 - "Documentação Funcional — Lattice Cockpit"
Cohesion: 0.17
Nodes (11): 10. Tema, 11. Dados, 1. Estrutura geral, 2. Visão geral (Home), 4. Gerador de Vídeo, 5. YouTube Shorts, 6. Canais, 7. Notificações (+3 more)

### Community 59 - "💻 Como Executar"
Cohesion: 0.29
Nodes (6): 💻 Como Executar, 🚀 Moneyprint — Cockpit Frontend Layout, Opção 1: Via Dois Cliques (Windows), Opção 2: Via Node.js / NPM, Opção 3: Abrir Diretamente no Navegador, 📱 Telas & Módulos Disponíveis

### Community 84 - "no_cache_for_static_app"
Cohesion: 0.67
Nodes (3): no_cache_for_static_app(), Without this, Chrome heuristically caches the HTML document itself (no Cache-…, middleware

## Knowledge Gaps
- **145 isolated node(s):** `MODELS`, `ACCENTS`, `INITIAL_APP_SETTINGS`, `INITIAL_ACCOUNT`, `DEMO_BLOCKS` (+140 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **39 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `GatewayContractTests` connect `GatewayContractTests` to `._completed_job`, `patch`, `._channel`, `._async_return`, `._ai_images_job`?**
  _High betweenness centrality (0.041) - this node is a cross-community bridge._
- **Why does `database()` connect `database` to `main.py`, `create_job`, `Any`, `row_as_job`, `refresh_channel`, `get`, `review_job`?**
  _High betweenness centrality (0.002) - this node is a cross-community bridge._
- **Why does `no_cache_for_static_app()` connect `no_cache_for_static_app` to `get`, `main.py`?**
  _High betweenness centrality (0.001) - this node is a cross-community bridge._
- **What connects `MODELS`, `ACCENTS`, `INITIAL_APP_SETTINGS` to the rest of the system?**
  _145 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `support.js` be split into smaller, more focused modules?**
  _Cohesion score 0.06698564593301436 - nodes in this community are weakly interconnected._
- **Should `main.py` be split into smaller, more focused modules?**
  _Cohesion score 0.1036036036036036 - nodes in this community are weakly interconnected._
- **Should `cockpit-app.js` be split into smaller, more focused modules?**
  _Cohesion score 0.11219512195121951 - nodes in this community are weakly interconnected._