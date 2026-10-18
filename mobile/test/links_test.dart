import 'package:flutter_test/flutter_test.dart';
import 'package:nascon_league/core/links.dart';
import 'package:nascon_league/screens/club_screen.dart';
import 'package:nascon_league/screens/costream_screen.dart';
import 'package:nascon_league/screens/entity_screen.dart';
import 'package:nascon_league/screens/match_screen.dart';

void main() {
  test('nascon:// links resolve to their entity id', () async {
    expect(await DeepLinks.entityOf('nascon://entity/match-m1'), 'match-m1');
    expect(await DeepLinks.entityOf('nascon://stream/cs-session-101'), 'cs-session-101');
    expect(await DeepLinks.entityOf('nascon://entity/player-alpha-1'), 'player-alpha-1');
    expect(await DeepLinks.entityOf('nascon://entity/club-alpha-1'), 'club-alpha-1');
    expect(await DeepLinks.entityOf(' nascon://entity/cs-session-101 '), 'cs-session-101');
  });

  test('web player URLs resolve without the network', () async {
    expect(await DeepLinks.entityOf('http://localhost:8080/web/matches/match-m1/live'), 'match-m1');
    expect(await DeepLinks.entityOf('http://192.168.1.20:8080/web/costream/cs-session-101'), 'cs-session-101');
    expect(await DeepLinks.entityOf('http://localhost:8080/web/players/player-alpha-1'), 'player-alpha-1');
    expect(await DeepLinks.entityOf('http://localhost:8080/web/clubs/club-alpha-1'), 'club-alpha-1');
  });

  test('anything else is not ours', () async {
    expect(await DeepLinks.entityOf('hello world'), isNull);
    expect(await DeepLinks.entityOf('https://example.com/about'), isNull);
  });

  test('each entity type opens the right screen', () {
    expect(DeepLinks.screenFor('match-m1'), isA<MatchScreen>());
    expect(DeepLinks.screenFor('cs-session-101'), isA<CoStreamScreen>());
    expect(DeepLinks.screenFor('club-alpha-1'), isA<ClubProfileScreen>());
    expect(DeepLinks.screenFor('player-alpha-1'), isA<EntityScreen>());
    expect(DeepLinks.screenFor('unknown-thing'), isNull);
  });
}
