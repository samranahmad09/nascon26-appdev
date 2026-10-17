import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../core/net.dart';
import '../core/storage.dart';
import '../core/sync.dart';
import '../ui/tokens.dart';
import '../ui/widgets.dart';

/// Sync status: connection, the pending offline queue and per-action results.
class QueueScreen extends StatelessWidget {
  const QueueScreen({super.key});

  String _time(String iso) {
    final d = DateTime.tryParse(iso);
    return d == null ? iso : DateFormat('d MMM, HH:mm:ss').format(d.toLocal());
  }

  @override
  Widget build(BuildContext context) {
    final net = context.watch<Net>();
    final q = context.watch<SyncQueue>();
    final settings = context.watch<Settings>();
    final pending = q.pending;
    final tt = Theme.of(context).textTheme;
    final results = q.lastResults;
    final applied = results.where((r) => r.status == 'applied').length;
    final rejected = results.where((r) => r.status == 'rejected').length;
    final dup = results.where((r) => r.status == 'duplicate').length;
    final (word, icon, tone, detail) = switch (net.state) {
      NetState.syncing => ('Syncing', Icons.sync, Tone.brand, 'Sending your saved actions to the server…'),
      NetState.online => ('Online', Icons.cloud_done_outlined, Tone.success,
          'Connected to the server. Saved actions sync automatically.'),
      NetState.offline => ('Offline', Icons.cloud_off, Tone.warning,
          'Scoring and roster actions are saved on this phone and sync when you reconnect.'),
    };
    final (fg, bg) = toneColors(context, tone);
    return Scaffold(
      appBar: brandBar(context, 'Sync'),
      body: Constrained(
        child: ListView(padding: const EdgeInsets.all(T.s16), children: [
          Container(
            padding: const EdgeInsets.all(T.s16),
            decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(T.r16)),
            child: Row(children: [
              Icon(icon, color: fg, size: 36),
              const SizedBox(width: T.s16),
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(word, style: tt.titleLarge?.copyWith(color: fg)),
                  Text(detail, style: TextStyle(color: fg, fontSize: T.meta)),
                ]),
              ),
            ]),
          ),
          const SizedBox(height: T.s16),
          FilledButton.icon(
            style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(56)),
            onPressed: net.syncing || pending.isEmpty
                ? null
                : () async {
                    final problem = await q.syncNow();
                    if (!context.mounted) return;
                    if (problem != null) {
                      showError(context, problem);
                    } else {
                      final r = q.lastResults;
                      showSuccess(context,
                          'Sync finished: ${r.where((x) => x.status == 'applied').length} applied, ${r.where((x) => x.status == 'rejected').length} rejected.');
                    }
                  },
            icon: net.syncing
                ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.sync),
            label: Text(net.syncing
                ? 'Syncing…'
                : pending.isEmpty
                    ? 'Nothing to sync'
                    : 'Sync now (${pending.length})'),
          ),
          if (q.lastError != null && pending.isNotEmpty) ...[
            const SizedBox(height: T.s12),
            Container(
              padding: const EdgeInsets.all(T.s12),
              decoration: BoxDecoration(color: T.dangerBg, borderRadius: BorderRadius.circular(T.r12)),
              child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                const Icon(Icons.error_outline, color: T.danger),
                const SizedBox(width: T.s12),
                Expanded(
                  child: Text("Last sync didn't finish. ${q.lastError!}",
                      style: const TextStyle(color: T.danger, fontWeight: T.bold, fontSize: T.meta)),
                ),
              ]),
            ),
          ],
          SectionTitle('Waiting to sync', inset: false, trailing: Text('${pending.length}', style: tt.bodySmall)),
          if (pending.isEmpty)
            const Padding(
              padding: EdgeInsets.symmetric(horizontal: T.s8),
              child: Text('No actions waiting. Anything you do offline will appear here.'),
            )
          else
            for (final a in pending)
              Padding(
                padding: const EdgeInsets.only(bottom: T.s8),
                child: SoftCard(
                  padding: const EdgeInsets.fromLTRB(T.s16, T.s12, T.s8, T.s12),
                  child: Row(children: [
                    Icon(a.type == 'score_event' ? Icons.scoreboard_outlined : Icons.swap_horiz,
                        color: Theme.of(context).colorScheme.primary),
                    const SizedBox(width: T.s12),
                    Expanded(
                      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        Text(a.label, style: tt.titleMedium),
                        Text('${a.type == 'score_event' ? 'Scoring' : 'Roster'} · saved ${_time(a.queuedAt)} by ${a.by}',
                            style: tt.bodySmall),
                        const SizedBox(height: T.s4),
                        const StatusPill(Icons.hourglass_empty, 'Pending', Tone.warning),
                      ]),
                    ),
                    IconButton(
                      tooltip: 'Discard this action',
                      icon: const Icon(Icons.delete_outline),
                      onPressed: () async {
                        final ok = await confirm(context,
                            title: 'Discard this action?',
                            message: '"${a.label}" will be removed from this phone and never sent.',
                            confirmLabel: 'Discard',
                            danger: true,
                            icon: Icons.delete_outline);
                        if (ok) await q.remove(a.id);
                      },
                    ),
                  ]),
                ),
              ),
          if (results.isNotEmpty) ...[
            SectionTitle('Last sync results', inset: false,
                trailing: TextButton(onPressed: q.clearResults, child: const Text('Clear'))),
            if (q.lastSyncAt != null)
              Padding(
                padding: const EdgeInsets.only(bottom: T.s8),
                child: Text('Finished ${_time(q.lastSyncAt!)}', style: tt.bodySmall),
              ),
            Wrap(spacing: T.s8, runSpacing: T.s8, children: [
              StatusPill(Icons.check_circle, '$applied applied', Tone.success),
              StatusPill(Icons.cancel, '$rejected rejected', Tone.danger),
              if (dup > 0) StatusPill(Icons.content_copy, '$dup already synced', Tone.neutral),
            ]),
            const SizedBox(height: T.s12),
            for (final r in results)
              Padding(
                padding: const EdgeInsets.only(bottom: T.s8),
                child: SoftCard(
                  child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Icon(
                      r.status == 'applied'
                          ? Icons.check_circle
                          : r.status == 'rejected'
                              ? Icons.cancel
                              : Icons.content_copy,
                      color: r.status == 'applied'
                          ? T.success
                          : r.status == 'rejected'
                              ? T.danger
                              : T.neutral,
                    ),
                    const SizedBox(width: T.s12),
                    Expanded(
                      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        Text(r.label, style: tt.titleMedium),
                        const SizedBox(height: T.s4),
                        Text(
                          '${r.status == 'applied' ? 'Applied' : r.status == 'rejected' ? 'Rejected' : 'Already synced'}: ${r.reason}',
                          style: tt.bodyMedium,
                        ),
                      ]),
                    ),
                  ]),
                ),
              ),
          ],
          const SizedBox(height: T.s16),
          Text('Device ID: ${settings.deviceId}', style: tt.bodySmall),
        ]),
      ),
    );
  }
}
