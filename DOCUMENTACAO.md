# MoneyPrinter Cockpit

Documentação da aplicação local que gera, revisa e publica vídeos pelo MoneyPrinterTurbo. Versão 1.2.0.

Esta é a documentação do produto em execução. O modo `npm run static` abre só o protótipo visual: não autentica, não chama a API e não gera vídeo.

## O que a aplicação faz

O cockpit é uma interface local para o trabalho diário de canais de YouTube e TikTok:

- criar Short ou vídeo longo a partir do perfil de um canal;
- acompanhar trabalhos, revisão, biblioteca e publicação;
- conectar canais e autorizar, por canal do YouTube, uma grade de publicação automática;
- consultar crescimento (metas YPP, oportunidade e planejamento) e resultados dos canais.

Há um administrador local. Não há cadastro público, organizações, planos, faturas nem painel de assinantes.

## Como subir

Pré-requisitos: Docker Desktop em execução e PowerShell.

```powershell
.\setup.ps1
docker compose up -d --build --wait
```

`setup.ps1` cria `runtime/secrets/admin.json`, `runtime/secrets/mpt_api_key.txt` e `runtime/moneyprinter/config.toml`. Chaves de Moonshot/Kimi, Pexels e Upload-Post podem ficar em branco e ser preenchidas depois. Os valores de exemplo estão em `.env.example`. Não coloque segredos no navegador nem no repositório.

Abra [http://127.0.0.1:3000/Login.html](http://127.0.0.1:3000/Login.html).

| Comando | Efeito |
| --- | --- |
| `npm start` | `docker compose up -d --wait` |
| `npm run static` | `node server.js`, apenas o protótipo |
| `docker compose ps` | estado dos serviços |
| `docker compose logs cockpit-operator --tail 100` | log do agendador |

Readiness: `GET /api/health/ready`. Liveness: `GET /api/health/live`. A versão sai em `GET /api/version` e no campo `version` da readiness.

## Serviços

| Serviço | Função |
| --- | --- |
| `cockpit-api` | API e interface em `127.0.0.1:3000` |
| `cockpit-operator` | Publicação automática na grade configurada |
| `cockpit-generation-worker` | Processamento da fila de geração |
| `moneyprinterturbo` | Roteiro, voz, materiais e render |
| `redis` | Fila do Turbo e stream de eventos do Growth OS |
| `postgresql` | Domínio do Growth OS em `127.0.0.1:5433` |
| `temporal` | Workflows duráveis em `127.0.0.1:7233` |
| `growth-worker` | Planejamento |
| `growth-outbox` | Publicação at-least-once dos eventos no Redis |

O MinIO está descrito nas variáveis de object storage, mas o serviço está comentado no `compose.yaml` e não sobe com a composição atual.

PostgreSQL e Temporal só escutam em `127.0.0.1`. As senhas padrão de desenvolvimento precisam ser trocadas antes de qualquer uso fora da máquina local.

## Acesso

O administrador é o e-mail gravado em `runtime/secrets/admin.json`. A senha original não pode ser lida de volta: o arquivo guarda salt, iterações e hash PBKDF2-SHA256 (600.000 iterações). Para trocar a senha, rode `.\setup.ps1 -AdminOnly` ou substitua o hash e recrie o `cockpit-api`.

Com `COCKPIT_DISABLE_LOGIN=0`, o login em `POST /api/auth/login` cria o cookie `cockpit_session`. Rotas administrativas exigem essa sessão e o token CSRF. `POST /api/auth/logout` encerra a sessão. `GET /api/auth/session` devolve o administrador atual.

Com `COCKPIT_DISABLE_LOGIN=1`, a API trata toda requisição como sessão local e o login responde 404.

Tentativas repetidas de login são limitadas por 15 minutos.

## Estrutura do código

- `gateway/main.py` — serviços compartilhados (banco, sessões, cliente do Turbo, helpers de job, agentes) e montagem da aplicação FastAPI. Os workers (`cockpit-operator`, `cockpit-generation-worker`) e os testes usam esse módulo diretamente.
- `gateway/routers/` — rotas HTTP por área: `system` (saúde, versão, status, logs, notificações), `auth`, `projects`, `settings`, `jobs`, `assistant`, `scripts`, `channels` e `agents`. Cada módulo expõe um `APIRouter` e acessa os serviços por `gateway.main`, no mesmo padrão de `gateway/auto_generation.py`.
- `gateway/growth/` — domínio do Growth OS (PostgreSQL, Temporal, outbox).
- `gateway/auto_generation.py` — preparação durável e revisão editorial antes do render.
- `lattice-app/` — protótipo visual congelado; sem build e fora do produto (veja o README do diretório).
- `scraps/`, `screenshots/`, `uploads/`, `screens/`, `graphify-out/`, `.playwright-cli/` — artefatos locais de desenvolvimento, ignorados pelo git.

Testes do gateway: `pip install -r gateway/requirements.txt -r gateway/requirements-dev.txt` e `python -m pytest gateway/tests`. O CI (`.github/workflows/ci.yml`) roda esses testes e o `npm run version:check` a cada push e pull request.

## Interface

O menu principal segue o trabalho diário:

| Tela | Uso |
| --- | --- |
| Início | Painel do dia |
| Criar | Escolha o canal e depois Short ou vídeo longo. O perfil do canal preenche os parâmetros. Ajustes desta geração ficam em Opções avançadas |
| Produções | Trabalhos, revisão, biblioteca e publicação |
| Canais | Contas conectadas, perfil e automação |
| Crescimento | Estratégia, agentes e Growth OS |
| Resultados | Métricas dos canais e dos vídeos publicados |

Chat, notificações, configurações e logs ficam fora desse menu: o chat abre pelo botão Chat; notificações, configurações e logs ficam no topo.

Rascunhos de criação são guardados no navegador por canal e formato. Na primeira abertura de um canal, um rascunho antigo compatível é recuperado sem apagar a cópia original.

O tema claro/escuro fica no `localStorage`.

Arquivos HTML de atividade, usuários, planos, faturas e cadastro continuam no repositório como páginas estáticas antigas. Eles não fazem parte do cockpit em Docker e não têm API de billing.

## Geração

O Turbo baixa até três materiais em paralelo, reutiliza um cache local de 10 GB por 14 dias e consulta Pexels, Pixabay e Coverr nessa ordem quando um provedor não devolve um clipe válido. `MATERIAL_*` em `.env.example` ajusta esses limites. O encoder final é `libx264`.

Roteiros recebem orientação de abertura, progressão, exemplos, conclusão e texto próprio para narração. Uma reprovação editorial permite uma tentativa automática de correção. Roteiros enviados ou revisados manualmente continuam sob controle de quem opera. O painel mostra duração estimada, quantidade de palavras e frases longas. Essas estimativas não substituem a duração medida no áudio.

A curadoria compara até cinco candidatos por cena. Até três cenas são avaliadas ao mesmo tempo. Downloads preferem a versão Full HD disponível. A revisão final mede o áudio com FFmpeg e bloqueia pico de -40 dB ou menos e silêncios acima de quatro segundos. Essa medição não transcreve a voz nem comprova pronúncia.

Trabalhos antigos são considerados parados depois de 45 minutos (Short), 180 minutos (vídeo longo) ou 360 minutos na fila. Esses prazos vêm de `SHORT_STALE_MINUTES`, `LONG_STALE_MINUTES` e `QUEUE_STALE_MINUTES`.

## Publicação

Publicar continua sendo uma ação explícita, exceto nos canais do YouTube com operador autorizado.

Em Crescimento, no operador de cada canal do YouTube, a autorização começa desligada. Quando ligada, a grade define dias, horário e fuso IANA. O serviço `cockpit-operator` gera o vídeo, confere arquivo e metadados, envia ao YouTube como privado com `publishAt` e reconcilia a publicação pública. TikTok e canais sem autorização permanecem em revisão. Falha pausa o operador e aparece em Agentes e Notificações. Salvar o operador de novo, depois de corrigir a causa, limpa a pausa. Um envio de resultado incerto precisa ser conferido no YouTube antes de nova tentativa.

## Growth OS

A base local do Growth OS usa PostgreSQL 16, Temporal e Redis Streams. A tela calcula o gap YPP só com métricas orgânicas qualificadas, produz um Opportunity Score explicável e inicia um workflow de planejamento. A publicação desse fluxo permanece em revisão humana.

Rotas novas usam o prefixo `/api/v1/growth`:

- `GET /api/v1/growth/health`
- `POST /api/v1/growth/goals/evaluate`
- `POST /api/v1/growth/opportunities/score`
- `POST /api/v1/growth/workflows`
- `GET /api/v1/growth/workflows/{workflow_id}`

As rotas anteriores, sob `/api`, continuam ativas.

Diagnóstico:

```powershell
docker compose ps
docker compose logs growth-worker growth-outbox --tail 100
docker compose exec -T redis redis-cli XLEN growth:events
```

## Assistente

O chat responde em linguagem natural com o provedor de linguagem já configurado no cockpit. Ele consulta canais e trabalhos, calcula metas YPP, pontua oportunidades, inicia um plano durável ou gera um roteiro de Short. Pedido incompleto vira pergunta de esclarecimento. A conversa não publica nem aprova conteúdo.

## API principal

Tudo abaixo exige sessão, salvo health e login. Swagger só aparece com `ENABLE_API_DOCS=1`.

| Área | Rotas |
| --- | --- |
| Sessão | `/api/auth/login`, `/api/auth/logout`, `/api/auth/session` |
| Saúde | `/api/health/live`, `/api/health/ready`, `/api/health`, `/api/version`, `/api/status` |
| Canais | `/api/channels`, conexão YouTube e TikTok, refresh |
| Trabalhos | criação por projeto, consulta, retry, cancelamento, eventos, artefatos, metadados, aprovação e publicação |
| Lotes | `/api/generation-batches` e revisão por item |
| Roteiros | `/api/scripts/short`, `/api/scripts/long`, `/api/scripts/terms` |
| Operador | `/api/agents/operators` e execução manual por canal |
| Analytics | `/api/analytics/overview`, `/api/agents/analytics` |
| Ajustes | `/api/settings/providers`, `/api/settings/generation-defaults` |
| Assistente | `POST /api/assistant/chat` |
| Avisos | `/api/notifications`, `/api/logs` |

O estado operacional do cockpit fica no SQLite em `runtime`. O domínio novo de crescimento fica no PostgreSQL.

## O que não usar como guia

`npm start` não sobe o `server.js`. O servidor Node é somente `npm run static`.

Não há deploy previsto para Vercel, Netlify ou um Nginx com os HTML soltos. A operação suportada é o Docker Compose nesta máquina.

Não existe o fluxo de telas `#/gerador`, `#/youtube` e `#/custos` como menu atual. Essas views antigas, quando ainda existem no código, são alcançadas de dentro de Criar, Produções, Crescimento e Resultados.

## Problemas comuns

**A página pede login e a senha não é aceita.** Confira o e-mail em `runtime/secrets/admin.json`. A senha não está nesse arquivo. Redefina com `.\setup.ps1 -AdminOnly` e recrie o `cockpit-api`.

**A interface abre sem pedir senha.** O `cockpit-api` está com `COCKPIT_DISABLE_LOGIN=1`.

**`/api/health/ready` não responde.** Veja `docker compose ps` e o log do `cockpit-api`. A API espera o Turbo e o PostgreSQL saudáveis.

**Geração não anda.** Confira `moneyprinterturbo` e `cockpit-generation-worker`, e se as chaves do provedor de linguagem e do Pexels foram gravadas no setup.
