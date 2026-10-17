import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:uuid/uuid.dart';

import 'api.dart';
import 'errors.dart';
import 'net.dart';
import 'repo.dart';
import 'session.dart';
import 'storage.dart';

String utcStamp([DateTime? t]) {
  final d = (t ?? DateTime.now()).toUtc();
  String two(int v) => v.toString().padLeft(2, '0');
  return '${d.year}-${two(d.month)}-${two(d.day)}T${two(d.hour)}:${two(d.minute)}:${two(d.second)}Z';
}

String newId(String prefix) => '$prefix-${const Uuid().v4()}';

/// One queued offline action. `action` is exactly the Section 5.14 format.
class QueuedAction {
  final Map<String, dynamic> action;
  final String label;
  final String by;
  QueuedAction(this.action, this.label, this.by);

  String get id => '${action['action_id']}';
  String get type => '${action['type']}';
  String get queuedAt => '${action['queued_at']}';

  Map<String, dynamic> toJson() => {'action': action, 'label': label, 'by': by};
  static QueuedAction fromJson(Map<String, dynamic> m) => QueuedAction(
      (m['action'] as Map).cast<String, dynamic>(), '${m['label']}', '${m['by']}');
}

class SyncResult {
  final String actionId;
  final String label;
  final String status; // applied | rejected | duplicate
  final String? resolution;
  final String? errorCode;
  final int? serverVersion;
  SyncResult(this.actionId, this.label, this.status, this.resolution,
      this.errorCode, this.serverVersion);

  String get reason {
    if (status == 'applied') {
      return resolution == 'rebased_append'
          ? 'Applied. Others had made changes meanwhile, so it was added after theirs.'
          : 'Applied on the server.';
    }
    if (status == 'duplicate') return 'Already on the server, so it was skipped (nothing changed).';
    return friendlyError(errorCode);
  }

  Map<String, dynamic> toJson() => {
        'action_id': actionId,
        'label': label,
        'status': status,
        'resolution': resolution,
        'error_code': errorCode,
        'server_version': serverVersion,
      };
  static SyncResult fromJson(Map<String, dynamic> m) => SyncResult(
      '${m['action_id']}',
      '${m['label']}',
      '${m['status']}',
      m['resolution'] as String?,
      m['error_code'] as String?,
      m['server_version'] as int?);
}

/// Persistent offline queue (Hive) + sync through POST /audit/v1/offline-sync.
class SyncQueue extends ChangeNotifier {
  final Api api;
  final Net net;
  final Settings settings;
  final Session session;
  final Repo repo;
  SyncQueue(this.api, this.net, this.settings, this.session, this.repo) {
    net.onBackOnline(() => syncNow(auto: true));
  }

  List<SyncResult> lastResults = [];
  String? lastSyncAt;
  String? lastError;
  bool _running = false;

  void load() {
    final m = LocalStore.readJson(LocalStore.meta, 'last_sync');
    if (m != null) {
      lastSyncAt = m['at'] as String?;
      lastResults = ((m['results'] as List?) ?? const [])
          .map((e) => SyncResult.fromJson((e as Map).cast<String, dynamic>()))
          .toList();
    }
  }

  List<QueuedAction> get pending {
    final list = LocalStore.queue.values
        .map((s) {
          try {
            return QueuedAction.fromJson(jsonDecode(s) as Map<String, dynamic>);
          } catch (_) {
            return null;
          }
        })
        .whereType<QueuedAction>()
        .toList();
    list.sort((a, b) => a.queuedAt.compareTo(b.queuedAt));
    return list;
  }

  int get pendingCount => LocalStore.queue.length;

  Future<QueuedAction> enqueue({
    required String type,
    required int baseVersion,
    required Map<String, dynamic> payload,
    required String label,
  }) async {
    final a = QueuedAction({
      'action_id': newId('act'),
      'type': type,
      'queued_at': utcStamp(),
      'base_version': baseVersion,
      'payload': payload,
    }, label, session.displayName);
    await LocalStore.queue.put(a.id, jsonEncode(a.toJson()));
    notifyListeners();
    return a;
  }

  Future<void> remove(String actionId) async {
    await LocalStore.queue.delete(actionId);
    notifyListeners();
  }

  /// Returns null on success, or a plain-language problem.
  Future<String?> syncNow({bool auto = false}) async {
    if (_running) return null;
    final items = pending;
    if (items.isEmpty) return null;
    if (!net.online) {
      final ok = await net.probe();
      if (!ok) return 'You\'re offline. The actions stay saved and will sync when you\'re back online.';
    }
    _running = true;
    lastError = null;
    net.setSyncing(true);
    notifyListeners();
    try {
      final results = <SyncResult>[];
      for (var i = 0; i < items.length; i += 100) {
        final chunk = items.sublist(i, i + 100 > items.length ? items.length : i + 100);
        final res = await api.post('/audit/v1/offline-sync', {
          'device_id': settings.deviceId,
          'actions': chunk.map((a) => a.action).toList(),
        }, timeout: const Duration(seconds: 30)) as Map<String, dynamic>;
        final byId = {for (final a in chunk) a.id: a};
        for (final r in (res['results'] as List? ?? const []).cast<Map>()) {
          final id = '${r['action_id']}';
          results.add(SyncResult(
            id,
            byId[id]?.label ?? id,
            '${r['status']}',
            r['resolution'] as String?,
            r['error_code'] as String?,
            r['server_version'] is int ? r['server_version'] as int : null,
          ));
          await _afterResult(byId[id], r);
          await LocalStore.queue.delete(id);
        }
      }
      lastResults = results;
      lastSyncAt = DateTime.now().toIso8601String();
      await LocalStore.writeJson(LocalStore.meta, 'last_sync', {
        'at': lastSyncAt,
        'results': results.map((r) => r.toJson()).toList(),
      });
      repo.bump();
      return null;
    } on ApiException catch (e) {
      lastError = e.isMissingRoute
          ? "The server can't accept synced actions yet. They stay saved on this phone; try again later."
          : '${e.message} Your actions stay saved on this phone.';
      return lastError;
    } finally {
      _running = false;
      net.setSyncing(false);
      notifyListeners();
    }
  }

  Future<void> _afterResult(QueuedAction? a, Map r) async {
    if (a == null) return;
    if (a.type == 'roster_transfer' && r['server_version'] is int) {
      final pid = '${(a.action['payload'] as Map)['player_id']}';
      await repo.setCachedHistoryLength(pid, r['server_version'] as int);
    }
  }

  Future<void> clearResults() async {
    lastResults = [];
    await LocalStore.meta.delete('last_sync');
    notifyListeners();
  }
}
