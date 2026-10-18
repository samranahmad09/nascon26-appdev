import 'package:flutter/material.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

import '../app.dart';
import '../core/links.dart';
import '../ui/tokens.dart';
import '../ui/widgets.dart';

/// Native camera QR scanner: NaSCon short URLs (/q/...), web-player URLs and nascon:// links
/// open the matching screen in the app.
class QrScanScreen extends StatefulWidget {
  const QrScanScreen({super.key});
  @override
  State<QrScanScreen> createState() => _QrScanScreenState();
}

class _QrScanScreenState extends State<QrScanScreen> {
  final _controller = MobileScannerController(
    detectionSpeed: DetectionSpeed.noDuplicates,
    formats: const [BarcodeFormat.qrCode],
  );
  bool _busy = false;
  String? _last;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _onDetect(BarcodeCapture capture) async {
    if (_busy) return;
    final value = capture.barcodes.map((b) => b.rawValue).whereType<String>().firstOrNull;
    if (value == null || value == _last) return;
    setState(() {
      _busy = true;
      _last = value;
    });
    final id = await DeepLinks.entityOf(value);
    final screen = id == null ? null : DeepLinks.screenFor(id);
    if (!mounted) return;
    if (screen == null) {
      showError(context, "That QR code isn't a NaSCon link. Try a match, club, player or co-stream code.");
      setState(() => _busy = false);
      return;
    }
    showSuccess(context, 'Opening $id');
    navKey.currentState?.pushReplacement(MaterialPageRoute(builder: (_) => screen));
  }

  @override
  Widget build(BuildContext context) {
    final tt = Theme.of(context).textTheme;
    return Semantics(
      identifier: 'screen-qr-scanner',
      label: 'QR scanner',
      container: true,
      child: Scaffold(
        appBar: brandBar(context, 'Scan a QR code'),
        body: Column(children: [
          Expanded(
            child: Padding(
              padding: const EdgeInsets.all(T.s16),
              child: ClipRRect(
                borderRadius: BorderRadius.circular(T.r24),
                child: Stack(fit: StackFit.expand, children: [
                  MobileScanner(controller: _controller, onDetect: _onDetect),
                  IgnorePointer(
                    child: Center(
                      child: Container(
                        width: 240,
                        height: 240,
                        decoration: BoxDecoration(
                          border: Border.all(color: Colors.white, width: 3),
                          borderRadius: BorderRadius.circular(T.r24),
                        ),
                      ),
                    ),
                  ),
                  if (_busy)
                    Container(
                      color: Colors.black45,
                      alignment: Alignment.center,
                      child: const CircularProgressIndicator(color: Colors.white),
                    ),
                ]),
              ),
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(T.s24, 0, T.s24, T.s16),
            child: Text('Point the camera at a NaSCon QR poster: matches, clubs, players and co-streams open here.',
                textAlign: TextAlign.center, style: tt.bodyLarge),
          ),
          SafeArea(
            top: false,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(T.s16, 0, T.s16, T.s16),
              child: Row(children: [
                Expanded(
                  child: OutlinedButton.icon(
                    onPressed: () => _controller.toggleTorch(),
                    icon: const Icon(Icons.flashlight_on_outlined),
                    label: const Text('Torch'),
                  ),
                ),
                const SizedBox(width: T.s12),
                Expanded(
                  child: OutlinedButton.icon(
                    onPressed: () => _controller.switchCamera(),
                    icon: const Icon(Icons.cameraswitch_outlined),
                    label: const Text('Flip'),
                  ),
                ),
              ]),
            ),
          ),
        ]),
      ),
    );
  }
}

/// App-bar action that opens the scanner.
class ScanButton extends StatelessWidget {
  const ScanButton({super.key});
  @override
  Widget build(BuildContext context) => Semantics(
        identifier: 'action-scan-qr',
        child: IconButton(
          tooltip: 'Scan a QR code',
          icon: const Icon(Icons.qr_code_scanner),
          onPressed: () =>
              Navigator.of(context).push(MaterialPageRoute(builder: (_) => const QrScanScreen())),
        ),
      );
}
