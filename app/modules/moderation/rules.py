"""Deterministic chat moderation rules (Section 5.8, FR-5.3). Pure functions, no I/O.

Profanity: each whitespace-delimited token is normalised (NFKC, lower case, leetspeak @->a 0->o 1->i 3->e
$->s, then every non-alphanumeric character such as _ . - * or a zero-width joiner is stripped) and searched
for a dictionary word. "!" is tried both as "i" (sh!t) and as a stripped separator (s!c@m). The offending
token is replaced in place with ***, keeping its leading/trailing punctuation and all whitespace
("sc@m!" -> "***!", "f.u.c.k.i.n.g" -> "***").

Flood: 4 or more consecutive identical characters (case-insensitive) or 3 or more consecutive ! / ? marks,
checked on the raw text.
"""
import re
import unicodedata

MASK = "***"
DICTIONARY = ("badword", "porn", "scam", "fuck", "shit", "bitch", "asshole", "idiot_spam")
LEET = {"@": "a", "0": "o", "1": "i", "3": "e", "$": "s"}

PROFANITY = "profanity_leetspeak"
FLOOD = "flood_character_repeat"
RATE_LIMIT = "rate_limit_exceeded"

_WORDS = tuple(sorted({"".join(c for c in w if c.isalnum()) for w in DICTIONARY}, key=len, reverse=True))
_REPEAT_RE = re.compile(r"(.)\1{3,}", re.IGNORECASE | re.DOTALL)
_MARKS_RE = re.compile(r"[!?]{3,}")
_TOKEN_RE = re.compile(r"\S+")


def is_flood(text: str) -> bool:
    return _REPEAT_RE.search(text) is not None or _MARKS_RE.search(text) is not None


def _normalise(token: str, bang: str) -> tuple[str, list[int]]:
    """Normalised token plus, for every normalised character, the index of the raw character it came from."""
    chars: list[str] = []
    index: list[int] = []
    for i, raw in enumerate(token):
        mapped = bang if raw == "!" else "".join(LEET.get(c, c) for c in unicodedata.normalize("NFKC", raw).lower())
        for c in mapped:
            if c.isalnum():
                chars.append(c)
                index.append(i)
    return "".join(chars), index


def _spans(token: str) -> list[tuple[int, int]]:
    spans = []
    for bang in ("i", ""):
        norm, index = _normalise(token, bang)
        for word in _WORDS:
            start = norm.find(word)
            while start != -1:
                spans.append((index[start], index[start + len(word) - 1] + 1))
                start = norm.find(word, start + 1)
    return spans


def _mask_token(token: str) -> str | None:
    """The offending token loses everything from its first to its last word character (or matched
    leetspeak character); leading and trailing punctuation stays."""
    spans = _spans(token)
    if not spans:
        return None
    alnum = [i for i, c in enumerate(token) if c.isalnum()]
    start = min([s for s, _ in spans] + alnum[:1])
    end = max([e for _, e in spans] + [alnum[-1] + 1] if alnum else [e for _, e in spans])
    return token[:start] + MASK + token[end:]


def mask(text: str) -> tuple[str, bool]:
    """Returns (text with every offending token masked, whether anything was masked)."""
    hit = False

    def repl(m: re.Match) -> str:
        nonlocal hit
        masked = _mask_token(m.group(0))
        if masked is None:
            return m.group(0)
        hit = True
        return masked

    return _TOKEN_RE.sub(repl, text), hit


def evaluate(text: str) -> tuple[str | None, str]:
    """(reason, text): FLOOD with the raw text, PROFANITY with the masked text, or (None, text) if clean."""
    if is_flood(text):
        return FLOOD, text
    masked, hit = mask(text)
    if hit:
        return PROFANITY, masked
    return None, text
