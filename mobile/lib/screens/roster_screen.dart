import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../app.dart';
import '../core/cart.dart';
import '../core/errors.dart';
import '../core/net.dart';
import '../core/repo.dart';
import '../core/session.dart';
import '../core/sync.dart';
import '../ui/motion.dart';
import '../ui/tokens.dart';
import '../ui/widgets.dart';
import 'club_screen.dart';
import 'home_shell.dart';
import 'queue_screen.dart';

String _ymd(DateTime d) => DateFormat('yyyy-MM-dd').format(d);

class RosterScreen extends StatefulWidget {
  final bool standalone;
  const RosterScreen({super.key, this.standalone = false});
  @override
  State<RosterScreen> createState() => _RosterScreenState();
}

class _RosterScreenState extends State<RosterScreen> {
  String? clubId;

  @override
  void initState() {
    super.initState();
    clubId = svc.session.clubId;
  }

  @override
  Widget build(BuildContext context) {
    context.watch<Session>();
    context.watch<SyncQueue>();
    return Scaffold(
      appBar: brandBar(context, 'Roster',
          actions: widget.standalone ? const [] : const [ProfileButton()]),
      body: clubId == null
          ? _ClubPicker(onPick: (id) => setState(() => clubId = id))
          : LoadedView(
              key: ValueKey('roster-$clubId'),
              loadingText: 'Loading your squad…',
              load: () async {
                final c = await svc.repo.club(clubId!);
                // Keep base_version (history length) fresh for offline actions.
                if (!c.fromCache && svc.net.online) {
                  for (final p in (c.data['roster'] as List? ?? const []).cast<Map>()) {
                    svc.repo.rosterHistoryLength('${p['player_id']}');
                  }
                }
                return c;
              },
              builder: (context, loaded, refresh) => _RosterBody(loaded: loaded, clubId: clubId!),
            ),
    );
  }
}

class _ClubPicker extends StatelessWidget {
  final void Function(String) onPick;
  const _ClubPicker({required this.onPick});
  @override
  Widget build(BuildContext context) => LoadedView(
        loadingText: 'Loading clubs…',
        load: svc.repo.clubs,
        builder: (context, loaded, _) {
          final clubs = (loaded.data['clubs'] as List? ?? const []).cast<Map>();
          return ListView(padding: const EdgeInsets.all(T.s16), children: [
            Text('Choose a club to manage', style: Theme.of(context).textTheme.titleLarge),
            const SizedBox(height: T.s12),
            for (final c in clubs)
              Padding(
                padding: const EdgeInsets.only(bottom: T.s8),
                child: SoftCard(
                  onTap: () => onPick('${c['club_id']}'),
                  child: Row(children: [
                    const Icon(Icons.shield_outlined),
                    const SizedBox(width: T.s12),
                    Expanded(child: Text('${c['name']}', style: Theme.of(context).textTheme.titleMedium)),
                    const Icon(Icons.chevron_right),
                  ]),
                ),
              ),
          ]);
        },
      );
}

class _RosterBody extends StatelessWidget {
  final Loaded loaded;
  final String clubId;
  const _RosterBody({required this.loaded, required this.clubId});

  @override
  Widget build(BuildContext context) {
    final c = loaded.data;
    final roster = (c['roster'] as List? ?? const []).cast<Map>();
    final pending = svc.sync.pending.where((a) => a.type == 'roster_transfer').toList();
    final pendingByPlayer = <String, QueuedAction>{
      for (final a in pending) '${(a.action['payload'] as Map)['player_id']}': a
    };
    final tt = Theme.of(context).textTheme;
    return Constrained(
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.only(bottom: T.s32),
        children: [
          DataBanner(loaded),
          Padding(
            padding: const EdgeInsets.fromLTRB(T.s16, T.s16, T.s16, 0),
            child: Text('${c['name'] ?? clubId}', style: tt.titleLarge),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(T.s16, T.s4, T.s16, 0),
            child: Text('Tap a player to transfer, loan or release them. Changes take effect today.',
                style: tt.bodyMedium),
          ),
          if (pending.isNotEmpty)
            Padding(
              padding: const EdgeInsets.fromLTRB(T.s16, T.s12, T.s16, 0),
              child: SoftCard(
                onTap: () => Navigator.of(context).push(MaterialPageRoute(
                    builder: (_) => const QueueScreen())),
                child: Row(children: [
                  Icon(Icons.schedule_send, color: context.pal.warning),
                  const SizedBox(width: T.s12),
                  Expanded(
                    child: Text(
                        '${pending.length} roster change${pending.length == 1 ? '' : 's'} waiting to sync',
                        style: tt.titleMedium),
                  ),
                ]),
              ),
            ),
          SectionTitle('Squad', trailing: Text('${roster.length} players', style: tt.bodySmall)),
          if (roster.isEmpty)
            const EmptyState(
                icon: Icons.group_off_outlined,
                title: 'No players yet',
                message: 'Players registered to this club will be listed here.')
          else
            for (final p in roster)
              FadeSlideIn(index: roster.indexOf(p), child: Padding(
                padding: const EdgeInsets.fromLTRB(T.s16, 0, T.s16, T.s8),
                child: SoftCard(
                  padding: const EdgeInsets.symmetric(vertical: T.s4),
                  semanticLabel:
                      '${p['name']}, ${positionLabel(p['position'] as String?)}, ${roleLabel(p['role'] as String?)}'
                      '${pendingByPlayer.containsKey('${p['player_id']}') ? ', change waiting to sync' : ''}. Opens roster actions.',
                  onTap: () => _actions(context, p.cast<String, dynamic>(), c),
                  child: ExcludeSemantics(
                    child: ListTile(
                      leading: CircleAvatar(
                        backgroundColor:
                            Theme.of(context).colorScheme.primary.withValues(alpha: 0.10),
                        child: Text('${p['shirt_number'] ?? '–'}',
                            style: TextStyle(
                                fontWeight: T.bold,
                                color: Theme.of(context).colorScheme.primary)),
                      ),
                      title: Text('${p['name']}', style: tt.titleMedium),
                      subtitle: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        Text('${positionLabel(p['position'] as String?)} · ${roleLabel(p['role'] as String?)}'),
                        if (pendingByPlayer.containsKey('${p['player_id']}')) ...[
                          const SizedBox(height: T.s4),
                          StatusPill(
                              Icons.schedule_send,
                              '${titleCase('${(pendingByPlayer['${p['player_id']}']!.action['payload'] as Map)['type']}')} waiting to sync',
                              Tone.warning),
                        ],
                      ]),
                      trailing: const Icon(Icons.more_vert),
                    ),
                  ),
                ),
              )),
        ],
      ),
    );
  }

  Future<void> _actions(BuildContext context, Map<String, dynamic> p, Map club) async {
    final choice = await showModalBottomSheet<String>(
      context: context,
      builder: (c) => SafeArea(
        child: SingleChildScrollView(
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(T.s24, 0, T.s24, T.s8),
              child: Text('${p['name']}', style: Theme.of(c).textTheme.titleLarge),
            ),
            _sheetTile(c, 'transfer', Icons.swap_horiz, 'Transfer',
                'Move permanently to another club'),
            _sheetTile(c, 'loan', Icons.event_repeat, 'Loan',
                'Lend to another club until a return date'),
            _sheetTile(c, 'release', Icons.person_remove_outlined, 'Release',
                'Remove from the league', danger: true),
            _sheetTile(c, 'history', Icons.history, 'View history',
                'Every past move of this player'),
            const SizedBox(height: T.s8),
          ]),
        ),
      ),
    );
    if (choice == null || !context.mounted) return;
    if (choice == 'history') {
      _history(context, p);
      return;
    }
    await showDialog(
      context: context,
      builder: (_) => _MoveDialog(type: choice, player: p, club: club),
    );
  }

  Widget _sheetTile(BuildContext c, String v, IconData icon, String title, String sub,
          {bool danger = false}) =>
      ListTile(
        minTileHeight: 64,
        leading: Icon(icon, color: danger ? c.pal.danger : Theme.of(c).colorScheme.primary),
        title: Text(title,
            style: TextStyle(
                fontWeight: T.bold, fontSize: T.body, color: danger ? c.pal.danger : c.pal.ink)),
        subtitle: Text(sub),
        onTap: () => Navigator.pop(c, v),
      );

  void _history(BuildContext context, Map p) {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      builder: (c) => DraggableScrollableSheet(
        expand: false,
        initialChildSize: 0.6,
        builder: (c, scroll) => FutureBuilder(
          future: svc.api.get('/audit/v1/roster/history/${p['player_id']}'),
          builder: (c, snap) {
            if (snap.connectionState != ConnectionState.done) {
              return const LoadingState('Loading history…');
            }
            if (snap.hasError) {
              return EmptyState(
                  icon: Icons.cloud_off, title: 'History unavailable', message: friendlyAny(snap.error!));
            }
            final h = ((snap.data as Map?)?['history'] as List? ?? const []).cast<Map>();
            svc.repo.setCachedHistoryLength('${p['player_id']}', h.length);
            if (h.isEmpty) {
              return const EmptyState(
                  icon: Icons.history, title: 'No moves yet', message: 'This player has never been moved.');
            }
            return ListView(controller: scroll, padding: const EdgeInsets.all(T.s16), children: [
              Text('${p['name']}: history', style: Theme.of(c).textTheme.titleLarge),
              const SizedBox(height: T.s8),
              for (final e in h)
                ListTile(
                  leading: const Icon(Icons.swap_horiz),
                  title: Text(titleCase('${e['action']}')),
                  subtitle: Text(
                      '${e['from_squad_id'] ?? '—'} → ${e['to_squad_id'] ?? 'left the league'} · ${e['effective_date'] ?? ''}'
                      '${(e['why'] ?? '').toString().isNotEmpty ? '\n${e['why']}' : ''}'),
                ),
            ]);
          },
        ),
      ),
    );
  }
}

class _MoveDialog extends StatefulWidget {
  final String type;
  final Map<String, dynamic> player;
  final Map club;
  const _MoveDialog({required this.type, required this.player, required this.club});
  @override
  State<_MoveDialog> createState() => _MoveDialogState();
}

class _MoveDialogState extends State<_MoveDialog> {
  String? dest;
  DateTime loanEnd = DateTime.now().add(const Duration(days: 30));
  late final reason = TextEditingController(
      text: switch (widget.type) {
    'transfer' => 'Tactical move',
    'loan' => 'Loan for match practice',
    _ => 'Released by the club',
  });
  List<Map> clubs = [];
  bool busy = false;

  String get fromSquad =>
      '${widget.player['squad_id'] ?? widget.club['club_id']}';

  @override
  void initState() {
    super.initState();
    _loadClubs();
  }

  Future<void> _loadClubs() async {
    try {
      final l = await svc.repo.clubs();
      final all = (l.data['clubs'] as List? ?? const []).cast<Map>();
      final tenant = widget.club['tenant_id'];
      setState(() {
        clubs = all
            .where((c) => '${c['club_id']}' != fromSquad && (tenant == null || c['tenant_id'] == tenant))
            .toList();
        if (clubs.isNotEmpty) dest = '${clubs.first['club_id']}';
      });
    } catch (_) {
      setState(() {
        clubs = [
          {'club_id': fromSquad == 'club-alpha-2' ? 'club-alpha-1' : 'club-alpha-2', 'name': fromSquad == 'club-alpha-2' ? 'club-alpha-1' : 'club-alpha-2'}
        ];
        dest = '${clubs.first['club_id']}';
      });
    }
  }

  String _clubName(String? id) =>
      '${clubs.firstWhere((c) => '${c['club_id']}' == id, orElse: () => {'name': id})['name']}';

  String get verb => switch (widget.type) {
        'transfer' => 'Transfer',
        'loan' => 'Loan',
        _ => 'Release',
      };

  Future<void> _submit() async {
    final name = '${widget.player['name']}';
    final today = DateTime.now();
    final summary = switch (widget.type) {
      'transfer' => '$name will move permanently to ${_clubName(dest)} from today.',
      'loan' =>
        '$name will play for ${_clubName(dest)} until ${DateFormat('d MMM yyyy').format(loanEnd)}, then return.',
      _ => '$name will leave the league today. This can\'t be undone from the app.',
    };
    final ok = await confirm(context,
        title: '$verb $name?',
        message: summary,
        confirmLabel: verb,
        danger: widget.type == 'release',
        icon: widget.type == 'release' ? Icons.warning_amber_rounded : Icons.swap_horiz);
    if (!ok || !mounted) return;
    final pid = '${widget.player['player_id']}';
    final body = {
      'request_id': newId('tr'),
      'league_id': '${widget.club['league_id'] ?? 'league-alpha-1'}',
      'player_id': pid,
      'type': widget.type,
      'from_squad_id': fromSquad,
      'to_squad_id': widget.type == 'release' ? null : dest,
      'effective_date': _ymd(today),
      'loan_end_date': widget.type == 'loan' ? _ymd(loanEnd) : null,
      'reason': reason.text.trim().isEmpty ? verb : reason.text.trim(),
      'requested_by': svc.session.userId,
    };
    final label = widget.type == 'release'
        ? 'Release $name'
        : '$verb $name → ${_clubName(dest)}';
    setState(() => busy = true);
    final messenger = ScaffoldMessenger.of(context);
    final nav = Navigator.of(context);
    final snackFg = context.pal.onSnack;
    try {
      if (svc.net.online) {
        try {
          final res = await svc.api.post('/audit/v1/roster/transfer', body) as Map;
          final n = svc.repo.cachedHistoryLength(pid);
          await svc.repo.setCachedHistoryLength(pid, n + 1);
          svc.repo.bump();
          nav.pop();
          final deferred = res['state'] == 'deferred_until_match_end';
          _toast(messenger, Icons.check_circle, snackFg,
              deferred ? '$label: accepted. It takes effect when the live match ends.' : '$label: done.');
          return;
        } on ApiException catch (e) {
          if (!e.network) {
            setState(() => busy = false);
            if (mounted) showError(context, e.message);
            return;
          }
        }
      }
      await svc.sync.enqueue(
        type: 'roster_transfer',
        baseVersion: svc.repo.cachedHistoryLength(pid),
        payload: body,
        label: label,
      );
      nav.pop();
      _toast(messenger, Icons.schedule_send, snackFg,
          'You\'re offline, so "$label" was saved on this phone. It will sync when you\'re back online.');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  void _toast(ScaffoldMessengerState m, IconData icon, Color c, String msg) {
    m.hideCurrentSnackBar();
    m.showSnackBar(SnackBar(
      content: Row(children: [
        Icon(icon, color: c),
        const SizedBox(width: T.s12),
        Expanded(child: Text(msg)),
      ]),
    ));
  }

  @override
  Widget build(BuildContext context) {
    final net = context.watch<Net>();
    return AlertDialog(
      title: Text('$verb ${widget.player['name']}'),
      scrollable: true,
      content: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        if (!net.online)
          const Padding(
            padding: EdgeInsets.only(bottom: T.s12),
            child: StatusPill(Icons.cloud_off, 'Offline: this will be queued', Tone.warning),
          ),
        if (widget.type != 'release') ...[
          DropdownButtonFormField<String>(
            initialValue: dest,
            isExpanded: true,
            decoration: const InputDecoration(labelText: 'To club', prefixIcon: Icon(Icons.shield_outlined)),
            items: [
              for (final c in clubs)
                DropdownMenuItem(value: '${c['club_id']}', child: Text('${c['name']}', overflow: TextOverflow.ellipsis)),
            ],
            onChanged: (v) => setState(() => dest = v),
          ),
          const SizedBox(height: T.s12),
        ],
        if (widget.type == 'loan') ...[
          OutlinedButton.icon(
            onPressed: () async {
              final d = await showDatePicker(
                context: context,
                initialDate: loanEnd,
                firstDate: DateTime.now().add(const Duration(days: 1)),
                lastDate: DateTime.now().add(const Duration(days: 365)),
                helpText: 'Loan ends on',
              );
              if (d != null) setState(() => loanEnd = d);
            },
            icon: const Icon(Icons.event),
            label: Text('Returns after ${DateFormat('d MMM yyyy').format(loanEnd)}'),
          ),
          const SizedBox(height: T.s12),
        ],
        TextField(
          controller: reason,
          decoration: const InputDecoration(labelText: 'Reason', prefixIcon: Icon(Icons.notes)),
        ),
      ]),
      actions: [
        TextButton(onPressed: busy ? null : () => Navigator.pop(context), child: const Text('Cancel')),
        FilledButton(
          style: widget.type == 'release' ? FilledButton.styleFrom(backgroundColor: context.pal.danger) : null,
          onPressed: busy || (widget.type != 'release' && dest == null) ? null : _submit,
          child: busy
              ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))
              : Text(net.online ? verb : '$verb offline'),
        ),
      ],
    );
  }
}
