"""Turns coverage.py JSON into the per-area figures COVERAGE.json declares (Section 9.3)."""
import json
import sys

AREAS = {
    "fixtures": ["app/modules/fixtures/"],
    "scoring": ["app/modules/scoring/", "app/modules/sports/"],
    "standings": ["app/modules/standings/"],
    "store": ["app/modules/store/"],
    "roster": ["app/modules/roster/", "app/modules/offline/"],
    "rbac": ["app/modules/rbac/", "app/core/auth.py", "app/core/middleware.py"],
}


DAY2_AREAS = {
    "streaming": ["app/modules/streaming/"],
    "costream": ["app/modules/costream/"],
    "qr_guest": ["app/modules/qr_guest/"],
    "moderation_dlq": ["app/modules/moderation/"],
    "observability": ["app/modules/observability/", "app/core/metrics.py"],
}


def main(path: str, areas: dict = AREAS) -> None:
    files = json.load(open(path))["files"]
    result = {}
    for area, prefixes in areas.items():
        stmts = covered = 0
        for name, data in files.items():
            if any(name.replace("\\", "/").startswith(p) for p in prefixes):
                stmts += data["summary"]["num_statements"]
                covered += data["summary"]["covered_lines"]
        result[area] = round(100.0 * covered / stmts, 1) if stmts else 0.0
    print(json.dumps(result))


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "cov.json"
    main(path)
    print("COVERAGE_DAY2.json figures:")
    main(path, DAY2_AREAS)
