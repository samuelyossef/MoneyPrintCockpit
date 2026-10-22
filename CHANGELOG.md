# Changelog

Mudanças relevantes do MoneyPrinter Cockpit são registradas aqui por versão. As versões seguem SemVer: MAJOR para incompatibilidades, MINOR para funcionalidades compatíveis e PATCH para correções compatíveis.

## [Unreleased]

- Divide os serviços de domínio do gateway em `gateway/services/` (`util`, `db`, `auth`, `observability`, `projects`, `turbo`, `jobs`, `settings`, `scripts`, `assistant`, `channels`, `agents`); `gateway/main.py` mantém configuração, modelos, montagem da aplicação e um facade que re-exporta os serviços, preservando `gateway.main` como namespace compartilhado (testes e workers não mudam).
- `setup.ps1` aplica a senha local do PostgreSQL em serviço com `ALTER USER` quando o volume do Growth OS já existe — não é mais preciso `docker compose down -v` nem perder dados — e ganha o switch `-RotateGrowthPassword` para troca manual a qualquer momento.
- Divide as rotas HTTP do gateway em `gateway/routers/` (`system`, `auth`, `projects`, `settings`, `jobs`, `assistant`, `scripts`, `channels`, `agents`); `gateway/main.py` mantém os serviços compartilhados e a montagem da aplicação.
- Adiciona CI no GitHub Actions: testes do gateway com pytest e checagem do SemVer unificado (`npm run version:check`).
- Remove do versionamento os artefatos locais (`scraps/`, `screenshots/`, `uploads/`, `screens/`, `graphify-out/`, `.playwright-cli/` e PNGs/HTMLs soltos na raiz); os arquivos permanecem no disco e passam a ser ignorados pelo git.
- Remove `support.js` do versionamento: é um runtime gerado do canvas de design, sem consumidor no produto (o arquivo permanece no disco, ignorado).
- Marca `lattice-app/` como protótipo visual congelado e fonte única desses arquivos; cópias antigas em `uploads/` foram removidas.
- `setup.ps1` passa a gerar senhas locais aleatórias do Growth OS (PostgreSQL/MinIO) em `.env` (fora do versionamento); os defaults de desenvolvimento continuam apenas como fallback do Compose.
- Ajusta os healthchecks do `cockpit-api` e do `moneyprinterturbo` no `compose.yaml` (intervalo/timeout menores e `start_period` do cockpit-api de 90s para 30s) para acelerar o `docker compose up -d --wait`.
- `server.js` (modo `npm run static`) responde 404, e não 500, para diretórios sem `index.html`.

## [1.2.0] - 2026-09-26

- Estabelece o primeiro identificador SemVer unificado para interface, API e pacote.
- Adiciona endpoint de versão, identificação no cockpit e fluxo documentado de atualização.
