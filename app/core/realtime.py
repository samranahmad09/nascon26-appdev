"""In-process WebSocket fan-out. publish() is safe to call from sync route handlers (threadpool)."""
import asyncio

_loop: asyncio.AbstractEventLoop | None = None
_subs: dict[str, set] = {}


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def subscribe(topic: str, ws) -> None:
    _subs.setdefault(topic, set()).add(ws)


def unsubscribe(ws) -> None:
    for subs in _subs.values():
        subs.discard(ws)


async def _broadcast(topic: str, message: dict) -> None:
    targets = set(_subs.get(topic, ())) | set(_subs.get("*", ()))
    for ws in targets:
        try:
            await ws.send_json({"topic": topic, **message})
        except Exception:
            unsubscribe(ws)


def publish(topic: str, message: dict) -> None:
    loop = _loop
    if loop is None or loop.is_closed():
        return
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        loop.create_task(_broadcast(topic, message))
    else:
        loop.call_soon_threadsafe(lambda: loop.create_task(_broadcast(topic, message)))
