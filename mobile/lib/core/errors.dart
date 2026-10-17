/// Turns server error codes into plain sentences. Raw codes are never shown.
class ApiException implements Exception {
  final int status;
  final String code;
  final String serverMessage;
  final Map<String, dynamic> details;
  final bool network;

  ApiException({
    required this.status,
    required this.code,
    this.serverMessage = '',
    this.details = const {},
    this.network = false,
  });

  factory ApiException.network() =>
      ApiException(status: 0, code: 'NETWORK', network: true);

  factory ApiException.timeout() =>
      ApiException(status: 0, code: 'TIMEOUT', network: true);

  /// A route the server does not have yet (FastAPI default 404).
  bool get isMissingRoute => status == 404 && code == 'NOT_FOUND';

  String get message => friendlyError(code, status: status, details: details);

  @override
  String toString() => message;
}

const Map<String, String> _messages = {
  'NETWORK':
      'Can\'t reach the server. Check your connection, or the server address in Settings.',
  'TIMEOUT': 'The server took too long to answer. Please try again.',
  'OFFLINE_LOGIN':
      "You're offline. Only an account that has signed in on this phone before can sign in now, with the same password.",
  'NOT_SAVED_OFFLINE':
      "You're offline and this page hasn't been saved on this phone yet. Open it once while online.",
  'INVALID_CREDENTIALS':
      'That email and password don\'t match. Check them and try again.',
  'UNAUTHORIZED': 'Your session has ended. Please sign in again.',
  'TOKEN_EXPIRED': 'Your session has ended. Please sign in again.',
  'INVALID_TOKEN': 'Your session has ended. Please sign in again.',
  'FORBIDDEN': 'Your account isn\'t allowed to do that.',
  'CROSS_TENANT_ACCESS': 'That belongs to another organisation.',
  'NOT_FOUND': 'We couldn\'t find that. It may have been removed.',
  'VALIDATION_ERROR':
      'Some details are missing or not valid. Please check them and try again.',
  'RATE_LIMITED':
      'Too many requests right now. Please wait a few seconds and try again.',
  'INTERNAL_ERROR':
      'Something went wrong on the server. Please try again in a moment.',
  // Store
  'OUT_OF_STOCK':
      'Sorry, that item just sold out. Remove it or choose another size.',
  'PAYMENT_DECLINED':
      'Your card was declined. You have not been charged. Try another card.',
  'PAYMENT_CAPTURE_FAILED':
      'The bank approved the card but the payment could not be completed, so the order was cancelled. You have not been charged.',
  'INVALID_DISCOUNT_CODE': 'That discount code isn\'t valid.',
  'DUPLICATE_ORDER_ID': 'This order was already placed.',
  'VARIANT_NOT_FOUND': 'One of the items is no longer sold.',
  // Scoring
  'MATCH_NOT_FOUND': 'That match doesn\'t exist on the server.',
  'MATCH_EXISTS': 'That match already exists.',
  'SPORT_NOT_FOUND': 'That sport isn\'t set up on the server.',
  'MATCH_NOT_IN_PROGRESS':
      'Scores can only be added while the match is being played (it hasn\'t started or has already ended).',
  'UNKNOWN_EVENT_TYPE': 'That kind of score isn\'t used in this sport.',
  'INVALID_COMPENSATION':
      'That event can\'t be cancelled: it was already cancelled, or it isn\'t a scoring event of this match.',
  'EVENT_NOT_FOUND': 'The event you tried to cancel doesn\'t exist.',
  'DUPLICATE_EVENT_ID':
      'This event was already recorded with different details.',
  'ILLEGAL_TRANSITION':
      'The match can\'t do that right now (for example, it has already started or ended).',
  'TIE_NOT_ALLOWED':
      'This match can\'t end level. Record a tie-break winner first.',
  'INNINGS_COMPLETE': 'That team\'s innings is already over.',
  'SERIES_ALREADY_DECIDED': 'This series has already been decided.',
  // Roster
  'LEAGUE_NOT_FOUND': 'That league doesn\'t exist on the server.',
  'PLAYER_NOT_FOUND': 'That player doesn\'t exist on the server.',
  'SQUAD_NOT_FOUND': 'That squad doesn\'t exist on the server.',
  'CROSS_TENANT_TRANSFER':
      'Players can only move between clubs of the same organisation.',
  'NOT_IN_SQUAD':
      'This player is no longer in that squad (someone else moved them first), so nothing changed.',
  'TRANSFER_WINDOW_CLOSED':
      'The transfer window is closed today, so transfers and loans aren\'t allowed. A release is still possible.',
  'PLAYER_ON_LOAN':
      'This player is on loan right now, so they can\'t be moved until the loan ends.',
  'SQUAD_FULL': 'The receiving squad is already full.',
  'MIN_POSITION_VIOLATION':
      'This move would leave the club without enough players in that position.',
  // Sync
  'INVALID_BASE_VERSION':
      'The server\'s data was reset or changed in a way this phone didn\'t expect, so the action was not applied. Refresh and try again.',
};

String friendlyError(String? code,
    {int status = 0, Map<String, dynamic> details = const {}}) {
  if (code != null && _messages.containsKey(code)) return _messages[code]!;
  if (status == 401) return _messages['UNAUTHORIZED']!;
  if (status == 403) return _messages['FORBIDDEN']!;
  if (status == 404) return _messages['NOT_FOUND']!;
  if (status == 409) {
    return 'That conflicts with the latest data on the server. Refresh and try again.';
  }
  if (status == 429) return _messages['RATE_LIMITED']!;
  if (status >= 500) return _messages['INTERNAL_ERROR']!;
  if (status == 400 || status == 422) return _messages['VALIDATION_ERROR']!;
  return 'Something went wrong. Please try again.';
}

String friendlyAny(Object e) {
  if (e is ApiException) return e.message;
  return 'Something went wrong. Please try again.';
}
