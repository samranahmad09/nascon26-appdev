import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'core/api.dart';
import 'core/cart.dart';
import 'core/net.dart';
import 'core/repo.dart';
import 'core/session.dart';
import 'core/storage.dart';
import 'core/sync.dart';
import 'screens/home_shell.dart';
import 'screens/login_screen.dart';
import 'ui/tokens.dart';

/// App-wide services (one instance each).
class Services {
  final settings = Settings();
  final session = Session();
  late final net = Net(settings);
  late final api = Api(settings, net, () => session.token ?? guest.token);
  final guest = GuestAuth();
  late final repo = Repo(api, session, settings);
  late final sync = SyncQueue(api, net, settings, session, repo);
  final cart = Cart();
  late final brand = BrandNotifier(session);

  Future<void> init() async {
    await LocalStore.init();
    settings.load();
    sync.load();
    await session.restore();
    brand.refresh();
    session.addListener(() {
      if (!session.loggedIn) navKey.currentState?.popUntil((r) => r.isFirst);
    });
    api.ensureGuest = () => guest.ensure(this);
    api.onGuestRejected = () => guest.token = null;
    api.onUnauthorized = () {
      session.logout(reason: 'Your session has ended. Please sign in again.');
    };
    await net.start();
    if (session.loggedIn) afterLogin();
  }

  /// Called after sign-in or on start with a saved session.
  void afterLogin() {
    Future(() async {
      if (await net.probe()) {
        try {
          final me = await api.get('/api/v1/me');
          if (me is Map<String, dynamic>) session.updateFromMe(me);
        } catch (_) {}
        await sync.syncNow(auto: true);
        await repo.prefetch();
      }
    });
  }
}

/// Anonymous read-only viewer token (guest_anon_tok_*) used when nobody is signed in.
class GuestAuth {
  String? token;
  Future<String?>? _inflight;

  Future<String?> ensure(Services s) {
    if (s.session.loggedIn || token != null) return Future.value(token);
    return _inflight ??= _fetch(s).whenComplete(() => _inflight = null);
  }

  Future<String?> _fetch(Services s) async {
    try {
      final res = await s.api.post('/api/v1/guest/token', const <String, dynamic>{}, auth: false);
      if (res is Map) {
        final t = res['token'] ?? res['guest_token'] ?? res['access_token'];
        if (t is String && t.isNotEmpty) token = t;
      }
    } catch (_) {}
    return token;
  }
}

late Services svc;
final navKey = GlobalKey<NavigatorState>();

class BrandNotifier extends ChangeNotifier {
  final Session session;
  Brand brand = Brand.fromTenant(null);
  BrandNotifier(this.session) {
    session.addListener(refresh);
  }
  void refresh() {
    brand = Brand.fromTenant(session.tenant);
    notifyListeners();
  }
}

class NasconApp extends StatelessWidget {
  const NasconApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MultiProvider(
      providers: [
        ChangeNotifierProvider.value(value: svc.settings),
        ChangeNotifierProvider.value(value: svc.session),
        ChangeNotifierProvider.value(value: svc.net),
        ChangeNotifierProvider.value(value: svc.repo),
        ChangeNotifierProvider.value(value: svc.sync),
        ChangeNotifierProvider.value(value: svc.cart),
        ChangeNotifierProvider.value(value: svc.brand),
      ],
      child: Consumer3<BrandNotifier, Session, Settings>(
        builder: (context, b, session, settings, _) => MaterialApp(
          title: 'NaSCon League',
          navigatorKey: navKey,
          debugShowCheckedModeBanner: false,
          theme: buildTheme(b.brand, Brightness.light),
          darkTheme: buildTheme(b.brand, Brightness.dark),
          themeMode: settings.themeMode,
          themeAnimationDuration: const Duration(milliseconds: 250),
          themeAnimationCurve: Curves.easeOutCubic,
          home: session.loggedIn
              ? HomeShell(key: ValueKey(session.userId))
              : const LoginScreen(),
        ),
      ),
    );
  }
}
