import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:provider/provider.dart';

import '../app.dart';
import '../core/session.dart';
import '../core/storage.dart';
import '../core/sync.dart';
import '../ui/tokens.dart';
import '../ui/widgets.dart';
import 'queue_screen.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});
  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  late final _url = TextEditingController(text: svc.settings.baseUrl);
  bool _testing = false;

  Future<bool> _test(String url) async {
    try {
      final r = await http
          .get(Uri.parse('${Settings.normalizeUrl(url)}/health'))
          .timeout(const Duration(seconds: 5));
      return r.statusCode < 500;
    } catch (_) {
      return false;
    }
  }

  Future<void> _save() async {
    setState(() => _testing = true);
    final ok = await _test(_url.text);
    await svc.settings.setBaseUrl(_url.text);
    _url.text = svc.settings.baseUrl;
    await svc.net.probe();
    if (!mounted) return;
    setState(() => _testing = false);
    if (ok) {
      showSuccess(context, 'Saved. The server answered at ${svc.settings.baseUrl}.');
    } else {
      showError(context,
          'Saved, but the server didn\'t answer at ${svc.settings.baseUrl}. Check the address and that the phone is on the same Wi-Fi.');
    }
  }

  @override
  Widget build(BuildContext context) {
    final session = context.watch<Session>();
    final settings = context.watch<Settings>();
    final q = context.watch<SyncQueue>();
    final tt = Theme.of(context).textTheme;
    return Scaffold(
      appBar: brandBar(context, session.loggedIn ? 'Profile & settings' : 'Settings'),
      body: Constrained(
        child: ListView(padding: const EdgeInsets.all(T.s16), children: [
          if (session.loggedIn) ...[
            SoftCard(
              child: Row(children: [
                CircleAvatar(
                  radius: 28,
                  backgroundColor: Theme.of(context).colorScheme.primary.withValues(alpha: 0.12),
                  child: Text(session.displayName.isEmpty ? '?' : session.displayName[0].toUpperCase(),
                      style: TextStyle(
                          fontSize: T.title,
                          fontWeight: T.bold,
                          color: Theme.of(context).colorScheme.primary)),
                ),
                const SizedBox(width: T.s16),
                Expanded(
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text(session.displayName, style: tt.titleMedium),
                    Text('${session.user?['email'] ?? ''}', style: tt.bodySmall),
                    const SizedBox(height: T.s8),
                    Wrap(spacing: T.s8, runSpacing: T.s8, children: [
                      StatusPill(Icons.verified_user_outlined, session.roleLabel, Tone.brand),
                      StatusPill(Icons.apartment, session.tenantName, Tone.neutral),
                      if (session.offlineLogin)
                        const StatusPill(Icons.cloud_off, 'Signed in offline', Tone.warning),
                    ]),
                  ]),
                ),
              ]),
            ),
            const SizedBox(height: T.s12),
            SoftCard(
              padding: EdgeInsets.zero,
              onTap: () => Navigator.of(context)
                  .push(MaterialPageRoute(builder: (_) => const QueueScreen())),
              child: ListTile(
                minTileHeight: 64,
                leading: const Icon(Icons.sync),
                title: const Text('Sync and offline actions'),
                subtitle: Text(q.pendingCount == 0
                    ? 'Nothing waiting to sync'
                    : '${q.pendingCount} waiting to sync'),
                trailing: const Icon(Icons.chevron_right),
              ),
            ),
          ],
          const SectionTitle('Server address'),
          Text('On the emulator use http://10.0.2.2:8080. On a phone use the laptop\'s Wi-Fi address, e.g. http://192.168.1.20:8080.',
              style: tt.bodyMedium),
          const SizedBox(height: T.s12),
          TextField(
            controller: _url,
            keyboardType: TextInputType.url,
            autocorrect: false,
            decoration: const InputDecoration(
                labelText: 'Server URL', prefixIcon: Icon(Icons.dns_outlined)),
            onSubmitted: (_) => _save(),
          ),
          const SizedBox(height: T.s12),
          FilledButton.icon(
            onPressed: _testing ? null : _save,
            icon: _testing
                ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.save_outlined),
            label: Text(_testing ? 'Checking the server…' : 'Save and test connection'),
          ),
          const SectionTitle('Data'),
          SoftCard(
            padding: EdgeInsets.zero,
            child: SwitchListTile(
              value: settings.useSampleData,
              onChanged: svc.settings.setUseSampleData,
              title: const Text('Use sample data for missing features'),
              subtitle: const Text(
                  'Only when the server has no such screen yet. Sample screens are clearly labelled.'),
            ),
          ),
          const SizedBox(height: T.s8),
          Text('Device ID: ${settings.deviceId}', style: tt.bodySmall),
          if (session.loggedIn) ...[
            const SizedBox(height: T.s32),
            OutlinedButton.icon(
              style: OutlinedButton.styleFrom(
                foregroundColor: T.danger,
                side: const BorderSide(color: T.danger, width: 1.5),
                minimumSize: const Size.fromHeight(52),
              ),
              onPressed: () async {
                final ok = await confirm(context,
                    title: 'Log out?',
                    message: q.pendingCount == 0
                        ? 'You\'ll need your password to sign in again.'
                        : '${q.pendingCount} saved action${q.pendingCount == 1 ? '' : 's'} will stay on this phone and sync after the next sign-in.',
                    confirmLabel: 'Log out',
                    danger: true,
                    icon: Icons.logout);
                if (ok) {
                  svc.cart.clear();
                  await svc.session.logout();
                }
              },
              icon: const Icon(Icons.logout),
              label: const Text('Log out'),
            ),
          ],
        ]),
      ),
    );
  }
}
