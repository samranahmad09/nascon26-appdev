import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app.dart';
import '../core/session.dart';
import '../ui/motion.dart';
import '../ui/tokens.dart';
import '../ui/widgets.dart';
import 'club_screen.dart';
import 'creator_screen.dart';
import 'fixtures_screen.dart';
import 'roster_screen.dart';
import 'scoring_screen.dart';
import 'settings_screen.dart';
import 'standings_screen.dart';
import 'store_screen.dart';

class _Tab {
  final String label;
  final IconData icon;
  final IconData selected;
  final Widget Function() build;
  const _Tab(this.label, this.icon, this.selected, this.build);
}

/// Role-aware home: each role only sees the tabs it can use.
class HomeShell extends StatefulWidget {
  const HomeShell({super.key});
  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int index = 0;

  List<_Tab> _tabs(Session s) => [
        _Tab('Fixtures', Icons.calendar_month_outlined, Icons.calendar_month,
            () => const FixturesScreen()),
        _Tab('Standings', Icons.leaderboard_outlined, Icons.leaderboard,
            () => const StandingsScreen()),
        _Tab('Store', Icons.storefront_outlined, Icons.storefront, () => const StoreScreen()),
        _Tab('Club', Icons.shield_outlined, Icons.shield, () => const ClubsScreen()),
        if (s.role == 'super_admin')
          _Tab('Manage', Icons.tune_outlined, Icons.tune, () => const ManageScreen())
        else if (s.role == 'league_organizer')
          _Tab('Scoring', Icons.scoreboard_outlined, Icons.scoreboard,
              () => const ScoringScreen())
        else if (s.role == 'club_manager')
          _Tab('Roster', Icons.manage_accounts_outlined, Icons.manage_accounts,
              () => const RosterScreen())
        else if (s.role == 'player')
          _Tab('Go live', Icons.videocam_outlined, Icons.videocam,
              () => const CreatorScreen(standalone: false)),
      ];

  @override
  Widget build(BuildContext context) {
    final session = context.watch<Session>();
    final tabs = _tabs(session);
    if (index >= tabs.length) index = 0;
    return PopScope(
      canPop: index == 0,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) setState(() => index = 0);
      },
      child: Scaffold(
        body: FadeIndexedStack(
          index: index,
          children: [for (final t in tabs) t.build()],
        ),
        bottomNavigationBar: NavigationBar(
          selectedIndex: index,
          onDestinationSelected: (i) => setState(() => index = i),
          labelBehavior: NavigationDestinationLabelBehavior.alwaysShow,
          destinations: [
            for (final t in tabs)
              NavigationDestination(
                icon: Icon(t.icon),
                selectedIcon: Icon(t.selected),
                label: t.label,
                tooltip: t.label,
              ),
          ],
        ),
      ),
    );
  }
}

/// Profile/settings shortcut used in every tab's app bar.
class ProfileButton extends StatelessWidget {
  const ProfileButton({super.key});
  @override
  Widget build(BuildContext context) {
    final s = context.watch<Session>();
    final p = Theme.of(context).colorScheme.primary;
    final initial = s.displayName.isEmpty ? '?' : s.displayName[0].toUpperCase();
    return IconButton(
      tooltip: 'Profile and settings',
      onPressed: () => Navigator.of(context)
          .push(MaterialPageRoute(builder: (_) => const SettingsScreen())),
      icon: CircleAvatar(
        radius: 16,
        backgroundColor: p.withValues(alpha: 0.12),
        child: Text(initial, style: TextStyle(color: p, fontWeight: T.bold)),
      ),
    );
  }
}

/// Super admin: both organizer and manager tools.
class ManageScreen extends StatelessWidget {
  const ManageScreen({super.key});
  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: brandBar(context, 'Manage', actions: const [ProfileButton()]),
      body: Constrained(
        child: ListView(padding: const EdgeInsets.all(T.s16), children: [
          SoftCard(
            onTap: () => Navigator.of(context).push(MaterialPageRoute(
                builder: (_) => const ScoringScreen(standalone: true))),
            child: const ListTile(
              contentPadding: EdgeInsets.zero,
              leading: Icon(Icons.scoreboard_outlined, size: 32),
              title: Text('Scoring entry'),
              subtitle: Text('Add a score or cancel an earlier event'),
              trailing: Icon(Icons.chevron_right),
            ),
          ),
          const SizedBox(height: T.s12),
          SoftCard(
            onTap: () => Navigator.of(context).push(MaterialPageRoute(
                builder: (_) => const RosterScreen(standalone: true))),
            child: const ListTile(
              contentPadding: EdgeInsets.zero,
              leading: Icon(Icons.manage_accounts_outlined, size: 32),
              title: Text('Roster management'),
              subtitle: Text('Transfer, loan or release players'),
              trailing: Icon(Icons.chevron_right),
            ),
          ),
        ]),
      ),
    );
  }
}

/// Shared helper: leagues list for pickers.
Future<List<Map>> loadLeagues() async {
  final l = await svc.repo.leagues();
  return (l.data['leagues'] as List? ?? const []).cast<Map>();
}
