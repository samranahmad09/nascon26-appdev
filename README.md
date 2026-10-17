# NaSCon '26 Multi-Sport League & Club Operations Platform

Multi-tenant league platform: rule-driven sports, fixture generation, event-sourced scoring,
standings, rosters with transfers and loans, a concurrency-safe merch store, 5-role RBAC,
and an offline-first Flutter app.

## Run the backend

```bash
DAY1_SALT=<salt-from-team-slip> docker compose up -d --build
curl http://localhost:8080/health        # {"status":"ok"}
```

One command brings up everything on a clean Linux machine and seeds the baseline data:

| Service | Image | Purpose |
|---|---|---|
| `api` | built from `Dockerfile` (FastAPI, Python 3.12) | REST API, WebSocket, audit routes on port 8080 |
| `db` | postgres:16-alpine | Source of truth (append-only event log, orders, rosters) |
| `redis` | redis:7-alpine | Domain event stream (Redis Streams) |
| `worker` | same image as `api` | Consumes domain events |
| `minio` | bitnamilegacy/minio:2024.9.22 | Object store for PDF receipts |

`DAY1_SALT` is read only from the environment. See `.env.example` for every variable.

## Tests and coverage

```bash
./run_tests.sh
```

Runs pytest inside the API image against the compose Postgres, then prints per-area line
coverage in the `COVERAGE.json` format.

## Demo accounts

Present after every startup and every `POST /audit/v1/reset`. Password for all: `Nascon@26`

| Role | Email | Scope |
|---|---|---|
| Platform Super-Admin | admin@nascon.test | Platform-wide |
| League Organizer | organizer@nascon.test | Owns league-alpha-1 |
| Club Manager | manager@nascon.test | Owns club-alpha-1 |
| Player | player@nascon.test | player-alpha-1 |
| General Viewer | viewer@nascon.test | Read-only, tenant-alpha |

## Mobile app

Flutter (Android). Source in `mobile/`, installable build at `mobile/release/app-release.apk`
(Android 10+). The server address is editable in the app's Settings screen: use the laptop's
LAN IP with port 8080 on a phone, or `10.0.2.2:8080` on the emulator.

## Repository map

| Path | Contents |
|---|---|
| `app/core/` | Config, DB pool, error format, validation, HMAC signing, JWT, rate limit, reset/seed |
| `app/modules/<area>/` | One package per domain area (fixtures, scoring, standings, store, rbac, roster, offline, sports, app_api) |
| `tests/` | pytest suite |
| `TRACEABILITY.md` | FR/NFR to source and tests |
| `ADR.md` | Architecture decision records |
