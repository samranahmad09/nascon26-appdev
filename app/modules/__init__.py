from . import (app_api, costream, fixtures, moderation, observability, offline, qr_guest, rbac, roster, scoring,
               social, sports, standings, store, streaming, web)

# Order matters only for route matching; every module owns a disjoint set of paths.
MODULES = [sports, fixtures, scoring, standings, store, rbac, roster, offline, app_api,
           streaming, costream, qr_guest, moderation, observability, web, social]
