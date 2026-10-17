import 'dart:async';

import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;

import 'storage.dart';

enum NetState { online, offline, syncing }

/// Online = the phone has a network AND the backend answered recently.
class Net extends ChangeNotifier {
  final Settings settings;
  Net(this.settings);

  bool hasNetwork = true;
  bool serverReachable = true;
  bool syncing = false;
  final List<VoidCallback> _backOnline = [];
  StreamSubscription? _sub;
  Timer? _timer;

  bool get online => hasNetwork && serverReachable;
  NetState get state =>
      syncing ? NetState.syncing : (online ? NetState.online : NetState.offline);

  void onBackOnline(VoidCallback cb) => _backOnline.add(cb);

  Future<void> start() async {
    try {
      final r = await Connectivity().checkConnectivity();
      hasNetwork = !_isNone(r);
      if (!hasNetwork) serverReachable = false;
    } catch (_) {}
    _sub = Connectivity().onConnectivityChanged.listen((r) {
      hasNetwork = !_isNone(r);
      if (!hasNetwork) {
        // Reachability must be re-proven after the network comes back,
        // so the back-online callbacks (auto-sync) always fire.
        serverReachable = false;
      }
      notifyListeners();
      if (hasNetwork) probe();
    });
    probe();
    // /health is outside the rate-limited /api/v1 group; 15 s keeps us well under any limit.
    _timer = Timer.periodic(const Duration(seconds: 15), (_) => probe());
  }

  bool _isNone(List<ConnectivityResult> r) =>
      r.isEmpty || r.every((c) => c == ConnectivityResult.none);

  Future<bool> probe() async {
    if (!hasNetwork) {
      _setReachable(false);
      return false;
    }
    try {
      final r = await http
          .get(Uri.parse('${settings.baseUrl}/health'))
          .timeout(const Duration(seconds: 5));
      _setReachable(r.statusCode < 500);
    } catch (_) {
      _setReachable(false);
    }
    return online;
  }

  void markReachable(bool ok) => _setReachable(ok);

  void _setReachable(bool ok) {
    final was = online;
    serverReachable = ok;
    if (was != online) {
      notifyListeners();
      if (online) {
        for (final cb in List.of(_backOnline)) {
          cb();
        }
      }
    }
  }

  void setSyncing(bool v) {
    if (syncing == v) return;
    syncing = v;
    notifyListeners();
  }

  @override
  void dispose() {
    _sub?.cancel();
    _timer?.cancel();
    super.dispose();
  }
}
