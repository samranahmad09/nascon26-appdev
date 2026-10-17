import datetime as dt
import json
import re

from fastapi import Request

from .config import MAX_BODY_BYTES
from .errors import ApiError, validation_error

ID_RE = re.compile(r"[A-Za-z0-9_.:\-]{1,64}")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


async def json_body(request: Request):
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise ApiError(413, "PAYLOAD_TOO_LARGE", "Request body exceeds 1 MB.")
    if not raw.strip():
        return None
    try:
        data = json.loads(raw)
        storable = _storable(data)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise validation_error(["body"], "Request body is not valid JSON or is nested too deeply.")
    if not storable:
        fields = ["body"]
        if isinstance(data, dict) and all(_storable(k) for k in data):
            fields = [k for k, v in data.items() if not _storable(v)]
        raise validation_error(fields, "Text may not contain NUL characters or unpaired surrogates.")
    return data


def _storable(value) -> bool:
    """Postgres text cannot hold NUL and UTF-8 cannot encode lone surrogates; reject them up front
    so hostile strings become a 400 instead of a 500 deep inside a module."""
    if isinstance(value, str):
        if "\x00" in value:
            return False
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return False
        return True
    if isinstance(value, dict):
        return all(_storable(k) and _storable(v) for k, v in value.items())
    if isinstance(value, list):
        return all(_storable(v) for v in value)
    return True


def is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def is_id(value) -> bool:
    return isinstance(value, str) and ID_RE.fullmatch(value) is not None


def parse_date(value):
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Validator:
    def __init__(self, body, allow_empty: bool = False):
        if body is None and allow_empty:
            body = {}
        if not isinstance(body, dict):
            raise validation_error(["body"], "Request body must be a JSON object.")
        self.body = body
        self.errors: list[str] = []

    def bad(self, field: str) -> None:
        if field not in self.errors:
            self.errors.append(field)

    def _missing(self, key, required, nullable):
        if key not in self.body or self.body[key] is None:
            if required and not (nullable and key in self.body):
                self.bad(key)
            return True
        return False

    def string(self, key, required=True, min_len=1, max_len=None, regex=None, nullable=False):
        if self._missing(key, required, nullable):
            return None
        v = self.body[key]
        if not isinstance(v, str) or len(v) < min_len or (max_len is not None and len(v) > max_len):
            self.bad(key)
            return None
        if regex is not None and not re.fullmatch(regex, v):
            self.bad(key)
            return None
        return v

    def ident(self, key, required=True, nullable=False):
        if self._missing(key, required, nullable):
            return None
        v = self.body[key]
        if not is_id(v):
            self.bad(key)
            return None
        return v

    def integer(self, key, required=True, minimum=None, maximum=None, nullable=False):
        if self._missing(key, required, nullable):
            return None
        v = self.body[key]
        if not is_int(v) or (minimum is not None and v < minimum) or (maximum is not None and v > maximum):
            self.bad(key)
            return None
        return v

    def boolean(self, key, required=True, nullable=False):
        if self._missing(key, required, nullable):
            return None
        v = self.body[key]
        if not isinstance(v, bool):
            self.bad(key)
            return None
        return v

    def obj(self, key, required=True, nullable=False):
        if self._missing(key, required, nullable):
            return None
        v = self.body[key]
        if not isinstance(v, dict):
            self.bad(key)
            return None
        return v

    def array(self, key, required=True, min_len=0, max_len=None, nullable=False):
        if self._missing(key, required, nullable):
            return None
        v = self.body[key]
        if not isinstance(v, list) or len(v) < min_len or (max_len is not None and len(v) > max_len):
            self.bad(key)
            return None
        return v

    def enum(self, key, choices, required=True, nullable=False):
        if self._missing(key, required, nullable):
            return None
        v = self.body[key]
        if not isinstance(v, str) or v not in choices:
            self.bad(key)
            return None
        return v

    def date(self, key, required=True, nullable=False):
        if self._missing(key, required, nullable):
            return None
        d = parse_date(self.body[key])
        if d is None:
            self.bad(key)
        return d

    def check(self) -> None:
        if self.errors:
            raise validation_error(self.errors)
