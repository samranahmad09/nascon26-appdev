# Traceability Matrix

Every functional and non-functional requirement mapped to the source that implements it and the automated tests that verify it.

| ID | Requirement | Source | Tests |
|---|---|---|---|
| FR-1.1 | Multi-sport taxonomy, mystery sport from JSON, event-sourced scoring | `app/modules/sports/`, `app/modules/scoring/` | `tests/test_sports.py`, `tests/test_scoring.py` |
| FR-1.2 | League and tournament setup | `app/modules/standings/` | `tests/test_standings.py` |
| FR-1.3 | Fixture scheduling (round robin, Swiss, single/double elimination) | `app/modules/fixtures/` | `tests/test_fixtures.py` |
| FR-1.4 | Standings, tie-breakers, H2H, qualification status | `app/modules/standings/` | `tests/test_standings.py` |
| FR-2.1 | Club profiles and tenant isolation | `app/core/seed.py`, `app/modules/app_api/` | `tests/test_rbac.py` |
| FR-2.2 | Roster, transfers, loans, audit trail | `app/modules/roster/` | `tests/test_roster.py` |
| FR-2.3 | Merchandise store, mock gateway, PDF receipts | `app/modules/store/` | `tests/test_store.py` |
| FR-5.1 | Five-tier RBAC with tenant isolation | `app/modules/rbac/`, `app/core/auth.py` | `tests/test_rbac.py` |
| NFR-3 | Concurrency correctness (no overselling) | `app/modules/store/` | `tests/test_store.py` |
| NFR-4 | Offline tolerance and sync | `app/modules/offline/`, `mobile/` | `tests/test_offline.py` |
| NFR-5 | Security: JWT, rate limit, validation, 413 | `app/core/auth.py`, `app/core/middleware.py`, `app/core/validate.py` | `tests/test_core.py`, `tests/test_rbac.py` |
| NFR-7 | One-command reproducible stack | `docker-compose.yml`, `Dockerfile`, `app/core/routes.py` | `tests/test_core.py` |
