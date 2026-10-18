"""Prometheus text exposition (NFR-6). Modules register value providers; the five required metric
families are always present (0 until a module provides a value)."""
import logging

from . import realtime

log = logging.getLogger("metrics")

REQUIRED = {
    "nascon_active_ws_connections": ("gauge", "Open WebSocket connections"),
    "nascon_active_streams_total": ("gauge", "Live ingest streams currently publishing"),
    "nascon_stream_bitrate_kbps": ("gauge", "Aggregate ingest bitrate across live streams in kbps"),
    "nascon_dlq_depth": ("gauge", "Notifications waiting in the dead-letter queue"),
    "nascon_qr_scans_total": ("counter", "Dynamic QR scans resolved since the last reset"),
}

_providers: dict[str, object] = {"nascon_active_ws_connections": realtime.active_connections}
_extra: dict[str, tuple[str, str]] = {}


def register(name: str, provider, kind: str = "gauge", help_text: str = "") -> None:
    """provider() returns a number, or a list of (labels_dict, number) samples."""
    _providers[name] = provider
    if name not in REQUIRED:
        _extra[name] = (kind, help_text or name)


def _fmt_labels(labels: dict) -> str:
    if not labels:
        return ""
    inner = ",".join(f'{k}="{str(v)}"'.replace("\\", "\\\\") for k, v in sorted(labels.items()))
    return "{" + inner + "}"


def render() -> str:
    lines: list[str] = []
    families = {**REQUIRED, **_extra}
    for name, (kind, help_text) in families.items():
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {kind}")
        provider = _providers.get(name)
        samples = [({}, 0)]
        if provider is not None:
            try:
                value = provider()
                samples = value if isinstance(value, list) else [({}, value)]
            except Exception:
                log.exception("metric %s provider failed", name)
        for labels, number in samples or [({}, 0)]:
            lines.append(f"{name}{_fmt_labels(labels)} {float(number):g}")
    return "\n".join(lines) + "\n"
