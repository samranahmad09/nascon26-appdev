import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app.dart';
import '../core/cart.dart';
import '../core/session.dart';
import '../ui/motion.dart';
import '../ui/tokens.dart';
import '../ui/widgets.dart';
import 'home_shell.dart';

class ClubsScreen extends StatelessWidget {
  const ClubsScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final session = context.watch<Session>();
    return Scaffold(
      appBar: brandBar(context, 'Clubs', actions: const [ProfileButton()]),
      body: LoadedView(
        loadingText: 'Loading clubs…',
        load: svc.repo.clubs,
        builder: (context, loaded, refresh) {
          final clubs = (loaded.data['clubs'] as List? ?? const []).cast<Map>().toList();
          final mine = session.clubId;
          clubs.sort((a, b) => (a['club_id'] == mine ? 0 : 1).compareTo(b['club_id'] == mine ? 0 : 1));
          if (clubs.isEmpty) {
            return ListView(children: const [
              SizedBox(height: T.s48),
              EmptyState(
                  icon: Icons.shield_outlined,
                  title: 'No clubs yet',
                  message: 'Clubs in your organisation will be listed here.'),
            ]);
          }
          return Constrained(
            child: ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.only(bottom: T.s32),
              children: [
                DataBanner(loaded),
                const SizedBox(height: T.s8),
                for (final c in clubs)
                  FadeSlideIn(index: clubs.indexOf(c), child: Padding(
                    padding: const EdgeInsets.fromLTRB(T.s16, T.s8, T.s16, 0),
                    child: SoftCard(
                      semanticLabel:
                          '${c['name']}${c['club_id'] == mine ? ', your club' : ''}. Opens the club profile.',
                      onTap: () => Navigator.of(context).push(MaterialPageRoute(
                          builder: (_) => ClubProfileScreen(clubId: '${c['club_id']}', name: '${c['name']}'))),
                      child: ExcludeSemantics(
                        child: Row(children: [
                          _ClubCrest('${c['name']}'),
                          const SizedBox(width: T.s12),
                          Expanded(
                            child: Text('${c['name']}',
                                style: Theme.of(context).textTheme.titleMedium,
                                maxLines: 2,
                                overflow: TextOverflow.ellipsis),
                          ),
                          if (c['club_id'] == mine) ...[
                            const StatusPill(Icons.star, 'Your club', Tone.brand),
                            const SizedBox(width: T.s4),
                          ],
                          Icon(Icons.chevron_right, color: context.pal.inkMuted),
                        ]),
                      ),
                    ),
                  )),
              ],
            ),
          );
        },
      ),
    );
  }
}

class _ClubCrest extends StatelessWidget {
  final String name;
  final double size;
  const _ClubCrest(this.name, {this.size = 44});
  @override
  Widget build(BuildContext context) {
    final p = Theme.of(context).colorScheme.primary;
    final initials = name
        .split(RegExp(r'\s+'))
        .where((w) => w.isNotEmpty)
        .take(2)
        .map((w) => w[0].toUpperCase())
        .join();
    return Container(
      width: size,
      height: size,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: p.withValues(alpha: 0.10),
        borderRadius: BorderRadius.circular(T.r12),
      ),
      child: Text(initials,
          style: TextStyle(color: p, fontWeight: T.bold, fontSize: size * 0.36)),
    );
  }
}

String roleLabel(String? r) => switch (r) {
      'captain' => 'Captain',
      'vice_captain' => 'Vice-captain',
      'starter' => 'Starter',
      'substitute' => 'Substitute',
      _ => titleCase(r ?? ''),
    };

String positionLabel(String? p) => switch ((p ?? '').toLowerCase()) {
      'gk' => 'Goalkeeper',
      'df' => 'Defender',
      'mf' => 'Midfielder',
      'fw' => 'Forward',
      'bat' || 'batter' => 'Batter',
      'bowl' || 'bowler' => 'Bowler',
      'ar' || 'allrounder' => 'All-rounder',
      'wk' => 'Wicket-keeper',
      _ => titleCase(p ?? ''),
    };

class ClubProfileScreen extends StatelessWidget {
  final String clubId;
  final String name;
  const ClubProfileScreen({super.key, required this.clubId, required this.name});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: brandBar(context, name),
      body: LoadedView(
        loadingText: 'Loading club…',
        load: () => svc.repo.club(clubId),
        builder: (context, loaded, refresh) {
          final c = loaded.data;
          final profile = (c['profile'] as Map?) ?? const {};
          final venue = (profile['home_venue'] as Map?) ?? const {};
          final socials = (profile['socials'] as Map?) ?? const {};
          final staff = (profile['staff'] as List? ?? const []).cast<Map>();
          final history = (profile['history'] as List? ?? const []).cast<Map>();
          final roster = (c['roster'] as List? ?? const []).cast<Map>();
          final tt = Theme.of(context).textTheme;
          return Constrained(
            child: ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.only(bottom: T.s32),
              children: [
                DataBanner(loaded),
                Padding(
                  padding: const EdgeInsets.fromLTRB(T.s16, T.s16, T.s16, 0),
                  child: SoftCard(
                    padding: const EdgeInsets.all(T.s24),
                    child: Column(children: [
                      _ClubCrest('${c['name'] ?? name}', size: 72),
                      const SizedBox(height: T.s12),
                      Text('${c['name'] ?? name}',
                          textAlign: TextAlign.center, style: tt.titleLarge),
                      const SizedBox(height: T.s8),
                      Wrap(
                        alignment: WrapAlignment.center,
                        spacing: T.s8,
                        runSpacing: T.s8,
                        children: [
                          StatusPill(Icons.groups_outlined, '${roster.length} players', Tone.brand),
                          if (staff.isNotEmpty)
                            StatusPill(Icons.badge_outlined, '${staff.length} staff', Tone.neutral),
                        ],
                      ),
                    ]),
                  ),
                ),
                const SectionTitle('Home venue'),
                _InfoTile(
                  icon: Icons.stadium_outlined,
                  title: '${venue['name'] ?? 'Not set'}',
                  subtitle: venue['lat'] != null
                      ? 'Location ${(venue['lat'] as num).toStringAsFixed(4)}, ${(venue['lng'] as num?)?.toStringAsFixed(4) ?? ''}'
                      : null,
                ),
                if (socials.isNotEmpty) ...[
                  const SectionTitle('Social'),
                  for (final e in socials.entries)
                    _InfoTile(
                        icon: _socialIcon('${e.key}'),
                        title: '${e.value}',
                        subtitle: titleCase('${e.key}')),
                ],
                if (staff.isNotEmpty) ...[
                  const SectionTitle('Staff'),
                  for (final s in staff)
                    _InfoTile(
                        icon: Icons.badge_outlined,
                        title: '${s['name']}',
                        subtitle: titleCase('${s['role']}')),
                ],
                SectionTitle('Squad', trailing: Text('${roster.length}', style: tt.bodySmall)),
                if (roster.isEmpty)
                  const Padding(
                    padding: EdgeInsets.symmetric(horizontal: T.s16),
                    child: Text('No players registered yet.'),
                  )
                else
                  for (final p in roster)
                    _InfoTile(
                      leading: CircleAvatar(
                        backgroundColor: Theme.of(context).colorScheme.primary.withValues(alpha: 0.10),
                        child: Text('${p['shirt_number'] ?? '–'}',
                            style: TextStyle(
                                fontWeight: T.bold,
                                color: Theme.of(context).colorScheme.primary)),
                      ),
                      title: '${p['name']}',
                      subtitle:
                          '${positionLabel(p['position'] as String?)} · ${roleLabel(p['role'] as String?)}',
                    ),
                if (history.isNotEmpty) ...[
                  const SectionTitle('Season history'),
                  for (final h in history)
                    _InfoTile(
                      icon: Icons.history,
                      title: 'Season ${h['season']}',
                      subtitle:
                          'Played ${h['played']} · Won ${h['won']} · Drawn ${h['drawn']} · Lost ${h['lost']}',
                    ),
                ],
              ],
            ),
          );
        },
      ),
    );
  }

  IconData _socialIcon(String k) => switch (k.toLowerCase()) {
        'website' || 'web' => Icons.language,
        'email' => Icons.mail_outline,
        'phone' => Icons.phone_outlined,
        _ => Icons.alternate_email,
      };
}

class _InfoTile extends StatelessWidget {
  final IconData? icon;
  final Widget? leading;
  final String title;
  final String? subtitle;
  const _InfoTile({this.icon, this.leading, required this.title, this.subtitle});
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.fromLTRB(T.s16, 0, T.s16, T.s8),
        child: SoftCard(
          padding: const EdgeInsets.symmetric(vertical: T.s4),
          child: ListTile(
            leading: leading ??
                Icon(icon, color: Theme.of(context).colorScheme.primary),
            title: Text(title, style: Theme.of(context).textTheme.titleMedium),
            subtitle: subtitle == null ? null : Text(subtitle!),
          ),
        ),
      );
}
