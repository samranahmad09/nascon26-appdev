import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:http/http.dart' as http;

import '../app.dart';
import '../screens/club_screen.dart';
import '../screens/costream_screen.dart';
import '../screens/entity_screen.dart';
import '../screens/match_screen.dart';
import '../ui/widgets.dart';

/// Routes nascon:// links (intent-filter), NaSCon QR short URLs (/q/{code})
/// and web-player URLs (/web/...) to the matching native screen.
class DeepLinks {
  static const _channel = MethodChannel('nascon/links');

  /// Call once after runApp: handles the cold-start link and later warm-start links.
  static void init() {
    _channel.setMethodCallHandler((call) async {
      if (call.method == 'link' && call.arguments is String) {
        await open(call.arguments as String);
      }
    });
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      try {
        final link = await _channel.invokeMethod<String>('initialLink');
        if (link != null && link.isNotEmpty) await open(link);
      } catch (_) {}
    });
  }

  static final _webPath = RegExp(r'/web/(matches|players|clubs|costream)/([^/?#]+)');

  /// nascon://entity/match-m1, nascon://stream/cs-session-101, http://host/q/m1-live,
  /// http://host/web/matches/match-m1/live -> "match-m1" / "cs-session-101" / ...
  static Future<String?> entityOf(String raw) async {
    final uri = Uri.tryParse(raw.trim());
    if (uri == null) return null;
    if (uri.scheme == 'nascon') {
      final parts = [uri.host, ...uri.pathSegments].where((p) => p.isNotEmpty).toList();
      return parts.isEmpty ? null : parts.last;
    }
    if (uri.scheme == 'http' || uri.scheme == 'https') {
      final direct = _webPath.firstMatch(uri.path);
      if (direct != null) return direct.group(2);
      final q = uri.pathSegments.indexOf('q');
      if (q >= 0 && q + 1 < uri.pathSegments.length) {
        return _resolveShortCode(uri.pathSegments[q + 1]);
      }
    }
    return null;
  }

  /// Follows the server's 302 for a short code (asked of our configured server,
  /// since printed codes say "localhost").
  static Future<String?> _resolveShortCode(String code) async {
    final client = http.Client();
    try {
      final req = http.Request('GET', svc.api.uri('/q/$code'))..followRedirects = false;
      final res = await client.send(req).timeout(const Duration(seconds: 6));
      final loc = res.headers['location'];
      if (loc == null) return null;
      return _webPath.firstMatch(Uri.parse(loc).path)?.group(2);
    } catch (_) {
      return null;
    } finally {
      client.close();
    }
  }

  static Widget? screenFor(String id) {
    if (id.startsWith('cs-session-')) return CoStreamScreen(sessionId: id);
    if (id.startsWith('club-')) return ClubProfileScreen(clubId: id, name: 'Club');
    if (id.startsWith('player-')) return EntityScreen(entityId: id);
    if (id.startsWith('match-') || id.startsWith('demo-') || id.startsWith('m-')) {
      return MatchScreen(matchId: id);
    }
    return null;
  }

  /// Opens the screen for a link. Returns false (and tells the user) when it isn't ours.
  static Future<bool> open(String raw) async {
    final id = await entityOf(raw);
    final screen = id == null ? null : screenFor(id);
    final nav = navKey.currentState;
    if (screen == null || nav == null) {
      final ctx = navKey.currentContext;
      if (ctx != null && ctx.mounted) {
        showError(ctx, "That code isn't a NaSCon match, club, player or co-stream link.");
      }
      return false;
    }
    nav.push(MaterialPageRoute(builder: (_) => screen));
    return true;
  }
}
