import threading

# The API runs as one uvicorn worker. Mutations of event logs, leagues, rosters, sports and
# offline-sync run under this lock so sequence numbers and idempotency checks never race.
# Checkout does NOT rely on it: stock is reserved with an atomic conditional UPDATE in Postgres.
GLOBAL = threading.RLock()
