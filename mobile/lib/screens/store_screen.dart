import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:open_filex/open_filex.dart';
import 'package:path_provider/path_provider.dart';
import 'package:provider/provider.dart';
import 'package:share_plus/share_plus.dart';

import '../app.dart';
import '../core/cart.dart';
import '../core/errors.dart';
import '../core/net.dart';
import '../core/storage.dart';
import '../core/sync.dart';
import '../ui/tokens.dart';
import '../ui/widgets.dart';
import 'home_shell.dart';

IconData categoryIcon(String c) {
  final s = c.toLowerCase();
  if (s.contains('jersey') || s.contains('shirt') || s.contains('kit')) return Icons.checkroom;
  if (s.contains('ball')) return Icons.sports_soccer;
  if (s.contains('cap') || s.contains('hat')) return Icons.face_retouching_natural;
  if (s.contains('ticket')) return Icons.confirmation_number_outlined;
  return Icons.shopping_bag_outlined;
}

int _minPrice(Map p) {
  final v = (p['variants'] as List? ?? const []).cast<Map>();
  if (v.isEmpty) return 0;
  return v.map((e) => (e['price_minor'] as num?)?.toInt() ?? 0).reduce((a, b) => a < b ? a : b);
}

int _totalStock(Map p) => (p['variants'] as List? ?? const [])
    .cast<Map>()
    .fold(0, (s, e) => s + ((e['stock'] as num?)?.toInt() ?? 0));

class CartButton extends StatelessWidget {
  const CartButton({super.key});
  @override
  Widget build(BuildContext context) {
    final cart = context.watch<Cart>();
    return IconButton(
      tooltip: cart.count == 0 ? 'Cart (empty)' : 'Cart, ${cart.count} items',
      onPressed: () =>
          Navigator.of(context).push(MaterialPageRoute(builder: (_) => const CartScreen())),
      icon: Badge(
        isLabelVisible: cart.count > 0,
        label: Text('${cart.count}'),
        child: const Icon(Icons.shopping_cart_outlined),
      ),
    );
  }
}

class StoreScreen extends StatefulWidget {
  const StoreScreen({super.key});
  @override
  State<StoreScreen> createState() => _StoreScreenState();
}

class _StoreScreenState extends State<StoreScreen> {
  String category = 'All';

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: brandBar(context, 'Store', actions: const [CartButton(), ProfileButton()]),
      body: LoadedView(
        loadingText: 'Loading the store…',
        load: svc.repo.products,
        builder: (context, loaded, refresh) {
          final all = (loaded.data['products'] as List? ?? const []).cast<Map>();
          final cats = ['All', ...{for (final p in all) '${p['category'] ?? 'Other'}'}];
          final list = category == 'All'
              ? all
              : all.where((p) => '${p['category'] ?? 'Other'}' == category).toList();
          return Constrained(
            child: ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.only(bottom: T.s32),
              children: [
                DataBanner(loaded),
                if (all.isEmpty)
                  const Padding(
                    padding: EdgeInsets.only(top: T.s48),
                    child: EmptyState(
                        icon: Icons.storefront_outlined,
                        title: 'The store is empty',
                        message: 'Club merchandise will appear here when it is added.'),
                  )
                else ...[
                  if (Orders.all().isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.fromLTRB(T.s16, T.s12, T.s16, 0),
                      child: SoftCard(
                        padding: const EdgeInsets.symmetric(horizontal: T.s16, vertical: T.s12),
                        onTap: () => Navigator.of(context)
                            .push(MaterialPageRoute(builder: (_) => const OrdersScreen())),
                        child: Row(children: [
                          const Icon(Icons.receipt_long_outlined),
                          const SizedBox(width: T.s12),
                          Expanded(
                            child: Text('Your orders and receipts (${Orders.all().length})',
                                style: Theme.of(context).textTheme.titleMedium),
                          ),
                          const Icon(Icons.chevron_right, color: T.inkMuted),
                        ]),
                      ),
                    ),
                  SizedBox(
                    height: 64,
                    child: ListView(
                      scrollDirection: Axis.horizontal,
                      padding: const EdgeInsets.symmetric(horizontal: T.s16, vertical: T.s8),
                      children: [
                        for (final c in cats)
                          Padding(
                            padding: const EdgeInsets.only(right: T.s8),
                            child: ChoiceChip(
                              label: Text(c),
                              selected: category == c,
                              onSelected: (_) => setState(() => category = c),
                            ),
                          ),
                      ],
                    ),
                  ),
                  for (final p in list)
                    Padding(
                      padding: const EdgeInsets.fromLTRB(T.s16, 0, T.s16, T.s12),
                      child: ProductCard(p.cast<String, dynamic>()),
                    ),
                ],
              ],
            ),
          );
        },
      ),
    );
  }
}

class ProductCard extends StatelessWidget {
  final Map<String, dynamic> p;
  const ProductCard(this.p, {super.key});
  @override
  Widget build(BuildContext context) {
    final sec = Theme.of(context).colorScheme.secondary;
    final stock = _totalStock(p);
    final cur = '${((p['variants'] as List?)?.firstOrNull as Map?)?['currency'] ?? 'PKR'}';
    final tt = Theme.of(context).textTheme;
    return SoftCard(
      semanticLabel:
          '${p['name']}, from ${money(_minPrice(p), cur)}. ${stock == 0 ? 'Sold out.' : ''} Opens the product.',
      onTap: () => Navigator.of(context)
          .push(MaterialPageRoute(builder: (_) => ProductScreen(product: p))),
      child: ExcludeSemantics(
        child: Row(children: [
          Container(
            width: 72,
            height: 72,
            decoration: BoxDecoration(
              color: sec.withValues(alpha: 0.22),
              borderRadius: BorderRadius.circular(T.r16),
            ),
            child: Icon(categoryIcon('${p['category']}'), size: 36, color: T.ink),
          ),
          const SizedBox(width: T.s16),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('${p['name']}',
                  style: tt.titleMedium, maxLines: 2, overflow: TextOverflow.ellipsis),
              const SizedBox(height: T.s4),
              Text('From ${money(_minPrice(p), cur)}',
                  style: tt.bodyLarge?.copyWith(fontWeight: T.bold, color: T.ink)),
              const SizedBox(height: T.s4),
              if (stock == 0)
                const StatusPill(Icons.remove_shopping_cart_outlined, 'Sold out', Tone.danger)
              else if (stock <= 3)
                StatusPill(Icons.local_fire_department_outlined, 'Only $stock left', Tone.warning)
              else
                Text('${p['category'] ?? ''}', style: tt.bodySmall),
            ]),
          ),
          const Icon(Icons.chevron_right, color: T.inkMuted),
        ]),
      ),
    );
  }
}

class ProductScreen extends StatefulWidget {
  final Map<String, dynamic> product;
  const ProductScreen({super.key, required this.product});
  @override
  State<ProductScreen> createState() => _ProductScreenState();
}

class _ProductScreenState extends State<ProductScreen> {
  Map<String, dynamic>? variant;
  int qty = 1;

  late final List<Map<String, dynamic>> variants = (widget.product['variants'] as List? ?? const [])
      .map((e) => Map<String, dynamic>.from(e as Map))
      .toList();

  @override
  void initState() {
    super.initState();
    final inStock = variants.where((v) => ((v['stock'] as num?) ?? 0) > 0).toList();
    if (inStock.isNotEmpty) variant = inStock.first;
  }

  String _label(Map v) {
    final multiColor = variants.map((e) => e['color']).toSet().length > 1;
    final parts = [v['size'], if (multiColor) v['color']].where((e) => e != null);
    return parts.join(' · ');
  }

  int get _stock => (variant?['stock'] as num?)?.toInt() ?? 0;
  int get _max => _stock < 10 ? _stock : 10;

  @override
  Widget build(BuildContext context) {
    final p = widget.product;
    final tt = Theme.of(context).textTheme;
    final sec = Theme.of(context).colorScheme.secondary;
    final cur = '${variant?['currency'] ?? 'PKR'}';
    final price = (variant?['price_minor'] as num?)?.toInt() ?? _minPrice(p);
    return Scaffold(
      appBar: brandBar(context, 'Product', actions: const [CartButton()]),
      body: Constrained(
        child: ListView(padding: const EdgeInsets.all(T.s16), children: [
          Container(
            height: 160,
            decoration: BoxDecoration(
              color: sec.withValues(alpha: 0.22),
              borderRadius: BorderRadius.circular(T.r24),
            ),
            child: Icon(categoryIcon('${p['category']}'), size: 88, color: T.ink),
          ),
          const SizedBox(height: T.s16),
          Text('${p['name']}', style: tt.titleLarge),
          const SizedBox(height: T.s4),
          Text(money(price, cur), style: tt.displaySmall?.copyWith(fontSize: 32)),
          if ((p['description'] ?? '').toString().isNotEmpty) ...[
            const SizedBox(height: T.s8),
            Text('${p['description']}', style: tt.bodyLarge),
          ],
          const SectionTitle('Choose a size', inset: false),
          Wrap(spacing: T.s8, runSpacing: T.s8, children: [
            for (final v in variants)
              Builder(builder: (context) {
                final s = (v['stock'] as num?)?.toInt() ?? 0;
                final selected = variant != null && v['variant_id'] == variant!['variant_id'];
                return ChoiceChip(
                  label: Text(s == 0 ? '${_label(v)} · Sold out' : _label(v)),
                  selected: selected,
                  showCheckmark: true,
                  tooltip: s == 0 ? '${_label(v)} is sold out' : 'Size ${_label(v)}',
                  onSelected: s == 0
                      ? null
                      : (_) => setState(() {
                            variant = v;
                            if (qty > _max) qty = _max < 1 ? 1 : _max;
                          }),
                );
              }),
          ]),
          const SizedBox(height: T.s12),
          if (variant != null)
            Row(children: [
              Icon(_stock <= 3 ? Icons.local_fire_department_outlined : Icons.inventory_2_outlined,
                  size: 18, color: _stock <= 3 ? T.warning : T.inkMuted),
              const SizedBox(width: T.s8),
              Text(_stock <= 3 ? 'Only $_stock left' : '$_stock in stock',
                  style: tt.bodyMedium?.copyWith(
                      color: _stock <= 3 ? T.warning : T.inkMuted, fontWeight: T.bold)),
            ])
          else
            const StatusPill(Icons.remove_shopping_cart_outlined, 'Sold out in every size', Tone.danger),
          const SectionTitle('Quantity', inset: false),
          Row(children: [
            IconButton.outlined(
              tooltip: 'One less',
              onPressed: qty > 1 ? () => setState(() => qty--) : null,
              icon: const Icon(Icons.remove),
            ),
            SizedBox(
              width: 56,
              child: Text('$qty',
                  textAlign: TextAlign.center,
                  style: tt.titleLarge?.copyWith(fontFeatures: T.tabular),
                  semanticsLabel: 'Quantity $qty'),
            ),
            IconButton.outlined(
              tooltip: 'One more',
              onPressed: variant != null && qty < _max ? () => setState(() => qty++) : null,
              icon: const Icon(Icons.add),
            ),
          ]),
          const SizedBox(height: T.s24),
        ]),
      ),
      bottomNavigationBar: SafeArea(
        child: Container(
          padding: const EdgeInsets.fromLTRB(T.s16, T.s12, T.s16, T.s12),
          decoration: BoxDecoration(color: Colors.white, boxShadow: T.shadow(T.ink)),
          child: Row(children: [
            Expanded(
              flex: 2,
              child: OutlinedButton.icon(
                style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(56)),
                onPressed: variant == null
                    ? null
                    : () {
                        final ok = svc.cart.add(p, variant!, qty);
                        if (ok) {
                          showSuccess(context, 'Added to cart: ${p['name']} (${_label(variant!)}) × $qty');
                        } else {
                          showError(context, 'You can\'t add more of this size: the limit is $_max.');
                        }
                      },
                icon: const Icon(Icons.add_shopping_cart),
                label: const Text('Add', maxLines: 1),
              ),
            ),
            const SizedBox(width: T.s12),
            Expanded(
              flex: 3,
              child: FilledButton.icon(
                style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(56)),
                onPressed: variant == null
                    ? null
                    : () => Navigator.of(context).push(MaterialPageRoute(
                        builder: (_) => CheckoutScreen(
                            lines: [CartLine(p, variant!, qty)], fromCart: false))),
                icon: const Icon(Icons.bolt),
                label: FittedBox(child: Text('Buy now · ${money(price * qty, cur)}')),
              ),
            ),
          ]),
        ),
      ),
    );
  }
}

class CartScreen extends StatelessWidget {
  const CartScreen({super.key});
  @override
  Widget build(BuildContext context) {
    final cart = context.watch<Cart>();
    final tt = Theme.of(context).textTheme;
    return Scaffold(
      appBar: brandBar(context, 'Your cart'),
      body: cart.lines.isEmpty
          ? EmptyState(
              icon: Icons.shopping_cart_outlined,
              title: 'Your cart is empty',
              message: 'Browse the store and add a jersey or scarf to get started.',
              actionLabel: 'Back to the store',
              onAction: () => Navigator.of(context).pop(),
            )
          : Constrained(
              child: ListView(padding: const EdgeInsets.all(T.s16), children: [
                for (final l in cart.lines)
                  Padding(
                    padding: const EdgeInsets.only(bottom: T.s12),
                    child: SoftCard(
                      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        Text(l.title, style: tt.titleMedium),
                        Text('${l.options} · ${money(l.unit, l.variant['currency'] ?? 'PKR')} each',
                            style: tt.bodySmall),
                        const SizedBox(height: T.s8),
                        Row(children: [
                          IconButton(
                            tooltip: 'One less',
                            onPressed: () => cart.setQty(l, l.qty - 1),
                            icon: const Icon(Icons.remove_circle_outline),
                          ),
                          Text('${l.qty}', style: tt.titleMedium, semanticsLabel: 'Quantity ${l.qty}'),
                          IconButton(
                            tooltip: 'One more',
                            onPressed: () => cart.setQty(l, l.qty + 1),
                            icon: const Icon(Icons.add_circle_outline),
                          ),
                          const Spacer(),
                          Text(money(l.total, l.variant['currency'] ?? 'PKR'),
                              style: tt.titleMedium?.copyWith(fontFeatures: T.tabular)),
                          IconButton(
                            tooltip: 'Remove from cart',
                            onPressed: () => cart.remove(l),
                            icon: const Icon(Icons.delete_outline),
                          ),
                        ]),
                      ]),
                    ),
                  ),
              ]),
            ),
      bottomNavigationBar: cart.lines.isEmpty
          ? null
          : SafeArea(
              child: Padding(
                padding: const EdgeInsets.all(T.s16),
                child: FilledButton.icon(
                  style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(56)),
                  onPressed: () => Navigator.of(context).push(MaterialPageRoute(
                      builder: (_) => CheckoutScreen(lines: List.of(cart.lines), fromCart: true))),
                  icon: const Icon(Icons.lock_outline),
                  label: Text('Checkout · ${money(cart.subtotal, cart.currency)}'),
                ),
              ),
            ),
    );
  }
}

class _Card {
  final String token;
  final String title;
  final String hint;
  const _Card(this.token, this.title, this.hint);
}

const _cards = [
  _Card('tok_success', 'Visa ending 4242', 'Test card: payment goes through'),
  _Card('tok_declined', 'Mastercard ending 0002', 'Test card: the bank declines it'),
  _Card('tok_capture_fail', 'Visa ending 9995', 'Test card: approved, then capture fails'),
];

class CheckoutScreen extends StatefulWidget {
  final List<CartLine> lines;
  final bool fromCart;
  const CheckoutScreen({super.key, required this.lines, required this.fromCart});
  @override
  State<CheckoutScreen> createState() => _CheckoutScreenState();
}

class _CheckoutScreenState extends State<CheckoutScreen> {
  String token = 'tok_success';
  final _code = TextEditingController();
  bool _busy = false;
  String? _error;

  int get subtotal => widget.lines.fold(0, (s, l) => s + l.total);
  bool get _discount => _code.text.trim().toUpperCase() == 'WELCOME10';
  int get discount => _discount ? (subtotal * 10) ~/ 100 : 0;
  String get cur => '${widget.lines.first.variant['currency'] ?? 'PKR'}';

  Future<void> _pay() async {
    final card = _cards.firstWhere((c) => c.token == token);
    final ok = await confirm(context,
        title: 'Pay ${money(subtotal - discount, cur)}?',
        message: 'You\'ll be charged with ${card.title}. This places the order.',
        confirmLabel: 'Pay now',
        icon: Icons.lock_outline);
    if (!ok || !mounted) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    final orderId = shortId('ord');
    final code = _code.text.trim();
    try {
      final res = await svc.api.post('/audit/v1/checkout', {
        'order_id': orderId,
        'customer_id': svc.session.userId.isEmpty ? 'guest' : svc.session.userId,
        'items': [
          for (final l in widget.lines) {'variant_id': l.variantId, 'quantity': l.qty}
        ],
        'discount_code': code.isEmpty ? null : code.toUpperCase(),
        'payment': {'token': token},
      }, timeout: const Duration(seconds: 30)) as Map<String, dynamic>;
      if (widget.fromCart) svc.cart.clear();
      final names = {for (final l in widget.lines) l.variantId: '${l.title} (${l.options})'};
      await Orders.add(res, names);
      svc.repo.bump();
      if (!mounted) return;
      Navigator.of(context).pushReplacement(MaterialPageRoute(
          builder: (_) => ReceiptScreen(order: res, names: names)));
    } on ApiException catch (e) {
      final msg = e.code == 'OUT_OF_STOCK'
          ? 'Sorry, ${_variantName(e.details['variant_id'])} just sold out. Nothing was charged. Choose another size.'
          : e.message;
      setState(() => _error = msg);
      if (mounted) showError(context, msg);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  String _variantName(dynamic id) {
    for (final l in widget.lines) {
      if (l.variantId == '$id') return '${l.title} (${l.options})';
    }
    return 'an item';
  }

  @override
  Widget build(BuildContext context) {
    final net = context.watch<Net>();
    final tt = Theme.of(context).textTheme;
    final total = subtotal - discount;
    return Scaffold(
      appBar: brandBar(context, 'Checkout'),
      body: Constrained(
        child: ListView(padding: const EdgeInsets.all(T.s16), children: [
          SoftCard(
            child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              Text('Order summary', style: tt.titleMedium),
              const SizedBox(height: T.s8),
              for (final l in widget.lines)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: T.s4),
                  child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Expanded(child: Text('${l.qty} × ${l.title} (${l.options})', style: tt.bodyLarge)),
                    const SizedBox(width: T.s8),
                    Text(money(l.total, cur), style: tt.bodyLarge?.copyWith(fontFeatures: T.tabular)),
                  ]),
                ),
              const Divider(height: T.s24),
              _row('Subtotal', money(subtotal, cur)),
              if (discount > 0) _row('Discount (WELCOME10)', '− ${money(discount, cur)}'),
              const SizedBox(height: T.s4),
              Row(children: [
                Expanded(child: Text('Total', style: tt.titleLarge)),
                Text(money(total, cur), style: tt.titleLarge?.copyWith(fontFeatures: T.tabular)),
              ]),
            ]),
          ),
          const SectionTitle('Discount code', inset: false),
          TextField(
            controller: _code,
            textCapitalization: TextCapitalization.characters,
            onChanged: (_) => setState(() {}),
            decoration: InputDecoration(
              hintText: 'Optional, e.g. WELCOME10',
              prefixIcon: const Icon(Icons.local_offer_outlined),
              suffixIcon: _discount
                  ? const Tooltip(
                      message: 'Code applied',
                      child: Icon(Icons.check_circle, color: T.success))
                  : null,
            ),
          ),
          const SectionTitle('Pay with (test cards)', inset: false),
          RadioGroup<String>(
            groupValue: token,
            onChanged: (v) => setState(() => token = v ?? token),
            child: Column(children: [
              for (final c in _cards)
                Padding(
                  padding: const EdgeInsets.only(bottom: T.s8),
                  child: SoftCard(
                    padding: EdgeInsets.zero,
                    onTap: () => setState(() => token = c.token),
                    child: RadioListTile<String>(
                      value: c.token,
                      title: Text(c.title),
                      subtitle: Text(c.hint),
                      secondary: const Icon(Icons.credit_card),
                    ),
                  ),
                ),
            ]),
          ),
          if (_error != null) ...[
            const SizedBox(height: T.s8),
            Container(
              padding: const EdgeInsets.all(T.s12),
              decoration: BoxDecoration(color: T.dangerBg, borderRadius: BorderRadius.circular(T.r12)),
              child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                const Icon(Icons.error_outline, color: T.danger),
                const SizedBox(width: T.s12),
                Expanded(
                    child: Text(_error!,
                        style: const TextStyle(color: T.danger, fontWeight: T.bold, fontSize: T.meta))),
              ]),
            ),
          ],
          if (!net.online) ...[
            const SizedBox(height: T.s8),
            const StatusPill(Icons.cloud_off, 'You\'re offline. Payment needs a connection.', Tone.warning),
          ],
          const SizedBox(height: T.s24),
        ]),
      ),
      bottomNavigationBar: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(T.s16),
          child: FilledButton.icon(
            style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(56)),
            onPressed: _busy || !net.online ? null : _pay,
            icon: _busy
                ? const SizedBox(
                    width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.lock_outline),
            label: Text(_busy ? 'Processing payment…' : 'Pay ${money(total, cur)}'),
          ),
        ),
      ),
    );
  }

  Widget _row(String a, String b) => Padding(
        padding: const EdgeInsets.symmetric(vertical: T.s4),
        child: Row(children: [
          Expanded(child: Text(a, style: Theme.of(context).textTheme.bodyLarge)),
          Text(b, style: Theme.of(context).textTheme.bodyLarge?.copyWith(fontFeatures: T.tabular)),
        ]),
      );
}

const _files = MethodChannel('nascon/files');

/// Paid orders kept on this phone so a receipt can be reopened later.
class Orders {
  static List<Map<String, dynamic>> all() {
    final m = LocalStore.readJson(LocalStore.meta, 'orders');
    return ((m?['list'] as List?) ?? const [])
        .map((e) => Map<String, dynamic>.from(e as Map))
        .toList();
  }

  static Future<void> add(Map<String, dynamic> order, Map<String, String> names) async {
    final list = [
      {'order': order, 'names': names},
      ...all().where((o) => (o['order'] as Map?)?['order_id'] != order['order_id']),
    ].take(20).toList();
    await LocalStore.writeJson(LocalStore.meta, 'orders', {'list': list});
  }
}

class OrdersScreen extends StatelessWidget {
  const OrdersScreen({super.key});
  @override
  Widget build(BuildContext context) {
    final orders = Orders.all();
    final tt = Theme.of(context).textTheme;
    return Scaffold(
      appBar: brandBar(context, 'Your orders'),
      body: orders.isEmpty
          ? const EmptyState(
              icon: Icons.receipt_long_outlined,
              title: 'No orders yet',
              message: 'Receipts of what you buy on this phone will be kept here.')
          : Constrained(
              child: ListView(padding: const EdgeInsets.all(T.s16), children: [
                for (final o in orders)
                  Padding(
                    padding: const EdgeInsets.only(bottom: T.s12),
                    child: Builder(builder: (context) {
                      final order = Map<String, dynamic>.from(o['order'] as Map);
                      final payload = ((order['receipt'] as Map?)?['payload'] as Map?) ?? order;
                      final names = Map<String, String>.from(
                          (o['names'] as Map? ?? const {}).map((k, v) => MapEntry('$k', '$v')));
                      return SoftCard(
                        semanticLabel: 'Order ${order['order_id']}, opens the receipt.',
                        onTap: () => Navigator.of(context).push(MaterialPageRoute(
                            builder: (_) => ReceiptScreen(order: order, names: names))),
                        child: ExcludeSemantics(
                          child: Row(children: [
                            const Icon(Icons.receipt_long_outlined, size: 32),
                            const SizedBox(width: T.s12),
                            Expanded(
                              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                                Text('${order['order_id']}', style: tt.titleMedium),
                                Text(when(payload['paid_at'] as String?), style: tt.bodySmall),
                              ]),
                            ),
                            Text(money((payload['total_minor'] as num?) ?? 0, '${payload['currency'] ?? 'PKR'}'),
                                style: tt.titleMedium?.copyWith(fontFeatures: T.tabular)),
                            const Icon(Icons.chevron_right, color: T.inkMuted),
                          ]),
                        ),
                      );
                    }),
                  ),
              ]),
            ),
    );
  }
}

class ReceiptScreen extends StatefulWidget {
  final Map<String, dynamic> order;
  final Map<String, String> names; // variant_id -> "Product (size · colour)"
  const ReceiptScreen({super.key, required this.order, required this.names});
  @override
  State<ReceiptScreen> createState() => _ReceiptScreenState();
}

class _ReceiptScreenState extends State<ReceiptScreen> with SingleTickerProviderStateMixin {
  late final AnimationController _anim =
      AnimationController(vsync: this, duration: const Duration(milliseconds: 600))..forward();
  bool _busy = false;

  @override
  void dispose() {
    _anim.dispose();
    super.dispose();
  }

  Map get receipt => (widget.order['receipt'] as Map?) ?? const {};
  Map get payload => (receipt['payload'] as Map?) ?? widget.order;
  String get orderId => '${widget.order['order_id']}';

  Future<File?> _pdfFile() async {
    final b64 = receipt['pdf_base64'] as String?;
    if (b64 == null || b64.isEmpty) {
      showError(context, 'The server didn\'t include a PDF for this receipt.');
      return null;
    }
    final dir = await getTemporaryDirectory();
    final f = File('${dir.path}/receipt-$orderId.pdf');
    await f.writeAsBytes(base64Decode(b64), flush: true);
    return f;
  }

  Future<void> _open() async {
    setState(() => _busy = true);
    try {
      final f = await _pdfFile();
      if (f == null) return;
      final r = await OpenFilex.open(f.path, type: 'application/pdf');
      if (r.type != ResultType.done && mounted) {
        showError(context, 'No PDF viewer found. Use "Save PDF" to keep it in Downloads.');
      }
    } catch (_) {
      if (mounted) showError(context, 'Couldn\'t open the PDF. Please try again.');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _save() async {
    setState(() => _busy = true);
    try {
      final b64 = receipt['pdf_base64'] as String?;
      if (b64 == null) {
        showError(context, 'The server didn\'t include a PDF for this receipt.');
        return;
      }
      final where = await _files.invokeMethod<String>('saveToDownloads', {
        'name': 'receipt-$orderId.pdf',
        'bytes': base64Decode(b64),
      });
      if (mounted) showSuccess(context, 'Saved to $where');
    } catch (_) {
      final f = await _pdfFile();
      if (f != null) {
        await SharePlus.instance.share(ShareParams(
            files: [XFile(f.path, mimeType: 'application/pdf')], text: 'Receipt $orderId'));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  String _nameOf(String variantId) => widget.names[variantId] ?? variantId;

  @override
  Widget build(BuildContext context) {
    final tt = Theme.of(context).textTheme;
    final cur = '${payload['currency'] ?? widget.order['currency'] ?? 'PKR'}';
    final items = (payload['items'] as List? ?? const []).cast<Map>();
    return Scaffold(
      appBar: brandBar(context, 'Receipt'),
      body: Constrained(
        child: ListView(padding: const EdgeInsets.all(T.s16), children: [
          ScaleTransition(
            scale: CurvedAnimation(parent: _anim, curve: Curves.elasticOut),
            child: Container(
              width: 88,
              height: 88,
              alignment: Alignment.center,
              decoration: const BoxDecoration(color: T.successBg, shape: BoxShape.circle),
              child: const Icon(Icons.check_rounded, size: 56, color: T.success,
                  semanticLabel: 'Payment successful'),
            ),
          ),
          const SizedBox(height: T.s16),
          Text('Payment successful', textAlign: TextAlign.center, style: tt.titleLarge),
          const SizedBox(height: T.s4),
          Text('Thanks for supporting your club! Your order is paid.',
              textAlign: TextAlign.center, style: tt.bodyLarge),
          const SizedBox(height: T.s24),
          SoftCard(
            child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              _kv(context, 'Order', orderId),
              _kv(context, 'Paid', when(payload['paid_at'] as String?)),
              _kv(context, 'Status', titleCase('${widget.order['status'] ?? 'paid'}')),
              const Divider(height: T.s24),
              for (final i in items)
                _kv(context, '${i['quantity']} × ${_nameOf('${i['variant_id']}')}',
                    money(((i['unit_price_minor'] as num?) ?? 0) * ((i['quantity'] as num?) ?? 1), cur)),
              const Divider(height: T.s24),
              _kv(context, 'Subtotal', money((payload['subtotal_minor'] as num?) ?? 0, cur)),
              if (((payload['discount_minor'] as num?) ?? 0) > 0)
                _kv(context, 'Discount', '− ${money(payload['discount_minor'] as num, cur)}'),
              Row(children: [
                Expanded(child: Text('Total paid', style: tt.titleLarge)),
                Text(money((payload['total_minor'] as num?) ?? 0, cur),
                    style: tt.titleLarge?.copyWith(fontFeatures: T.tabular)),
              ]),
              if (receipt['signature'] != null) ...[
                const SizedBox(height: T.s12),
                Row(children: [
                  const Icon(Icons.verified_user_outlined, size: 18, color: T.success),
                  const SizedBox(width: T.s8),
                  Expanded(
                    child: Text(
                        'Signed receipt ${'${receipt['signature']}'.substring(0, '${receipt['signature']}'.length.clamp(0, 12))}…',
                        style: tt.bodySmall),
                  ),
                ]),
              ],
            ]),
          ),
          const SizedBox(height: T.s24),
          FilledButton.icon(
            style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(56)),
            onPressed: _busy ? null : _open,
            icon: const Icon(Icons.picture_as_pdf_outlined),
            label: const Text('Open PDF receipt'),
          ),
          const SizedBox(height: T.s12),
          OutlinedButton.icon(
            style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(52)),
            onPressed: _busy ? null : _save,
            icon: const Icon(Icons.download_outlined),
            label: const Text('Save PDF to Downloads'),
          ),
          const SizedBox(height: T.s12),
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('Back to the store'),
          ),
        ]),
      ),
    );
  }

  Widget _kv(BuildContext context, String k, String v) => Padding(
        padding: const EdgeInsets.symmetric(vertical: T.s4),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Expanded(child: Text(k, style: Theme.of(context).textTheme.bodyLarge)),
          const SizedBox(width: T.s8),
          Flexible(
            child: Text(v,
                textAlign: TextAlign.end,
                style: Theme.of(context)
                    .textTheme
                    .bodyLarge
                    ?.copyWith(fontWeight: T.bold, color: T.ink, fontFeatures: T.tabular)),
          ),
        ]),
      );
}
