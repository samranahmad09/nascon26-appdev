import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app.dart';
import '../core/cart.dart';
import '../core/repo.dart';
import '../core/storage.dart';
import '../ui/motion.dart';
import '../ui/tokens.dart';
import '../ui/widgets.dart';
import 'home_shell.dart';
import 'match_screen.dart';
import 'qr_scan_screen.dart';

/// Loads the league list and the data for the selected league in one go.
Future<Loaded> loadForLeague(Future<Loaded> Function(String leagueId) inner,
    {bool ownedOnly = false}) async {
  final ls = await svc.repo.leagues();
  var leagues = (ls.data['leagues'] as List? ?? const []).cast<Map>();
  final owned = svc.session.leagueIds;
  if (ownedOnly && svc.session.role == 'league_organizer' && owned.isNotEmpty) {
    leagues = leagues.where((l) => owned.contains('${l['league_id']}')).toList();
  }
  final id = currentLeague(leagues);
  if (id.isEmpty) {
    return Loaded({'_leagues': leagues, '_league_id': ''},
        fromCache: ls.fromCache, sample: ls.sample, savedAt: ls.savedAt);
  }
  final d = await inner(id);
  return Loaded({...d.data, '_leagues': leagues, '_league_id': id},
      fromCache: d.fromCache || ls.fromCache,
      sample: d.sample || ls.sample,
      savedAt: d.savedAt ?? ls.savedAt);
}

class FixturesScreen extends StatelessWidget {
  const FixturesScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final settings = context.watch<Settings>();
    return Scaffold(
      appBar: brandBar(context, 'Fixtures', actions: const [ScanButton(), ProfileButton()]),
      body: LoadedView(
        key: ValueKey('fx-${settings.leagueId}'),
        loadingText: 'Loading fixtures…',
        load: () => loadForLeague(svc.repo.fixtures),
        builder: (context, loaded, refresh) {
          final leagues = (loaded.data['_leagues'] as List).cast<Map>();
          final matches = (loaded.data['matches'] as List? ?? const []).cast<Map>();
          final live = matches.where((m) => m['status'] == 'in_progress').toList();
          final byRound = <String, List<Map>>{};
          var n = 0; // stagger order
          for (final m in matches) {
            if (m['status'] == 'in_progress') continue; // shown under "Live now"
            byRound.putIfAbsent('${m['round'] ?? '-'}', () => []).add(m);
          }
          return Constrained(
            child: ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.only(bottom: T.s32),
              children: [
                DataBanner(loaded),
                LeaguePicker(leagues),
                if (leagues.isEmpty)
                  const Padding(
                    padding: EdgeInsets.only(top: T.s48),
                    child: EmptyState(
                        icon: Icons.emoji_events_outlined,
                        title: 'No leagues yet',
                        message: 'When your organisation creates a league it will show up here.'),
                  )
                else if (matches.isEmpty)
                  const Padding(
                    padding: EdgeInsets.only(top: T.s48),
                    child: EmptyState(
                        icon: Icons.event_busy,
                        title: 'No matches yet',
                        message:
                            'Fixtures for this league will appear here once they are scheduled.'),
                  )
                else ...[
                  if (live.isNotEmpty) ...[
                    const SectionTitle('Live now'),
                    for (final m in live) _pad(FadeSlideIn(index: n++, child: MatchCard(m))),
                  ],
                  for (final e in byRound.entries) ...[
                    SectionTitle(e.key == '-' ? 'Matches' : 'Round ${e.key}'),
                    for (final m in e.value) _pad(FadeSlideIn(index: n++, child: MatchCard(m))),
                  ],
                ],
              ],
            ),
          );
        },
      ),
    );
  }

  Widget _pad(Widget w) => Padding(
      padding: const EdgeInsets.fromLTRB(T.s16, 0, T.s16, T.s12), child: w);
}

class MatchCard extends StatelessWidget {
  final Map m;
  const MatchCard(this.m, {super.key});

  @override
  Widget build(BuildContext context) {
    final home = (m['home'] as Map?) ?? const {};
    final away = (m['away'] as Map?) ?? const {};
    final status = '${m['status']}';
    final showScore = status != 'upcoming' && m['home_score'] != null;
    final tt = Theme.of(context).textTheme;
    final label =
        '${home['name']} ${showScore ? m['home_score'] : ''} versus ${away['name']} ${showScore ? m['away_score'] : ''}. '
        '${status == 'in_progress' ? 'Live' : status == 'completed' ? 'Full time' : 'Upcoming'}. ${when(m['scheduled_at'] as String?)}. Opens match details.';
    return SoftCard(
      semanticLabel: label,
      onTap: () => Navigator.of(context).push(MaterialPageRoute(
          builder: (_) => MatchScreen(matchId: '${m['match_id']}', summary: m))),
      child: ExcludeSemantics(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Expanded(
              child: Row(children: [
                Icon(Icons.schedule, size: 16, color: context.pal.inkMuted),
                const SizedBox(width: T.s4),
                Flexible(
                  child: Text(when(m['scheduled_at'] as String?),
                      style: tt.bodySmall, overflow: TextOverflow.ellipsis),
                ),
              ]),
            ),
            const SizedBox(width: T.s8),
            matchStatusPill(status),
          ]),
          const SizedBox(height: T.s12),
          Row(children: [
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text('${home['name'] ?? 'Home'}',
                    style: tt.titleMedium, maxLines: 1, overflow: TextOverflow.ellipsis),
                const SizedBox(height: T.s8),
                Text('${away['name'] ?? 'Away'}',
                    style: tt.titleMedium, maxLines: 1, overflow: TextOverflow.ellipsis),
              ]),
            ),
            if (showScore)
              Hero(
                tag: 'score-${m['match_id']}',
                flightShuttleBuilder: scoreFlight,
                child: Material(
                  type: MaterialType.transparency,
                  child: Column(crossAxisAlignment: CrossAxisAlignment.end, children: [
                    Text('${m['home_score']}',
                        style: tt.titleLarge?.copyWith(fontFeatures: T.tabular, height: 1.04)),
                    const SizedBox(height: T.s8),
                    Text('${m['away_score']}',
                        style: tt.titleLarge?.copyWith(fontFeatures: T.tabular, height: 1.04)),
                  ]),
                ),
              ),
          ]),
          if ((m['venue'] ?? '').toString().isNotEmpty) ...[
            const SizedBox(height: T.s12),
            Row(children: [
              Icon(Icons.place_outlined, size: 16, color: context.pal.inkMuted),
              const SizedBox(width: T.s4),
              Expanded(
                child: Text('${m['venue']}',
                    style: tt.bodySmall, maxLines: 1, overflow: TextOverflow.ellipsis),
              ),
              Icon(Icons.chevron_right, color: context.pal.inkMuted),
            ]),
          ],
        ]),
      ),
    );
  }
}

class _TeamLine extends StatelessWidget {
  final String name;
  final String? score;
  const _TeamLine(this.name, this.score);
  @override
  Widget build(BuildContext context) {
    final tt = Theme.of(context).textTheme;
    return Row(children: [
      Expanded(
        child: Text(name, style: tt.titleMedium, maxLines: 1, overflow: TextOverflow.ellipsis),
      ),
      if (score != null)
        Text(score!,
            style: tt.titleLarge?.copyWith(fontFeatures: T.tabular)),
    ]);
  }
}
