"""Settings and HTTP helpers shared by the Selenium tests (imported by conftest.py and the test modules)."""
import json
import os
import urllib.error
import urllib.request


def _first_reachable(env: str, candidates: tuple[tuple[str, str], ...]) -> str:
    """The env value if set; else the first candidate that answers (host run first, then inside compose)."""
    if os.environ.get(env):
        return os.environ[env].rstrip("/")
    for url, probe in candidates:
        try:
            with urllib.request.urlopen(url + probe, timeout=2):
                return url
        except urllib.error.HTTPError:
            return url
        except OSError:
            continue
    return candidates[0][0]


GRID_URL = _first_reachable("GRID_URL", (("http://localhost:4444", "/status"), ("http://selenium-hub:4444", "/status")))
API_URL = _first_reachable("API_URL", (("http://localhost:8080", "/health"), ("http://backend:8080", "/health")))
BASE_URL = os.environ.get("BASE_URL", "http://backend:8080").rstrip("/")
BROWSERS = [b.strip() for b in os.environ.get("SELENIUM_BROWSERS", "chrome,firefox").split(",") if b.strip()]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api(method: str, path: str, body=None):
    """(status, json) from the backend as seen by the test process."""
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(API_URL + path, data=data, method=method,
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            raw = res.read()
            return res.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as err:
        raw = err.read()
        try:
            return err.code, json.loads(raw) if raw else None
        except ValueError:
            return err.code, None


def raw_redirect(path: str):
    """(status, Location) of a GET without following redirects."""
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(API_URL + path, timeout=15) as res:
            return res.status, res.headers.get("Location")
    except urllib.error.HTTPError as err:
        return err.code, err.headers.get("Location")
