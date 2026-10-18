# Architecture Decision Records

Eight short records of the decisions behind the platform, covering Phase I (league and club operations) and
Phase II (streaming, guest access, moderation and the QA harness).

---

## ADR-1: Modular monolith in FastAPI, one package per domain area

**Context.** Two hard build windows (4 h and 3.75 h), eleven graded backend areas across both phases, and a grader
with 4 vCPU / 8 GB RAM that must build and start the whole stack in minutes.

**Decision.** One Python 3.12 FastAPI service (`api`) with a package per area under `app/modules/`: Phase I
(sports, fixtures, scoring, standings, store, rbac, roster, offline, app_api) and Phase II (streaming, costream,
qr_guest, moderation, observability, web, social). Shared plumbing lives in `app/core/` (validation, error
envelope, signing, JWT, DB pool, middleware, metrics registry, reset registry). Modules talk through small
`service.py` contracts, never through each other's tables.

**Consequences.** One image and one place for cross-cutting rules (error body, validation order, 413, rate limit,
guest boundary). Areas were built in parallel because each owns a folder. Scaling out would need the in-process
pieces (ADR-3, ADR-7) moved to Postgres/Redis first.

## ADR-2: PostgreSQL is the source of truth; scoring is event-sourced

**Context.** Scores must be rebuilt from an append-only stream, corrected only by compensating events, and every
derived view (standings, QR targets, guest overlays) must reflect the live match state.

**Decision.** Match events live in an append-only table (no UPDATE/DELETE). Projections, standings and the QR
`target_route` are computed from the log. Status transitions (start, end, postpone, cancel, abandon) are events
too, so a printed QR poster automatically retargets when a match is completed or postponed. Idempotency is keyed
on client ids (event_id, order_id, request_id, action_id, guest_token, session_id) stored with the original
response.

**Consequences.** History is never lost and every correction is auditable. Hot read paths cache derived values and
invalidate them when the log for that match grows (ADR-3).

## ADR-3: Concurrency and throughput on a single worker

**Context.** Event sequence numbers, roster rules and idempotent replays must never race; checkout must survive 20
simultaneous buyers; and the Phase II load profile (500 viewers + 50 chat posters) needs p95 < 800 ms.

**Decision.** One uvicorn worker. Domain-log mutations run under a process-wide lock inside a DB transaction;
checkout reserves stock with a conditional `UPDATE ... WHERE stock >= qty`; moderation and DLQ counters use atomic
upserts. Cross-cutting guards are pure ASGI middleware (not `BaseHTTPMiddleware`), and the QR resolution path is an
async route serving pre-serialised bytes from an in-memory cache that is invalidated whenever that match's event log changes.

**Consequences.** Measured with `tests/k6_load.js`: 3,597 req/s, p95 195 ms, 0% errors (the BaseHTTPMiddleware
version managed 662 req/s and p95 1.87 s). Running several workers would need the lock, the rate limiter and the
WebSocket hub moved to Postgres advisory locks and Redis first.

## ADR-4: Sports are data, never code

**Context.** A "mystery sport" posted during grading must work with no code change, and any branch on a sport or
event name costs points.

**Decision.** Each sport is a validated JSON config (scoring model event / innings / round, periods, scoring
events, draw and overtime policy, tie-break order, roster rules). Engines branch only on `scoring_model` and on
config values. Seed data (sports, tenants, clubs, demo fixtures, the Phase II `match-m1`) lives in JSON files.

**Consequences.** New sports are a POST away; nothing in Python names a sport or an event type.

## ADR-5: Signed records with canonical JSON and per-phase salts from the environment

**Context.** Phase I events and receipts are signed with DAY1_SALT; Phase II stream keys, co-stream states, QR
links, cart merges and moderation records are signed with DAY2_SALT. The grader recomputes every signature.

**Decision.** One signing module (`app/core/signing.py`) with canonical JSON (sorted keys, no whitespace, UTF-8,
non-ASCII unescaped) and two entry points: `sign(id, payload)` (DAY1_SALT) and `sign_day2(entity_id, sig_payload)`
(DAY2_SALT, `entity_id + "|" + canonical`). Each Phase II route builds exactly the `sig_payload` object from the
spec's table. Both salts are read only from the environment (forwarded by compose; `.env` is git- and
docker-ignored). Both Appendix A references are in the test suite.

**Consequences.** Tampering with any stored record is detectable; rotating a salt only needs a restart.

## ADR-6: Custom JWT, a policy matrix, and a server-side guest boundary

**Context.** Five member roles with ownership scopes and absolute tenant isolation (Phase I), plus anonymous guests
who may watch, read scores and stage a cart but never write (Phase II).

**Decision.** HS256 JWTs carry role, tenant and scope ids; verification pins the algorithm and rejects
non-canonical segments, so `alg:none`, tampered and expired tokens are 401. Authorization is a declarative policy
matrix evaluated before anything else. Guests get `guest_anon_tok_*` tokens; a pure ASGI `GuestWriteBlock` rejects
every POST/PUT/PATCH/DELETE carrying one with 403 `GUEST_WRITE_FORBIDDEN` before validation, except the guest
cart staging and merge routes. The cart-merge probe drives that same guard to prove the block.

**Consequences.** Hiding buttons in a UI is never the control; the server is. Guests convert to members through the
progressive login modal, and the guest cart merges atomically and idempotently into the member cart.

## ADR-7: Real-time, notifications, media and observability

**Context.** Live scores must reach clients in under 500 ms; co-stream chat needs one ordered room; failed
notifications need retry and a dead-letter queue; streams need authenticated ingest that can be cut instantly; and
operations need Prometheus metrics.

**Decision.** An in-process WebSocket hub (`/ws/matches/{id}`, `/api/v1/ws?topic=`) fed after each committed write.
Chat goes through synchronous moderation (leetspeak normalisation, flood and rate rules) with a per-room
monotonically increasing sequence. Notifications retry up to 3 times with exponential backoff, then land in a
Postgres-backed DLQ. MediaMTX authorises every publish/read through the API's `/internal/media/auth` hook (only
active stream keys), and revoke/rotate kicks live publishers through the MediaMTX control API. `/metrics` renders
a registry that modules feed (WebSocket connections, active streams, bitrate, DLQ depth, QR scans).

**Consequences.** Redis Streams carry domain events to the worker; the WebSocket hub is per process (ADR-3). The
media server is self-hosted, so no third-party streaming SaaS sees our streams.

## ADR-8: Offline-first native app, deep links, and a zero-install guest web player

**Context.** The app must work in airplane mode with a persistent action queue (Phase I), open straight onto the
right screen from `nascon://` links and QR codes (Phase II), and guests on any phone must watch without installing
anything.

**Decision.** Flutter (Android) with a local cache and a persistent queue in the exact `/audit/v1/offline-sync`
format; Keystore token storage; a browsable `nascon://` intent-filter routed to Match Live / Co-Stream screens that
open in read-only guest mode without a login wall; an ML Kit QR scanner. The guest web player is server-rendered
HTML plus vanilla JS under `/web/*` (hls.js vendored locally), carrying the nine required `data-testid`s and WCAG
2.1 AA rules, verified by Selenium Grid 4 (`tests/selenium`), Appium 2 (`tests/appium`) and k6 (`tests/k6_load.js`).

**Consequences.** One backend serves three surfaces (native app, guest web, graders) through the same domain
services, so the offline replays, guest reads and audit probes can never diverge.
