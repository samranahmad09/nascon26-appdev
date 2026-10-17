import 'dart:math' as math;

import 'package:flutter/material.dart';

import 'tokens.dart';

/// Fades and lifts a list item in once, staggered by [index] (first 8 items only).
class FadeSlideIn extends StatefulWidget {
  final int index;
  final Widget child;
  const FadeSlideIn({super.key, required this.index, required this.child});

  @override
  State<FadeSlideIn> createState() => _FadeSlideInState();
}

class _FadeSlideInState extends State<FadeSlideIn> with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(vsync: this, duration: Motion.long);
  late final Animation<double> _a = CurvedAnimation(parent: _c, curve: Motion.curve);
  bool _started = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_started) return;
    _started = true;
    if (Motion.reduced(context)) {
      _c.value = 1;
      return;
    }
    final delay = Duration(milliseconds: 40 * math.min(widget.index, 8));
    Future.delayed(delay, () {
      if (mounted) _c.forward();
    });
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => FadeTransition(
        opacity: _a,
        child: SlideTransition(
          position: Tween(begin: const Offset(0, 0.06), end: Offset.zero).animate(_a),
          child: widget.child,
        ),
      );
}

/// Shimmering placeholder block used while content loads.
class Shimmer extends StatefulWidget {
  final Widget child;
  const Shimmer({super.key, required this.child});
  @override
  State<Shimmer> createState() => _ShimmerState();
}

class _ShimmerState extends State<Shimmer> with SingleTickerProviderStateMixin {
  late final AnimationController _c =
      AnimationController(vsync: this, duration: const Duration(milliseconds: 1200));

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (Motion.reduced(context)) {
      _c.stop();
    } else if (!_c.isAnimating) {
      _c.repeat();
    }
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final pal = context.pal;
    final base = pal.neutralBg;
    final hi = pal.isDark ? const Color(0xFF2A364D) : Colors.white;
    return AnimatedBuilder(
      animation: _c,
      builder: (context, child) => ShaderMask(
        blendMode: BlendMode.srcATop,
        shaderCallback: (rect) {
          final dx = (_c.value * 2 - 0.5) * rect.width;
          return LinearGradient(
            colors: [base, hi, base],
            stops: const [0.35, 0.5, 0.65],
            transform: _Slide(dx),
          ).createShader(rect);
        },
        child: child,
      ),
      child: widget.child,
    );
  }
}

class _Slide extends GradientTransform {
  final double dx;
  const _Slide(this.dx);
  @override
  Matrix4 transform(Rect bounds, {TextDirection? textDirection}) =>
      Matrix4.translationValues(dx, 0, 0);
}

class _Bone extends StatelessWidget {
  final double? width;
  final double height;
  final double radius;
  const _Bone({this.width, required this.height, this.radius = T.r8});
  @override
  Widget build(BuildContext context) => Container(
        width: width,
        height: height,
        decoration: BoxDecoration(
          color: context.pal.neutralBg,
          borderRadius: BorderRadius.circular(radius),
        ),
      );
}

/// Card-shaped skeletons that mirror the real list layout.
class SkeletonList extends StatelessWidget {
  final String label; // read by screen readers
  final int count;
  final bool hero;
  const SkeletonList({super.key, required this.label, this.count = 5, this.hero = false});

  @override
  Widget build(BuildContext context) {
    final pal = context.pal;
    Widget card() => Container(
          margin: const EdgeInsets.fromLTRB(T.s16, 0, T.s16, T.s12),
          padding: const EdgeInsets.all(T.s16),
          decoration: BoxDecoration(
            color: pal.card,
            borderRadius: BorderRadius.circular(T.r16),
            border: Border.all(color: pal.line),
          ),
          child: const Row(children: [
            _Bone(width: 44, height: 44, radius: T.r12),
            SizedBox(width: T.s12),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                _Bone(height: 16, width: 180),
                SizedBox(height: T.s8),
                _Bone(height: 12, width: 120),
              ]),
            ),
            SizedBox(width: T.s12),
            _Bone(width: 28, height: 24),
          ]),
        );
    return Semantics(
      label: label,
      liveRegion: true,
      child: ExcludeSemantics(
        child: Shimmer(
          child: ListView(
            physics: const NeverScrollableScrollPhysics(),
            padding: const EdgeInsets.only(top: T.s16),
            children: [
              if (hero)
                Container(
                  height: 180,
                  margin: const EdgeInsets.fromLTRB(T.s16, 0, T.s16, T.s16),
                  decoration: BoxDecoration(
                    color: pal.neutralBg,
                    borderRadius: BorderRadius.circular(T.r24),
                  ),
                ),
              for (var i = 0; i < count; i++) card(),
            ],
          ),
        ),
      ),
    );
  }
}

/// IndexedStack that keeps every tab alive and cross-fades the newly selected one.
class FadeIndexedStack extends StatefulWidget {
  final int index;
  final List<Widget> children;
  const FadeIndexedStack({super.key, required this.index, required this.children});
  @override
  State<FadeIndexedStack> createState() => _FadeIndexedStackState();
}

class _FadeIndexedStackState extends State<FadeIndexedStack> with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(vsync: this, duration: Motion.medium, value: 1);

  @override
  void didUpdateWidget(FadeIndexedStack old) {
    super.didUpdateWidget(old);
    if (old.index != widget.index && !Motion.reduced(context)) {
      _c.forward(from: 0);
    }
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final a = CurvedAnimation(parent: _c, curve: Motion.curve);
    return FadeTransition(
      opacity: a,
      child: SlideTransition(
        position: Tween(begin: const Offset(0, 0.015), end: Offset.zero).animate(a),
        child: IndexedStack(index: widget.index, children: widget.children),
      ),
    );
  }
}

/// Briefly scales its child up when [value] changes (badges, counters).
class BumpOnChange extends StatefulWidget {
  final Object? value;
  final Widget child;
  final double scale;
  const BumpOnChange({super.key, required this.value, required this.child, this.scale = 1.25});
  @override
  State<BumpOnChange> createState() => _BumpOnChangeState();
}

class _BumpOnChangeState extends State<BumpOnChange> with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(vsync: this, duration: Motion.long);

  @override
  void didUpdateWidget(BumpOnChange old) {
    super.didUpdateWidget(old);
    if (old.value != widget.value && !Motion.reduced(context)) _c.forward(from: 0);
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
        animation: _c,
        builder: (context, child) {
          // up then back down: 0 -> 1 -> 0 over the run
          final t = Curves.easeOutCubic.transform(math.sin(_c.value * math.pi).clamp(0.0, 1.0));
          return Transform.scale(scale: 1 + (widget.scale - 1) * t, child: child);
        },
        child: widget.child,
      );
}

/// A check mark that draws itself inside a circle (payment success).
class AnimatedCheck extends StatefulWidget {
  final double size;
  final Color color;
  final Color background;
  const AnimatedCheck({super.key, required this.size, required this.color, required this.background});
  @override
  State<AnimatedCheck> createState() => _AnimatedCheckState();
}

class _AnimatedCheckState extends State<AnimatedCheck> with SingleTickerProviderStateMixin {
  late final AnimationController _c =
      AnimationController(vsync: this, duration: const Duration(milliseconds: 700));

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (Motion.reduced(context)) {
      _c.value = 1;
    } else if (_c.value == 0) {
      _c.forward();
    }
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
        animation: _c,
        builder: (context, _) {
          final circle = Curves.easeOutBack.transform((_c.value / 0.5).clamp(0.0, 1.0));
          final tick = Curves.easeOutCubic.transform(((_c.value - 0.35) / 0.65).clamp(0.0, 1.0));
          return SizedBox(
            width: widget.size * 1.5,
            height: widget.size * 1.5,
            child: Stack(alignment: Alignment.center, children: [
              // soft glow ring
              Opacity(
                opacity: (1 - _c.value).clamp(0.0, 0.6),
                child: Container(
                  width: widget.size * (1 + 0.5 * _c.value),
                  height: widget.size * (1 + 0.5 * _c.value),
                  decoration: BoxDecoration(shape: BoxShape.circle, color: widget.background),
                ),
              ),
              Transform.scale(
                scale: circle,
                child: Container(
                  width: widget.size,
                  height: widget.size,
                  decoration: BoxDecoration(color: widget.background, shape: BoxShape.circle),
                  child: CustomPaint(painter: _CheckPainter(tick, widget.color)),
                ),
              ),
            ]),
          );
        },
      );
}

class _CheckPainter extends CustomPainter {
  final double t;
  final Color color;
  _CheckPainter(this.t, this.color);

  @override
  void paint(Canvas canvas, Size size) {
    final p = Path()
      ..moveTo(size.width * 0.28, size.height * 0.52)
      ..lineTo(size.width * 0.44, size.height * 0.67)
      ..lineTo(size.width * 0.73, size.height * 0.36);
    final paint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = size.width * 0.09
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round;
    for (final m in p.computeMetrics()) {
      canvas.drawPath(m.extractPath(0, m.length * t), paint);
    }
  }

  @override
  bool shouldRepaint(_CheckPainter old) => old.t != t || old.color != color;
}
