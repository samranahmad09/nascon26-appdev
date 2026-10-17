import 'dart:ui' show FontFeature;

import 'package:flutter/material.dart';

/// Design tokens. One place for colour roles, type scale, spacing, radii and elevation.
///
/// Colour follows 60/30/10: a cool neutral base (60), navy ink for text (30),
/// and the tenant's primary colour as the accent for actions and key indicators (10).
/// Meaning is never carried by colour alone: every status also has an icon and a word.
class T {
  // Neutral base (60) and ink (30). Text colours keep >= 4.5:1 on white.
  static const bg = Color(0xFFF4F6FB);
  static const card = Colors.white;
  static const ink = Color(0xFF0F172A); // headings
  static const inkBody = Color(0xFF334155); // body
  static const inkMuted = Color(0xFF475569); // secondary text (7.5:1 on white)
  static const line = Color(0xFFE2E8F0);

  // Semantic roles (text colour + soft background), all with icons in use.
  static const success = Color(0xFF0E7A3E);
  static const successBg = Color(0xFFE3F5EA);
  static const danger = Color(0xFFB42318);
  static const dangerBg = Color(0xFFFDECEA);
  static const warning = Color(0xFF8A5300);
  static const warningBg = Color(0xFFFFF3D6);
  static const neutral = Color(0xFF475569);
  static const neutralBg = Color(0xFFEDF1F7);

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
  static List<BoxShadow> shadow(Color tint, {bool hero = false}) => [
        BoxShadow(
          color: tint.withValues(alpha: hero ? 0.22 : 0.08),
          blurRadius: hero ? 24 : 16,
          offset: Offset(0, hero ? 8 : 4),
        ),
      ];
}

double _lum(Color c) => c.computeLuminance();

double contrast(Color a, Color b) {
  final l1 = _lum(a), l2 = _lum(b);
  final hi = l1 > l2 ? l1 : l2, lo = l1 > l2 ? l2 : l1;
  return (hi + 0.05) / (lo + 0.05);
}

/// Darkens a colour until it reaches the contrast ratio against [bg].
Color ensureContrast(Color c, Color bg, double ratio) {
  var hsl = HSLColor.fromColor(c);
  var out = c;
  var guard = 0;
  while (contrast(out, bg) < ratio && guard < 40) {
    hsl = hsl.withLightness((hsl.lightness - 0.02).clamp(0.0, 1.0));
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
  final Color primary; // accessible accent (darkened if needed)
  final Color secondary;
  final String name;
  final String? logoUrl;
  const Brand(this.primary, this.secondary, this.name, this.logoUrl);

  static Brand fromTenant(Map<String, dynamic>? tenant) {
    final b = (tenant?['branding'] as Map?) ?? const {};
    final p = parseHex(b['primary_color'] as String?, T.defaultPrimary);
    final s = parseHex(b['secondary_color'] as String?, T.defaultSecondary);
    return Brand(ensureContrast(p, Colors.white, 4.5), s,
        (tenant?['name'] ?? 'NaSCon League').toString(), b['logo_url'] as String?);
  }
}

ThemeData buildTheme(Brand brand) {
  final p = brand.primary;
  final scheme = ColorScheme.fromSeed(seedColor: p).copyWith(
    primary: p,
    onPrimary: onColor(p),
    primaryContainer: Color.alphaBlend(p.withValues(alpha: 0.10), Colors.white),
    onPrimaryContainer: ensureContrast(p, Colors.white, 7),
    secondary: brand.secondary,
    onSecondary: onColor(brand.secondary),
    surface: T.card,
    onSurface: T.ink,
    onSurfaceVariant: T.inkMuted,
    error: T.danger,
    outline: T.line,
  );
  TextStyle ts(double size, FontWeight w, [Color c = T.ink]) =>
      TextStyle(fontSize: size, fontWeight: w, color: c, height: 1.3);
  final text = TextTheme(
    displaySmall: ts(T.display, T.bold).copyWith(fontFeatures: T.tabular, height: 1.1),
    headlineSmall: ts(T.title, T.bold),
    titleLarge: ts(T.title, T.bold),
    titleMedium: ts(T.body, T.bold),
    titleSmall: ts(T.meta, T.bold),
    bodyLarge: ts(T.body, T.regular, T.inkBody),
    bodyMedium: ts(T.meta, T.regular, T.inkBody),
    bodySmall: ts(T.meta, T.regular, T.inkMuted),
    labelLarge: ts(T.body, T.bold),
    labelMedium: ts(T.meta, T.bold),
    labelSmall: ts(12, T.bold),
  );
  final shape12 = RoundedRectangleBorder(borderRadius: BorderRadius.circular(T.r12));
  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    scaffoldBackgroundColor: T.bg,
    textTheme: text,
    materialTapTargetSize: MaterialTapTargetSize.padded,
    appBarTheme: AppBarTheme(
      backgroundColor: T.bg,
      foregroundColor: T.ink,
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      scrolledUnderElevation: 1,
      centerTitle: false,
      titleTextStyle: ts(T.title, T.bold),
    ),
    cardTheme: CardThemeData(
      color: T.card,
      elevation: 0,
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(T.r16),
        side: BorderSide(color: p.withValues(alpha: 0.08)),
      ),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        minimumSize: const Size(64, 52),
        shape: shape12,
        textStyle: ts(T.body, T.bold),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        minimumSize: const Size(64, T.tap),
        shape: shape12,
        side: BorderSide(color: p.withValues(alpha: 0.5), width: 1.5),
        foregroundColor: p,
        textStyle: ts(T.body, T.bold),
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(
        minimumSize: const Size(T.tap, T.tap),
        foregroundColor: p,
        textStyle: ts(T.body, T.bold),
      ),
    ),
    iconButtonTheme: IconButtonThemeData(
      style: IconButton.styleFrom(minimumSize: const Size(T.tap, T.tap)),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: Colors.white,
      contentPadding: const EdgeInsets.symmetric(horizontal: T.s16, vertical: T.s16),
      border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(T.r12),
          borderSide: const BorderSide(color: T.line)),
      enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(T.r12),
          borderSide: const BorderSide(color: T.line)),
      focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(T.r12),
          borderSide: BorderSide(color: p, width: 2)),
      labelStyle: ts(T.body, T.regular, T.inkMuted),
    ),
    chipTheme: ChipThemeData(
      labelStyle: ts(T.meta, T.bold),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(T.r12)),
      side: const BorderSide(color: T.line),
      padding: const EdgeInsets.symmetric(horizontal: T.s8, vertical: T.s8),
    ),
    navigationBarTheme: NavigationBarThemeData(
      height: 72,
      backgroundColor: Colors.white,
      surfaceTintColor: Colors.transparent,
      indicatorColor: p.withValues(alpha: 0.12),
      labelTextStyle: WidgetStateProperty.resolveWith((s) => ts(
          12, T.bold, s.contains(WidgetState.selected) ? p : T.inkMuted)),
      iconTheme: WidgetStateProperty.resolveWith((s) =>
          IconThemeData(color: s.contains(WidgetState.selected) ? p : T.inkMuted)),
    ),
    snackBarTheme: SnackBarThemeData(
      behavior: SnackBarBehavior.floating,
      backgroundColor: T.ink,
      contentTextStyle: ts(T.meta, T.regular, Colors.white),
      shape: shape12,
    ),
    dialogTheme: DialogThemeData(
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(T.r24)),
      backgroundColor: Colors.white,
      surfaceTintColor: Colors.transparent,
      titleTextStyle: ts(T.title, T.bold),
      contentTextStyle: ts(T.body, T.regular, T.inkBody),
    ),
    bottomSheetTheme: const BottomSheetThemeData(
      backgroundColor: Colors.white,
      surfaceTintColor: Colors.transparent,
      showDragHandle: true,
      shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(T.r24))),
    ),
    listTileTheme: const ListTileThemeData(
      contentPadding: EdgeInsets.symmetric(horizontal: T.s16),
      minVerticalPadding: T.s12,
    ),
    dividerTheme: const DividerThemeData(color: T.line, space: 1, thickness: 1),
    progressIndicatorTheme: ProgressIndicatorThemeData(color: p),
  );
}
