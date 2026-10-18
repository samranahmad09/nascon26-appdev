import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app.dart';
import '../core/cart.dart';
import '../core/errors.dart';
import '../core/net.dart';
import '../core/repo.dart';
import '../core/storage.dart';
import '../core/sync.dart';
import '../screens/queue_screen.dart';
import 'motion.dart';
import 'tokens.dart';

enum Tone { success, danger, warning, neutral, brand }

(Color, Color) toneColors(BuildContext context, Tone t) {
  final p = Theme.of(context).colorScheme.primary;
  return switch (t) {
    Tone.success => (context.pal.success, context.pal.successBg),
    Tone.danger => (context.pal.danger, context.pal.dangerBg),
    Tone.warning => (context.pal.warning, context.pal.warningBg),
    Tone.neutral => (context.pal.neutral, context.pal.neutralBg),
    Tone.brand => (
        ensureContrast(p, context.pal.card, 5),
        Color.alphaBlend(p.withValues(alpha: context.pal.isDark ? 0.20 : 0.10), context.pal.card)
      ),
  };
}

/// Icon + word + colour. Never colour alone.
class StatusPill extends StatelessWidget {
  final IconData icon;
  final String text;
  final Tone tone;
  final Widget? leading;
  const StatusPill(this.icon, this.text, this.tone, {super.key, this.leading});

  @override
  Widget build(BuildContext context) {
    final (fg, bg) = toneColors(context, tone);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: T.s8 + 2, vertical: T.s4 + 2),
      decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(999)),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        leading ?? Icon(icon, size: 16, color: fg),
        const SizedBox(width: T.s4 + 2),
        Flexible(
          child: Text(text,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(fontSize: T.meta, fontWeight: T.bold, color: fg)),
        ),
      ]),
    );
  }
}

StatusPill matchStatusPill(String? status, {bool compact = false}) => switch (status) {
      'in_progress' => const StatusPill(Icons.sensors, 'Live', Tone.danger),
      'completed' => const StatusPill(Icons.flag_outlined, 'Full time', Tone.neutral),
      _ => const StatusPill(Icons.schedule, 'Upcoming', Tone.brand),
    };

StatusPill qualificationPill(String? status) => switch (status) {
      'clinched' => const StatusPill(Icons.verified, 'Qualified', Tone.success),
      'eliminated' => const StatusPill(Icons.block, 'Eliminated', Tone.danger),
      _ => const StatusPill(Icons.hourglass_bottom, 'Still possible', Tone.warning),
    };

/// Tenant logo, or a badge drawn from the tenant name when there is no image.
class TenantBadge extends StatelessWidget {
  final double size;
  const TenantBadge({super.key, this.size = 36});

  @override
  Widget build(BuildContext context) {
    final brand = context.watch<BrandNotifier>().brand;
    final settings = svc.settings;
    final initials = brand.name
        .split(RegExp(r'\s+'))
        .where((w) => w.isNotEmpty)
        .take(2)
        .map((w) => w[0].toUpperCase())
        .join();
    final badge = Container(
      width: size,
      height: size,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: brand.primary,
        shape: BoxShape.circle,
        border: Border.all(color: brand.secondary, width: size / 12),
      ),
      child: Text(initials.isEmpty ? 'N' : initials,
          style: TextStyle(
              color: onColor(brand.primary),
              fontWeight: T.bold,
              fontSize: size * 0.36)),
    );
    final url = brand.logoUrl;
    return Semantics(
      label: '${brand.name} logo',
      image: true,
      child: ExcludeSemantics(
        child: url == null || url.isEmpty
            ? badge
            : ClipOval(
                child: Image.network(
                  url.startsWith('http') ? url : '${settings.baseUrl}$url',
                  width: size,
                  height: size,
                  fit: BoxFit.cover,
                  errorBuilder: (_, __, ___) => badge,
                  loadingBuilder: (c, child, p) => p == null ? child : badge,
                ),
              ),
      ),
    );
  }
}

/// Always-visible Online / Offline / Syncing indicator. Tap opens the sync queue.
class NetIndicator extends StatelessWidget {
  const NetIndicator({super.key});

  @override
  Widget build(BuildContext context) {
    final net = context.watch<Net>();
    final q = context.watch<SyncQueue>();
    final pending = q.pendingCount;
    final StatusPill pill = switch (net.state) {
      NetState.syncing => StatusPill(Icons.sync, 'Syncing', Tone.brand,
          leading: SizedBox(
              width: 14,
              height: 14,
              child: CircularProgressIndicator(
                  strokeWidth: 2, color: toneColors(context, Tone.brand).$1))),
      NetState.online => const StatusPill(Icons.cloud_done_outlined, 'Online', Tone.success),
      NetState.offline => const StatusPill(Icons.cloud_off, 'Offline', Tone.warning),
    };
    final word = switch (net.state) {
      NetState.syncing => 'Syncing',
      NetState.online => 'Online',
      NetState.offline => 'Offline',
    };
    return Semantics(
      button: true,
      label:
          'Connection: $word. ${pending == 0 ? 'Nothing waiting to sync' : '$pending action${pending == 1 ? '' : 's'} waiting to sync'}. Opens the sync queue.',
      child: ExcludeSemantics(
        child: Tooltip(
          message: 'Sync status and pending actions',
          child: InkWell(
            borderRadius: BorderRadius.circular(999),
            onTap: () => Navigator.of(context)
                .push(MaterialPageRoute(builder: (_) => const QueueScreen())),
            child: ConstrainedBox(
              constraints: const BoxConstraints(minHeight: T.tap),
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: T.s4),
                child: Center(
                  child: BumpOnChange(
                    value: pending,
                    child: Badge(
                      isLabelVisible: pending > 0,
                      label: Text('$pending'),
                      backgroundColor: context.pal.danger,
                      textColor: onColor(context.pal.danger),
                      child: AnimatedSwitcher(
                        duration: Motion.of(context, Motion.medium),
                        switchInCurve: Motion.curve,
                        switchOutCurve: Curves.easeIn,
                        transitionBuilder: (child, a) => FadeTransition(
                          opacity: a,
                          child: ScaleTransition(
                              scale: Tween(begin: 0.85, end: 1.0).animate(a), child: child),
                        ),
                        child: KeyedSubtree(
                          key: ValueKey(net.state),
                          child: net.state == NetState.syncing ? _Pulse(child: pill) : pill,
                        ),
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// Gentle opacity pulse while something is in progress (off with reduced motion).
class _Pulse extends StatefulWidget {
  final Widget child;
  const _Pulse({required this.child});
  @override
  State<_Pulse> createState() => _PulseState();
}

class _PulseState extends State<_Pulse> with SingleTickerProviderStateMixin {
  late final AnimationController _c =
      AnimationController(vsync: this, duration: const Duration(milliseconds: 700));

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (Motion.reduced(context)) {
      _c.value = 1;
    } else if (!_c.isAnimating) {
      _c.repeat(reverse: true);
    }
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) =>
      FadeTransition(opacity: Tween(begin: 0.55, end: 1.0).animate(_c), child: widget.child);
}

/// Standard app bar: tenant badge + title + connection indicator + extra actions.
PreferredSizeWidget brandBar(BuildContext context, String title,
    {List<Widget> actions = const [], bool showBadge = true}) {
  final canPop = ModalRoute.of(context)?.canPop ?? false;
  return AppBar(
    titleSpacing: canPop ? 0 : T.s16,
    title: Row(children: [
      if (showBadge && !canPop) ...[
        const TenantBadge(size: 32),
        const SizedBox(width: T.s12),
      ],
      Expanded(
        child: Semantics(
          identifier: 'title-${title.toLowerCase().replaceAll(RegExp(r'[^a-z0-9]+'), '-')}',
          header: true,
          child: Text(title, maxLines: 1, overflow: TextOverflow.ellipsis),
        ),
      ),
    ]),
    actions: [
      const NetIndicator(),
      ...actions,
      const SizedBox(width: T.s4),
    ],
  );
}

class Constrained extends StatelessWidget {
  final Widget child;
  const Constrained({super.key, required this.child});
  @override
  Widget build(BuildContext context) => Align(
        alignment: Alignment.topCenter,
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: T.maxContent),
          child: child,
        ),
      );
}

class EmptyState extends StatelessWidget {
  final IconData icon;
  final String title;
  final String message;
  final String? actionLabel;
  final VoidCallback? onAction;
  const EmptyState(
      {super.key,
      required this.icon,
      required this.title,
      required this.message,
      this.actionLabel,
      this.onAction});

  @override
  Widget build(BuildContext context) {
    final p = Theme.of(context).colorScheme.primary;
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(T.s32),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Container(
            width: 72,
            height: 72,
            decoration: BoxDecoration(
                color: p.withValues(alpha: 0.10), shape: BoxShape.circle),
            child: Icon(icon, size: 36, color: p),
          ),
          const SizedBox(height: T.s16),
          Text(title,
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: T.s8),
          Text(message,
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.bodyLarge),
          if (actionLabel != null) ...[
            const SizedBox(height: T.s24),
            FilledButton.tonal(onPressed: onAction, child: Text(actionLabel!)),
          ],
        ]),
      ),
    );
  }
}

class LoadingState extends StatelessWidget {
  final String text;
  const LoadingState(this.text, {super.key});
  @override
  Widget build(BuildContext context) => Center(
        child: Semantics(
          liveRegion: true,
          label: text,
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            const CircularProgressIndicator(),
            const SizedBox(height: T.s16),
            Text(text, style: Theme.of(context).textTheme.bodyLarge),
          ]),
        ),
      );
}

class ErrorState extends StatelessWidget {
  final Object error;
  final VoidCallback onRetry;
  const ErrorState(this.error, this.onRetry, {super.key});
  @override
  Widget build(BuildContext context) {
    final offline = error is ApiException && (error as ApiException).network;
    return EmptyState(
      icon: offline ? Icons.cloud_off : Icons.error_outline,
      title: offline ? 'You\'re offline' : 'Couldn\'t load this',
      message: friendlyAny(error),
      actionLabel: 'Try again',
      onAction: onRetry,
    );
  }
}

/// Explains where the data on screen came from when it is not fresh.
class DataBanner extends StatelessWidget {
  final Loaded loaded;
  const DataBanner(this.loaded, {super.key});
  @override
  Widget build(BuildContext context) {
    if (loaded.sample) {
      return const _Banner(Icons.science_outlined,
          'Sample data: the server doesn\'t provide this yet.', Tone.brand);
    }
    if (loaded.fromCache) {
      return _Banner(Icons.history,
          'Offline: showing what was saved on ${savedAgo(loaded.savedAt)}.', Tone.warning);
    }
    return const SizedBox.shrink();
  }
}

class _Banner extends StatelessWidget {
  final IconData icon;
  final String text;
  final Tone tone;
  const _Banner(this.icon, this.text, this.tone);
  @override
  Widget build(BuildContext context) {
    final (fg, bg) = toneColors(context, tone);
    return Container(
      margin: const EdgeInsets.fromLTRB(T.s16, T.s8, T.s16, 0),
      padding: const EdgeInsets.all(T.s12),
      decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(T.r12)),
      child: Row(children: [
        Icon(icon, color: fg, size: 20),
        const SizedBox(width: T.s8),
        Expanded(
            child: Text(text,
                style: TextStyle(color: fg, fontSize: T.meta, fontWeight: T.bold))),
      ]),
    );
  }
}

/// Loads a [Loaded] and renders loading / error / data, with pull to refresh.
/// Reloads automatically after writes or a sync (Repo.revision).
class LoadedView extends StatefulWidget {
  final Future<Loaded> Function() load;
  final Widget Function(BuildContext, Loaded, Future<void> Function() refresh) builder;
  final String loadingText;
  const LoadedView(
      {super.key, required this.load, required this.builder, required this.loadingText});

  @override
  State<LoadedView> createState() => _LoadedViewState();
}

class _LoadedViewState extends State<LoadedView> {
  Loaded? data;
  Object? error;
  bool loading = true;
  int _rev = -1;

  @override
  void initState() {
    super.initState();
    _rev = svc.repo.revision;
    svc.repo.addListener(_onRepo);
    _load();
  }

  void _onRepo() {
    if (svc.repo.revision != _rev) {
      _rev = svc.repo.revision;
      _load(quiet: true);
    }
  }

  @override
  void dispose() {
    svc.repo.removeListener(_onRepo);
    super.dispose();
  }

  Future<void> _load({bool quiet = false}) async {
    if (!quiet) {
      setState(() {
        loading = data == null;
        error = null;
      });
    }
    try {
      final d = await widget.load();
      if (!mounted) return;
      setState(() {
        data = d;
        error = null;
        loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        if (data == null) error = e;
        loading = false;
      });
      if (data != null && quiet == false) showError(context, friendlyAny(e));
    }
  }

  @override
  Widget build(BuildContext context) {
    if (loading && data == null) return SkeletonList(label: widget.loadingText);
    if (error != null && data == null) return ErrorState(error!, _load);
    return RefreshIndicator(
      onRefresh: _load,
      child: widget.builder(context, data!, _load),
    );
  }
}

void showSuccess(BuildContext context, String msg) => _snack(context, msg, Icons.check_circle, context.pal.successBg);
void showError(BuildContext context, String msg) => _snack(context, msg, Icons.error_outline, context.pal.dangerBg);
void showInfo(BuildContext context, String msg) =>
    _snack(context, msg, Icons.info_outline, context.pal.onSnack);

void _snack(BuildContext context, String msg, IconData icon, Color iconColor) {
  final m = ScaffoldMessenger.maybeOf(context);
  if (m == null) return;
  m.hideCurrentSnackBar();
  m.showSnackBar(SnackBar(
    content: Row(children: [
      Icon(icon, color: iconColor, size: 22),
      const SizedBox(width: T.s12),
      Expanded(child: Text(msg)),
    ]),
    duration: const Duration(seconds: 4),
  ));
}

Future<bool> confirm(BuildContext context,
    {required String title,
    required String message,
    required String confirmLabel,
    bool danger = false,
    IconData icon = Icons.help_outline}) async {
  final r = await showDialog<bool>(
    context: context,
    builder: (c) => AlertDialog(
      icon: Icon(icon, color: danger ? context.pal.danger : Theme.of(c).colorScheme.primary, size: 32),
      title: Text(title, textAlign: TextAlign.center),
      content: Text(message),
      actionsAlignment: MainAxisAlignment.spaceBetween,
      actions: [
        TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancel')),
        FilledButton(
          style: danger ? FilledButton.styleFrom(backgroundColor: context.pal.danger) : null,
          onPressed: () => Navigator.pop(c, true),
          child: Text(confirmLabel),
        ),
      ],
    ),
  );
  return r ?? false;
}

class SectionTitle extends StatelessWidget {
  final String text;
  final Widget? trailing;
  final bool inset; // false inside lists that already have side padding
  const SectionTitle(this.text, {super.key, this.trailing, this.inset = true});
  @override
  Widget build(BuildContext context) => Padding(
        padding: EdgeInsets.fromLTRB(inset ? T.s16 : 0, T.s24, inset ? T.s16 : 0, T.s8),
        child: Row(children: [
          Expanded(
              child: Semantics(
                  header: true,
                  child: Text(text, style: Theme.of(context).textTheme.titleMedium))),
          if (trailing != null) trailing!,
        ]),
      );
}

/// White rounded card with a soft brand-tinted shadow.
class SoftCard extends StatelessWidget {
  final Widget child;
  final EdgeInsetsGeometry padding;
  final VoidCallback? onTap;
  final String? semanticLabel;
  const SoftCard(
      {super.key,
      required this.child,
      this.padding = const EdgeInsets.all(T.s16),
      this.onTap,
      this.semanticLabel});
  @override
  Widget build(BuildContext context) {
    final p = Theme.of(context).colorScheme.primary;
    final content = Material(
      color: context.pal.card,
      borderRadius: BorderRadius.circular(T.r16),
      child: InkWell(
        borderRadius: BorderRadius.circular(T.r16),
        onTap: onTap,
        child: Padding(padding: padding, child: child),
      ),
    );
    return Container(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(T.r16),
        boxShadow: T.shadow(p),
        border: Border.all(color: p.withValues(alpha: 0.06)),
      ),
      child: semanticLabel == null
          ? content
          : Semantics(button: onTap != null, label: semanticLabel, child: content),
    );
  }
}

/// League picker shared by Fixtures, Standings and Scoring.
class LeaguePicker extends StatelessWidget {
  final List<Map> leagues;
  const LeaguePicker(this.leagues, {super.key});
  @override
  Widget build(BuildContext context) {
    final settings = context.watch<Settings>();
    if (leagues.isEmpty) return const SizedBox.shrink();
    final ids = leagues.map((l) => '${l['league_id']}').toList();
    final current = ids.contains(settings.leagueId) ? settings.leagueId! : ids.first;
    if (leagues.length == 1) {
      final l = leagues.first;
      return Padding(
        padding: const EdgeInsets.fromLTRB(T.s16, T.s8, T.s16, 0),
        child: Row(children: [
          Icon(Icons.emoji_events_outlined, color: Theme.of(context).colorScheme.primary),
          const SizedBox(width: T.s8),
          Expanded(
            child: Text('${l['name']}${l['season'] != null ? ' · ${l['season']}' : ''}',
                style: Theme.of(context).textTheme.titleMedium,
                maxLines: 2,
                overflow: TextOverflow.ellipsis),
          ),
        ]),
      );
    }
    return Padding(
      padding: const EdgeInsets.fromLTRB(T.s16, T.s8, T.s16, 0),
      child: DropdownButtonFormField<String>(
        initialValue: current,
        isExpanded: true,
        decoration: const InputDecoration(
            labelText: 'League', prefixIcon: Icon(Icons.emoji_events_outlined)),
        items: leagues
            .map((l) => DropdownMenuItem(
                value: '${l['league_id']}',
                child: Text('${l['name']}', overflow: TextOverflow.ellipsis)))
            .toList(),
        onChanged: (v) {
          if (v != null) svc.settings.setLeague(v);
        },
      ),
    );
  }
}

String currentLeague(List<Map> leagues) {
  final ids = leagues.map((l) => '${l['league_id']}').toList();
  final s = svc.settings.leagueId;
  if (s != null && ids.contains(s)) return s;
  for (final id in svc.session.leagueIds) {
    if (ids.contains(id)) return id;
  }
  return ids.isEmpty ? '' : ids.first;
}
