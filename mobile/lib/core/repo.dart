import 'package:flutter/foundation.dart';

import 'api.dart';
import 'errors.dart';
import 'sample_data.dart';
import 'session.dart';
import 'storage.dart';

/// Result of a read: fresh from the server, from the local database, or sample data.
class Loaded {
  final Map<String, dynamic> data;
  final bool fromCache;
  final bool sample;
  final DateTime? savedAt;
  const Loaded(this.data, {this.fromCache = false, this.sample = false, this.savedAt});
}

/// Network-first reads with a local-database fallback (offline tolerance).
class Repo extends ChangeNotifier {
  final Api api;
  final Session session;
  final Settings settings;
  Repo(this.api, this.session, this.settings);

  /// Bumped after writes/sync so open screens reload.
  int revision = 0;
  void bump() {
    revision++;
    notifyListeners();
  }

  String _key(String path) => '${session.cacheScope}|$path';

  Future<Loaded> read(String path, Map<String, dynamic> Function()? sample) async {
    try {
      final res = await api.get(path);
      final data = res is Map<String, dynamic> ? res : <String, dynamic>{'items': res};
      await LocalStore.writeJson(LocalStore.cache, _key(path), {
        'saved_at': DateTime.now().toIso8601String(),
        'data': data,
      });
      return Loaded(data);
    } on ApiException catch (e) {
      if (e.isMissingRoute && settings.useSampleData && sample != null) {
        return Loaded(sample(), sample: true);
      }
      if (e.network || e.status >= 500 || e.status == 429) {
        final c = cached(path);
        if (c != null) return c;
        if (e.network) {
          throw ApiException(status: 0, code: 'NOT_SAVED_OFFLINE', network: true);
        }
      }
      rethrow;
    }
  }

  Loaded? cached(String path) {
    final c = LocalStore.readJson(LocalStore.cache, _key(path));
    if (c == null || c['data'] is! Map) return null;
    return Loaded((c['data'] as Map).cast<String, dynamic>(),
        fromCache: true, savedAt: DateTime.tryParse('${c['saved_at']}'));
  }

  Future<Loaded> leagues() => read('/api/v1/leagues', SampleData.leagues);
  Future<Loaded> fixtures(String leagueId) =>
      read('/api/v1/leagues/$leagueId/fixtures', () => SampleData.fixtures(leagueId));
  Future<Loaded> match(String matchId) =>
      read('/api/v1/matches/$matchId', () => SampleData.match(matchId));
  Future<Loaded> standings(String leagueId) =>
      read('/api/v1/leagues/$leagueId/standings', () => SampleData.standings(leagueId));
  Future<Loaded> clubs() => read('/api/v1/clubs', SampleData.clubs);
  Future<Loaded> club(String clubId) =>
      read('/api/v1/clubs/$clubId', () => SampleData.club(clubId));
  Future<Loaded> products() => read('/api/v1/products', SampleData.products);

  /// Saves a fresh match projection (e.g. from the WebSocket) into the cache.
  Future<void> storeMatch(String matchId, Map<String, dynamic> data) =>
      LocalStore.writeJson(LocalStore.cache, _key('/api/v1/matches/$matchId'), {
        'saved_at': DateTime.now().toIso8601String(),
        'data': data,
      });

  /// Roster history length = base_version for an offline roster action.
  Future<int?> rosterHistoryLength(String playerId) async {
    final path = '/audit/v1/roster/history/$playerId';
    try {
      final res = await api.get(path) as Map<String, dynamic>;
      final n = (res['history'] as List?)?.length ?? 0;
      await LocalStore.meta.put('hist|$playerId', '$n');
      return n;
    } catch (_) {
      final v = LocalStore.meta.get('hist|$playerId');
      return v == null ? null : int.tryParse(v);
    }
  }

  int cachedHistoryLength(String playerId) =>
      int.tryParse(LocalStore.meta.get('hist|$playerId') ?? '') ?? 0;

  Future<void> setCachedHistoryLength(String playerId, int n) =>
      LocalStore.meta.put('hist|$playerId', '$n');

  /// Warm the local database right after sign-in so the offline desk check works
  /// even for screens the user has not opened yet.
  Future<void> prefetch() async {
    try {
      final ls = await leagues();
      final leagueList = (ls.data['leagues'] as List? ?? const []).cast<Map>();
      final ids = <String>{
        ...session.leagueIds,
        ...leagueList.map((l) => '${l['league_id']}'),
      }.take(4);
      for (final id in ids) {
        final f = await fixtures(id);
        await standings(id).catchError((_) => const Loaded({}));
        if (session.isOrganizer) {
          for (final m in (f.data['matches'] as List? ?? const []).cast<Map>()) {
            if (m['status'] == 'in_progress' || m['status'] == 'upcoming') {
              await match('${m['match_id']}').catchError((_) => const Loaded({}));
            }
          }
        }
      }
      await clubs();
      final cid = session.clubId;
      if (cid != null) {
        final c = await club(cid);
        if (session.isManager) {
          for (final p in (c.data['roster'] as List? ?? const []).cast<Map>()) {
            await rosterHistoryLength('${p['player_id']}');
          }
        }
      }
      await products();
    } catch (_) {
      // best effort
    }
  }
}
