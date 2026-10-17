import 'dart:convert';
import 'dart:math';

import 'package:crypto/crypto.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'api.dart';
import 'errors.dart';

/// Signed-in user. The token lives in flutter_secure_storage (Android Keystore).
class Session extends ChangeNotifier {
  static const _storage = FlutterSecureStorage();

  String? token;
  Map<String, dynamic>? user;
  Map<String, dynamic>? tenant;
  String? notice; // shown on the login screen (e.g. session ended)
  bool offlineLogin = false;

  bool get loggedIn => token != null && user != null;
  String get role => (user?['role'] ?? 'viewer').toString();
  String get userId => (user?['user_id'] ?? '').toString();
  String get displayName =>
      (user?['display_name'] ?? user?['email'] ?? 'User').toString();
  String? get clubId => user?['club_id']?.toString();
  String? get playerId => user?['player_id']?.toString();
  List<String> get leagueIds =>
      ((user?['league_ids'] as List?) ?? const []).map((e) => '$e').toList();
  String get tenantName => (tenant?['name'] ?? 'NaSCon League').toString();
  String get cacheScope => (user?['tenant_id'] ?? 'platform').toString();

  bool get isOrganizer => role == 'league_organizer' || role == 'super_admin';
  bool get isManager => role == 'club_manager' || role == 'super_admin';

  String get roleLabel => switch (role) {
        'super_admin' => 'Platform admin',
        'league_organizer' => 'League organizer',
        'club_manager' => 'Club manager',
        'player' => 'Player',
        _ => 'Viewer',
      };

  Future<void> restore() async {
    try {
      final raw = await _storage.read(key: 'session');
      if (raw == null) return;
      final m = jsonDecode(raw) as Map<String, dynamic>;
      token = m['token'] as String?;
      user = (m['user'] as Map?)?.cast<String, dynamic>();
      tenant = (m['tenant'] as Map?)?.cast<String, dynamic>();
    } catch (_) {
      token = null;
    }
  }

  Future<void> _save() => _storage.write(
      key: 'session',
      value: jsonEncode({'token': token, 'user': user, 'tenant': tenant}));

  /// Online login; when the server can't be reached, falls back to an account
  /// that signed in on this device before (password checked against a salted hash).
  Future<void> login(Api api, String email, String password) async {
    final e = email.trim().toLowerCase();
    try {
      final res = await api.post('/api/v1/auth/login',
          {'email': e, 'password': password},
          auth: false) as Map<String, dynamic>;
      token = res['token'] as String;
      user = (res['user'] as Map).cast<String, dynamic>();
      tenant = (res['tenant'] as Map?)?.cast<String, dynamic>();
      offlineLogin = false;
      notice = null;
      await _save();
      await _remember(e, password);
      notifyListeners();
    } on ApiException catch (ex) {
      if (!ex.network) rethrow;
      final ok = await _offlineLogin(e, password);
      if (!ok) {
        throw ApiException(
            status: 0,
            code: 'OFFLINE_LOGIN',
            network: true,
            serverMessage: '');
      }
    }
  }

  Future<Map<String, dynamic>> _accounts() async {
    try {
      final raw = await _storage.read(key: 'accounts');
      if (raw == null) return {};
      return (jsonDecode(raw) as Map).cast<String, dynamic>();
    } catch (_) {
      return {};
    }
  }

  String _hash(String salt, String pw) =>
      sha256.convert(utf8.encode('$salt|$pw')).toString();

  Future<void> _remember(String email, String pw) async {
    final all = await _accounts();
    final rnd = Random.secure();
    final salt = List.generate(16, (_) => rnd.nextInt(256))
        .map((b) => b.toRadixString(16).padLeft(2, '0'))
        .join();
    all[email] = {
      'salt': salt,
      'hash': _hash(salt, pw),
      'token': token,
      'user': user,
      'tenant': tenant,
    };
    await _storage.write(key: 'accounts', value: jsonEncode(all));
  }

  Future<List<String>> rememberedEmails() async => (await _accounts()).keys.toList();

  Future<bool> _offlineLogin(String email, String pw) async {
    final all = await _accounts();
    final a = all[email];
    if (a is! Map) return false;
    if (_hash('${a['salt']}', pw) != a['hash']) return false;
    final t = a['token'] as String?;
    if (t == null || _expired(t)) return false;
    token = t;
    user = (a['user'] as Map).cast<String, dynamic>();
    tenant = (a['tenant'] as Map?)?.cast<String, dynamic>();
    offlineLogin = true;
    notice = null;
    await _save();
    notifyListeners();
    return true;
  }

  bool _expired(String jwt) {
    try {
      final parts = jwt.split('.');
      final payload =
          jsonDecode(utf8.decode(base64Url.decode(base64Url.normalize(parts[1]))));
      final exp = payload['exp'];
      if (exp is num) {
        return DateTime.now().millisecondsSinceEpoch ~/ 1000 > exp;
      }
    } catch (_) {}
    return false;
  }

  void updateFromMe(Map<String, dynamic> me) {
    if (me['user'] is Map) user = (me['user'] as Map).cast<String, dynamic>();
    if (me.containsKey('tenant')) {
      tenant = (me['tenant'] as Map?)?.cast<String, dynamic>();
    }
    _save();
    notifyListeners();
  }

  Future<void> logout({String? reason}) async {
    token = null;
    user = null;
    tenant = null;
    notice = reason;
    await _storage.delete(key: 'session');
    notifyListeners();
  }
}
