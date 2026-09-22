# MoneyPrinter Cockpit local

Cockpit local para geração, revisão e publicação controlada de vídeos pelo MoneyPrinterTurbo. A aplicação usa FastAPI, SQLite e Docker; não há billing, organizações, planos ou usuários simulados.

## Primeiro uso

No PowerShell, com o Docker Desktop aberto:

```powershell
.\setup.ps1
docker compose up -d --build --wait
```

O setup solicita o nome, e-mail e senha do único administrador local. A senha nunca é gravada: somente um hash PBKDF2-SHA256 fica em `runtime/secrets/admin.json`.

Abra [http://127.0.0.1:3000/Login.html](http://127.0.0.1:3000/Login.html), entre e use o cockpit. A porta permanece limitada ao loopback.

## Operação

```powershell
npm start          # inicia o Docker
npm run static     # somente protótipo visual; não tem API, login nem geração real
docker compose up -d --build --wait
docker compose ps
```

O healthcheck de readiness é `GET /api/health/ready`; liveness é `GET /api/health/live`. Rotas administrativas exigem sessão e CSRF. Swagger fica desativado; defina `ENABLE_API_DOCS=1` apenas para diagnóstico local autenticado.

### Materiais stock mais rápidos

O Turbo baixa até três materiais em paralelo, reutiliza um cache local de 10 GB por 14 dias e usa Pexels, Pixabay e Coverr nessa ordem quando um provedor não retorna um clipe válido. As variáveis `MATERIAL_*` em `.env.example` permitem ajustar esses limites sem colocar chaves no Docker ou no navegador. O encoder final continua sendo `libx264`; não há redução de resolução, FPS, áudio ou legendas.

As telas mantidas são: visão geral, canais, gerador longo, Shorts, agentes, analytics, logs, jobs, notificações, configurações e login.

Publicações reais continuam uma ação explícita do operador; a reconstrução e os testes não publicam nem consomem créditos externos.

## Growth OS P0

A composição local também inicia a fundação do Growth OS:

- PostgreSQL 16 para o novo domínio e seus eventos;
- Temporal para workflows com checkpoint, retry e retomada;
- Redis Streams para distribuição at-least-once do outbox;
- MinIO para object storage compatível com S3;
- workers separados para planejamento e publicação de eventos.

A tela **Growth OS** calcula o gap YPP usando apenas métricas orgânicas qualificadas, produz um Opportunity Score explicável e inicia um workflow durável de planejamento. A publicação permanece em revisão humana por padrão.

Endpoints novos usam o prefixo `/api/v1/growth`. As rotas antigas continuam ativas enquanto a migração ocorre por etapas.

## Assistente conversacional

A área **Assistente** oferece uma conversa em linguagem natural sem substituir as telas atuais. Ela usa o provedor de linguagem já configurado no Cockpit para responder perguntas e encaminhar pedidos compatíveis com as funções existentes: consultar canais e trabalhos, calcular metas YPP, pontuar oportunidades, iniciar um plano durável ou gerar um roteiro de Short. Pedidos incompletos geram perguntas de esclarecimento. A conversa não publica nem aprova conteúdo; workflows de planejamento ficam em revisão humana.

Diagnóstico rápido:

```powershell
docker compose ps
docker compose logs growth-worker growth-outbox --tail 100
docker compose exec -T redis redis-cli XLEN growth:events
```

PostgreSQL, Temporal e MinIO só são expostos em `127.0.0.1`. As credenciais padrão de desenvolvimento devem ser trocadas antes de qualquer uso fora da máquina local.
