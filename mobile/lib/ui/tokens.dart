import 'package:flutter/material.dart';

/// Design tokens: type scale, spacing, radii, elevation and motion.
/// Colour roles live in [Pal] (light and dark), read through `context.pal`.
///
/// Colour follows 60/30/10: a neutral base (60), ink for text (30), and the
/// tenant's primary colour as the accent for actions and key indicators (10).
/// Meaning is never carried by colour alone: every status also has an icon and a word.
class T {
  // Default brand before a tenant is known.
  static const defaultPrimary = Color(0xFF0B5FFF);
  static const defaultSecondary = Color(0xFFFFB800);

  // Type scale: 4 sizes, 2 weights. Numbers use tabular figures.
  static const display = 40.0; // score, totals
  static const title = 20.0; // screen + card titles, points
  static const body = 16.0; // body, list titles, buttons
  static const meta = 14.0; // captions, chips, badges
  static const regular = FontWeight.w400;
  static const bold = FontWeight.w700;
  static const tabular = [FontFeature.tabularFigures()];

  // 8-point spacing.
  static const s4 = 4.0;
  static const s8 = 8.0;
  static const s12 = 12.0;
  static const s16 = 16.0;
  static const s24 = 24.0;
  static const s32 = 32.0;
  static const s48 = 48.0;

  // Radii.
  static const r8 = 8.0;
  static const r12 = 12.0;
  static const r16 = 16.0;
  static const r24 = 24.0;

  static const tap = 48.0; // minimum tap target
  static const maxContent = 640.0; // keeps lines readable in landscape

  /// Soft, brand-tinted shadows (never pure black).
  static List<BoxShadow> shadow(Color tint, {bool hero = false, bool dark = false}) => [
        BoxShadow(
          color: dark
              ? Colors.black.withValues(alpha: hero ? 0.45 : 0.30)
              : tint.withValues(alpha: hero ? 0.22 : 0.08),
          blurRadius: hero ? 24 : 16,
          offset: Offset(0, hero ? 8 : 4),
        ),
      ];
}

/// Colour roles for one brightness. Text colours keep >= 4.5:1 on [card] and [bg].
class Pal {
  final Brightness brightness;
  final Color bg, card, ink, inkBody, inkMuted, line;
  final Color success, successBg, danger, dangerBg, warning, warningBg, neutral, neutralBg;
  final Color snack, onSnack;
  const Pal({
    required this.brightness,
    required this.bg,
    required this.card,
    required this.ink,
    required this.inkBody,
    required this.inkMuted,
    required this.line,
    required this.success,
    required this.successBg,
    required this.danger,
    required this.dangerBg,
    required this.warning,
    required this.warningBg,
    required this.neutral,
    required this.neutralBg,
    required this.snack,
    required this.onSnack,
  });

  bool get isDark => brightness == Brightness.dark;

  static const light = Pal(
    brightness: Brightness.light,
    bg: Color(0xFFF4F6FB),
    card: Colors.white,
    ink: Color(0xFF0F172A),
    inkBody: Color(0xFF334155),
    inkMuted: Color(0xFF475569),
    line: Color(0xFFE2E8F0),
    success: Color(0xFF0E7A3E),
    successBg: Color(0xFFE3F5EA),
    danger: Color(0xFFB42318),
    dangerBg: Color(0xFFFDECEA),
    warning: Color(0xFF8A5300),
    warningBg: Color(0xFFFFF3D6),
    neutral: Color(0xFF475569),
    neutralBg: Color(0xFFEDF1F7),
    snack: Color(0xFF0F172A),
    onSnack: Colors.white,
  );

  static const dark = Pal(
    brightness: Brightness.dark,
    bg: Color(0xFF0B1220),
    card: Color(0xFF141C2F),
    ink: Color(0xFFF1F5F9),
    inkBody: Color(0xFFCBD5E1),
    inkMuted: Color(0xFF9AA8BC),
    line: Color(0xFF26324A),
    success: Color(0xFF4ADE80),
    successBg: Color(0xFF0F2C1C),
    danger: Color(0xFFFC8181),
    dangerBg: Color(0xFF3B1517),
    warning: Color(0xFFFBBF24),
    warningBg: Color(0xFF33270A),
    neutral: Color(0xFFCBD5E1),
    neutralBg: Color(0xFF1F2A3D),
    snack: Color(0xFFE2E8F0),
    onSnack: Color(0xFF0F172A),
  );
}

extension PalX on BuildContext {
  /// Palette for the current theme brightness (registers a Theme dependency).
  Pal get pal => Theme.of(this).brightness == Brightness.dark ? Pal.dark : Pal.light;

  /// Accent (tenant primary, contrast-adjusted for the current brightness).
  Color get accent => Theme.of(this).colorScheme.primary;
}

/// Motion tokens. Every animation goes through [Motion.of] so the system
/// "remove animations" setting turns motion off everywhere.
class Motion {
  static const short = Duration(milliseconds: 150);
  static const medium = Duration(milliseconds: 250);
  static const long = Duration(milliseconds: 350);
  static const curve = Curves.easeOutCubic;
  static const emphasized = Cubic(0.05, 0.7, 0.1, 1.0); // emphasized decelerate

  static bool reduced(BuildContext context) =>
      MediaQuery.maybeDisableAnimationsOf(context) ?? false;

  /// Duration scaled to zero when the user asked for reduced motion.
  static Duration of(BuildContext context, Duration d) => reduced(context) ? Duration.zero : d;
}

/// Shared-axis style page transition (slide + fade), off when motion is reduced.
class SharedAxisPageTransitionsBuilder extends PageTransitionsBuilder {
  const SharedAxisPageTransitionsBuilder();

  @override
  Widget buildTransitions<T>(PageRoute<T> route, BuildContext context, Animation<double> animation,
      Animation<double> secondaryAnimation, Widget child) {
    if (Motion.reduced(context)) return child;
    final enter = CurvedAnimation(parent: animation, curve: Motion.emphasized, reverseCurve: Curves.easeInCubic);
    final exit = CurvedAnimation(parent: secondaryAnimation, curve: Motion.curve);
    return SlideTransition(
      position: Tween(begin: const Offset(0.08, 0), end: Offset.zero).animate(enter),
      child: FadeTransition(
        opacity: Tween(begin: 0.0, end: 1.0).animate(
            CurvedAnimation(parent: animation, curve: const Interval(0.0, 0.7, curve: Curves.easeOut))),
        child: SlideTransition(
          position: Tween(begin: Offset.zero, end: const Offset(-0.06, 0)).animate(exit),
          child: FadeTransition(
            opacity: Tween(begin: 1.0, end: 0.0).animate(
                CurvedAnimation(parent: secondaryAnimation, curve: const Interval(0.0, 0.6))),
            child: child,
          ),
        ),
      ),
    );
  }
}

double _lum(Color c) => c.computeLuminance();

double contrast(Color a, Color b) {
  final l1 = _lum(a), l2 = _lum(b);
  final hi = l1 > l2 ? l1 : l2, lo = l1 > l2 ? l2 : l1;
  return (hi + 0.05) / (lo + 0.05);
}

/// Darkens (on light backgrounds) or lightens (on dark ones) a colour until it
/// reaches the contrast ratio against [bg].
Color ensureContrast(Color c, Color bg, double ratio) {
  final lighten = _lum(bg) < 0.2;
  var hsl = HSLColor.fromColor(c);
  var out = c;
  var guard = 0;
  while (contrast(out, bg) < ratio && guard < 50) {
    hsl = hsl.withLightness((hsl.lightness + (lighten ? 0.02 : -0.02)).clamp(0.0, 1.0));
    out = hsl.toColor();
    guard++;
  }
  return out;
}

Color onColor(Color c) =>
    contrast(Colors.white, c) >= 4.5 ? Colors.white : const Color(0xFF111111);

Color parseHex(String? hex, Color fallback) {
  if (hex == null) return fallback;
  var h = hex.replaceAll('#', '').trim();
  if (h.length == 6) h = 'FF$h';
  final v = int.tryParse(h, radix: 16);
  return v == null ? fallback : Color(v);
}

class Brand {
  final Color raw; // tenant primary as configured
  final Color primary; // accessible accent on light surfaces
  final Color secondary;
  final String name;
  final String? logoUrl;
  const Brand(this.raw, this.primary, this.secondary, this.name, this.logoUrl);

  static Brand fromTenant(Map<String, dynamic>? tenant) {
    final b = (tenant?['branding'] as Map?) ?? const {};
    final p = parseHex(b['primary_color'] as String?, T.defaultPrimary);
    final s = parseHex(b['secondary_color'] as String?, T.defaultSecondary);
    return Brand(p, ensureContrast(p, Colors.white, 4.5), s,
        (tenant?['name'] ?? 'NaSCon League').toString(), b['logo_url'] as String?);
  }
}

ThemeData buildTheme(Brand brand, Brightness brightness) {
  final pal = brightness == Brightness.dark ? Pal.dark : Pal.light;
  // Accent must read as text/icon on cards in both themes.
  final p = pal.isDark ? ensureContrast(brand.raw, pal.card, 4.5) : brand.primary;
  final scheme = ColorScheme.fromSeed(seedColor: brand.raw, brightness: brightness).copyWith(
    primary: p,
    onPrimary: onColor(p),
    primaryContainer: Color.alphaBlend(p.withValues(alpha: pal.isDark ? 0.18 : 0.10), pal.card),
    onPrimaryContainer: ensureContrast(p, pal.card, 7),
    secondary: brand.secondary,
    onSecondary: onColor(brand.secondary),
    surface: pal.card,
    onSurface: pal.ink,
    onSurfaceVariant: pal.inkMuted,
    error: pal.danger,
    outline: pal.line,
    surfaceContainerHighest: pal.neutralBg,
  );
  TextStyle ts(double size, FontWeight w, Color c) =>
      TextStyle(fontSize: size, fontWeight: w, color: c, height: 1.3);
  final text = TextTheme(
    displaySmall: ts(T.display, T.bold, pal.ink).copyWith(fontFeatures: T.tabular, height: 1.1),
    headlineSmall: ts(T.title, T.bold, pal.ink),
    titleLarge: ts(T.title, T.bold, pal.ink),
    titleMedium: ts(T.body, T.bold, pal.ink),
    titleSmall: ts(T.meta, T.bold, pal.ink),
    bodyLarge: ts(T.body, T.regular, pal.inkBody),
    bodyMedium: ts(T.meta, T.regular, pal.inkBody),
    bodySmall: ts(T.meta, T.regular, pal.inkMuted),
    labelLarge: ts(T.body, T.bold, pal.ink),
    labelMedium: ts(T.meta, T.bold, pal.ink),
    labelSmall: ts(12, T.bold, pal.ink),
  );
  final shape12 = RoundedRectangleBorder(borderRadius: BorderRadius.circular(T.r12));
  return ThemeData(
    useMaterial3: true,
    brightness: brightness,
    colorScheme: scheme,
    scaffoldBackgroundColor: pal.bg,
    canvasColor: pal.bg,
    textTheme: text,
    materialTapTargetSize: MaterialTapTargetSize.padded,
    pageTransitionsTheme: const PageTransitionsTheme(builders: {
      TargetPlatform.android: SharedAxisPageTransitionsBuilder(),
      TargetPlatform.iOS: SharedAxisPageTransitionsBuilder(),
    }),
    appBarTheme: AppBarTheme(
      backgroundColor: pal.bg,
      foregroundColor: pal.ink,
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      scrolledUnderElevation: 1,
      centerTitle: false,
      titleTextStyle: ts(T.title, T.bold, pal.ink),
    ),
    cardTheme: CardThemeData(
      color: pal.card,
      elevation: 0,
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(T.r16),
        side: BorderSide(color: pal.isDark ? pal.line : p.withValues(alpha: 0.08)),
      ),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        minimumSize: const Size(64, 52),
        shape: shape12,
        textStyle: ts(T.body, T.bold, onColor(p)),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        minimumSize: const Size(64, T.tap),
        shape: shape12,
        side: BorderSide(color: p.withValues(alpha: 0.6), width: 1.5),
        foregroundColor: p,
        textStyle: ts(T.body, T.bold, p),
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(
        minimumSize: const Size(T.tap, T.tap),
        foregroundColor: p,
        textStyle: ts(T.body, T.bold, p),
      ),
    ),
    iconButtonTheme: IconButtonThemeData(
      style: IconButton.styleFrom(minimumSize: const Size(T.tap, T.tap)),
    ),
    iconTheme: IconThemeData(color: pal.ink),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: pal.card,
      contentPadding: const EdgeInsets.symmetric(horizontal: T.s16, vertical: T.s16),
      border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(T.r12), borderSide: BorderSide(color: pal.line)),
      enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(T.r12), borderSide: BorderSide(color: pal.line)),
      focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(T.r12), borderSide: BorderSide(color: p, width: 2)),
      labelStyle: ts(T.body, T.regular, pal.inkMuted),
      hintStyle: ts(T.body, T.regular, pal.inkMuted),
    ),
    chipTheme: ChipThemeData(
      labelStyle: ts(T.meta, T.bold, pal.ink),
      backgroundColor: pal.card,
      selectedColor: p.withValues(alpha: pal.isDark ? 0.28 : 0.14),
      checkmarkColor: pal.ink,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(T.r12)),
      side: BorderSide(color: pal.line),
      padding: const EdgeInsets.symmetric(horizontal: T.s8, vertical: T.s8),
    ),
    navigationBarTheme: NavigationBarThemeData(
      height: 72,
      backgroundColor: pal.card,
      surfaceTintColor: Colors.transparent,
      indicatorColor: p.withValues(alpha: pal.isDark ? 0.24 : 0.12),
      labelTextStyle: WidgetStateProperty.resolveWith(
          (s) => ts(12, T.bold, s.contains(WidgetState.selected) ? p : pal.inkMuted)),
      iconTheme: WidgetStateProperty.resolveWith(
          (s) => IconThemeData(color: s.contains(WidgetState.selected) ? p : pal.inkMuted)),
    ),
    snackBarTheme: SnackBarThemeData(
      behavior: SnackBarBehavior.floating,
      backgroundColor: pal.snack,
      contentTextStyle: ts(T.meta, T.regular, pal.onSnack),
      actionTextColor: pal.isDark ? T.defaultPrimary : brand.secondary,
      shape: shape12,
    ),
    dialogTheme: DialogThemeData(
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(T.r24)),
      backgroundColor: pal.card,
      surfaceTintColor: Colors.transparent,
      titleTextStyle: ts(T.title, T.bold, pal.ink),
      contentTextStyle: ts(T.body, T.regular, pal.inkBody),
    ),
    bottomSheetTheme: BottomSheetThemeData(
      backgroundColor: pal.card,
      surfaceTintColor: Colors.transparent,
      showDragHandle: true,
      shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(T.r24))),
    ),
    listTileTheme: ListTileThemeData(
      contentPadding: const EdgeInsets.symmetric(horizontal: T.s16),
      minVerticalPadding: T.s12,
      iconColor: pal.inkMuted,
      textColor: pal.ink,
    ),
    radioTheme: RadioThemeData(
      fillColor: WidgetStateProperty.resolveWith((s) => s.contains(WidgetState.selected) ? p : pal.inkMuted),
    ),
    switchTheme: const SwitchThemeData(),
    dividerTheme: DividerThemeData(color: pal.line, space: 1, thickness: 1),
    progressIndicatorTheme: ProgressIndicatorThemeData(color: p),
  );
}
