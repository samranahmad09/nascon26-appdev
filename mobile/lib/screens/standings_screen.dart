import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app.dart';
import '../core/storage.dart';
import '../ui/tokens.dart';
import '../ui/widgets.dart';
import 'fixtures_screen.dart';
import 'home_shell.dart';

class StandingsScreen extends StatelessWidget {
  const StandingsScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final settings = context.watch<Settings>();
    return Scaffold(
      appBar: brandBar(context, 'Standings', actions: const [ProfileButton()]),
      body: LoadedView(
        key: ValueKey('st-${settings.leagueId}'),
        loadingText: 'Loading standings…',
        load: () => loadForLeague(svc.repo.standings),
        builder: (context, loaded, refresh) {
          final leagues = (loaded.data['_leagues'] as List).cast<Map>();
          final rows = (loaded.data['table'] as List? ?? const []).cast<Map>();
          final names = ((loaded.data['team_names'] as Map?) ?? const {})
              .map((k, v) => MapEntry('$k', '$v'));
          final spots = (loaded.data['qualification_spots'] as num?)?.toInt() ?? 0;
          return Constrained(
            child: ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.only(bottom: T.s32),
              children: [
                DataBanner(loaded),
                LeaguePicker(leagues),
                if (rows.isEmpty)
                  const Padding(
                    padding: EdgeInsets.only(top: T.s48),
                    child: EmptyState(
                        icon: Icons.leaderboard_outlined,
                        title: 'No table yet',
                        message: 'The points table appears after the first match is played.'),
                  )
                else ...[
                  if (spots > 0)
                    Padding(
                      padding: const EdgeInsets.fromLTRB(T.s16, T.s12, T.s16, 0),
                      child: Row(children: [
                        Icon(Icons.info_outline,
                            size: 18, color: Theme.of(context).colorScheme.primary),
                        const SizedBox(width: T.s8),
                        Expanded(
                          child: Text('Top $spots qualify. Status shows who is through.',
                              style: Theme.of(context).textTheme.bodyMedium),
                        ),
                      ]),
                    ),
                  const SizedBox(height: T.s12),
                  for (var i = 0; i < rows.length; i++) ...[
                    if (spots > 0 && i == spots) const _QualifyLine(),
                    Padding(
                      padding: const EdgeInsets.fromLTRB(T.s16, 0, T.s16, T.s8),
                      child: StandingsRow(
                        row: rows[i],
                        name: names['${rows[i]['team_id']}'] ?? '${rows[i]['team_id']}',
                        inZone: i < spots,
                      ),
                    ),
                  ],
                  const _Legend(),
                ],
              ],
            ),
          );
        },
      ),
    );
  }
}

class StandingsRow extends StatelessWidget {
  final Map row;
  final String name;
  final bool inZone;
  const StandingsRow({super.key, required this.row, required this.name, required this.inZone});

  @override
  Widget build(BuildContext context) {
    final p = Theme.of(context).colorScheme.primary;
    final tt = Theme.of(context).textTheme;
    final diff = (row['diff'] as num?) ?? 0;
    final gd = diff > 0 ? '+$diff' : '$diff';
    final status = '${row['status']}';
    final statusWord = status == 'clinched'
        ? 'qualified'
        : status == 'eliminated'
            ? 'eliminated'
            : 'still possible';
    return Semantics(
      label: 'Rank ${row['rank']}, $name, ${row['points']} points, played ${row['played']}, '
          'won ${row['won']}, drawn ${row['drawn']}, lost ${row['lost']}, goal difference $gd, $statusWord.',
      child: ExcludeSemantics(
        child: SoftCard(
          padding: const EdgeInsets.all(T.s12),
          child: Row(children: [
            Container(
              width: 36,
              height: 36,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                color: inZone ? p : T.neutralBg,
                shape: BoxShape.circle,
              ),
              child: Text('${row['rank']}',
                  style: TextStyle(
                      fontWeight: T.bold,
                      fontSize: T.body,
                      color: inZone ? onColor(p) : T.ink)),
            ),
            const SizedBox(width: T.s12),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(name, style: tt.titleMedium, maxLines: 1, overflow: TextOverflow.ellipsis),
                const SizedBox(height: T.s4),
                Text(
                  'P ${row['played']} · W ${row['won']} · D ${row['drawn']} · L ${row['lost']} · GD $gd',
                  style: tt.bodySmall?.copyWith(fontFeatures: T.tabular),
                  maxLines: 2,
                ),
                const SizedBox(height: T.s8),
                qualificationPill(status),
              ]),
            ),
            const SizedBox(width: T.s8),
            Column(children: [
              Text('${row['points']}',
                  style: tt.titleLarge?.copyWith(fontFeatures: T.tabular, color: p)),
              Text('pts', style: tt.bodySmall),
            ]),
          ]),
        ),
      ),
    );
  }
}

class _QualifyLine extends StatelessWidget {
  const _QualifyLine();
  @override
  Widget build(BuildContext context) {
    final p = Theme.of(context).colorScheme.primary;
    return Padding(
      padding: const EdgeInsets.fromLTRB(T.s16, T.s4, T.s16, T.s12),
      child: Row(children: [
        Expanded(child: Divider(color: p.withValues(alpha: 0.5), thickness: 2)),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: T.s8),
          child: Text('Qualification line',
              style: TextStyle(color: p, fontWeight: T.bold, fontSize: T.meta)),
        ),
        Expanded(child: Divider(color: p.withValues(alpha: 0.5), thickness: 2)),
      ]),
    );
  }
}

class _Legend extends StatelessWidget {
  const _Legend();
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.fromLTRB(T.s16, T.s16, T.s16, 0),
        child: Wrap(spacing: T.s8, runSpacing: T.s8, children: [
          qualificationPill('clinched'),
          qualificationPill('still_possible'),
          qualificationPill('eliminated'),
          Text('P played · W won · D drawn · L lost · GD goal difference',
              style: Theme.of(context).textTheme.bodySmall),
        ]),
      );
}
