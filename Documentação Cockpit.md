# Documentação Funcional — Lattice Cockpit

Guia de uso do protótipo `Cockpit.html`, um dashboard administrativo para um gerador de vídeos por IA (roteiro, voz, filmagens stock e legendas) com publicação em canais de YouTube e TikTok.

---

## 1. Estrutura geral

- **Sidebar** (esquerda): navegação principal, organizada em seções (Plataforma, Pessoas, Studio, Observabilidade, Conta).
- **Topbar**: status do ambiente, modelo de IA ativo, tema claro/escuro e notificações.
- **Área de conteúdo**: cabeçalho da view (título + subtítulo) e os widgets da página.

Um **drawer** lateral (inspeção rápida) e **toasts** de confirmação sobrepõem essas áreas quando acionados.

---

## 2. Visão geral (Home)

Mostra os **vídeos recentes** gerados, com:

- Filtro por formato: Todos / Verticais / Horizontais.
- Alternância de exibição em **grade** (miniaturas) ou **lista** (tabela com canal, plataforma e formato) — cada vídeo mostra a qual canal foi gerado.
- **Filtro avançado**: busca por título, e filtros por canal e por plataforma.
- Seleção múltipla (checkbox por vídeo) com barra de ações para **excluir selecionados**.
- Menu "⋯" em cada vídeo com **Baixar MP4**, **Publicar no YouTube**, **Copiar Caminho** e **Excluir vídeo**.

---

## 4. Gerador de Vídeo

Formulário de criação (card "Criador de vídeo HD"):

- **Tema / tópico do vídeo** — texto livre ou um dos chips de sugestão (Psicologia, Profundezas, Hábitos Ricos, Espaço, História Oculta); clicar num chip preenche o campo e marca o chip selecionado.
- **Selecione o canal** — associa o vídeo gerado a um canal cadastrado em Canais (aparece depois em Vídeos recentes).
- **Roteiro customizado** (opcional).
- **Idioma da narração** e **voz sintética**.
- **Formato / proporção de tela** — opções clicáveis (9:16 Vertical, 16:9 Paisagem, 1:1 Quadrado), com destaque visual na opção selecionada.
- Quantidade de frases/cenas, duração do clipe, estilo de legenda (tamanho, cor do texto, cor da borda) e trilha sonora de fundo.

Ao clicar em **Gerar vídeo HD completo**:

- O card **Pipeline em tempo real** mostra o título do vídeo sendo gerado, uma barra de progresso e os 5 estágios (Roteiro IA, Keywords, Voz e Legendas, Filmagens Stock, Render HD) preenchendo em sequência.
- O card **Player de pré-visualização** muda para "Gerando…" durante o processo e para "Pronto" ao final.
- O vídeo concluído é adicionado a **Vídeos recentes** na Visão geral.
- Abaixo do player: **Baixar MP4**, **Publicar no YouTube** e **Copiar Caminho**.

---

## 5. YouTube Shorts

Página dedicada ao fluxo de shorts automatizados:

- **Gerador de YouTube Shorts** — conta (gerenciadas em Canais), motor de renderização, voz sintética, tema/tópico, roteiro específico, nicho e upload automático no YouTube Studio.
- **Player de pré-visualização**, ao lado do formulário, em formato **9:16** — com os mesmos botões Baixar MP4 / Publicar no YouTube / Copiar Caminho do Gerador de Vídeo.
- **Publicar vídeo existente da biblioteca no YouTube** — escolhe um vídeo já gerado, canal de destino, título e descrição/tags para upload.

---

## 6. Canais

Lista de contas conectadas (YouTube, YouTube Shorts, TikTok) com seguidores, vídeos publicados, views totais, última publicação e status de conexão. No cabeçalho, dois indicadores mostram a contagem de canais por rede (ícone + cor de cada plataforma).

Clicar num canal abre o **perfil do canal**, com abas:

- **Dados da conta** — nome, plataforma, handle, formato padrão, nicho, idioma e caminho do perfil Firefox; ao lado, métricas (seguidores, vídeos publicados, views totais) com gráficos de evolução, no mesmo estilo da página Analytics.
- **Conexão/API** — status do token, escopos concedidos, ações de reconectar/desconectar, e preferências de publicação automática (upload e legendas automáticas).
- **Vídeos publicados** — tabela dos vídeos daquele canal.
- **Auditoria** — histórico de ações na conta.

O botão **+ Conectar canal** abre o formulário "Adicionar Conta", com apelido/nome do canal, nicho, idioma e caminho do perfil Firefox — a mesma estrutura de dados aparece depois no perfil do canal já conectado.

---

## 7. Notificações

Lista de alertas (renderização concluída, provedor de IA com poucos créditos, falhas, novidades de modelo) com severidade e contagem de não lidas na sidebar. Clicar numa notificação ou em "marcar todas como lidas" atualiza o estado.

---

## 8. Configurações

Quatro abas: **Modelos e IA**, **Vídeo e Stock**, **Navegador e Sistema**, **Voz e Legendas** — chaves de API, modelos padrão/fallback, resolução e duração padrão de clipe, e preferências de narração.

---

## 9. Analytics e Logs

- **Analytics** (antiga "Custos de IA"): vídeos gerados, views totais, seguidores totais, gasto com IA e receita total; gráficos de vídeos gerados e views no tempo; desempenho por plataforma (YouTube vs. TikTok); e ranking de canais por seguidores, views e receita (clicável, abre o canal).
- **Logs e traces**: console de eventos com busca e filtro por nível/serviço; clicar numa linha abre o drawer com contexto completo.

---

## 10. Tema

Botão de sol/lua no topbar alterna entre claro e escuro (persistido em `localStorage`).

---

## 11. Dados

Todos os dados (canais, vídeos, notificações, logs, analytics) são fixos, definidos em `shared/cockpit-data.js`, e não representam dados reais — servem para demonstrar o comportamento do protótipo.
