import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app.dart';
import '../core/cart.dart';
import '../core/errors.dart';
import '../core/net.dart';
import '../core/repo.dart';
import '../core/storage.dart';
import '../core/sync.dart';
import '../ui/tokens.dart';
import '../ui/widgets.dart';
import 'fixtures_screen.dart';
import 'home_shell.dart';
import 'match_screen.dart';

List<String> scoringTypes(Map m) {
  final se = m['scoring_events'];
  if (se is List) {
    return se
        .map((e) => e is Map ? '${e['event_type'] ?? e['name'] ?? e['type'] ?? e['id']}' : '$e')
        .toList();
  }
  if (se is Map) return se.keys.map((k) => '$k').toList();
  return const [];
}

/// Organizer: pick a match, then add a score or cancel an earlier event.
class ScoringScreen extends StatelessWidget {
  final bool standalone;
  const ScoringScreen({super.key, this.standalone = false});

  @override
  Widget build(BuildContext context) {
    final settings = context.watch<Settings>();
    return Scaffold(
      appBar: brandBar(context, 'Scoring',
          actions: standalone ? const [] : const [ProfileButton()]),
      body: LoadedView(
        key: ValueKey('sc-${settings.leagueId}'),
        loadingText: 'Loading matches…',
        load: () => loadForLeague(svc.repo.fixtures),
        builder: (context, loaded, refresh) {
          final leagues = (loaded.data['_leagues'] as List).cast<Map>();
          final matches = (loaded.data['matches'] as List? ?? const []).cast<Map>().toList();
          int rank(Map m) => switch ('${m['status']}') {
                'in_progress' => 0,
                'upcoming' => 1,
                _ => 2,
              };
          matches.sort((a, b) => rank(a).compareTo(rank(b)));
          return Constrained(
            child: ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.only(bottom: T.s32),
              children: [
                DataBanner(loaded),
                LeaguePicker(leagues),
                Padding(
                  padding: const EdgeInsets.fromLTRB(T.s16, T.s12, T.s16, 0),
                  child: Text('Choose a match to score. Live matches are listed first.',
                      style: Theme.of(context).textTheme.bodyMedium),
                ),
                if (matches.isEmpty)
                  const Padding(
                    padding: EdgeInsets.only(top: T.s48),
                    child: EmptyState(
                        icon: Icons.scoreboard_outlined,
                        title: 'No matches yet',
                        message: 'Matches of your league will appear here to score.'),
                  )
                else
                  for (final m in matches)
                    Padding(
                      padding: const EdgeInsets.fromLTRB(T.s16, T.s12, T.s16, 0),
                      child: SoftCard(
                        semanticLabel:
                            '${(m['home'] as Map?)?['name']} versus ${(m['away'] as Map?)?['name']}. Opens scoring.',
                        onTap: () => Navigator.of(context).push(MaterialPageRoute(
                            builder: (_) => ScoreEntryScreen(matchId: '${m['match_id']}', summary: m))),
                        child: ExcludeSemantics(
                          child: Row(children: [
                            Expanded(
                              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                                Text('${(m['home'] as Map?)?['name']} v ${(m['away'] as Map?)?['name']}',
                                    style: Theme.of(context).textTheme.titleMedium,
                                    maxLines: 2,
                                    overflow: TextOverflow.ellipsis),
                                const SizedBox(height: T.s8),
                                matchStatusPill('${m['status']}'),
                              ]),
                            ),
                            const Icon(Icons.edit_note, size: 28),
                          ]),
                        ),
                      ),
                    ),
              ],
            ),
          );
        },
      ),
    );
  }
}

class ScoreEntryScreen extends StatefulWidget {
  final String matchId;
  final Map? summary;
  const ScoreEntryScreen({super.key, required this.matchId, this.summary});
  @override
  State<ScoreEntryScreen> createState() => _ScoreEntryScreenState();
}

class _ScoreEntryScreenState extends State<ScoreEntryScreen> {
  Loaded? loaded;
  Object? error;
  bool loading = true;
  bool busy = false;
  String? team; // team_id

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final l = await svc.repo.match(widget.matchId);
      if (!mounted) return;
      setState(() {
        loaded = l;
        loading = false;
        error = null;
        team ??= '${(l.data['home'] as Map?)?['team_id']}';
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        loading = false;
        error = e;
      });
    }
  }

  Map get m => loaded!.data;

  Map<String, String> get names => {
        '${(m['home'] as Map?)?['team_id']}':
            '${m['home_name'] ?? (widget.summary?['home'] as Map?)?['name'] ?? 'Home'}',
        '${(m['away'] as Map?)?['team_id']}':
            '${m['away_name'] ?? (widget.summary?['away'] as Map?)?['name'] ?? 'Away'}',
      };

  /// Version the device last saw = server version + actions queued on top of it.
  int get baseVersion => (m['version'] as num?)?.toInt() ?? (m['events'] as List? ?? const []).length;

  Future<void> _send(Map<String, dynamic> body, String label) async {
    setState(() => busy = true);
    try {
      if (svc.net.online) {
        try {
          await svc.api.post('/audit/v1/events', body);
          if (!mounted) return;
          showSuccess(context, '$label: saved.');
          svc.repo.bump();
          await _load();
          return;
        } on ApiException catch (e) {
          if (!e.network) {
            if (mounted) showError(context, e.message);
            return;
          }
        }
      }
      await svc.sync.enqueue(
          type: 'score_event', baseVersion: baseVersion, payload: body, label: label);
      if (mounted) {
        showInfo(context,
            'You\'re offline, so "$label" was saved on this phone. It will sync when you\'re back online.');
      }
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> _score(String type) async {
    final teamName = names[team] ?? 'team';
    final label = '${titleCase(type)} for $teamName';
    final ok = await confirm(context,
        title: 'Add ${titleCase(type)}?',
        message: '$label will be added to the match record.',
        confirmLabel: 'Add',
        icon: Icons.sports_score);
    if (!ok) return;
    await _send({
      'event_id': newId('evt'),
      'match_id': widget.matchId,
      'type': 'score',
      'payload': {'team_id': team, 'event_type': type},
    }, label);
  }

  Future<void> _compensate(Map e) async {
    final reason = TextEditingController(text: 'Scoring error');
    final title = eventTitle(e, names);
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        icon: const Icon(Icons.undo, color: T.danger, size: 32),
        title: const Text('Cancel this event?', textAlign: TextAlign.center),
        scrollable: true,
        content: Column(mainAxisSize: MainAxisSize.min, children: [
          Text('"$title" (#${e['sequence']}) will be cancelled by a correction. The original stays in the log, struck through.'),
          const SizedBox(height: T.s16),
          TextField(
            controller: reason,
            decoration: const InputDecoration(labelText: 'Reason', prefixIcon: Icon(Icons.notes)),
          ),
        ]),
        actionsAlignment: MainAxisAlignment.spaceBetween,
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Keep it')),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: T.danger),
            onPressed: () => Navigator.pop(c, true),
            child: const Text('Cancel event'),
          ),
        ],
      ),
    );
    if (ok != true) return;
    await _send({
      'event_id': newId('evt'),
      'match_id': widget.matchId,
      'type': 'compensation',
      'payload': {
        'compensates': '${e['event_id']}',
        'reason': reason.text.trim().isEmpty ? 'Correction' : reason.text.trim(),
      },
    }, 'Cancel "$title"');
  }

  Future<void> _transition(String type) async {
    final start = type == 'match_start';
    final ok = await confirm(context,
        title: start ? 'Start the match?' : 'End the match?',
        message: start
            ? 'Scores can be added once the match has started.'
            : 'No more scores can be added after the match ends. The result goes into the standings.',
        confirmLabel: start ? 'Start match' : 'End match',
        danger: !start,
        icon: start ? Icons.play_circle_outline : Icons.flag_outlined);
    if (!ok) return;
    await _send({
      'event_id': newId('evt'),
      'match_id': widget.matchId,
      'type': type,
      'payload': <String, dynamic>{},
    }, start ? 'Start match' : 'End match');
  }

  @override
  Widget build(BuildContext context) {
    context.watch<SyncQueue>();
    context.watch<Net>();
    return Scaffold(
      appBar: brandBar(context, 'Score entry'),
      body: loading
          ? const LoadingState('Loading match…')
          : loaded == null
              ? ErrorState(error ?? ApiException.network(), () {
                  setState(() => loading = true);
                  _load();
                })
              : RefreshIndicator(onRefresh: _load, child: _body(context)),
    );
  }

  Widget _body(BuildContext context) {
    final tt = Theme.of(context).textTheme;
    final status = '${m['status']}';
    final types = scoringTypes(m);
    final events = (m['events'] as List? ?? const []).cast<Map>();
    final undoable = events
        .where((e) =>
            ['score', 'discipline', 'tiebreak_winner'].contains(e['type']) && e['compensated'] != true)
        .toList()
        .reversed
        .toList();
    final queued = svc.sync.pending
        .where((a) => a.type == 'score_event' && (a.action['payload'] as Map)['match_id'] == widget.matchId)
        .toList();
    final teamIds = names.keys.toList();
    return Constrained(
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.only(bottom: T.s32),
        children: [
          DataBanner(loaded!),
          Padding(
            padding: const EdgeInsets.fromLTRB(T.s16, T.s12, T.s16, 0),
            child: ScoreHeader(m: m, names: names, live: false),
          ),
          if (status == 'upcoming')
            Padding(
              padding: const EdgeInsets.fromLTRB(T.s16, T.s16, T.s16, 0),
              child: FilledButton.icon(
                style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(52)),
                onPressed: busy ? null : () => _transition('match_start'),
                icon: const Icon(Icons.play_arrow),
                label: const Text('Start match'),
              ),
            ),
          const SectionTitle('Add a score'),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: T.s16),
            child: SegmentedButton<String>(
              segments: [
                for (final id in teamIds)
                  ButtonSegment(
                    value: id,
                    label: Text(names[id]!, maxLines: 1, overflow: TextOverflow.ellipsis),
                    icon: const Icon(Icons.shield_outlined),
                  ),
              ],
              selected: {team ?? teamIds.first},
              onSelectionChanged: (s) => setState(() => team = s.first),
              style: const ButtonStyle(minimumSize: WidgetStatePropertyAll(Size(48, 52))),
            ),
          ),
          const SizedBox(height: T.s12),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: T.s16),
            child: types.isEmpty
                ? const Text('This sport has no scoring types on the server.')
                : Wrap(spacing: T.s8, runSpacing: T.s8, children: [
                    for (final t in types)
                      FilledButton.icon(
                        style: FilledButton.styleFrom(minimumSize: const Size(120, 52)),
                        onPressed: busy ? null : () => _score(t),
                        icon: const Icon(Icons.add),
                        label: Text(titleCase(t)),
                      ),
                  ]),
          ),
          if (status != 'in_progress')
            Padding(
              padding: const EdgeInsets.fromLTRB(T.s16, T.s8, T.s16, 0),
              child: StatusPill(
                  Icons.info_outline,
                  status == 'upcoming'
                      ? 'Start the match before adding scores.'
                      : 'This match has ended; scores can no longer be added.',
                  Tone.warning),
            ),
          if (queued.isNotEmpty) ...[
            const SectionTitle('Waiting to sync'),
            for (final a in queued)
              Padding(
                padding: const EdgeInsets.fromLTRB(T.s16, 0, T.s16, T.s8),
                child: SoftCard(
                  child: Row(children: [
                    const Icon(Icons.schedule_send, color: T.warning),
                    const SizedBox(width: T.s12),
                    Expanded(child: Text(a.label, style: tt.titleMedium)),
                    const StatusPill(Icons.hourglass_empty, 'Pending', Tone.warning),
                  ]),
                ),
              ),
          ],
          const SectionTitle('Cancel an earlier event'),
          if (undoable.isEmpty)
            const Padding(
              padding: EdgeInsets.symmetric(horizontal: T.s16),
              child: Text('No scoring events to cancel yet.'),
            )
          else
            for (final e in undoable)
              Padding(
                padding: const EdgeInsets.fromLTRB(T.s16, 0, T.s16, T.s8),
                child: SoftCard(
                  padding: const EdgeInsets.fromLTRB(T.s16, T.s8, T.s8, T.s8),
                  child: Row(children: [
                    Expanded(
                      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        Text(eventTitle(e, names), style: tt.titleMedium),
                        Text('Event #${e['sequence']}', style: tt.bodySmall),
                      ]),
                    ),
                    TextButton.icon(
                      style: TextButton.styleFrom(foregroundColor: T.danger),
                      onPressed: busy ? null : () => _compensate(e),
                      icon: const Icon(Icons.undo),
                      label: const Text('Cancel'),
                    ),
                  ]),
                ),
              ),
          if (status == 'in_progress') ...[
            const SizedBox(height: T.s16),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: T.s16),
              child: OutlinedButton.icon(
                style: OutlinedButton.styleFrom(
                    foregroundColor: T.danger,
                    side: const BorderSide(color: T.danger, width: 1.5),
                    minimumSize: const Size.fromHeight(52)),
                onPressed: busy ? null : () => _transition('match_end'),
                icon: const Icon(Icons.flag_outlined),
                label: const Text('End match'),
              ),
            ),
          ],
          const SizedBox(height: T.s8),
          Center(
            child: TextButton.icon(
              onPressed: () => Navigator.of(context).push(MaterialPageRoute(
                  builder: (_) => MatchScreen(matchId: widget.matchId, summary: widget.summary))),
              icon: const Icon(Icons.visibility_outlined),
              label: const Text('Open the public match view'),
            ),
          ),
        ],
      ),
    );
  }
}
