from . import app_api, fixtures, offline, rbac, roster, scoring, sports, standings, store

# Order matters only for route matching; every module owns a disjoint set of paths.
MODULES = [sports, fixtures, scoring, standings, store, rbac, roster, offline, app_api]
