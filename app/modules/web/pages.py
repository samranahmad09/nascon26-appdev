"""Server-rendered HTML for /web/*. The first paint already carries the score, teams and pinned product, so the
player and banner are visible before any script runs. Every dynamic value goes through esc()."""
import html
import json

from fastapi.responses import HTMLResponse

HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}

ICONS = {
    "play": '<path d="M8 5v14l11-7z"/>',
    "pause": '<path d="M6 5h4v14H6zM14 5h4v14h-4z"/>',
    "muted": '<path d="M3 9v6h4l5 5V4L7 9H3zm13.6 3 2.7-2.7-1.4-1.4-2.7 2.7-2.7-2.7-1.4 1.4 2.7 2.7-2.7 2.7 1.4 1.4 '
             '2.7-2.7 2.7 2.7 1.4-1.4z"/>',
    "volume": '<path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3A4.5 4.5 0 0 0 14 8v8a4.5 4.5 0 0 0 2.5-4zM14 3.2v2.1a7 7 0 0 1 0 '
              '13.4v2.1a9 9 0 0 0 0-17.6z"/>',
    "full": '<path d="M4 4h6v2H6v4H4zm10 0h6v6h-2V6h-4zM4 14h2v4h4v2H4zm14 0h2v6h-6v-2h4z"/>',
    "cart": '<path d="M7 18a2 2 0 1 0 0 4 2 2 0 0 0 0-4zm10 0a2 2 0 1 0 0 4 2 2 0 0 0 0-4zM5.2 4l.6 2H21l-2 8H8.1l.5 2H19v2'
            'H7L4.3 6H2V4z"/>',
}


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def icon(name: str) -> str:
    return f'<svg class="icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">{ICONS[name]}</svg>'


def boot_json(data: dict) -> str:
    raw = json.dumps(data, separators=(",", ":"), default=str)
    return raw.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


def document(title: str, body: str, assets: dict, *, page: str, boot: dict | None = None, status: int = 200) -> HTMLResponse:
    script = ""
    if boot is not None:
        script = (f'<script type="application/json" id="boot">{boot_json(boot)}</script>\n'
                  f'<script src="{assets["player.js"]}" defer></script>')
    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0b1220">
<meta name="color-scheme" content="dark">
<title>{esc(title)} | NaSCon Live</title>
<link rel="stylesheet" href="{assets["player.css"]}">
</head>
<body class="page-{esc(page)}">
<a class="skip-link" href="#main">Skip to main content</a>
{body}
{script}
</body>
</html>"""
    return HTMLResponse(doc, status_code=status, headers=HEADERS)


def topbar(with_cart: bool) -> str:
    cart = ""
    if with_cart:
        cart = (f'<div class="cart-pill" id="cart-pill" role="status" aria-label="Items in your cart">{icon("cart")}'
                f'<span class="cart-label">Cart</span>'
                f'<span data-testid="guest-cart-count" id="cart-count" class="cart-count">0</span></div>')
    return f"""<header class="topbar">
  <span class="brand"><span class="brand-mark" aria-hidden="true">N</span>NaSCon Live</span>
  <div class="topbar-right">
    <span class="viewer-chip" id="viewer-chip">Watching as guest</span>
    {cart}
  </div>
</header>"""


STATUS_LABELS = {"in_progress": "Live", "upcoming": "Upcoming", "completed": "Full time", "postponed": "Postponed",
                 "cancelled": "Cancelled", "abandoned": "Abandoned"}

STAGE_COPY = {
    "live": "Stream starting soon",
    "preview": "Kick-off soon. The stream opens here when the match starts.",
    "vod": "The replay is being prepared.",
    "costream": "Waiting for the hosts to go live",
}


def _merch_card(product: dict | None) -> str:
    if not product:
        return """<div data-testid="pinned-merch-card" class="card merch" id="merch" aria-labelledby="merch-name">
  <p class="eyebrow">Club shop</p>
  <h2 class="merch-name" id="merch-name">Merchandise coming soon</h2>
  <p class="merch-meta"><span id="merch-variant"></span> <span id="merch-price"></span></p>
  <p class="merch-sku" id="merch-sku"></p>
  <div class="merch-actions">
    <button data-testid="add-to-guest-cart-btn" id="add-to-cart" class="btn primary" type="button" disabled>Add to cart</button>
    <button id="checkout" class="btn ghost" type="button">Checkout</button>
  </div>
  <p class="form-note" id="cart-note" role="status" aria-live="polite"></p>
</div>"""
    variant_id = product.get("cart_variant_id") or ""
    size = product.get("cart_variant_size")
    return f"""<div data-testid="pinned-merch-card" class="card merch" id="merch" aria-labelledby="merch-name"
     data-product-id="{esc(product.get("product_id"))}" data-variant-id="{esc(variant_id)}">
  <p class="eyebrow">Pinned by the club</p>
  <h2 class="merch-name" id="merch-name">{esc(product.get("name"))}</h2>
  <p class="merch-meta"><span id="merch-variant">{esc(f"Size {size}" if size else "")}</span>
    <span id="merch-price" class="merch-price">{esc(product.get("price_label"))}</span></p>
  <p class="merch-sku" id="merch-sku">{esc(product.get("product_id"))} / {esc(variant_id)}</p>
  <div class="merch-actions">
    <button data-testid="add-to-guest-cart-btn" id="add-to-cart" class="btn primary" type="button"
            aria-describedby="merch-name">{icon("cart")}<span>Add to cart</span></button>
    <button id="checkout" class="btn ghost" type="button">Checkout</button>
  </div>
  <p class="form-note" id="cart-note" role="status" aria-live="polite"></p>
</div>"""


def _scoreboard(match: dict | None) -> str:
    if not match:
        return """<div class="scoreboard" role="group" aria-label="Match score">
  <span class="team home"><span class="team-name" id="home-name">Home</span></span>
  <div class="score-wrap">
    <span class="status-chip" id="match-status" data-status="upcoming">Upcoming</span>
    <div data-testid="live-score-banner" id="score-banner" class="score" role="status" aria-live="polite" aria-atomic="true">0 - 0</div>
  </div>
  <span class="team away"><span class="team-name" id="away-name">Away</span></span>
</div>"""
    home, away, status = match["home"], match["away"], match["status"]
    return f"""<div class="scoreboard" role="group" aria-label="Match score">
  <a class="team home" href="/web/clubs/{esc(home["team_id"])}"><span class="team-name" id="home-name">{esc(home["name"])}</span></a>
  <div class="score-wrap">
    <span class="status-chip" id="match-status" data-status="{esc(status)}">{esc(STATUS_LABELS.get(status, status))}</span>
    <div data-testid="live-score-banner" id="score-banner" class="score" role="status" aria-live="polite" aria-atomic="true">{esc(match["score_text"])}</div>
  </div>
  <a class="team away" href="/web/clubs/{esc(away["team_id"])}"><span class="team-name" id="away-name">{esc(away["name"])}</span></a>
</div>"""


def _participants(costream: dict | None) -> str:
    if costream is None:
        return ""
    names = costream.get("participant_names") or {}
    items = "".join(f'<li>{esc(names.get(p, p))}{" (host)" if p == costream.get("active_host") else ""}</li>'
                    for p in costream.get("active_participants") or [])
    layout = costream.get("layout") or "grid_4"
    return f"""<div class="card costream-meta" aria-labelledby="hosts-title">
  <h2 id="hosts-title">On stream <span class="layout-chip" id="layout-chip">{esc(layout)}</span></h2>
  <ul class="hosts" id="hosts-list">{items or '<li class="muted">No hosts live yet</li>'}</ul>
</div>"""


def player_page(kind: str, entity_id: str, match: dict | None, product: dict | None, playback_url: str | None,
                chat_room_id: str, assets: dict, costream: dict | None = None) -> HTMLResponse:
    if match:
        teams = f'{match["home"]["name"]} vs {match["away"]["name"]}'
    else:
        teams = "Live match"
    title = f"Co-stream {entity_id}" if kind == "costream" else teams
    video_label = f"Co-stream video {entity_id}" if kind == "costream" else f"Match video: {teams}"
    boot = {
        "kind": kind, "entity_id": entity_id, "match_id": match["match_id"] if match else None,
        "status": match["status"] if match else None, "score_text": match["score_text"] if match else None,
        "playback_url": playback_url, "chat_room_id": chat_room_id, "product": product,
        "overlay_path": (f"/web/api/costream/{entity_id}" if kind == "costream"
                         else f"/web/api/matches/{entity_id}/overlay"),
        "hls_src": assets["hls.min.js"],
    }
    body = f"""{topbar(True)}
<main id="main" class="layout" tabindex="-1">
  <section class="stage" aria-label="Stream and score">
    <h1 class="page-title">{esc(title)}</h1>
    <div class="video-shell" id="video-shell">
      <video data-testid="guest-video-player" id="video" class="video" playsinline muted autoplay preload="metadata"
             aria-label="{esc(video_label)}"></video>
      <div class="stream-state" id="stream-state" role="status" aria-live="polite">
        <span class="pulse" aria-hidden="true"></span><span id="stream-state-text">{esc(STAGE_COPY[kind])}</span>
      </div>
    </div>
    <div class="controls" role="group" aria-label="Video controls">
      <button type="button" class="ctl" id="ctl-play" aria-label="Pause video">{icon("pause")}</button>
      <button type="button" class="ctl" id="ctl-mute" aria-label="Unmute video">{icon("muted")}</button>
      <button type="button" class="ctl ctl-live" id="ctl-live" aria-label="Jump to the live edge">Live</button>
      <button type="button" class="ctl" id="ctl-full" aria-label="Enter full screen">{icon("full")}</button>
    </div>
    {_scoreboard(match)}
    {_participants(costream)}
  </section>
  <aside class="side" aria-label="Shop and chat">
    {_merch_card(product)}
    <section class="card chat" aria-labelledby="chat-title">
      <h2 id="chat-title">Live chat</h2>
      <ul data-testid="chat-message-list" id="chat-list" class="chat-list" role="log" aria-live="polite"
          aria-label="Chat messages" tabindex="0"></ul>
      <form id="chat-form" class="chat-form" autocomplete="off" novalidate>
        <label for="chat-input" class="sr-only">Chat message</label>
        <input data-testid="chat-input-field" id="chat-input" class="input" type="text" maxlength="280"
               placeholder="Chat with fans" aria-label="Chat message">
        <button data-testid="chat-send-btn" id="chat-send" class="btn primary" type="submit"
                aria-label="Send chat message">Send</button>
      </form>
      <p class="form-note" id="chat-note" role="status" aria-live="polite"></p>
    </section>
  </aside>
</main>
<div data-testid="progressive-auth-modal" id="auth-modal" class="modal" role="dialog" aria-modal="true"
     aria-labelledby="auth-title" aria-describedby="auth-desc" hidden>
  <div class="modal-card">
    <h2 id="auth-title">Log in to continue</h2>
    <p id="auth-desc" class="muted">Guests can watch, follow the score and fill a cart. Log in to chat or check out:
      your cart moves to your account and the video picks up where you left it.</p>
    <form id="auth-form" class="auth-form" novalidate>
      <label for="auth-email">Email</label>
      <input id="auth-email" class="input" type="email" name="email" autocomplete="username" required>
      <label for="auth-password">Password</label>
      <input id="auth-password" class="input" type="password" name="password" autocomplete="current-password" required>
      <p class="form-error" id="auth-error" role="alert"></p>
      <button type="submit" class="btn primary" id="auth-submit">Log in and continue</button>
      <button type="button" class="btn ghost" id="auth-cancel">Keep watching as guest</button>
    </form>
  </div>
</div>"""
    return document(title, body, assets, page="player", boot=boot)


def notice_page(match: dict | None, match_id: str, state: str, assets: dict) -> HTMLResponse:
    label = STATUS_LABELS.get(state, "Schedule update")
    teams = f'{match["home"]["name"]} vs {match["away"]["name"]}' if match else match_id
    body = f"""{topbar(False)}
<main id="main" class="layout single" tabindex="-1">
  <section class="card notice" aria-labelledby="notice-title">
    <p class="eyebrow">{esc(teams)}</p>
    <h1 id="notice-title">Match {esc(label.lower())}</h1>
    <p class="muted">This match is <strong id="notice-state">{esc(state)}</strong>. Keep this page: the same QR code
      will open the new stream once the match is rescheduled.</p>
    <a class="btn ghost" href="/web/matches/{esc(match_id)}">Check the latest status</a>
  </section>
</main>"""
    return document(f"Match {label.lower()}", body, assets, page="notice")


def player_profile_page(player: dict, assets: dict) -> HTMLResponse:
    club = ""
    if player.get("club_id"):
        club = f'<a class="btn ghost" href="/web/clubs/{esc(player["club_id"])}">{esc(player.get("club_name") or player["club_id"])}</a>'
    number = f'#{esc(player["shirt_number"])} ' if player.get("shirt_number") is not None else ""
    body = f"""{topbar(False)}
<main id="main" class="layout single" tabindex="-1">
  <section class="card profile" aria-labelledby="player-name">
    <p class="eyebrow">Athlete</p>
    <h1 id="player-name">{number}{esc(player["name"])}</h1>
    <dl class="facts">
      <div><dt>Position</dt><dd>{esc((player.get("position") or "-").upper())}</dd></div>
      <div><dt>Role</dt><dd>{esc(player.get("role") or "-")}</dd></div>
    </dl>
    {club}
  </section>
</main>"""
    return document(player["name"], body, assets, page="profile")


def club_page(club: dict, assets: dict) -> HTMLResponse:
    rows = "".join(f'<li><a href="/web/players/{esc(p["player_id"])}">'
                   f'{esc(p["shirt_number"]) + " " if p.get("shirt_number") is not None else ""}{esc(p["name"])}</a>'
                   f'<span class="muted">{esc((p.get("position") or "").upper())}</span></li>'
                   for p in club["players"])
    venue = f'<p class="muted">Home ground: {esc(club["venue"])}</p>' if club.get("venue") else ""
    body = f"""{topbar(False)}
<main id="main" class="layout single" tabindex="-1">
  <section class="card profile" aria-labelledby="club-name">
    <p class="eyebrow">Club</p>
    <h1 id="club-name">{esc(club["name"])}</h1>
    {venue}
    <h2>Squad</h2>
    <ul class="squad">{rows or '<li class="muted">Squad not announced yet</li>'}</ul>
  </section>
</main>"""
    return document(club["name"], body, assets, page="club")


def missing_page(what: str, assets: dict) -> HTMLResponse:
    body = f"""{topbar(False)}
<main id="main" class="layout single" tabindex="-1">
  <section class="card notice" aria-labelledby="missing-title">
    <h1 id="missing-title">{esc(what)} not found</h1>
    <p class="muted">The link may be out of date. Scan the QR code at the venue again.</p>
  </section>
</main>"""
    return document(f"{what} not found", body, assets, page="missing", status=404)
