import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app.dart';
import '../core/errors.dart';
import '../core/net.dart';
import '../core/session.dart';
import '../core/storage.dart';
import '../ui/tokens.dart';
import '../ui/widgets.dart';
import 'settings_screen.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});
  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _email = TextEditingController();
  final _password = TextEditingController();
  final _form = GlobalKey<FormState>();
  bool _busy = false;
  bool _hide = true;
  String? _error;

  static const demo = [
    ('League organizer', 'organizer@nascon.test', Icons.sports),
    ('Club manager', 'manager@nascon.test', Icons.groups),
    ('Player', 'player@nascon.test', Icons.person),
    ('Viewer', 'viewer@nascon.test', Icons.visibility),
    ('Platform admin', 'admin@nascon.test', Icons.admin_panel_settings),
  ];

  @override
  void initState() {
    super.initState();
    _error = svc.session.notice;
  }

  Future<void> _submit() async {
    if (!_form.currentState!.validate()) return;
    FocusScope.of(context).unfocus();
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await svc.session.login(svc.api, _email.text, _password.text);
      svc.afterLogin();
    } on ApiException catch (e) {
      setState(() => _error = e.message);
    } catch (e) {
      setState(() => _error = friendlyAny(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final net = context.watch<Net>();
    final settings = context.watch<Settings>();
    context.watch<Session>();
    final p = Theme.of(context).colorScheme.primary;
    final tt = Theme.of(context).textTheme;
    return Scaffold(
      appBar: AppBar(
        actions: [
          const NetIndicator(),
          IconButton(
            tooltip: 'Settings (server address)',
            icon: const Icon(Icons.settings_outlined),
            onPressed: () => Navigator.of(context)
                .push(MaterialPageRoute(builder: (_) => const SettingsScreen())),
          ),
        ],
      ),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(T.s24, 0, T.s24, T.s32),
          child: Constrained(
            child: Form(
              key: _form,
              child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                const Center(child: TenantBadge(size: 72)),
                const SizedBox(height: T.s16),
                Text('NaSCon League',
                    textAlign: TextAlign.center, style: tt.displaySmall?.copyWith(fontSize: 32)),
                const SizedBox(height: T.s8),
                Text('Fixtures, live scores, standings, the club store and team tools.',
                    textAlign: TextAlign.center, style: tt.bodyLarge),
                const SizedBox(height: T.s32),
                if (!net.online)
                  Padding(
                    padding: const EdgeInsets.only(bottom: T.s16),
                    child: _Notice(
                        Icons.cloud_off,
                        'You\'re offline. An account that signed in on this phone before can still sign in.',
                        Tone.warning),
                  ),
                TextFormField(
                  controller: _email,
                  keyboardType: TextInputType.emailAddress,
                  autofillHints: const [AutofillHints.email],
                  textInputAction: TextInputAction.next,
                  decoration: const InputDecoration(
                      labelText: 'Email', prefixIcon: Icon(Icons.mail_outline)),
                  validator: (v) => (v == null || !v.contains('@'))
                      ? 'Enter your email address'
                      : null,
                ),
                const SizedBox(height: T.s16),
                TextFormField(
                  controller: _password,
                  obscureText: _hide,
                  autofillHints: const [AutofillHints.password],
                  textInputAction: TextInputAction.done,
                  onFieldSubmitted: (_) => _submit(),
                  decoration: InputDecoration(
                    labelText: 'Password',
                    prefixIcon: const Icon(Icons.lock_outline),
                    suffixIcon: IconButton(
                      tooltip: _hide ? 'Show password' : 'Hide password',
                      icon: Icon(_hide ? Icons.visibility : Icons.visibility_off),
                      onPressed: () => setState(() => _hide = !_hide),
                    ),
                  ),
                  validator: (v) =>
                      (v == null || v.isEmpty) ? 'Enter your password' : null,
                ),
                if (_error != null) ...[
                  const SizedBox(height: T.s16),
                  _Notice(Icons.error_outline, _error!, Tone.danger),
                ],
                const SizedBox(height: T.s24),
                FilledButton.icon(
                  onPressed: _busy ? null : _submit,
                  style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(56)),
                  icon: _busy
                      ? SizedBox(
                          width: 20,
                          height: 20,
                          child: CircularProgressIndicator(
                              strokeWidth: 2, color: onColor(p)))
                      : const Icon(Icons.login),
                  label: Text(_busy ? 'Signing in…' : 'Sign in'),
                ),
                const SizedBox(height: T.s16),
                Row(children: [
                  Icon(Icons.dns_outlined, size: 20, color: context.pal.inkMuted),
                  const SizedBox(width: T.s8),
                  Expanded(
                    child: Text('Server: ${settings.baseUrl}',
                        style: tt.bodySmall, overflow: TextOverflow.ellipsis),
                  ),
                  TextButton(
                    onPressed: () => Navigator.of(context).push(
                        MaterialPageRoute(builder: (_) => const SettingsScreen())),
                    child: const Text('Change'),
                  ),
                ]),
                const SizedBox(height: T.s8),
                Card(
                  child: ExpansionTile(
                    shape: const Border(),
                    leading: const Icon(Icons.badge_outlined),
                    title: const Text('Demo accounts'),
                    subtitle: const Text('Tap one to fill the form'),
                    children: [
                      for (final d in demo)
                        ListTile(
                          leading: Icon(d.$3, color: p),
                          title: Text(d.$1),
                          subtitle: Text(d.$2),
                          onTap: () => setState(() {
                            _email.text = d.$2;
                            _password.text = 'Nascon@26';
                            _error = null;
                          }),
                        ),
                    ],
                  ),
                ),
              ]),
            ),
          ),
        ),
      ),
    );
  }
}

class _Notice extends StatelessWidget {
  final IconData icon;
  final String text;
  final Tone tone;
  const _Notice(this.icon, this.text, this.tone);
  @override
  Widget build(BuildContext context) {
    final (fg, bg) = toneColors(context, tone);
    return Semantics(
      liveRegion: true,
      child: Container(
        padding: const EdgeInsets.all(T.s12),
        decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(T.r12)),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Icon(icon, color: fg),
          const SizedBox(width: T.s12),
          Expanded(
              child: Text(text,
                  style: TextStyle(color: fg, fontWeight: T.bold, fontSize: T.meta))),
        ]),
      ),
    );
  }
}
