# 📚 Documentação Técnica e Funcional — Moneyprint / Lattice Cockpit

Documentação completa da interface frontend do **Moneyprint / Lattice Cockpit**, abrangendo arquitetura, guia de execução, catálogo de telas, design system, gerenciamento de dados e instruções para integração com backend real.

---

## 📑 Sumário

1. [Visão Geral](#1-visão-geral)
2. [Estrutura do Projeto](#2-estrutura-do-projeto)
3. [Instalação e Execução Local](#3-instalação-e-execução-local)
4. [Mapeamento Completo de Módulos e Telas](#4-mapeamento-completo-de-módulos-e-telas)
   - [4.1 Moneyprint Cockpit (Aplicação Principal)](#41-moneyprint-cockpit-aplicação-principal)
   - [4.2 Lattice Desktop App (React)](#42-lattice-desktop-app-react)
   - [4.3 Lattice Admin Suite](#43-lattice-admin-suite)
5. [Design System & Estilização](#5-design-system--estilização)
   - [5.1 Tokens de Cores (Claro & Escuro)](#51-tokens-de-cores-claro--escuro)
   - [5.2 Tipografia](#52-tipografia)
   - [5.3 Componentes de UI](#53-componentes-de-ui)
6. [Arquitetura de Dados e Estado Frontend](#6-arquitetura-de-dados-e-estado-frontend)
7. [Guia de Integração com Backend Real](#7-guia-de-integração-com-backend-real)
8. [Guia de Deploy para Produção](#8-guia-de-deploy-para-produção)
9. [Solução de Problemas (FAQ)](#9-solução-de-problemas-faq)

---

## 1. Visão Geral

O **Moneyprint / Lattice Cockpit** é uma suíte administrativa e estúdio de criação para geração automatizada de vídeos curtos (Shorts, Reels, TikTok) e horizontais através de Inteligência Artificial.

A plataforma integra em uma interface moderna e minimalista:
- **Pipeline de Vídeo com IA**: Geração de roteiro, narração com voz sintética, busca de filmagens stock, legendagem automática e renderização HD.
- **Gestão de Canais & Automação**: Conexão e monitoramento de contas no YouTube Shorts e TikTok.
- **Analytics & Custos**: Acompanhamento de visualizações, inscritos, receita e custos de tokens de IA.
- **Lattice Admin**: Painel SaaS com métricas de usuários, assinantes, planos e faturamento.

---

## 2. Estrutura do Projeto

```text
Moneyprint layout/
│
├── index.html                  # Ponto de entrada padrão do servidor (Cockpit)
├── Cockpit.html                # Aplicação SPA principal do Moneyprint Cockpit
├── server.js                   # Servidor HTTP nativo em Node.js (Zero dependências)
├── package.json                # Manifesto com scripts npm start e dev
├── iniciar.bat                 # Script de inicialização rápida para Windows (1 clique)
├── README.md                   # Instruções rápidas de uso
├── DOCUMENTACAO.md             # Esta documentação completa
├── Documentação Cockpit.md    # Especificação funcional original
│
├── shared/                     # Recursos compartilhados do Cockpit e Admin
│   ├── shell.css               # Design system base, grid, sidebar, topbar e reset
│   ├── cockpit.css             # Estilos específicos dos componentes do Cockpit
│   ├── shell.js                # Renderizador da sidebar/topbar das páginas Admin
│   ├── cockpit-app.js          # Roteamento SPA, inicialização, drawer e eventos globais
│   ├── cockpit-views.js        # Templates de renderização de todas as telas do Cockpit
│   ├── cockpit-data.js         # Estado inicial, mocks de canais, vídeos e métricas
│   ├── image-slot.js           # Gerenciamento de slots e placeholders de imagens
│   └── users.js                # Dados de mock para a tabela de usuários
│
├── lattice-app/                # Aplicação desktop reativa (React + Babel UMD)
│   ├── Lattice.html            # Ponto de entrada da aplicação Lattice Desktop
│   ├── app.jsx                 # Componente raiz e orquestrador de estado
│   ├── artifact-panel.jsx      # Painel de inspeção de artefatos e código
│   ├── composer.jsx            # Barra de prompt e comandos de IA
│   ├── connectors.jsx          # Conectores de APIs e serviços externos
│   ├── extras.jsx              # Utilitários visuais e microinterações
│   ├── i18n.jsx                # Sistema de internacionalização (PT/EN)
│   ├── modals.jsx              # Caixas de diálogo modais
│   ├── primitives.jsx          # Componentes atômicos (Button, Input, Badge, etc.)
│   ├── skills.jsx              # Habilidades e agentes de automação
│   ├── tweaks-panel.jsx        # Painel de ajustes finos de parâmetros de IA
│   └── views.jsx               # Telas do editor de agentes e automações
│
├── screens/ & screenshots/     # Imagens e referências visuais
└── scraps/                     # Rascunhos e protótipos visuais
```

---

## 3. Instalação e Execução Local

### Pré-requisitos
- **Navegador Moderno**: Google Chrome, Microsoft Edge, Brave, Firefox ou Safari.
- **Node.js** (opcional, recomendado): versão 18+ instalada.

### Método 1: Windows (Atalho com 1 Clique)
1. Dê um duplo clique no arquivo `iniciar.bat`.
2. O prompt de comando iniciará o servidor e seu navegador padrão abrirá automaticamente em `http://localhost:3000`.

### Método 2: Via Terminal (Node / NPM)
```bash
# Iniciar o servidor HTTP nativo
npm start

# Ou diretamente:
node server.js
```
Acesse no seu navegador: **`http://localhost:3000`**

### Método 3: Abertura Direta (Sem Servidor)
Como a aplicação utiliza arquivos HTML, CSS e JavaScript estáticos, você pode clicar duas vezes em qualquer arquivo `.html` (como `index.html` ou `Cockpit.html`) diretamente no Windows Explorer para abrir no navegador.

---

## 4. Mapeamento Completo de Módulos e Telas

### 4.1 Moneyprint Cockpit (Aplicação Principal)
Acesso: `http://localhost:3000/` ou `http://localhost:3000/Cockpit.html`

O Cockpit funciona como uma SPA (*Single Page Application*) controlada por hash navigation:

| Rota / Hash | Nome da Tela | Descrição |
| :--- | :--- | :--- |
| `#/overview` | **Visão Geral** | Dashboard inicial com grade de vídeos gerados, filtros por canal, formato (vertical/horizontal), busca textual, seleção múltipla e ações de download/publicação. |
| `#/gerador` | **Gerador de Vídeo** | Estúdio de criação HD: tópico do vídeo, seleção de canal, roteiro customizado, vozes sintéticas, proporção (9:16, 16:9, 1:1), monitor em tempo real do pipeline e preview player. |
| `#/youtube` | **YouTube Shorts** | Interface especializada para Shorts automatizados: motor de renderização Turbo, agendamento de postagem no YouTube Studio e publicação rápida de vídeos da biblioteca. |
| `#/clientes` | **Canais** | Gestão de contas conectadas (YouTube Shorts, YouTube Horizontal e TikTok), total de seguidores, views acumuladas, histórico de publicações e auditoria. |
| `#/custos` | **Analytics** | Indicadores de performance, gráficos de evolução de views, comparativo YouTube vs. TikTok e ranking de receita dos canais. |
| `#/notificacoes` | **Notificações** | Central de alertas sobre status de renderização, saldo de créditos em provedores de IA e avisos de sistema. |
| `#/logs` | **Logs e Traces** | Console de observabilidade com busca e filtragem por severidade (INFO, WARN, ERROR) e inspeção via drawer lateral. |
| `#/config` | **Configurações** | Abas para configuração de chaves de API, modelos de IA (OpenAI, Anthropic, Gemini), provedores de voz e padrões de vídeo. |

---

### 4.2 Lattice Desktop App (React)
Acesso: `http://localhost:3000/lattice-app/Lattice.html`

Aplicação desktop completa desenvolvida em React 18 (executado via Babel Standalone UMD, sem necessidade de build complexo):
- **Composer**: Interface de comandos e prompt com sugestões inteligentes.
- **Artifact Panel**: Visualizador interativo de artefatos gerados (código, previews e dados).
- **Tweaks Panel**: Controle em tempo real de hiperparâmetros de geração (temperatura, estilo, resolução).
- **Connectors**: Gerenciador de integrações e APIs externas.

---

### 4.3 Lattice Admin Suite
Conjunto de telas administrativas tradicionais para gestão de plataforma SaaS:

- **Activity** (`Activity.html`): Gráficos de atividade, tendências e registros em tempo real.
- **Users** (`Users.html`): Tabela interativa com busca, filtros de status (Ativo, Pendente, Suspenso), edição e atribuição de funções (Admin, Editor, Viewer).
- **Subscribers** (`Subscribers.html`): Gestão de assinantes de planos pagos e renovações.
- **Plans & Invoices** (`Plans.html`, `Invoices.html`): Catálogo de precificação e faturas emitidas com comprovantes.
- **Autenticação** (`Login.html`, `Signup.html`): Telas de login e cadastro prontas para integração com provedores OAuth ou JWT.

---

## 5. Design System & Estilização

A interface adota uma estética *editorial premium* baseada em design minimalista contemporâneo (combinação de bordas sutis, tipografia precisa, contrastes limpos e suporte nativo a temas Claro e Escuro).

### 5.1 Tokens de Cores (CSS Custom Properties)

As variáveis estão centralizadas em `shared/shell.css` e `shared/cockpit.css`:

```css
:root {
  /* Cores de Acento */
  --accent: #2563eb;          /* Azul primário */
  --accent-soft: rgba(37, 99, 235, 0.12);
  --accent-ink: #1d4ed8;

  /* Modo Claro */
  --bg: #ffffff;
  --surface: #ffffff;
  --rail: #ffffff;
  --rail-2: #f3f4f6;
  --border: #e5e7eb;
  --border-strong: #d1d5db;
  --ink: #111827;            /* Texto principal */
  --ink-2: #1f2937;          /* Texto secundário */
  --muted: #6b7280;          /* Texto atenuado */

  /* Estados */
  --ok: #16a34a;             /* Sucesso / Online */
  --warn: #d97706;           /* Alerta */
  --err: #ef4444;            /* Erro */
}

/* Modo Escuro (Ativado via data-theme="dark") */
html[data-theme="dark"] {
  --bg: #09090b;
  --surface: #18181b;
  --rail: #09090b;
  --rail-2: #1f1f23;
  --border: #27272a;
  --border-strong: #3f3f46;
  --ink: #ffffff;
  --ink-2: #f4f4f5;
  --muted: #a1a1aa;
}
```

### 5.2 Tipografia

Três famílias tipográficas selecionadas para diferentes funções:
- **Inter** (`var(--font-sans)`): Utilizada em títulos, rótulos, botões e corpo do texto.
- **JetBrains Mono** (`var(--font-mono)`): Utilizada em métricas numéricas, IDs, status de pipelines e logs.
- **Instrument Serif** (`var(--font-serif)`): Utilizada em destaques e branding editorial.

---

## 6. Arquitetura de Dados e Estado Frontend

Os dados da aplicação são gerenciados através de objetos reativos no arquivo `shared/cockpit-data.js`:

```javascript
// Exemplo de canais configurados
window.CK = {
  canais: [
    { id: "c1", nome: "Curiosidades Rápidas", rede: "yt_shorts", inscritos: 142000, ... },
    { id: "c2", nome: "Fatos Históricos", rede: "yt_shorts", inscritos: 89000, ... },
    ...
  ],
  videos: [ ... ],
  notificacoes: [ ... ],
  logs: [ ... ]
};
```

### Persistência Local
- O tema (Claro / Escuro) é persistido no `localStorage` sob a chave `lattice-cockpit-theme`.
- Vídeos recém-gerados através do simulador de pipeline são inseridos na lista de vídeos em memória e passam a aparecer imediatamente na **Visão Geral**.

---

## 7. Guia de Integração com Backend Real

Para transformar o protótipo frontend em uma plataforma de produção conectada a um backend (por exemplo, Python FastAPI / Node.js Express / NestJS), siga estes passos:

### 1. Endpoint de Geração de Vídeos
No arquivo `shared/cockpit-views.js` (função `gerador`), substitua a chamada do timer simulado pela chamada à sua API:

```javascript
async function handleGenerateVideo(payload) {
  // Exemplo de envio para o backend Python/MoneyPrinter
  const response = await fetch('/api/v1/videos/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      topic: payload.topic,
      channel_id: payload.channelId,
      voice: payload.voice,
      aspect_ratio: payload.aspectRatio,
      subtitle_style: payload.subtitleStyle
    })
  });
  const data = await response.json();
  return data.job_id;
}
```

### 2. Acompanhamento de Pipeline via WebSocket / SSE
Substitua os intervalos de simulação de etapas por um `EventSource` ou conexão WebSocket para refletir o status real do FFmpeg e Whisper:

```javascript
const sse = new EventSource(`/api/v1/jobs/${jobId}/events`);
sse.onmessage = (event) => {
  const step = JSON.parse(event.data);
  // Atualiza a barra de progresso e as etapas na interface
  updatePipelineUI(step.stage, step.progress);
};
```

---

## 8. Guia de Deploy para Produção

### Opção A: Vercel (Recomendado)
1. Instale a CLI da Vercel ou importe o repositório pelo painel web da Vercel.
2. Como o projeto é estático com `index.html` na raiz, nenhuma configuração de build é necessária. O deploy será concluído em segundos.

### Opção B: Netlify
1. Conecte o repositório no Netlify.
2. Defina o **Publish directory** como `.` (raiz).
3. Conclua o deploy.

### Opção C: Docker / Nginx
Crie um arquivo `Dockerfile` simples na raiz:
```dockerfile
FROM nginx:alpine
COPY . /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
```
Construa e execute:
```bash
docker build -t moneyprint-frontend .
docker run -d -p 80:80 moneyprint-frontend
```

---

## 9. Solução de Problemas (FAQ)

#### A página abre em branco ou sem estilos ao abrir o arquivo diretamente?
Verifique se a pasta `shared/` está no mesmo diretório do arquivo `.html` que você abriu. Para evitar restrições de segurança de CORS em arquivos locais (`file://`), use sempre o servidor embutido executando `iniciar.bat` ou `npm start`.

#### Como mudar a porta padrão do servidor?
Defina a variável de ambiente `PORT` antes de iniciar:
```bash
# Windows PowerShell
$env:PORT="8080"; npm start

# Linux / Mac
PORT=8080 npm start
```
Caso a porta 3000 já esteja sendo utilizada por outro programa, o `server.js` detectará automaticamente e subirá na próxima porta livre (3001, 3002, etc.).

---

*Documentação mantida para o projeto Moneyprint Layout / Lattice Cockpit.*
