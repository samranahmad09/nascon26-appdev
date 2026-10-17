# Architecture Decision Records

Eight short records of the decisions behind the platform, in the order they were made.

---

## ADR-1: Modular monolith in FastAPI, one package per domain area

**Context.** Phase I has six graded backend areas, a hard 4-hour build window, and a grader with 4 vCPU / 8 GB RAM that must build and start the stack in under 10 minutes.

**Decision.** One Python 3.12 FastAPI service (`api`) with a package per area under `app/modules/` (sports, fixtures, scoring, standings, store, rbac, roster, offline, app_api) and shared plumbing in `app/core/` (validation, error format, signing, JWT, DB pool, rate limiting, reset registry). Modules talk through small `service.py` functions, never through each other's tables.

**Consequences.** One image, fast build, one place for cross-cutting rules (error body, validation order, 413, rate limit). Areas could be developed in parallel because each owns a folder. Scaling out would need the in-process pieces (ADR-3, ADR-7) moved to Postgres/Redis first.

## ADR-2: PostgreSQL is the source of truth; scoring is event-sourced

**Context.** Scores must be rebuilt from an append-only stream, corrected only by compensating events, and standings must be recomputed from scratch after any correction.

**Decision.** Match events live in an append-only table (no UPDATE/DELETE). The score projection and the standings table are pure functions over the log, computed on every read. Idempotency is keyed on client ids (event_id, order_id, request_id, action_id) stored with the original response.

**Consequences.** History is never lost and every correction is auditable (the original row and its compensation both stay visible). Reads cost more than cached counters, which is fine at league scale and keeps the projection provably consistent with the log.

## ADR-3: Concurrency model: single writer lock for domain logs, atomic SQL for inventory

**Context.** Event sequence numbers, idempotent replays and roster rule checks must never race; checkout must survive 20 simultaneous buyers of the last unit.

**Decision.** The API runs one uvicorn worker. Mutations of event logs, leagues, rosters and offline sync run under one process-wide re-entrant lock inside a DB transaction. Checkout does not use that lock: it claims the order id with `INSERT ... ON CONFLICT` and reserves stock with a conditional `UPDATE ... WHERE stock >= qty` in one transaction, so correctness comes from Postgres row locks.

**Consequences.** Zero overselling and no negative stock regardless of worker count; the domain lock is simple and correct for one worker but is the first thing to replace (advisory locks) before running several API replicas.

## ADR-4: Sports are data, never code

**Context.** A "mystery sport" posted during grading must work with no code change, and any branch on a sport or event name costs 8 points.

**Decision.** Each sport is a validated JSON config (scoring model event / innings / round, periods, scoring events with points / runs+legal_ball+wicket / rounds, draw policy, overtime, tie-break order, roster rules). Engines branch only on `scoring_model` and on config values. Seeded sports, tenants, clubs and demo fixtures are JSON files (`seed_sports.json`, `seed_data.json`, `demo.json`).

**Consequences.** New sports are a POST away; limits such as innings completion or a decided best-of series come from the config. The cost is validation code for every field of the rule schema.

## ADR-5: Signed records with canonical JSON and a per-team salt from the environment

**Context.** Every score event and receipt must carry `HMAC_SHA256(DAY1_SALT, id + "|" + payload)` that the grader recomputes byte for byte.

**Decision.** One signing helper (`app/core/signing.py`) with canonical JSON (recursively sorted keys, no whitespace, UTF-8, non-ASCII unescaped). The salt is read only from the `DAY1_SALT` environment variable, forwarded by compose; it is never committed (`.env` is git- and docker-ignored). Verified against Appendix A test vector 1 in the test suite.

**Consequences.** Tampering with a stored event or receipt is detectable; rotating the salt only needs a restart.

## ADR-6: Custom JWT plus a policy matrix for five roles and strict tenant isolation

**Context.** Five roles (super admin, league organizer, club manager, player, viewer) with ownership scopes, absolute tenant isolation, and probes that check 401 vs 403 vs 404 semantics.

**Decision.** HS256 JWTs issued by the API carry role, tenant and scope ids (league_ids, club_id, player_id). Verification pins the algorithm, so `alg:none`, tampered and expired tokens are 401. Authorization is a declarative policy matrix (action x role -> rule) evaluated before anything else; cross-tenant access by a non-super-admin is always 403, and 404 is reserved for ids that do not exist at all.

**Consequences.** Every role boundary is a row in a table with a negative test. Keycloak or Ory would add an extra container and startup time with no gain for the graded scope.

## ADR-7: Domain events on Redis Streams, realtime over WebSockets

**Context.** Order status changes must emit structured domain events to an internal queue, and live scores and pinned products must reach connected clients instantly.

**Decision.** Domain events are appended to a Redis Stream (`domain-events`) consumed by the `worker` service. Realtime fan-out is an in-process WebSocket hub (`/api/v1/ws?topic=match:{id}`) fed after each successful write. Event emission is best effort and never fails the user's request.

**Consequences.** Redis is already in the approved stack and lighter than RabbitMQ on the 8 GB grader. The WebSocket hub is per process; multiple API replicas would publish through Redis pub/sub instead.

## ADR-8: Offline-first Flutter app with a persistent action queue and server-wins sync

**Context.** The app must keep working in airplane mode, queue scoring and roster actions, survive a force-close, and sync with conflict reporting.

**Decision.** Flutter (Android). Fixtures, standings, club profiles and match details are cached in a local database; offline actions are stored in a persistent queue in exactly the `/audit/v1/offline-sync` action format with a stable device id and the last-seen version as `base_version`. The server replays actions in `queued_at` order with the same domain services as the online routes and reports per action: applied (appended or rebased_append), duplicate, or rejected with server_wins and the rule's error code. The token is stored in the Android Keystore (flutter_secure_storage). The server address is editable in Settings.

**Consequences.** No divergent offline business logic: the server stays authoritative and the app explains every rejection in plain language. Stale-but-valid actions are rebased instead of rejected, which keeps scorers productive during connectivity gaps.
