# Lattice — Documentação Consolidada

Este documento unifica toda a arquitetura, estrutura de arquivos, guia de componentes, referência de funções e regras de negócio do Lattice.

---

## 1. Visão Geral e Arquitetura

O **Lattice** é um workspace desktop de inteligência artificial com design minimalista, neutro e de alta densidade de informação.

### Pilares da Arquitetura
*   **Zero Build Step**: O projeto roda inteiramente no navegador sem etapas de empacotamento ou compilação prévia.
*   **React 18**: Carregado via UMD (desenvolvimento).
*   **Babel Standalone**: Utilizado para transpilar JSX inline em tempo de execução diretamente no navegador.
*   **CSS Puro**: Variáveis nativas de CSS e media queries controlam os temas, densidades e layouts responsivos.
*   **Escopo Global (`window`)**: Como cada tag `<script type="text/babel">` é executada em escopos isolados pelo Babel Standalone, componentes compartilhados e funções utilitárias são exportados explicitamente vinculando-os ao objeto global `window`.

### Ordem de Importação de Scripts no `index.html`
```
tweaks-panel.jsx     → Controles visuais de tema e densidade (host protocol)
primitives.jsx       → Tooltip, Modal, Action, InlineEditable, ConfirmContext, hooks
i18n.jsx             → Strings e hook useT para 4 idiomas
composer.jsx         → Composer (input box com voz, anexos e switches)
modals.jsx           → Modais de conta (MyAccount) e configurações (Settings)
extras.jsx           → Componentes utilitários de overlay (Menu, SearchPalette)
artifact-panel.jsx   → Painel lateral de visualização e extração de tabelas
views.jsx            → Grid de projetos, detalhe do projeto, histórico, arquivo e file viewer
connectors.jsx       → Central de conexões externas e mockup de fluxos OAuth
skills.jsx           → Habilitadores de capacidades do assistente
app.jsx              → Ponto de entrada do app, Sidebar, Thread de mensagens e TasksPanel
```

---

## 2. Estrutura de Arquivos do Projeto

| Arquivo/Pasta | Propósito |
|---|---|
| `index.html` | Shell HTML principal, importador do CSS global e carregador dos scripts React/JSX. |
| `src/app.jsx` | App raiz, Sidebar, ConvoRow, Breadcrumb, Message, TasksPanel, dados de semente e roteamento. |
| `src/composer.jsx` | Composer com voz, anexos de arquivos, Think/Deep Search e Token Counter. |
| `src/primitives.jsx` | Componentes atômicos: Tooltip, Action, Modal, InlineEditable, `cn` e hooks de clique/media. |
| `src/i18n.jsx` | Dicionário de strings (en, pt, es, fr) + hook `useT()`. |
| `src/extras.jsx` | Menus dropdown contextuais, SearchPalette (⌘K) e MsgActions do assistente. |
| `src/modals.jsx` | Corpos e visualizações unificadas de Minha Conta e Configurações globais. |
| `src/artifact-panel.jsx` | Visualizador de entregáveis (HTML iframe sandbox, Markdown rico, tabelas e Code highlighter) + função `parseArtifacts` para processar tags `<artifact>` durante o streaming. |
| `src/views.jsx` | Projetos, Detalhes de Projetos, Upload de Conhecimento (Knowledge) e Histórico de conversas. |
| `src/connectors.jsx` | Central de conexões externas e mockup de fluxos OAuth. |
| `src/skills.jsx` | Habilidades e capacidades ativáveis do assistente de IA. |
| `src/tweaks-panel.jsx` | Painel flutuante de customização (Tema Escuro, Accent Colors e Densidades). |

---

## 3. Guia de Elementos e Componentes (Glossário Técnico)

### Primitivos (`primitives.jsx`)
*   `cn(...args)`: Utilitário para concatenação condicional de classes CSS.
*   `Tooltip({ label, side, delay, children })`: Exibe balão descritivo ao focar/hover com temporizador dinâmico.
*   `Action({ icon, label, onClick })`: Botão de ícone com descrição acessível oculta (`sr-only`).
*   `Modal({ open, onClose, title, children, width })`: Caixa de diálogo centralizada com foco de acessibilidade e fechar com `Esc`.
*   `InlineEditable({ value, onChange, placeholder, type })`: Componente de texto que se transforma em input ao clicar, salvando no Blur/Enter ou descartando no Esc.
*   `ConfirmContext` & `useConfirm()`: Contexto global de confirmação destrutiva que substitui as chamadas ao `confirm()` nativo do navegador.

### Internacionalização (`i18n.jsx`)
*   `STRINGS`: Dicionário aninhado suportando `en` (fallback), `pt`, `es` e `fr`.
*   `LangContext`: Provider de idioma encapsulado na raiz do `<App>`.
*   `useT()`: Retorna o tradutor reativo `t(key)` resolvendo caminhos de strings hierárquicas.

### Composições de Chat (`app.jsx` / `composer.jsx` / `artifact-panel.jsx`)
*   `Message({ m, streaming, onCopy, onRegen, onOpenArtifact })`: Renderiza bolhas de usuário e distribui blocos do assistente (`text` -> `TextBlock`, `artifact` -> `ArtifactCard`, `tool` -> `ToolCard`).
*   `TextBlock({ text, streaming })`: Processa Markdown estruturado através de `window.renderMarkdown(text, streaming)` injetando o cursor piscante (`.caret`) no final do fluxo ativo.
*   `parseArtifacts(text)`: Função utilitária global que varre a resposta da IA (incluindo streams parciais) extraindo tags `<artifact>` e gerando blocos de chat dinamicamente, mantendo o cache global `window.ARTIFACTS_SAMPLE` sincronizado.
*   `ToolCard({ block })`: Exibe chamadas de ferramentas de forma colapsável com visualização de saídas em formato diff/monospace.
*   `ArtifactPanel({ artifact, onClose })`: Painel lateral que exibe os entregáveis substantivos em abas dinâmicas (*Preview*, *Table* com chips e numeração de linhas, *Code/Raw*).
*   `TasksPanel`: Painel de tarefas assíncronas com transições dinâmicas de estados e controle de cancelamento ou reinicialização.

---

## 4. Regras de Negócio (Business Rules)

### 01. Conversas (RN-CV)
*   **RN-CV-01**: Novas conversas são geradas via "New chat" ou atalho `⌘N`. Iniciam sem fixação ou arquivamento e com data de atualização corrente.
*   **RN-CV-02**: Apenas uma conversa ativa por vez (`activeId`). Em telas móveis, selecionar uma linha fecha a barra lateral automaticamente.
*   **RN-CV-03**: Títulos de conversas são editáveis inline (duplo-clique ou atalho `F2`), salvando no blur/Enter e descartando com Esc.
*   **RN-CV-04**: Conversas fixadas vão para o grupo *Pinned* e perdem o status de arquivadas (são estados mutuamente exclusivos).
*   **RN-CV-05**: A triagem das conversas na sidebar é 100% automática (*Pinned*, *Today*, *Earlier*, *Archived*).
*   **RN-CV-06**: Exclusões de conversas passam obrigatoriamente pelo modal centralizado de confirmação. Se a conversa ativa for deletada, o sistema seleciona a próxima da lista.
*   **RN-CV-07**: Menu de contexto (Rename, Pin, Archive, Delete) é acessível por clique direito ou botão `⋯`.

### 02. Saída do Chat (RN-MS)
*   **RN-MS-01 a RN-MS-05**: Turnos do assistente são arrays ordenados de blocos tipados, processados por `Message`. Blocos inválidos ou desconhecidos não causam falhas catastróficas na aplicação (fallback para ToolCard).
*   **RN-MS-06**: A Thread de chat e a aba Markdown do Artifact Panel compartilham o mesmo renderizador de Markdown rico (`window.renderMarkdown`), garantindo a correta exibição de cabeçalhos, listas aninhadas, citações e tabelas.
*   **RN-MS-07**: O cursor (`.caret`) é exibido apenas no final do último bloco de texto da conversa enquanto `streaming === true`.
*   **RN-MS-08 e RN-MS-09**: Detalhes de execução de ferramentas montam apenas quando o card está expandido. Ações de mensagem (Copy, Like, Dislike) surgem no hover apenas ao final da geração.
*   **RN-MS-10**: Copiar mensagem serializa todo o histórico de blocos do turno (incluindo prosa e saída estruturada de ferramentas).

### 03. Composer (RN-CP)
*   **RN-CP-01 e RN-CP-02**: Envio é bloqueado se não houver texto ou anexos no input. `Enter` envia e `Shift+Enter` salta linha.
*   **RN-CP-03**: Auto-ajuste de altura (44px até 220px) antes de habilitar scrollbar vertical.
*   **RN-CP-04**: Entrada de voz degrada graciosamente caso a API `webkitSpeechRecognition` esteja ausente no browser.
*   **RN-CP-05**: Anexos liberam automaticamente suas Blob URLs (`URL.revokeObjectURL`) ao serem removidos ou no unmount do componente.
*   **RN-CP-06**: Toggles *Think* e *Deep Search* operam de forma independente.
*   **RN-CP-07**: Contador de tokens reflete a estimativa do tamanho do texto atualmente presente no rascunho de entrada.

### 04. Background Tasks (RN-TK)
*   **RN-TK-01 a RN-TK-03**: Tarefas têm status (*running*, *queued*, *done*, *error*). Botões inline (Stop, Cancel, Retry) utilizam `stopPropagation` para não abrir acidentalmente a expansão de metadados do card.
*   **RN-TK-04 e RN-TK-05**: Largura do painel persiste no `localStorage` (`lattice.tasks.w`). Abrir um artifact substitui temporariamente o painel de tarefas.

### 05. Artifacts (RN-AR)
*   **RN-AR-01 e RN-AR-02**: Abas disponíveis são geradas dinamicamente seguindo a precedência: *Table* (se CSV/dados tabulares presentes), *Preview* (se HTML/Markdown) e *Code/Raw*.
*   **RN-AR-03 e RN-AR-04**: HTML executa em iframe sandboxed. A aba de tabelas lê HTML, Markdown e CSV, alinhando colunas de valores numéricos à direita e oferecendo ferramentas de cópia e exportação.
*   **RN-AR-05 e RN-AR-06**: Fechamento do painel por botão ou tecla `Esc`. Largura de 380px a 1100px com reset para 580px em duplo-clique, persistido sob a chave `lattice.art.w`.

### 06 a 09. Projetos, Conhecimento, Histórico e Visualizador
*   **RN-PJ**: Criação exige nome. Permite edição inline em tempo real. System prompts salvam automaticamente e se aplicam a todas as conversas do projeto. Deletar projeto remove todas as conversas e metadados dele em cascata.
*   **RN-KB / RN-FV**: Upload por File Picker ou Drag and Drop com limite de 25MB por arquivo. Transição de status reativa (*indexing* -> *indexed*). Exclusão exige confirmação explícita expondo o nome do arquivo. Painel de arquivo permite download real dos metadados e re-sincronização.
*   **RN-HS**: Busca e filtros (projeto, data, com preview) agem por interseção cumulativa. Suporta multi-seleção de linhas para arquivamento ou exclusão em lote.

### 10. Conectores e Configurações (RN-CN / RN-CF / RN-CO)
*   **RN-CN**: Grade de 14 conectores integrados. Conexões efetuadas ou desconectadas registram eventos em tempo de execução na lista histórica de auditoria (`AUDIT_LOG`).
*   **RN-CF / RN-AP**: Modificações de preferências entram em vigor no estado global `appSettings` instantaneamente. Customização de Dark Mode, Accent Color e Spacing sincroniza de forma bidirecional e instantânea entre as Configurações e o painel flutuante de Tweaks.
*   **RN-CO**: **Nenhuma** ação destrutiva de exclusão (chats, projetos, arquivos de conhecimento ou bulk delete) utiliza diálogos nativos do navegador (`confirm()`). Todas são centralizadas em um único modal React unificado com botão vermelho em caso de risco (`danger: true`).

---

## 5. Atalhos Globais de Teclado (RN-AT)

Os seguintes atalhos agem globalmente na aplicação, exceto quando o cursor está posicionado em campos de entrada de texto e a tecla pressionada possui significado de edição:

| Atalho | Ação Executada |
|---|---|
| `⌘K` / `Ctrl+K` | Abre a paleta de buscas e comandos globais |
| `⌘N` / `Ctrl+N` | Inicia uma nova conversa na raiz |
| `⌘,` / `Ctrl+,` | Abre o painel de Configurações |
| `⌘B` / `Ctrl+B` | Expande ou recolhe a Sidebar |
| `⌘J` / `Ctrl+J` | Expande ou recolhe o painel lateral de Tarefas |
| `↵` (Enter) | Envia a mensagem no Composer (`⇧↵` para salto de linha) |
| `Esc` | Fecha modais, dropdowns, paleta de buscas ou visualizador de artifacts |
| `F2` | Inicia a renomeação da conversa ativa na barra lateral |

---

## 6. Comportamento Responsivo (RN-RA)

*   **Breakpoints de Telas Grandes (>1100px)**: Grid de layout completo exibindo simultaneamente Sidebar, Thread central e Painel Lateral (Tasks/Artifacts).
*   **Breakpoints de Telas Médias (≤1100px)**: O painel de tarefas/artifacts é reduzido automaticamente para a largura de 320px para preservar o tamanho de leitura do chat.
*   **Drawers Flutuantes (≤900px)**: Sidebar e painel de tarefas se convertem em drawers flutuantes (*slide-over drawers*) com backdrop escurecido e efeito de desfoque. O topo da tela ganha um botão hamburger de menu para controle.
*   **Telas de Dispositivos Móveis (≤760px)**: Na visualização de projetos, a exibição de prévia de mensagens é ocultada e as abas ganham rolagem horizontal fluida.
*   **Empilhamento de Layout (≤560px)**: Campos e colunas em painéis de configuração e itens de fontes de conhecimento empilham de forma vertical.
*   **Simplificações Telefônicas (≤540px)**: Pílulas indicativas de conexão e detalhes do Breadcrumb são ocultados para economizar espaço de tela. Elementos no composer são reduzidos a ícones atômicos.
