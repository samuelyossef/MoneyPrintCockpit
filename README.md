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

O menu principal segue o trabalho diário: **Início**, **Criar**, **Produções**, **Canais**, **Crescimento** e **Resultados**. Em Criar, escolha primeiro o canal e depois Short ou vídeo longo; o perfil do canal preenche os parâmetros, e os ajustes desta geração ficam em Opções avançadas. Produções reúne trabalhos, revisões, biblioteca e publicação. O chat fica no botão Chat; notificações, configurações e logs ficam no topo.

Rascunhos são guardados por canal e formato no navegador. Ao abrir um canal pela primeira vez, um rascunho antigo compatível com aquele canal é recuperado sem apagar a cópia original. As rotas de API e os dados persistidos no servidor mantêm os contratos anteriores.

Publicações manuais continuam uma ação explícita do operador. Em **Agentes → Operador**, cada canal do YouTube pode receber autorização individual para publicação automática e uma grade de dias, horário e fuso IANA. A autorização começa desligada. O worker `cockpit-operator` gera o vídeo na cadência configurada, verifica arquivo e metadados, envia ao YouTube como privado com `publishAt` e reconcilia a publicação pública. TikTok e canais sem autorização continuam em revisão. Falhas pausam o operador e aparecem em Agentes/Notificações; salvar novamente o operador após corrigir a causa limpa a pausa. Envios de resultado incerto exigem conferência no YouTube antes de qualquer nova tentativa.

O agendador roda no serviço `cockpit-operator`, separado da API. `docker compose ps cockpit-operator` e `docker compose logs cockpit-operator --tail 100` mostram seu estado. Os testes locais simulam o YouTube e não publicam nem consomem créditos externos; a validação real requer uma conta de teste e um projeto de API habilitado para publicação pública.

## Qualidade da geração por canal

Os roteiros gerados recebem orientações de abertura, progressão, exemplos concretos, conclusão e texto próprio para narração. Uma reprovação editorial permite uma tentativa automática de correção; roteiros enviados ou revisados manualmente continuam sob controle do usuário. O painel mostra duração estimada, quantidade de palavras e frases longas. Essas estimativas não substituem a duração medida no áudio.

A curadoria compara até cinco candidatos por cena e escolhe entre os aprovados por relevância, movimento e variedade. Downloads priorizam a versão Full HD disponível para evitar transferir 4K desnecessariamente. Até três cenas são avaliadas simultaneamente; resultados concluídos são persistidos e reaproveitados na retomada. A inspeção final também avalia até três quadros simultaneamente, sem bloquear a renovação da reserva do worker durante o processamento de mídia.

A revisão final mede o áudio com FFmpeg e bloqueia volume de pico de -40 dB ou menos e silêncios acima de quatro segundos, além dos controles existentes de imagem e duração. Essa medição avalia o áudio misturado: não transcreve a voz nem comprova pronúncia ou sincronização labial. Os provedores e perfis configurados continuam sendo utilizados.

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
