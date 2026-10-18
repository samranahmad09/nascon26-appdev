import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;

import 'errors.dart';
import 'net.dart';
import 'storage.dart';

/// Thin HTTP client. Every non-2xx body is {"error":{code,message,details}}.
class Api {
  final Settings settings;
  final Net net;
  String? Function() tokenOf;
  void Function()? onUnauthorized;
  /// Fetches an anonymous read-only guest token when nobody is signed in (FR-4.2).
  Future<String?> Function()? ensureGuest;
  void Function()? onGuestRejected;

  Api(this.settings, this.net, this.tokenOf);

  Uri uri(String path) => Uri.parse('${settings.baseUrl}$path');

  Map<String, String> _headers({bool auth = true, bool json = false}) {
    final h = <String, String>{'Accept': 'application/json'};
    if (json) h['Content-Type'] = 'application/json';
    final t = tokenOf();
    if (auth && t != null) h['Authorization'] = 'Bearer $t';
    return h;
  }

  Future<dynamic> get(String path, {bool auth = true}) async {
    if (auth && tokenOf() == null && ensureGuest != null) {
      try {
        await ensureGuest!();
      } catch (_) {}
    }
    return _send(() => http.get(uri(path), headers: _headers(auth: auth)),
        auth && path.startsWith('/api/v1'));
  }

  Future<dynamic> post(String path, Object body,
          {bool auth = true, Duration timeout = const Duration(seconds: 15)}) =>
      _send(
          () => http.post(uri(path),
              headers: _headers(auth: auth, json: true),
              body: jsonEncode(body)),
          auth && path.startsWith('/api/v1'),
          timeout: timeout);

  Future<dynamic> _send(Future<http.Response> Function() call, bool appRoute,
      {Duration timeout = const Duration(seconds: 12)}) async {
    if (!net.hasNetwork) throw ApiException.network();
    http.Response r;
    try {
      r = await call().timeout(timeout);
    } on TimeoutException {
      net.markReachable(false);
      throw ApiException.timeout();
    } on SocketException {
      net.markReachable(false);
      throw ApiException.network();
    } on http.ClientException {
      net.markReachable(false);
      throw ApiException.network();
    } on HandshakeException {
      net.markReachable(false);
      throw ApiException.network();
    }
    net.markReachable(true);
    dynamic body;
    try {
      body = r.bodyBytes.isEmpty ? null : jsonDecode(utf8.decode(r.bodyBytes));
    } on FormatException {
      body = null;
    }
    if (r.statusCode >= 200 && r.statusCode < 300) return body;
    final err = (body is Map && body['error'] is Map)
        ? Map<String, dynamic>.from(body['error'] as Map)
        : <String, dynamic>{};
    final ex = ApiException(
      status: r.statusCode,
      code: (err['code'] ?? 'HTTP_${r.statusCode}').toString(),
      serverMessage: (err['message'] ?? '').toString(),
      details: err['details'] is Map
          ? Map<String, dynamic>.from(err['details'] as Map)
          : <String, dynamic>{},
    );
    final usedGuest = (tokenOf() ?? '').startsWith('guest_');
    if (r.statusCode == 401 && usedGuest) {
      onGuestRejected?.call(); // expired guest token: fetch a fresh one next time
    } else if (r.statusCode == 401 && appRoute && tokenOf() != null) {
      onUnauthorized?.call();
    }
    throw ex;
  }
}
