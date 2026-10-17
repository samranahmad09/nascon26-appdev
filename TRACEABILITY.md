# Traceability Matrix

Every functional and non-functional requirement of the Part I specification mapped to the source that implements it
and the automated tests that verify it. Run everything with `./run_tests.sh` (pytest inside the API image against
the compose Postgres; prints per-area coverage in the `COVERAGE.json` format). Many test functions are parametrized,
so the suite runs several hundred cases.

## Functional requirements

| ID | Requirement | Source | Automated tests |
|---|---|---|---|
| FR-1.1 | Multi-sport taxonomy as data; mystery sport from JSON (`POST /audit/v1/sports`) | `app/modules/sports/logic.py`, `app/modules/sports/routes.py`, `app/modules/sports/seed_sports.json` | `tests/test_sports.py` |
| FR-1.1 | Event-sourced scoring: append-only log, compensating events, signed events, config-driven limits (`POST /audit/v1/events`, `GET /audit/v1/score/{id}`) | `app/modules/scoring/logic.py`, `app/modules/scoring/service.py`, `app/modules/scoring/routes.py`, `app/core/signing.py` | `tests/test_scoring.py`, `tests/test_core.py` (Appendix A vector 1) |
| FR-1.2 | League setup scoped to seasons (`POST /audit/v1/leagues`), seeded tenant leagues | `app/modules/scoring/service.py` (`register_league`), `app/core/seed.py`, `app/core/seed_data.json` | `tests/test_scoring_leagues.py`, `tests/test_app_api.py` |
| FR-1.3 | Fixture generation: round robin (circle method, byes, withdrawals), Swiss (exact minimum-cost rematch-free pairing, Buchholz), single and double elimination with bracket reset (`POST /audit/v1/fixtures`) | `app/modules/fixtures/round_robin.py`, `swiss.py`, `matching.py`, `weighted.py`, `elimination.py`, `routes.py` | `tests/test_fixtures_round_robin.py`, `tests/test_fixtures_swiss.py`, `tests/test_fixtures_elimination.py` |
| FR-1.3 | Match status state machine, illegal transitions rejected, real-time status broadcast | `app/modules/scoring/logic.py`, `app/core/realtime.py`, `app/modules/app_api/routes.py` (`/api/v1/ws`) | `tests/test_scoring.py` |
| FR-1.4 | Standings: configurable points, tie-break order incl. 3-way H2H mini-league and restart-on-split, NRR / map differential, drawing of lots, clinched / eliminated (`GET /audit/v1/standings/{id}`) | `app/modules/standings/logic.py`, `app/modules/standings/routes.py`, `app/core/signing.py` (`lots_key`) | `tests/test_standings.py`, `tests/test_standings_api.py`, `tests/test_standings_e2e.py`, `tests/test_core.py` (Appendix A vector 2) |
| FR-2.1 | Club profiles (venue, socials, staff, history, roster) with white-label tenant branding and strict tenant isolation | `app/core/seed.py`, `app/core/seed_data.json`, `app/modules/app_api/routes.py` (`/api/v1/clubs`, tenant logo) | `tests/test_app_api.py`, `tests/test_rbac.py` |
| FR-2.2 | Roster: transfer / loan / release, 8 ordered rule checks, transfer windows, squad size, positions, loan auto-return, deferred transfers during in-progress matches, immutable audit trail (`/audit/v1/roster/*`) | `app/modules/roster/service.py`, `app/modules/roster/routes.py` | `tests/test_roster.py` |
| FR-2.3 | Merch store: catalogue with variants, discount codes, concurrency-safe checkout, mock gateway (authorize / capture / decline / capture-fail / refund), order state events, signed PDF receipts, pinned products on match pages | `app/modules/store/service.py`, `catalog.py`, `gateway.py`, `receipt.py`, `routes.py`, `app/core/queue.py`, `app/worker.py` | `tests/test_store.py`, `tests/test_store_app.py`, `tests/test_app_api.py` |
| FR-5.1 | Five-tier RBAC as a policy matrix with scoped ownership and tenant isolation; negative tests for every boundary (`/audit/v1/rbac-matrix`, probes P1-P7) | `app/modules/rbac/policy.py`, `app/modules/rbac/routes.py`, `app/core/auth.py` | `tests/test_rbac.py` |

## Non-functional requirements

| ID | Requirement | Source | Automated tests |
|---|---|---|---|
| NFR-3 | Concurrency: 20 simultaneous buyers of the last unit -> exactly one order, no negative stock | `app/modules/store/service.py` (order-id claim + conditional `UPDATE ... WHERE stock >= qty`) | `tests/test_store.py` (thread + barrier burst), `scripts/precheck.py` |
| NFR-4 | Offline tolerance: queued actions replayed in `queued_at` order with appended / rebased / duplicate / server-wins results; mobile cache + persistent queue | `app/modules/offline/service.py`, `app/modules/offline/routes.py`, `mobile/` | `tests/test_offline.py`, `tests/test_offline_scoring.py` |
| NFR-5 | Security: JWT with pinned algorithm and canonical segments (alg:none / tampered / expired -> 401), rate limiting (`/api/v1/*` 100 per 10 s), 1 MB body limit, input validation, parameterized SQL, Keystore token storage in the app | `app/core/auth.py`, `app/core/middleware.py`, `app/core/validate.py`, `app/core/errors.py` | `tests/test_core.py`, `tests/test_rbac.py`, `tests/test_store.py` |
| NFR-7 | Reproducibility: one command brings up and seeds the stack; reset is repeatable and identical | `docker-compose.yml`, `Dockerfile`, `app/main.py`, `app/core/routes.py`, `app/core/registry.py` | `tests/test_core.py`, `scripts/precheck.py` |
