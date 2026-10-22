# MoneyPrinter Cockpit

Local cockpit for generating, reviewing, and publishing videos through MoneyPrinterTurbo. The application uses FastAPI, SQLite, and Docker. There is a single local administrator. There is no billing, no organizations, no plans, and no simulated users.

Usage and architecture are documented in [DOCUMENTATION.md](DOCUMENTATION.md). This file only covers how to start and operate the installation.

## First run

In PowerShell, with Docker Desktop running:

```powershell
.\setup.ps1
docker compose up -d --build --wait
```

Setup asks for the administrator name, email, and password. The password is not stored: `runtime/secrets/admin.json` keeps only a PBKDF2-SHA256 hash. The password must be at least 12 characters.

Open [http://127.0.0.1:3000/](http://127.0.0.1:3000/). The port is bound to loopback. There is no login screen.

## Operation

```powershell
npm start                              # docker compose up -d --wait
npm run static                         # visual prototype; no API, login, or generation
docker compose up -d --build --wait
docker compose ps
docker compose logs cockpit-api --tail 100
```

Readiness: `GET /api/health/ready`. Liveness: `GET /api/health/live`. Administrative routes require a session and CSRF. Swagger stays off; set `ENABLE_API_DOCS=1` only for authenticated local diagnosis.

Login stays off. `COCKPIT_DISABLE_LOGIN` defaults to `1`, so the interface and the API open as a local session. Set it to `0` only if you want the password screen back.

## Version

`VERSION` is the version shown in the cockpit and returned by `GET /api/version`, readiness, and the OpenAPI metadata. `package.json` mirrors that value. Check it with `npm run version:check`. To increment: `npm run version:bump -- patch` (or `minor`, `major`, or an explicit version). Record the summary in `CHANGELOG.md` and create the `vX.Y.Z` tag on the release commit.

Current version: 1.2.0.
