import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../app.dart';
import '../core/errors.dart';
import '../ui/tokens.dart';
import '../ui/video_tile.dart';
import '../ui/widgets.dart';

/// FR-3.2 solo broadcast: request a stream key, see the RTMP / WHIP ingest URLs,
/// the HLS playback URL, a preview and a QR viewers can scan.
class CreatorScreen extends StatefulWidget {
  final String? playerId;
  final bool standalone;
  const CreatorScreen({super.key, this.playerId, this.standalone = true});
  @override
  State<CreatorScreen> createState() => _CreatorScreenState();
}

class _CreatorScreenState extends State<CreatorScreen> {
  Map<String, dynamic>? key;
  bool busy = false;

  String get playerId => widget.playerId ?? svc.session.playerId ?? 'player-alpha-1';

  Future<void> _call(String action) async {
    if (!svc.session.loggedIn) {
      showError(context, 'Sign in as a player to go live.');
      return;
    }
    if (action != 'generate') {
      final ok = await confirm(context,
          title: action == 'rotate' ? 'Rotate the stream key?' : 'Stop and revoke the key?',
          message: action == 'rotate'
              ? 'Your current key stops working right away; OBS must use the new one.'
              : 'Any live broadcast with this key is cut off immediately.',
          confirmLabel: action == 'rotate' ? 'Rotate' : 'Revoke',
          danger: action == 'revoke',
          icon: action == 'rotate' ? Icons.autorenew : Icons.block);
      if (!ok) return;
    }
    setState(() => busy = true);
    try {
      final r = await svc.api.post('/audit/v1/stream/key',
          {'action': action, 'player_id': playerId, 'label': 'NaSCon app'});
      if (!mounted) return;
      setState(() => key = r is Map ? Map<String, dynamic>.from(r) : null);
      showSuccess(
          context,
          switch (action) {
            'revoke' => 'Stream key revoked.',
            'rotate' => 'New stream key ready.',
            _ => 'Stream key ready. Start broadcasting.',
          });
    } on ApiException catch (e) {
      if (mounted) showError(context, e.message);
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Widget _copyRow(String label, String? value, IconData icon) => SoftCard(
        padding: EdgeInsets.zero,
        child: ListTile(
          leading: Icon(icon),
          title: Text(label),
          subtitle: Text(value ?? '—', maxLines: 2, overflow: TextOverflow.ellipsis),
          trailing: value == null
              ? null
              : IconButton(
                  tooltip: 'Copy $label',
                  icon: const Icon(Icons.copy),
                  onPressed: () {
                    Clipboard.setData(ClipboardData(text: value));
                    showSuccess(context, '$label copied.');
                  },
                ),
        ),
      );

  @override
  Widget build(BuildContext context) {
    final tt = Theme.of(context).textTheme;
    final k = key;
    final active = k != null && k['status'] == 'active';
    final ingest = (k?['ingest_endpoints'] as Map?) ?? const {};
    final playback = k?['playback_url'] as String?;
    return Semantics(
      identifier: 'screen-creator',
      label: 'Go live',
      container: true,
      child: Scaffold(
        appBar: brandBar(context, 'Go live'),
        body: Constrained(
          child: ListView(padding: const EdgeInsets.all(T.s16), children: [
            AspectRatio(
              aspectRatio: 16 / 9,
              child: HlsTile(
                url: active ? playback : null,
                label: active ? 'Your stream' : 'Offline',
                placeholder: active ? 'Start OBS or WHIP to see your stream' : 'Get a stream key to go live',
                semanticsId: 'creator-preview',
              ),
            ),
            const SizedBox(height: T.s16),
            Row(children: [
              Expanded(
                child: FilledButton.icon(
                  onPressed: busy ? null : () => _call(active ? 'rotate' : 'generate'),
                  icon: Icon(active ? Icons.autorenew : Icons.key),
                  label: Text(active ? 'Rotate key' : 'Get stream key'),
                ),
              ),
              if (active) ...[
                const SizedBox(width: T.s12),
                Expanded(
                  child: OutlinedButton.icon(
                    style: OutlinedButton.styleFrom(
                        foregroundColor: context.pal.danger,
                        side: BorderSide(color: context.pal.danger, width: 1.5)),
                    onPressed: busy ? null : () => _call('revoke'),
                    icon: const Icon(Icons.stop_circle_outlined),
                    label: const Text('Revoke'),
                  ),
                ),
              ],
            ]),
            if (k != null) ...[
              const SectionTitle('Broadcast from OBS or WHIP', inset: false),
              _copyRow('Stream key', k['stream_key'] as String?, Icons.key),
              const SizedBox(height: T.s8),
              _copyRow('RTMP server (OBS)', ingest['rtmp'] as String?, Icons.settings_input_antenna),
              const SizedBox(height: T.s8),
              _copyRow('WHIP URL (WebRTC)', ingest['webrtc'] as String?, Icons.wifi_tethering),
              const SizedBox(height: T.s8),
              _copyRow('Playback (HLS)', playback, Icons.play_circle_outline),
              const SizedBox(height: T.s12),
              Text(
                  'OBS: Settings > Stream > Service "Custom", Server = the RTMP URL without the key, '
                  'Stream key = the key above. Or paste the WHIP URL into OBS 30+ (WHIP output).',
                  style: tt.bodyMedium),
              if (active) ...[
                const SectionTitle('Viewers scan this', inset: false),
                Center(
                  child: Container(
                    padding: const EdgeInsets.all(T.s12),
                    decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(T.r16)),
                    child: Image.network(
                      svc.api.uri('/audit/v1/qr/${Uri.encodeComponent(playerId)}/export.png').toString(),
                      width: 200,
                      height: 200,
                      semanticLabel: 'QR code to watch $playerId',
                      errorBuilder: (_, __, ___) =>
                          const SizedBox(width: 200, height: 200, child: Center(child: Text('QR unavailable'))),
                    ),
                  ),
                ),
              ],
            ] else
              Padding(
                padding: const EdgeInsets.only(top: T.s16),
                child: Text('Get a key, then broadcast with OBS (RTMP) or any WHIP app. '
                    'Your stream appears above and viewers can scan your QR.', style: tt.bodyLarge),
              ),
          ]),
        ),
      ),
    );
  }
}

String phoneUrl(String url) => phoneReachable(url);
