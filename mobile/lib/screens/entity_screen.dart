import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../app.dart';
import '../core/errors.dart';
import '../ui/tokens.dart';
import '../ui/widgets.dart';
import 'creator_screen.dart';

/// Landing screen for nascon://entity/player-* (and any entity without a richer screen):
/// what it is, its live state and its shareable QR.
class EntityScreen extends StatefulWidget {
  final String entityId;
  const EntityScreen({super.key, required this.entityId});
  @override
  State<EntityScreen> createState() => _EntityScreenState();
}

class _EntityScreenState extends State<EntityScreen> {
  Map<String, dynamic>? info;
  Object? error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final r = await svc.api.get('/audit/v1/qr/${Uri.encodeComponent(widget.entityId)}?inspect=true');
      if (mounted) setState(() => info = r is Map ? Map<String, dynamic>.from(r) : {});
    } catch (e) {
      if (mounted) setState(() => error = e);
    }
  }

  @override
  Widget build(BuildContext context) {
    final tt = Theme.of(context).textTheme;
    final isPlayer = widget.entityId.startsWith('player-');
    final mine = svc.session.playerId == widget.entityId;
    return Semantics(
      identifier: isPlayer ? 'screen-player' : 'screen-entity',
      label: isPlayer ? 'Player ${widget.entityId}' : widget.entityId,
      container: true,
      child: Scaffold(
        appBar: brandBar(context, isPlayer ? 'Player' : 'Details'),
        body: Constrained(
          child: ListView(padding: const EdgeInsets.all(T.s16), children: [
            SoftCard(
              padding: const EdgeInsets.all(T.s24),
              child: Column(children: [
                CircleAvatar(
                  radius: 36,
                  backgroundColor: context.accent.withValues(alpha: 0.12),
                  child: Icon(isPlayer ? Icons.person : Icons.qr_code_2, size: 36, color: context.accent),
                ),
                const SizedBox(height: T.s12),
                Text(widget.entityId, style: tt.titleLarge, textAlign: TextAlign.center),
                if (info?['entity_status'] != null) ...[
                  const SizedBox(height: T.s8),
                  StatusPill(Icons.info_outline, '${info!['entity_status']}', Tone.brand),
                ],
              ]),
            ),
            const SectionTitle('Share', inset: false),
            if (error != null)
              Text(friendlyAny(error!), style: tt.bodyMedium)
            else if (info == null)
              const LoadingState('Loading…')
            else ...[
              Center(
                child: Container(
                  padding: const EdgeInsets.all(T.s12),
                  decoration:
                      BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(T.r16)),
                  child: Image.network(
                    svc.api.uri('/audit/v1/qr/${Uri.encodeComponent(widget.entityId)}/export.png').toString(),
                    width: 200,
                    height: 200,
                    semanticLabel: 'QR code for ${widget.entityId}',
                    errorBuilder: (_, __, ___) => const SizedBox(
                        width: 200, height: 200, child: Center(child: Text('QR unavailable'))),
                  ),
                ),
              ),
              if (info!['short_url'] != null)
                ListTile(
                  leading: const Icon(Icons.link),
                  title: Text('${info!['short_url']}'),
                  subtitle: const Text('Short link (tap to copy)'),
                  onTap: () {
                    Clipboard.setData(ClipboardData(text: '${info!['short_url']}'));
                    showSuccess(context, 'Link copied.');
                  },
                ),
            ],
            if (isPlayer && (mine || svc.session.role == 'super_admin')) ...[
              const SizedBox(height: T.s16),
              FilledButton.icon(
                onPressed: () => Navigator.of(context).push(
                    MaterialPageRoute(builder: (_) => CreatorScreen(playerId: widget.entityId))),
                icon: const Icon(Icons.videocam),
                label: const Text('Go live'),
              ),
            ],
          ]),
        ),
      ),
    );
  }
}
