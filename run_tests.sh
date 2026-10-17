#!/usr/bin/env bash
# Runs the whole test suite inside the API image against the compose Postgres/Redis,
# then prints per-area line coverage in the COVERAGE.json format.
set -euo pipefail
cd "$(dirname "$0")"
export DAY1_SALT="${DAY1_SALT:-test-salt}"

docker compose up -d db redis
docker compose run --rm --build --no-deps -e DAY1_SALT api sh -c \
  "python -m pytest -q --cov=app --cov-report=term --cov-report=json:cov.json $* && echo 'COVERAGE.json figures:' && python scripts/coverage_areas.py cov.json"
