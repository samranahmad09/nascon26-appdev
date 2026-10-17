import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart' show ThemeMode;
import 'package:hive_flutter/hive_flutter.dart';
import 'package:uuid/uuid.dart';

/// Local database (Hive). Values are stored as JSON strings so no adapters are needed.
class LocalStore {
  static late Box<String> cache; // cached API reads
  static late Box<String> queue; // offline action queue (survives force-close)
  static late Box<String> meta; // settings and small state

  static Future<void> init() async {
    await Hive.initFlutter('nascon_db');
    cache = await Hive.openBox<String>('cache');
    queue = await Hive.openBox<String>('queue');
    meta = await Hive.openBox<String>('meta');
  }

  static Map<String, dynamic>? readJson(Box<String> box, String key) {
    final raw = box.get(key);
    if (raw == null) return null;
    try {
      final v = jsonDecode(raw);
      return v is Map<String, dynamic> ? v : null;
    } catch (_) {
      return null;
    }
  }

  static Future<void> writeJson(Box<String> box, String key, Object value) =>
      box.put(key, jsonEncode(value));
}

class Settings extends ChangeNotifier {
  static const defaultUrl = 'http://10.0.2.2:8080';

  String baseUrl = defaultUrl;
  bool useSampleData = false; // every app route is live; kept only as a developer fallback
  String deviceId = '';
  String? leagueId;
  ThemeMode themeMode = ThemeMode.system;

  void load() {
    final m = LocalStore.meta;
    baseUrl = m.get('base_url') ?? defaultUrl;
    useSampleData = (m.get('sample_data') ?? 'false') == 'true';
    leagueId = m.get('league_id');
    themeMode = switch (m.get('theme_mode')) {
      'light' => ThemeMode.light,
      'dark' => ThemeMode.dark,
      _ => ThemeMode.system,
    };
    var id = m.get('device_id');
    if (id == null || id.isEmpty) {
      id = 'dev-${const Uuid().v4().substring(0, 13)}';
      m.put('device_id', id);
    }
    deviceId = id;
  }

  static String normalizeUrl(String input) {
    var u = input.trim();
    if (u.isEmpty) return defaultUrl;
    if (!u.startsWith('http://') && !u.startsWith('https://')) u = 'http://$u';
    while (u.endsWith('/')) {
      u = u.substring(0, u.length - 1);
    }
    return u;
  }

  Future<void> setBaseUrl(String url) async {
    baseUrl = normalizeUrl(url);
    await LocalStore.meta.put('base_url', baseUrl);
    notifyListeners();
  }

  Future<void> setUseSampleData(bool v) async {
    useSampleData = v;
    await LocalStore.meta.put('sample_data', '$v');
    notifyListeners();
  }

  Future<void> setThemeMode(ThemeMode t) async {
    themeMode = t;
    await LocalStore.meta.put('theme_mode', t.name);
    notifyListeners();
  }

  Future<void> setLeague(String id) async {
    if (leagueId == id) return;
    leagueId = id;
    await LocalStore.meta.put('league_id', id);
    notifyListeners();
  }

  String get wsBase {
    if (baseUrl.startsWith('https://')) return 'wss://${baseUrl.substring(8)}';
    if (baseUrl.startsWith('http://')) return 'ws://${baseUrl.substring(7)}';
    return 'ws://$baseUrl';
  }
}
