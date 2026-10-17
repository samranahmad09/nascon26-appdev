import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import '../app.dart';
import '../core/cart.dart';
import '../core/errors.dart';
import '../core/net.dart';
import '../core/repo.dart';
import '../ui/tokens.dart';
import '../ui/widgets.dart';
import 'store_screen.dart';

String eventTitle(Map e, Map<String, String> names) {
  final p = (e['payload'] as Map?) ?? const {};
  final team = names['${p['team_id']}'];
  switch ('${e['type']}') {
    case 'score':
      return '${titleCase('${p['event_type'] ?? 'Score'}')}${team != null ? ' · $team' : ''}';
    case 'discipline':
      return 'Discipline ${p['points']} fair play${team != null ? ' · $team' : ''}';
    case 'tiebreak_winner':
      return 'Tie-break won${team != null ? ' · $team' : ''}';
    case 'match_start':
      return 'Match started';
    case 'match_end':
      return 'Match ended';
    case 'match_init':
      return 'Match created';
    case 'compensation':
      return 'Correction';
    default:
      return titleCase('${e['type']}');
  }
}

IconData eventIcon(String type) => switch (type) {
      'score' => Icons.sports_score,
      'discipline' => Icons.style_outlined,
      'tiebreak_winner' => Icons.military_tech_outlined,
      'match_start' => Icons.play_circle_outline,
      'match_end' => Icons.flag_outlined,
      'compensation' => Icons.undo,
      _ => Icons.add_circle_outline,
    };

String sideScore(Map? side) {
  if (side == null) return '-';
  final s = side['score'] ?? 0;
  final w = side['wickets'];
  return w == null ? '$s' : '$s/$w';
}

String? sideOvers(Map? side) {
  final o = side?['overs'];
  return o == null ? null : '$o ov';
}

class MatchScreen extends StatefulWidget {
  final String matchId;
  final Map? summary;
  const MatchScreen({super.key, required this.matchId, this.summary});
  @override
  State<MatchScreen> createState() => _MatchScreenState();
}

class _MatchScreenState extends State<MatchScreen> {
  Map<String, dynamic>? match;
  Loaded? loaded;
  Object? error;
  bool loading = true;
  WebSocketChannel? _ch;
  StreamSubscription? _sub;
  bool live = false;
  Timer? _retry;
  int _backoff = 2;
  bool _disposed = false;

  @override
  void initState() {
    super.initState();
    _load();
    _connect();
    svc.net.addListener(_onNet);
  }

  void _onNet() {
    if (svc.net.online && !live && _retry == null) _connect();
  }

  @override
  void dispose() {
    _disposed = true;
    svc.net.removeListener(_onNet);
    _retry?.cancel();
    _sub?.cancel();
    _ch?.sink.close();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final l = await svc.repo.match(widget.matchId);
      if (!mounted) return;
      setState(() {
        loaded = l;
        match = Map<String, dynamic>.from(l.data);
        loading = false;
        error = null;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        loading = false;
        if (match == null) error = e;
      });
    }
  }

  void _connect() {
    if (_disposed || !svc.net.online) return;
    _sub?.cancel();
    _ch?.sink.close();
    try {
      final token = svc.session.token ?? '';
      final uri = Uri.parse(
          '${svc.settings.wsBase}/api/v1/ws?topic=match:${Uri.encodeComponent(widget.matchId)}&token=$token');
      final ch = WebSocketChannel.connect(uri);
      _ch = ch;
      ch.ready.then((_) {
        if (_disposed) return;
        _backoff = 2;
        setState(() => live = true);
      }).catchError((_) {
        _scheduleRetry();
      });
      _sub = ch.stream.listen(_onMessage,
          onDone: _scheduleRetry, onError: (_) => _scheduleRetry(), cancelOnError: true);
    } catch (_) {
      _scheduleRetry();
    }
  }

  void _scheduleRetry() {
    if (_disposed) return;
    if (live) setState(() => live = false);
    _retry?.cancel();
    _retry = Timer(Duration(seconds: _backoff), () {
      _retry = null;
      _connect();
    });
    _backoff = (_backoff * 2).clamp(2, 30);
  }

  void _onMessage(dynamic raw) {
    try {
      final msg = jsonDecode(raw is String ? raw : utf8.decode(raw as List<int>));
      if (msg is! Map) return;
      if (msg['type'] == 'score_update' && msg['match'] is Map) {
        final m = Map<String, dynamic>.from(match ?? {});
        m.addAll((msg['match'] as Map).cast<String, dynamic>());
        setState(() => match = m);
        svc.repo.storeMatch(widget.matchId, m);
      } else if (msg['type'] == 'pinned_product') {
        final m = Map<String, dynamic>.from(match ?? {});
        m['pinned_product'] = msg['product'];
        setState(() => match = m);
        svc.repo.storeMatch(widget.matchId, m);
      }
    } catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
    context.watch<Net>();
    final s = widget.summary;
    final title = match != null
        ? '${match!['home_name'] ?? 'Home'} v ${match!['away_name'] ?? 'Away'}'
        : s != null
            ? '${(s['home'] as Map?)?['name']} v ${(s['away'] as Map?)?['name']}'
            : 'Match';
    return Scaffold(
      appBar: brandBar(context, title),
      body: loading
          ? const LoadingState('Loading match…')
          : (match == null)
              ? ErrorState(error ?? ApiException.network(), () {
                  setState(() => loading = true);
                  _load();
                })
              : RefreshIndicator(onRefresh: _load, child: _body(context)),
    );
  }

  Widget _body(BuildContext context) {
    final m = match!;
    final home = (m['home'] as Map?) ?? const {};
    final away = (m['away'] as Map?) ?? const {};
    final names = <String, String>{
      '${home['team_id']}': '${m['home_name'] ?? (widget.summary?['home'] as Map?)?['name'] ?? 'Home'}',
      '${away['team_id']}': '${m['away_name'] ?? (widget.summary?['away'] as Map?)?['name'] ?? 'Away'}',
    };
    final events = (m['events'] as List? ?? const []).cast<Map>().reversed.toList();
    final seqOf = {for (final e in events) '${e['event_id']}': e['sequence']};
    final pinned = m['pinned_product'] as Map?;
    return Constrained(
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.only(bottom: T.s32),
        children: [
          if (loaded != null) DataBanner(loaded!),
          Padding(
            padding: const EdgeInsets.fromLTRB(T.s16, T.s12, T.s16, 0),
            child: ScoreHeader(m: m, names: names, live: live),
          ),
          if (pinned != null) ...[
            const SectionTitle('Featured in the store'),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: T.s16),
              child: PinnedProductCard(pinned.cast<String, dynamic>()),
            ),
          ],
          SectionTitle('Match events',
              trailing: Text('${events.length}', style: Theme.of(context).textTheme.bodySmall)),
          if (events.isEmpty)
            const Padding(
              padding: EdgeInsets.all(T.s16),
              child: Text('No events yet. They will appear here as they happen.'),
            )
          else
            for (final e in events)
              Padding(
                padding: const EdgeInsets.fromLTRB(T.s16, 0, T.s16, T.s8),
                child: EventTile(e: e, names: names, seqOf: seqOf),
              ),
        ],
      ),
    );
  }
}

/// The dominant element of the match screen.
class ScoreHeader extends StatelessWidget {
  final Map m;
  final Map<String, String> names;
  final bool live;
  final bool showLive;
  const ScoreHeader(
      {super.key, required this.m, required this.names, required this.live, this.showLive = true});

  @override
  Widget build(BuildContext context) {
    final p = Theme.of(context).colorScheme.primary;
    final on = onColor(p);
    final home = (m['home'] as Map?) ?? const {};
    final away = (m['away'] as Map?) ?? const {};
    final status = '${m['status']}';
    final hn = names['${home['team_id']}'] ?? 'Home';
    final an = names['${away['team_id']}'] ?? 'Away';
    final outcome = m['outcome'];
    final winner = names['${m['winner_team_id']}'];
    final tt = Theme.of(context).textTheme;
    Widget team(String n, Map side) => Expanded(
          child: Column(children: [
            Text(n,
                textAlign: TextAlign.center,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: tt.titleMedium?.copyWith(color: on)),
            if (sideOvers(side) != null)
              Text(sideOvers(side)!, style: tt.bodySmall?.copyWith(color: on)),
          ]),
        );
    return Semantics(
      liveRegion: true,
      label: '$hn ${sideScore(home)}, $an ${sideScore(away)}. '
          '${status == 'in_progress' ? 'Live' : status == 'completed' ? 'Full time' : 'Not started'}.',
      child: ExcludeSemantics(
        child: Container(
          padding: const EdgeInsets.all(T.s24),
          decoration: BoxDecoration(
            color: p,
            borderRadius: BorderRadius.circular(T.r24),
            boxShadow: T.shadow(p, hero: true),
          ),
          child: Column(children: [
            Wrap(
              alignment: WrapAlignment.center,
              spacing: T.s8,
              runSpacing: T.s8,
              children: [
                _HeaderChip(
                  icon: status == 'in_progress'
                      ? Icons.sensors
                      : status == 'completed'
                          ? Icons.flag
                          : Icons.schedule,
                  text: status == 'in_progress'
                      ? 'Live'
                      : status == 'completed'
                          ? 'Full time'
                          : 'Upcoming',
                ),
                if (showLive)
                  _HeaderChip(
                      icon: live ? Icons.bolt : Icons.bolt_outlined,
                      text: live ? 'Real-time updates on' : 'Updates paused'),
              ],
            ),
            const SizedBox(height: T.s16),
            Row(crossAxisAlignment: CrossAxisAlignment.center, children: [
              team(hn, home),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: T.s8),
                child: FittedBox(
                  child: Text('${sideScore(home)} – ${sideScore(away)}',
                      style: tt.displaySmall?.copyWith(color: on)),
                ),
              ),
              team(an, away),
            ]),
            if (status == 'completed' && outcome != null) ...[
              const SizedBox(height: T.s12),
              Text(winner != null ? '$winner won' : titleCase('$outcome'),
                  style: tt.titleMedium?.copyWith(color: on)),
            ],
            if (m['scheduled_at'] != null || m['venue'] != null) ...[
              const SizedBox(height: T.s12),
              Text(
                [
                  if (m['scheduled_at'] != null) when(m['scheduled_at'] as String?),
                  if ((m['venue'] ?? '').toString().isNotEmpty) '${m['venue']}',
                ].join(' · '),
                textAlign: TextAlign.center,
                style: tt.bodyMedium?.copyWith(color: on),
              ),
            ],
          ]),
        ),
      ),
    );
  }
}

class _HeaderChip extends StatelessWidget {
  final IconData icon;
  final String text;
  const _HeaderChip({required this.icon, required this.text});
  @override
  Widget build(BuildContext context) {
    final on = onColor(Theme.of(context).colorScheme.primary);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: T.s12, vertical: T.s4 + 2),
      decoration: BoxDecoration(
        color: on.withValues(alpha: 0.16),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        Icon(icon, size: 16, color: on),
        const SizedBox(width: T.s4 + 2),
        Text(text, style: TextStyle(color: on, fontWeight: T.bold, fontSize: T.meta)),
      ]),
    );
  }
}

class EventTile extends StatelessWidget {
  final Map e;
  final Map<String, String> names;
  final Map<String, dynamic> seqOf;
  const EventTile({super.key, required this.e, required this.names, required this.seqOf});

  @override
  Widget build(BuildContext context) {
    final type = '${e['type']}';
    final compensated = e['compensated'] == true;
    final p = (e['payload'] as Map?) ?? const {};
    final tt = Theme.of(context).textTheme;
    final title = eventTitle(e, names);
    String? sub;
    if (type == 'compensation') {
      final target = seqOf['${p['compensates']}'];
      sub = 'Cancels event #${target ?? p['compensates']}'
          '${(p['reason'] ?? '').toString().isNotEmpty ? ': ${p['reason']}' : ''}';
    }
    final cancelledBy = seqOf['${e['compensated_by']}'];
    return Semantics(
      label: 'Event ${e['sequence']}: $title. ${sub ?? ''} ${compensated ? 'Cancelled by correction.' : ''}',
      child: ExcludeSemantics(
        child: Container(
          padding: const EdgeInsets.all(T.s12),
          decoration: BoxDecoration(
            color: compensated ? T.neutralBg : Colors.white,
            borderRadius: BorderRadius.circular(T.r12),
            border: Border.all(color: T.line),
          ),
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            CircleAvatar(
              radius: 18,
              backgroundColor: Theme.of(context).colorScheme.primary.withValues(alpha: 0.10),
              child: Icon(eventIcon(type), size: 20, color: Theme.of(context).colorScheme.primary),
            ),
            const SizedBox(width: T.s12),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(
                  title,
                  style: tt.titleMedium?.copyWith(
                    decoration: compensated ? TextDecoration.lineThrough : null,
                    color: compensated ? T.inkMuted : T.ink,
                  ),
                ),
                if (sub != null) Text(sub, style: tt.bodyMedium),
                if (compensated) ...[
                  const SizedBox(height: T.s4),
                  StatusPill(Icons.undo,
                      'Cancelled${cancelledBy != null ? ' by #$cancelledBy' : ''}', Tone.danger),
                ],
              ]),
            ),
            const SizedBox(width: T.s8),
            Text('#${e['sequence']}', style: tt.bodySmall),
          ]),
        ),
      ),
    );
  }
}

class PinnedProductCard extends StatelessWidget {
  final Map<String, dynamic> product;
  const PinnedProductCard(this.product, {super.key});
  @override
  Widget build(BuildContext context) {
    final variants = (product['variants'] as List? ?? const []).cast<Map>();
    final prices = variants.map((v) => (v['price_minor'] as num?) ?? 0).toList();
    final from = prices.isEmpty ? null : prices.reduce((a, b) => a < b ? a : b);
    final sec = Theme.of(context).colorScheme.secondary;
    return SoftCard(
      semanticLabel: 'Featured product ${product['name']}. Opens the product.',
      onTap: () => Navigator.of(context)
          .push(MaterialPageRoute(builder: (_) => ProductScreen(product: product))),
      child: ExcludeSemantics(
        child: Row(children: [
          Container(
            width: 56,
            height: 56,
            decoration: BoxDecoration(
              color: sec.withValues(alpha: 0.25),
              borderRadius: BorderRadius.circular(T.r12),
            ),
            child: Icon(categoryIcon('${product['category']}'), size: 30, color: T.ink),
          ),
          const SizedBox(width: T.s12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('${product['name']}',
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: Theme.of(context).textTheme.titleMedium),
              if (from != null)
                Text('From ${money(from, '${variants.first['currency'] ?? 'PKR'}')}',
                    style: Theme.of(context).textTheme.bodyMedium),
            ]),
          ),
          const Icon(Icons.chevron_right, color: T.inkMuted),
        ]),
      ),
    );
  }
}
