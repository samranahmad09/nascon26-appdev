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
| `minio` | bitnamilegacy/minio:2024.9.22 | S3-compatible object store, provisioned for receipt archival (receipts are generated on demand by the API today). `minio/minio` is no longer published on Docker Hub, so the Bitnami build of the same release is used |

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

## Phase II (Day 2)

```bash
DAY1_SALT=<day-1-salt> DAY2_SALT=<day-2-salt> docker compose up -d --build
```

The same command now also starts the media server and a Selenium Grid. Both salts are read only from the
environment.

| Service | Host port | Purpose |
|---|---|---|
| `api` (alias `backend`) | 8080 | REST + WebSocket API, guest web player `/web/*`, short links `/q/*`, `/metrics` |
| `media` (MediaMTX 1.9.1) | 1935 (RTMP), 8888 (HLS), 8889 (WebRTC WHIP/WHEP) | Live ingest and playback; publishing is authorised by the API |
| `selenium-hub` + `node-chrome-1`, `node-chrome-2`, `node-firefox` | 4444 | Selenium Grid 4 (`shm_size: 1gb`, VNC off) |
| Appium 2 (on the host, not in compose) | 4723 | `uiautomator2` driver, phone over USB |

Guest web player: open `http://localhost:8080/q/m1-live` (redirects to `/web/matches/match-m1/live`).
Deep links on the phone: `adb shell am start -W -a android.intent.action.VIEW -d "nascon://entity/match-m1"`.

### QA harness

| Suite | Command | Report |
|---|---|---|
| Unit + contract tests | `./run_tests.sh` | coverage printed in `COVERAGE.json` / `COVERAGE_DAY2.json` format |
| Selenium Grid 4 | `pip install -r tests/selenium/requirements.txt && pytest -n 3 tests/selenium/` (env `GRID_URL`, `BASE_URL`, `API_URL`) | `tests/reports/selenium-report.xml`, `.html` |
| Appium 2 | `adb reverse tcp:8080 tcp:8080`, start `appium`, then `pip install -r tests/appium/requirements.txt && pytest tests/appium/` | `tests/reports/appium-report.xml` |
| k6 (NFR-2) | `k6 run --summary-export=tests/reports/k6-summary.json tests/k6_load.js` (on Docker Desktop: `docker run --rm -i --network container:<api-container> grafana/k6:0.54.0 run - < tests/k6_load.js`) | `tests/reports/k6-summary.json` |
| CI | `.github/workflows/autograde.yml` on every push to `main` | Actions artifacts |

Measured on the team laptop on the final stack (single API worker): k6 NFR-2 profile 3,840 req/s, p95 115 ms,
0% errors; Selenium 10/10; Appium 9/9 on a Galaxy S25 Ultra; 1246 unit and contract tests passing; the GitHub
Actions `autograde` workflow green on a clean Ubuntu runner.
