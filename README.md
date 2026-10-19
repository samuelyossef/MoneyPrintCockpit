# MoneyPrint Cockpit 💸

Cockpit local para gerar, revisar e publicar vídeos pelo MoneyPrinterTurbo. A aplicação usa FastAPI, SQLite e Docker. Há um único administrador local. Não há billing, organizações, planos nem usuários simulados.

A documentação de uso e de arquitetura está em [DOCUMENTACAO.md](DOCUMENTACAO.md). Este arquivo cobre só como subir e operar a instalação.

## Primeiro uso

No PowerShell, com o Docker Desktop aberto:

```powershell
.\setup.ps1
docker compose up -d --build --wait
```

O setup pede nome, e-mail e senha do administrador. A senha não é gravada: `runtime/secrets/admin.json` guarda apenas um hash PBKDF2-SHA256. A senha precisa ter pelo menos 12 caracteres.

Abra [http://127.0.0.1:3000/Login.html](http://127.0.0.1:3000/Login.html). A porta fica limitada ao loopback.

## Operação

```powershell
npm start                              # docker compose up -d --wait
npm run static                         # protótipo visual; sem API, login nem geração
docker compose up -d --build --wait
docker compose ps
docker compose logs cockpit-api --tail 100
```

Readiness: `GET /api/health/ready`. Liveness: `GET /api/health/live`. Rotas administrativas exigem sessão e CSRF. O Swagger fica desligado; use `ENABLE_API_DOCS=1` só para diagnóstico local autenticado.

O login está ativo quando `COCKPIT_DISABLE_LOGIN` é `0` no serviço `cockpit-api`. Com `1`, a interface abre sem senha.

## Versão

`VERSION` é a versão exibida no cockpit e retornada por `GET /api/version`, pela readiness e pelos metadados OpenAPI. `package.json` espelha esse valor. Confira com `npm run version:check`. Para incrementar: `npm run version:bump -- patch` (ou `minor`, `major`, ou uma versão explícita). Registre o resumo em `CHANGELOG.md` e crie a tag `vX.Y.Z` no commit da release.

Versão atual: 1.2.0.
