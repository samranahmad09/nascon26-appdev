import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import '../app.dart';
import '../core/errors.dart';
import '../core/session.dart';
import '../core/storage.dart';
import '../core/sync.dart';
import '../ui/tokens.dart';
import '../ui/video_tile.dart';
import '../ui/widgets.dart';

/// Topic subscription on /api/v1/ws with automatic reconnect.
class TopicSocket {
  final String path;
  final void Function(Map<String, dynamic>) onMessage;
  final void Function(bool connected)? onState;
  WebSocketChannel? _ch;
  StreamSubscription? _sub;
  Timer? _retry;
  int _backoff = 2;
  bool _closed = false;

  TopicSocket(this.path, this.onMessage, {this.onState}) {
    _connect();
  }

  void _connect() {
    if (_closed) return;
    try {
      final token = svc.session.token ?? svc.guest.token ?? '';
      final sep = path.contains('?') ? '&' : '?';
      final ch = WebSocketChannel.connect(Uri.parse('${svc.settings.wsBase}$path${sep}token=$token'));
      _ch = ch;
      ch.ready.then((_) {
        _backoff = 2;
        onState?.call(true);
      }).catchError((_) {
        _again();
      });
      _sub = ch.stream.listen((raw) {
        try {
          final m = jsonDecode(raw is String ? raw : utf8.decode(raw as List<int>));
          if (m is Map) onMessage(m.cast<String, dynamic>());
        } catch (_) {}
      }, onDone: _again, onError: (_) => _again(), cancelOnError: true);
    } catch (_) {
      _again();
    }
  }

  void _again() {
    if (_closed) return;
    onState?.call(false);
    _retry?.cancel();
    _retry = Timer(Duration(seconds: _backoff), _connect);
    _backoff = (_backoff * 2).clamp(2, 20);
  }

  void close() {
    _closed = true;
    _retry?.cancel();
    _sub?.cancel();
    _ch?.sink.close();
  }
}

/// FR-3.3 co-stream: split view of every broadcaster plus the unified chat room.
class CoStreamScreen extends StatefulWidget {
  final String sessionId;
  const CoStreamScreen({super.key, required this.sessionId});
  @override
  State<CoStreamScreen> createState() => _CoStreamScreenState();
}

class _CoStreamScreenState extends State<CoStreamScreen> {
  Map<String, dynamic>? session;
  Object? error;
  bool composite = false;
  final messages = <int, Map<String, dynamic>>{};
  final _text = TextEditingController();
  final _scroll = ScrollController();
  TopicSocket? _chatSocket;
  bool _chatLive = false;
  bool _sending = false;

  String get room => '${session?['chat_room_id'] ?? 'chat-${widget.sessionId}'}';

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _chatSocket?.close();
    _text.dispose();
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final s = await loadCoStream(widget.sessionId);
      if (!mounted) return;
      setState(() {
        session = s;
        error = null;
      });
      _chatSocket?.close();
      _chatSocket = TopicSocket('/api/v1/ws?topic=${Uri.encodeComponent('chat:$room')}', _onSocket,
          onState: (c) {
        if (mounted) setState(() => _chatLive = c);
      });
      await _loadMessages();
    } catch (e) {
      if (mounted) setState(() => error = e);
    }
  }

  Future<void> _loadMessages() async {
    try {
      final res = await svc.api.get('/api/v1/chat/${Uri.encodeComponent(room)}/messages');
      final list = res is List ? res : (res is Map ? (res['messages'] ?? res['items'] ?? const []) : const []);
      if (!mounted) return;
      setState(() {
        for (final m in (list as List).whereType<Map>()) {
          _add(m.cast<String, dynamic>());
        }
      });
      _toBottom();
    } catch (_) {}
  }

  void _add(Map<String, dynamic> m) {
    final seq = (m['sequence'] as num?)?.toInt() ?? (messages.isEmpty ? 1 : messages.keys.last + 1);
    messages[seq] = m;
  }

  void _onSocket(Map<String, dynamic> msg) {
    if (msg['type'] == 'chat_message' && msg['message'] is Map) {
      setState(() => _add((msg['message'] as Map).cast<String, dynamic>()));
      _toBottom();
    } else if (msg['type'] == 'costream_update' && msg['session'] is Map) {
      setState(() => session = (msg['session'] as Map).cast<String, dynamic>());
    }
  }

  void _toBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) {
        _scroll.animateTo(_scroll.position.maxScrollExtent,
            duration: Motion.of(context, Motion.medium), curve: Motion.curve);
      }
    });
  }

  Future<void> _send() async {
    final text = _text.text.trim();
    if (text.isEmpty) return;
    if (!svc.session.loggedIn) {
      await promptSignIn(context, 'Sign in to send chat messages. Watching stays free.');
      return;
    }
    setState(() => _sending = true);
    try {
      final res = await svc.api.post('/api/v1/chat', {'chat_room_id': room, 'text': text});
      _text.clear();
      if (res is Map && (res['message'] is Map || res['sequence'] != null)) {
        final m = (res['message'] is Map ? res['message'] as Map : res).cast<String, dynamic>();
        setState(() => _add(m));
        _toBottom();
      }
    } on ApiException catch (e) {
      if (!mounted) return;
      if (e.code == 'GUEST_WRITE_FORBIDDEN') {
        await promptSignIn(context, 'Sign in to send chat messages.');
      } else if (e.status == 403) {
        showError(context, 'Only members of this co-stream can post in its chat.');
      } else {
        showError(context, e.message);
      }
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    context.watch<Session>();
    return Semantics(
      identifier: 'screen-costream',
      label: 'Co-stream ${widget.sessionId}',
      container: true,
      child: Scaffold(
        appBar: brandBar(context, 'Co-stream'),
        body: session == null
            ? (error == null
                ? const LoadingState('Joining the co-stream…')
                : ErrorState(error!, () {
                    setState(() => error = null);
                    _load();
                  }))
            : _body(context),
      ),
    );
  }

  List<String> get participants {
    final s = session ?? const {};
    final list = (s['active_participants'] ?? s['participants'] ?? const []) as List;
    return list.map((e) => e is Map ? '${e['player_id'] ?? e['id']}' : '$e').toList();
  }

  String? urlFor(String p) {
    final s = session ?? const {};
    final ps = s['participant_streams'] ?? s['streams'] ?? s['tiles'];
    if (ps is Map) {
      final v = ps[p];
      if (v is String) return v;
      if (v is Map) return (v['playback_url'] ?? v['hls'] ?? v['url']) as String?;
    }
    if (ps is List) {
      for (final e in ps.whereType<Map>()) {
        if ('${e['player_id'] ?? e['participant']}' == p) {
          return (e['playback_url'] ?? e['hls'] ?? e['url']) as String?;
        }
      }
    }
    return null;
  }

  Widget _body(BuildContext context) {
    final s = session!;
    final layout = '${s['layout'] ?? 'grid_4'}';
    final host = '${s['active_host'] ?? s['host_player_id'] ?? ''}';
    final status = '${s['status'] ?? 'active'}';
    final tt = Theme.of(context).textTheme;
    final ps = participants;
    final compositeUrl = s['composite_playback_url'] as String?;

    Widget tile(String p, int i, {double aspect = 16 / 9}) => AspectRatio(
          aspectRatio: aspect,
          child: HlsTile(
            url: urlFor(p),
            label: p == host ? '★ $p' : p,
            placeholder: urlFor(p) == null ? '$p is not live' : 'Waiting for $p',
            semanticsId: 'costream-tile-$i',
          ),
        );

    Widget grid;
    if (composite) {
      grid = AspectRatio(
        aspectRatio: 16 / 9,
        child: HlsTile(
            url: compositeUrl,
            label: 'Composite',
            placeholder: 'The composite stream is not live',
            semanticsId: 'costream-composite'),
      );
    } else if (layout == 'spotlight' && ps.isNotEmpty) {
      final main = ps.contains(host) ? host : ps.first;
      final rest = ps.where((p) => p != main).toList();
      grid = Column(children: [
        tile(main, 0),
        const SizedBox(height: T.s8),
        Row(children: [
          for (var i = 0; i < rest.length; i++) ...[
            if (i > 0) const SizedBox(width: T.s8),
            Expanded(child: tile(rest[i], i + 1)),
          ],
        ]),
      ]);
    } else {
      final cols = (layout == 'solo' || ps.length <= 1) ? 1 : 2;
      final shown = layout == 'grid_2' ? ps.take(2).toList() : ps.take(4).toList();
      grid = GridView.count(
        crossAxisCount: cols,
        shrinkWrap: true,
        physics: const NeverScrollableScrollPhysics(),
        crossAxisSpacing: T.s8,
        mainAxisSpacing: T.s8,
        childAspectRatio: layout == 'grid_2' ? 3 / 4 : 16 / 9,
        children: [for (var i = 0; i < shown.length; i++) tile(shown[i], i)],
      );
    }

    final msgs = messages.values.toList()
      ..sort((a, b) => ((a['sequence'] as num?) ?? 0).compareTo((b['sequence'] as num?) ?? 0));

    return Column(children: [
      Expanded(
        child: CustomScrollView(controller: _scroll, slivers: [
          SliverToBoxAdapter(
            child: Constrained(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(T.s16, T.s8, T.s16, 0),
                child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                  Wrap(spacing: T.s8, runSpacing: T.s8, children: [
                    StatusPill(
                        status == 'active' ? Icons.sensors : Icons.stop_circle_outlined,
                        status == 'active' ? 'Live co-stream' : titleCaseWord(status),
                        status == 'active' ? Tone.danger : Tone.neutral),
                    StatusPill(Icons.grid_view, layoutLabel(composite ? 'composite' : layout), Tone.brand),
                    if (host.isNotEmpty) StatusPill(Icons.star_outline, 'Host $host', Tone.neutral),
                  ]),
                  const SizedBox(height: T.s12),
                  if (compositeUrl != null)
                    Padding(
                      padding: const EdgeInsets.only(bottom: T.s12),
                      child: SegmentedButton<bool>(
                        segments: const [
                          ButtonSegment(value: false, icon: Icon(Icons.grid_view), label: Text('Split view')),
                          ButtonSegment(value: true, icon: Icon(Icons.layers_outlined), label: Text('Composite')),
                        ],
                        selected: {composite},
                        onSelectionChanged: (v) => setState(() => composite = v.first),
                      ),
                    ),
                  Semantics(identifier: 'costream-layout', container: true, child: grid),
                  SectionTitle('Chat',
                      inset: false,
                      trailing: StatusPill(_chatLive ? Icons.bolt : Icons.bolt_outlined,
                          _chatLive ? 'Live' : 'Connecting', _chatLive ? Tone.success : Tone.warning)),
                ]),
              ),
            ),
          ),
          SliverToBoxAdapter(
            child: Semantics(
              identifier: 'chat-message-list',
              container: true,
              label: 'Chat messages',
              child: Constrained(
                child: msgs.isEmpty
                    ? Padding(
                        padding: const EdgeInsets.all(T.s16),
                        child: Text('No messages yet. Say hello!', style: tt.bodyMedium),
                      )
                    : Column(children: [
                        for (final m in msgs)
                          Semantics(
                            identifier: 'chat-message',
                            label: '${m['sender'] ?? m['sender_id'] ?? 'Someone'}: ${m['text'] ?? ''}',
                            child: ExcludeSemantics(
                              child: ListTile(
                                dense: true,
                                leading: CircleAvatar(
                                  radius: 16,
                                  backgroundColor: context.accent.withValues(alpha: 0.12),
                                  child: Text(
                                      '${m['sender'] ?? m['sender_id'] ?? '?'}'.characters.first.toUpperCase(),
                                      style: TextStyle(color: context.accent, fontWeight: T.bold)),
                                ),
                                title: Text('${m['sender'] ?? m['sender_id'] ?? 'Someone'}',
                                    style: tt.titleSmall),
                                subtitle: Text('${m['text'] ?? ''}', style: tt.bodyLarge),
                              ),
                            ),
                          ),
                      ]),
              ),
            ),
          ),
          const SliverToBoxAdapter(child: SizedBox(height: T.s16)),
        ]),
      ),
      SafeArea(
        top: false,
        child: Container(
          padding: const EdgeInsets.fromLTRB(T.s12, T.s8, T.s8, T.s8),
          decoration: BoxDecoration(color: context.pal.card, border: Border(top: BorderSide(color: context.pal.line))),
          child: Row(children: [
            Expanded(
              child: Semantics(
                identifier: 'chat-input-field',
                child: TextField(
                  controller: _text,
                  minLines: 1,
                  maxLines: 3,
                  textInputAction: TextInputAction.send,
                  onSubmitted: (_) => _send(),
                  decoration: InputDecoration(
                    hintText: svc.session.loggedIn ? 'Message the room' : 'Sign in to chat',
                    contentPadding: const EdgeInsets.symmetric(horizontal: T.s12, vertical: T.s12),
                  ),
                ),
              ),
            ),
            const SizedBox(width: T.s8),
            Semantics(
              identifier: 'chat-send-btn',
              child: IconButton.filled(
                tooltip: 'Send message',
                onPressed: _sending ? null : _send,
                icon: _sending
                    ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.send),
              ),
            ),
          ]),
        ),
      ),
    ]);
  }
}

String titleCaseWord(String s) => s.isEmpty ? s : s[0].toUpperCase() + s.substring(1);

String layoutLabel(String l) => switch (l) {
      'grid_2' => '2-way grid',
      'grid_4' => '4-way grid',
      'spotlight' => 'Spotlight',
      'solo' => 'Solo',
      'composite' => 'Composite',
      _ => l,
    };

/// Reads a co-stream session: app GET routes first, then the audit route's
/// idempotent replay (for the seeded demo session), then the local copy.
Future<Map<String, dynamic>> loadCoStream(String id) async {
  final enc = Uri.encodeComponent(id);
  for (final p in ['/api/v1/costream/$enc', '/api/v1/costream/sessions/$enc', '/audit/v1/costream/session/$enc']) {
    try {
      final r = await svc.api.get(p);
      if (r is Map && (r['session_id'] != null || r['participants'] != null || r['active_participants'] != null)) {
        final m = Map<String, dynamic>.from(r);
        await LocalStore.writeJson(LocalStore.cache, 'costream|$id', m);
        return m;
      }
    } on ApiException catch (e) {
      if (e.network) break;
      if (!(e.status == 404 || e.status == 405)) rethrow;
    }
  }
  if (id == 'cs-session-101') {
    try {
      final r = await svc.api.post('/audit/v1/costream/session', demoCoStreamBody, auth: false);
      if (r is Map) {
        final m = Map<String, dynamic>.from(r);
        await LocalStore.writeJson(LocalStore.cache, 'costream|$id', m);
        return m;
      }
    } on ApiException catch (e) {
      if (!e.network && e.status != 409) rethrow;
    }
  }
  final cached = LocalStore.readJson(LocalStore.cache, 'costream|$id');
  if (cached != null) return cached;
  throw ApiException(status: 404, code: 'NOT_FOUND');
}

const demoCoStreamBody = {
  'session_id': 'cs-session-101',
  'host_player_id': 'player-alpha-1',
  'participants': ['player-alpha-1', 'player-alpha-2', 'player-beta-1', 'player-beta-2'],
  'layout': 'grid_4',
  'simulate_host_drop': false,
};

/// Writes need an account: explain, then offer the sign-in screen.
Future<void> promptSignIn(BuildContext context, String why) async {
  final go = await confirm(context,
      title: 'Sign in to continue', message: why, confirmLabel: 'Sign in', icon: Icons.login);
  if (go && context.mounted) {
    navKey.currentState?.popUntil((r) => r.isFirst);
  }
}

String newChatId() => newId('msg');
