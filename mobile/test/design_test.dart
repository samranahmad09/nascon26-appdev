import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:nascon_league/core/sync.dart';
import 'package:nascon_league/ui/tokens.dart';

void main() {
  final tenant = {
    'name': 'Alpha Sports',
    'branding': {'primary_color': '#0B5FFF', 'secondary_color': '#FFB800'},
  };

  for (final pal in [Pal.light, Pal.dark]) {
    final mode = pal.isDark ? 'dark' : 'light';
    test('$mode palette text keeps >= 4.5:1', () {
      for (final bg in [pal.card, pal.bg]) {
        expect(contrast(pal.ink, bg), greaterThanOrEqualTo(4.5));
        expect(contrast(pal.inkBody, bg), greaterThanOrEqualTo(4.5));
        expect(contrast(pal.inkMuted, bg), greaterThanOrEqualTo(4.5));
      }
      expect(contrast(pal.success, pal.successBg), greaterThanOrEqualTo(4.5));
      expect(contrast(pal.danger, pal.dangerBg), greaterThanOrEqualTo(4.5));
      expect(contrast(pal.warning, pal.warningBg), greaterThanOrEqualTo(4.5));
      expect(contrast(pal.neutral, pal.neutralBg), greaterThanOrEqualTo(4.5));
      expect(contrast(pal.onSnack, pal.snack), greaterThanOrEqualTo(4.5));
    });

    test('$mode theme accent and button text keep >= 4.5:1', () {
      final theme = buildTheme(Brand.fromTenant(tenant), pal.brightness);
      final cs = theme.colorScheme;
      expect(contrast(cs.primary, pal.card), greaterThanOrEqualTo(4.5));
      expect(contrast(cs.onPrimary, cs.primary), greaterThanOrEqualTo(4.5));
    });
  }

  test('score header keeps white text on the tenant colour', () {
    final brand = Brand.fromTenant(tenant);
    expect(contrast(onColor(brand.primary), brand.primary), greaterThanOrEqualTo(4.5));
  });

  test('a light tenant colour is darkened for text on white', () {
    final brand = Brand.fromTenant({
      'branding': {'primary_color': '#FFD54F', 'secondary_color': '#000000'},
    });
    expect(contrast(brand.primary, Colors.white), greaterThanOrEqualTo(4.5));
  });

  test('offline actions use the 5.14 UTC timestamp format', () {
    expect(utcStamp(DateTime.utc(2026, 10, 17, 9, 5, 3)), '2026-10-17T09:05:03Z');
    expect(RegExp(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$').hasMatch(utcStamp()), isTrue);
  });
}
