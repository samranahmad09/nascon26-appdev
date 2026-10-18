import 'dart:async';

import 'package:flutter/material.dart';
import 'package:video_player/video_player.dart';

import '../app.dart';
import 'tokens.dart';

/// Media URLs from the server say "localhost"; on a phone that must be the laptop,
/// so swap the host for the configured server's host (keeping the media port).
String phoneReachable(String url) {
  final u = Uri.tryParse(url);
  final base = Uri.tryParse(svc.settings.baseUrl);
  if (u == null || base == null) return url;
  if (u.host == 'localhost' || u.host == '127.0.0.1' || u.host == 'backend' || u.host == 'mediamtx') {
    if (base.host == 'localhost' || base.host == '127.0.0.1') return u.replace(host: '127.0.0.1').toString();
    return u.replace(host: base.host).toString();
  }
  return url;
}

/// One HLS player tile with a clear placeholder while nothing is live (retries every 8 s).
class HlsTile extends StatefulWidget {
  final String? url;
  final String label;
  final String placeholder;
  final bool muted;
  final String? semanticsId;
  const HlsTile({
    super.key,
    required this.url,
    required this.label,
    this.placeholder = 'Not live yet',
    this.muted = true,
    this.semanticsId,
  });

  @override
  State<HlsTile> createState() => _HlsTileState();
}

class _HlsTileState extends State<HlsTile> {
  VideoPlayerController? _c;
  bool _live = false;
  Timer? _retry;
  bool _disposed = false;

  @override
  void initState() {
    super.initState();
    _start();
  }

  @override
  void didUpdateWidget(HlsTile old) {
    super.didUpdateWidget(old);
    if (old.url != widget.url) {
      _stop();
      _start();
    }
  }

  Future<void> _start() async {
    final url = widget.url;
    if (url == null || url.isEmpty) return;
    final c = VideoPlayerController.networkUrl(Uri.parse(phoneReachable(url)),
        formatHint: VideoFormat.hls);
    _c = c;
    try {
      await c.initialize().timeout(const Duration(seconds: 10));
      if (_disposed || _c != c) {
        c.dispose();
        return;
      }
      await c.setVolume(widget.muted ? 0 : 1);
      await c.play();
      setState(() => _live = true);
    } catch (_) {
      if (_disposed) return;
      c.dispose();
      if (_c == c) _c = null;
      setState(() => _live = false);
      _retry = Timer(const Duration(seconds: 8), () {
        if (!_disposed) _start();
      });
    }
  }

  void _stop() {
    _retry?.cancel();
    _c?.dispose();
    _c = null;
    _live = false;
  }

  @override
  void dispose() {
    _disposed = true;
    _stop();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final pal = context.pal;
    final c = _c;
    final child = _live && c != null && c.value.isInitialized
        ? FittedBox(
            fit: BoxFit.cover,
            clipBehavior: Clip.hardEdge,
            child: SizedBox(
              width: c.value.size.width,
              height: c.value.size.height,
              child: VideoPlayer(c),
            ),
          )
        : Container(
            color: pal.isDark ? const Color(0xFF0E1526) : const Color(0xFF1E293B),
            alignment: Alignment.center,
            padding: const EdgeInsets.all(T.s8),
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              Icon(widget.url == null ? Icons.videocam_off_outlined : Icons.hourglass_empty,
                  color: Colors.white70, size: 28),
              const SizedBox(height: T.s4),
              Text(widget.placeholder,
                  textAlign: TextAlign.center,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(color: Colors.white, fontSize: T.meta, fontWeight: T.bold)),
            ]),
          );
    return Semantics(
      identifier: widget.semanticsId,
      label: '${widget.label}: ${_live ? 'live video' : widget.placeholder}',
      child: ClipRRect(
        borderRadius: BorderRadius.circular(T.r12),
        child: Stack(fit: StackFit.expand, children: [
          child,
          Positioned(
            left: T.s8,
            bottom: T.s8,
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: T.s8, vertical: 2),
              decoration: BoxDecoration(
                  color: Colors.black.withValues(alpha: 0.6), borderRadius: BorderRadius.circular(999)),
              child: Row(mainAxisSize: MainAxisSize.min, children: [
                Icon(_live ? Icons.circle : Icons.circle_outlined,
                    size: 10, color: _live ? const Color(0xFFFF5A5A) : Colors.white70),
                const SizedBox(width: T.s4),
                Text(widget.label,
                    style: const TextStyle(color: Colors.white, fontSize: 12, fontWeight: T.bold)),
              ]),
            ),
          ),
        ]),
      ),
    );
  }
}
