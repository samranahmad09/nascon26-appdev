import 'package:flutter/foundation.dart';
import 'package:intl/intl.dart';

String money(num minor, [String currency = 'PKR']) =>
    '$currency ${NumberFormat('#,##0.00').format(minor / 100)}';

String when(String? iso) {
  if (iso == null || iso.isEmpty) return 'Time to be confirmed';
  final d = DateTime.tryParse(iso);
  if (d == null) return iso;
  return DateFormat('EEE d MMM, HH:mm').format(d.toLocal());
}

String savedAgo(DateTime? t) {
  if (t == null) return 'earlier';
  return DateFormat('d MMM, HH:mm').format(t.toLocal());
}

String titleCase(String s) => s
    .replaceAll('_', ' ')
    .split(' ')
    .where((w) => w.isNotEmpty)
    .map((w) => w[0].toUpperCase() + w.substring(1))
    .join(' ');

class CartLine {
  final Map<String, dynamic> product;
  final Map<String, dynamic> variant;
  int qty;
  CartLine(this.product, this.variant, this.qty);

  String get variantId => '${variant['variant_id']}';
  int get unit => (variant['price_minor'] as num?)?.toInt() ?? 0;
  int get stock => (variant['stock'] as num?)?.toInt() ?? 0;
  int get total => unit * qty;
  String get title => '${product['name']}';
  String get options => [variant['size'], variant['color']]
      .where((e) => e != null && '$e'.isNotEmpty)
      .map((e) => titleCase('$e'))
      .join(' · ');
}

class Cart extends ChangeNotifier {
  final List<CartLine> lines = [];

  int get count => lines.fold(0, (s, l) => s + l.qty);
  int get subtotal => lines.fold(0, (s, l) => s + l.total);
  String get currency =>
      lines.isEmpty ? 'PKR' : '${lines.first.variant['currency'] ?? 'PKR'}';

  /// Returns false when the max quantity (10 or stock) would be exceeded.
  bool add(Map<String, dynamic> product, Map<String, dynamic> variant, int qty) {
    final id = '${variant['variant_id']}';
    final existing = lines.where((l) => l.variantId == id).toList();
    final limit = _limit(variant);
    if (existing.isNotEmpty) {
      final next = existing.first.qty + qty;
      if (next > limit) return false;
      existing.first.qty = next;
    } else {
      if (lines.length >= 10 || qty > limit) return false;
      lines.add(CartLine(product, variant, qty));
    }
    notifyListeners();
    return true;
  }

  int _limit(Map<String, dynamic> v) {
    final s = (v['stock'] as num?)?.toInt() ?? 10;
    return s < 10 ? s : 10;
  }

  void setQty(CartLine l, int q) {
    if (q <= 0) {
      lines.remove(l);
    } else {
      l.qty = q > _limit(l.variant) ? _limit(l.variant) : q;
    }
    notifyListeners();
  }

  void remove(CartLine l) {
    lines.remove(l);
    notifyListeners();
  }

  void clear() {
    lines.clear();
    notifyListeners();
  }
}
