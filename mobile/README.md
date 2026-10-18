# NaSCon League (Android)

Flutter client for the league platform. Release build: `mobile/release/app-release.apk` (Android 10 / API 29+).

## Run it

1. Start the backend on the laptop (`docker compose up`), port 8080.
2. Install the APK, open the app, tap **Settings** (gear on the login screen) and set the server:
   emulator `http://10.0.2.2:8080`, phone `http://<laptop Wi-Fi IP>:8080`. The address is saved.
3. Sign in with a demo account (password `Nascon@26`); the login screen can fill them in.

Build: `flutter build apk --release` inside `mobile/`, then copy
`build/app/outputs/flutter-apk/app-release.apk` to `mobile/release/`.

## What is where

| Area | Code |
| --- | --- |
| Design tokens + theme from tenant branding | `lib/ui/tokens.dart` |
| Shared components (status pill, connection indicator, empty/loading/error states, cards, dialogs) | `lib/ui/widgets.dart` |
| HTTP client, plain-language error mapping | `lib/core/api.dart`, `lib/core/errors.dart` |
| Secure session (token in Android Keystore via flutter_secure_storage), offline re-login | `lib/core/session.dart` |
| Local database (Hive): cached reads, offline queue, settings | `lib/core/storage.dart`, `lib/core/repo.dart` |
| Offline queue in the Section 5.14 format + sync via `POST /audit/v1/offline-sync` | `lib/core/sync.dart` |
| Online / Offline / Syncing detection (connectivity + `/health` every 15 s) | `lib/core/net.dart` |
| Screens | `lib/screens/` |

## Offline behaviour (Section 8.4)

* Fixtures, standings, club profiles, match details and the catalogue are stored in Hive every time they load
  (and prefetched right after sign-in), and are shown with an "Offline: showing what was saved" banner.
* Scoring and roster actions taken offline (or when a request cannot reach the server) are queued as
  `{action_id, type, queued_at, base_version, payload}` with a device id generated once. They survive a force-close.
* When the server is reachable again the queue syncs by itself; the **Sync** screen (tap the status pill in any
  app bar) also has a **Sync now** button and shows each action as applied / rejected with a plain reason.
* An account that signed in on this phone before can sign in again while offline (salted hash check), so the
  organizer and the manager can both act during the airplane-mode check.

`lib/core/sample_data.dart` holds clearly labelled sample data used during development before the server routes
existed. It is off by default (`Settings.useSampleData`) and only ever applies to a route that 404s as missing.

## Phase II (Day 2)

* **Deep links**: `nascon://entity/{match-*|club-*|player-*|cs-session-*}` and `nascon://stream/{cs-session-*}`
  (exact intent-filter in `AndroidManifest.xml`). `MainActivity` hands the cold-start link and every
  warm-start link (`onNewIntent`) to Dart over the `nascon/links` channel; `lib/core/links.dart` routes them.
  Flutter's own deep linking is turned off so it cannot race the router.
* **Guest mode (FR-4.2)**: with nobody signed in, reads fetch an anonymous token from
  `POST /api/v1/guest/token` (`GuestAuth` in `lib/app.dart`). Deep links, QR scans and "Browse" never
  stop on the login screen; writes (chat, checkout) ask the user to sign in.
* **Match Live**: score over `ws://{host}/ws/matches/{id}`, video tile when the match has a playback URL.
* **Co-Stream (FR-3.3)**: `lib/screens/costream_screen.dart`; grid_2 / grid_4 / spotlight / composite views of HLS
  tiles (`lib/ui/video_tile.dart`, placeholder while a participant is not live) and the unified chat
  `chat-{session_id}` (history + `/api/v1/ws?topic=chat:{room}` + `POST /api/v1/chat`).
* **QR scanner**: `lib/screens/qr_scan_screen.dart` (mobile_scanner). Short URLs `/q/{code}` are resolved by
  following the server's 302; `nascon://` and `/web/...` URLs map straight to screens.
* **Go live (FR-3.2)**: `lib/screens/creator_screen.dart`: stream key generate / rotate / revoke via
  `POST /audit/v1/stream/key`, RTMP + WHIP ingest URLs, HLS preview, viewer QR. Publishing is done from OBS
  (RTMP or WHIP).
* **Appium ids**: key widgets carry `Semantics(identifier: ...)`, exposed to UiAutomator as resource-ids:
  `screen-match-live`, `match-score`, `screen-costream`, `costream-tile-{i}`, `chat-message-list`,
  `chat-input`, `chat-send`, `screen-qr-scanner`, `screen-creator`, `screen-player`, `login-guest`,
  `login-scan-qr`, and `title-{screen-title}` on every app bar title.
* Media URLs that say `localhost` are rewritten to the configured server host (or kept on 127.0.0.1 with
  `adb reverse tcp:8888 tcp:8888` over USB).
