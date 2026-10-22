# MoneyPrinter Cockpit

Documentation for the local application that generates, reviews, and publishes videos through MoneyPrinterTurbo. Version 1.2.0.

This describes the product that is running. `npm run static` opens only the visual prototype: it does not authenticate, call the API, or generate video.

## What the application does

The cockpit is a local interface for day-to-day work on YouTube and TikTok channels:

- create a Short or a long video from a channel profile;
- follow jobs, review, the library, and publishing;
- connect channels and, per YouTube channel, authorize an automatic publishing schedule;
- check growth (YPP goals, opportunity, and planning) and channel results.

There is one local administrator. There is no public signup, no organizations, no plans, no invoices, and no subscriber panel.

## How to start

Prerequisites: Docker Desktop running, and PowerShell.

```powershell
.\setup.ps1
docker compose up -d --build --wait
```

`setup.ps1` creates `runtime/secrets/admin.json`, `runtime/secrets/mpt_api_key.txt`, and `runtime/moneyprinter/config.toml`. Moonshot/Kimi, Pexels, and Upload-Post keys may be left blank and filled in later. Example values are in `.env.example`. Do not put secrets in the browser or in the repository.

Open [http://127.0.0.1:3000/](http://127.0.0.1:3000/). Login is removed from the normal path.

| Command | Effect |
| --- | --- |
| `npm start` | `docker compose up -d --wait` |
| `npm run static` | `node server.js`, prototype only |
| `docker compose ps` | service status |
| `docker compose logs cockpit-operator --tail 100` | scheduler log |

Readiness: `GET /api/health/ready`. Liveness: `GET /api/health/live`. The version is returned by `GET /api/version` and by the `version` field of readiness.

## Services

| Service | Role |
| --- | --- |
| `cockpit-api` | API and interface on `127.0.0.1:3000` |
| `cockpit-operator` | Automatic publishing on the configured schedule |
| `cockpit-generation-worker` | Generation queue |
| `moneyprinterturbo` | Script, voice, materials, and render |
| `redis` | Turbo queue and Growth OS event stream |
| `postgresql` | Growth OS domain on `127.0.0.1:5433` |
| `temporal` | Durable workflows on `127.0.0.1:7233` |
| `growth-worker` | Planning |
| `growth-outbox` | At-least-once publication of events to Redis |

MinIO is described by the object-storage variables, but the service is commented out in `compose.yaml` and does not start with the current composition.

PostgreSQL and Temporal listen only on `127.0.0.1`. Change the default development passwords before any use outside this machine.

## Access

The administrator is the email stored in `runtime/secrets/admin.json`. The original password cannot be read back: the file stores salt, iterations, and a PBKDF2-SHA256 hash (600,000 iterations). To change the password, run `.\setup.ps1 -AdminOnly` or replace the hash and recreate `cockpit-api`.

`COCKPIT_DISABLE_LOGIN` defaults to `1`. The API treats every request as a local session, `POST /api/auth/login` responds 404, and `/Login.html` redirects to the cockpit. `GET /api/auth/session` still returns the local administrator and the CSRF token used by write requests.

Repeated login attempts are limited for 15 minutes.

## Interface

The main menu follows the daily work:

| Screen | Use |
| --- | --- |
| Home | The day's panel |
| Create | Pick the channel, then Short or long video. The channel profile fills the parameters. Adjustments for this generation stay under Advanced options |
| Productions | Jobs, review, library, and publishing |
| Channels | Connected accounts, profile, and automation |
| Growth | Strategy, agents, and Growth OS |
| Results | Metrics for channels and published videos |

Chat, notifications, settings, and logs sit outside that menu: chat opens from the Chat button; notifications, settings, and logs are in the top bar.

Creation drafts are stored in the browser per channel and format. The first time a channel is opened, a compatible older draft is recovered without deleting the original copy.

The light/dark theme is stored in `localStorage`.

Activity, users, plans, invoices, and signup HTML files remain in the repository as old static pages. They are not part of the Docker cockpit and have no billing API.

## Generation

Turbo downloads up to three materials in parallel, reuses a local 10 GB cache for 14 days, and queries Pexels, Pixabay, and Coverr in that order when a provider does not return a valid clip. `MATERIAL_*` in `.env.example` adjusts those limits. The final encoder is `libx264`.

Scripts receive guidance for the opening, progression, examples, conclusion, and text written to be spoken. An editorial rejection allows one automatic correction attempt. Scripts typed or reviewed manually stay under the operator's control. The panel shows estimated duration, word count, and long sentences. Those estimates do not replace the duration measured in the audio.

Curation compares up to five candidates per scene. Up to three scenes are evaluated at once. Downloads prefer the available Full HD rendition. Final review measures audio with FFmpeg and blocks a peak of -40 dB or lower and silences longer than four seconds. That measurement does not transcribe the voice and does not prove pronunciation.

Old jobs are treated as stalled after 45 minutes (Short), 180 minutes (long video), or 360 minutes in the queue. Those limits come from `SHORT_STALE_MINUTES`, `LONG_STALE_MINUTES`, and `QUEUE_STALE_MINUTES`.

Before a script is written, Create can request a research proposal: angle, audience questions, and a brief. A local reference video contributes only duration, hook, and pace. It is not copied into the script. The chosen stock clip records why it won: relevance, motion, free stock, and the discarded alternatives.

## Publishing

Publishing stays an explicit action, except on YouTube channels with an authorized operator.

In Growth, each YouTube channel operator starts unauthorized. When enabled, the schedule sets days, time, and an IANA time zone. `cockpit-operator` generates the video, checks the file and metadata, uploads to YouTube as private with `publishAt`, and reconciles the public publish. TikTok and channels without authorization stay in review. A failure pauses the operator and shows up in Agents and Notifications. Saving the operator again, after the cause is fixed, clears the pause. An upload with an uncertain result must be checked on YouTube before another attempt.

Automatic upload runs only after final review is approved. A blocked or failed review pauses the channel.

An approved long video can be cut into up to three vertical Shorts. Those clips enter the same channel publishing queue.

## Growth OS

The local Growth OS base uses PostgreSQL 16, Temporal, and Redis Streams. The screen calculates the YPP gap from qualified organic metrics only, produces an explainable Opportunity Score, and starts a planning workflow. Publishing from that flow stays in human review.

New routes use the `/api/v1/growth` prefix:

- `GET /api/v1/growth/health`
- `POST /api/v1/growth/goals/evaluate`
- `POST /api/v1/growth/opportunities/score`
- `POST /api/v1/growth/workflows`
- `GET /api/v1/growth/workflows/{workflow_id}`

The older routes, under `/api`, stay active.

Diagnosis:

```powershell
docker compose ps
docker compose logs growth-worker growth-outbox --tail 100
docker compose exec -T redis redis-cli XLEN growth:events
```

## Assistant

Chat answers in natural language with the language provider already configured in the cockpit. It looks up channels and jobs, calculates YPP goals, scores opportunities, starts a durable plan, or drafts a Short script. An incomplete request becomes a clarifying question. The conversation does not publish or approve content. A Short script is written only after the research proposal is confirmed.

## Main API

Everything below requires a session, except health and login. Swagger appears only with `ENABLE_API_DOCS=1`.

| Area | Routes |
| --- | --- |
| Session | `/api/auth/login`, `/api/auth/logout`, `/api/auth/session` |
| Health | `/api/health/live`, `/api/health/ready`, `/api/health`, `/api/version`, `/api/status` |
| Channels | `/api/channels`, YouTube and TikTok connect, refresh |
| Jobs | create by project, fetch, retry, cancel, events, artifacts, metadata, approval, and publishing |
| Batches | `/api/generation-batches` and per-item review |
| Scripts | `/api/scripts/proposal`, `/api/scripts/reference`, `/api/scripts/short`, `/api/scripts/long`, `/api/scripts/terms` |
| Operator | `/api/agents/operators` and a manual run per channel |
| Analytics | `/api/analytics/overview`, `/api/agents/analytics` |
| Settings | `/api/settings/providers`, `/api/settings/generation-defaults` |
| Assistant | `POST /api/assistant/chat` |
| Notices | `/api/notifications`, `/api/logs` |
| Derived Shorts | `POST /api/jobs/{job_id}/shorts` |

Operational cockpit state lives in SQLite under `runtime`. The newer growth domain lives in PostgreSQL.

## What not to use as a guide

`npm start` does not start `server.js`. The Node server is only `npm run static`.

There is no planned deploy to Vercel, Netlify, or a bare Nginx of the loose HTML files. The supported operation is Docker Compose on this machine.

There is no current menu of `#/gerador`, `#/youtube`, and `#/custos`. Those older views, where they still exist in the code, are reached from inside Create, Productions, Growth, and Results.

## Common problems

**The page still asks for a password.** Recreate `cockpit-api` so it picks up `COCKPIT_DISABLE_LOGIN=1`.

**`/api/health/ready` does not respond.** Check `docker compose ps` and the `cockpit-api` log. The API waits for Turbo and PostgreSQL to be healthy.

**Generation does not move.** Check `moneyprinterturbo` and `cockpit-generation-worker`, and whether the language-provider and Pexels keys were saved during setup.
