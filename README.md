# 🚀 Moneyprint — Cockpit Frontend Layout

Protótipo completo e interativo do painel administrativo do **Moneyprint / Lattice Cockpit** para criação e automação de vídeos com IA (YouTube Shorts, TikTok, geração de roteiros, voz e métricas).

📖 **[Consulte a Documentação Técnica Completa (DOCUMENTACAO.md)](./DOCUMENTACAO.md)**

---

## 💻 Como Executar

### Opção 1: Via Dois Cliques (Windows)
Basta clicar duas vezes no arquivo **`iniciar.bat`**. O servidor será iniciado e abrirá automaticamente no seu navegador padrão.

### Opção 2: Via Node.js / NPM
No terminal, dentro desta pasta:
```bash
npm start
# ou
node server.js
```
Acesse no navegador: **`http://localhost:3000`**

### Opção 3: Abrir Diretamente no Navegador
Você também pode abrir diretamente o arquivo **`index.html`** ou **`Cockpit.html`** no seu navegador Google Chrome, Edge, Firefox, etc.

---

## 📱 Telas & Módulos Disponíveis

1. **Visão Geral** (`/index.html` ou `/Cockpit.html`): Lista de vídeos recentes, filtros por canal/formato, download MP4, publicação e status.
2. **Canais** (`/Cockpit.html#clientes`): Gerenciamento de contas conectadas (YouTube, TikTok), métricas de seguidores, vídeos publicados e views.
3. **Gerador de Vídeo** (`/Cockpit.html#gerador`): Formulário completo com seleção de nicho, voz sintética, proporções (9:16, 16:9, 1:1), pipeline em tempo real e player de pré-visualização.
4. **YouTube Shorts** (`/Cockpit.html#youtube`): Fluxo dedicado para Shorts com motor Turbo HD e agendamento automático.
5. **Analytics** (`/Cockpit.html#custos`): Gráficos de desempenho, views no tempo, custos de IA e faturamento.
6. **Lattice Desktop App** (`/lattice-app/Lattice.html`): Interface rica baseada em componentes React.
7. **Lattice Admin Pages**:
   - `Activity.html`
   - `Users.html`
   - `Subscribers.html`
   - `Invoices.html`
   - `Plans.html`
   - `Settings.html`
   - `Login.html` / `Signup.html`
