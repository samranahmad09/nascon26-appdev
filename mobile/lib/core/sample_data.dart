/// SAMPLE DATA ONLY. Used when the server has no such route yet and the
/// "Use sample data" switch in Settings is on. Never mixed with server data.
class SampleData {
  static const _teams = {
    'club-alpha-1': 'Alpha FC',
    'club-alpha-2': 'Alpha Rovers',
    'club-alpha-3': 'Alpha United',
    'club-alpha-4': 'Alpha City',
  };

  static Map<String, dynamic> leagues() => {
        'leagues': [
          {
            'league_id': 'league-alpha-1',
            'name': 'Alpha Premier League',
            'sport_id': 'football',
            'season': '2026',
            'tenant_id': 'tenant-alpha'
          },
        ]
      };

  static Map<String, dynamic> _team(String id) => {'team_id': id, 'name': _teams[id]};

  static Map<String, dynamic> fixtures(String leagueId) => {
        'league_id': leagueId,
        'matches': [
          {
            'match_id': 'm-1',
            'round': 1,
            'home': _team('club-alpha-1'),
            'away': _team('club-alpha-2'),
            'status': 'completed',
            'scheduled_at': '2026-10-10T14:00:00Z',
            'venue': 'Alpha Stadium',
            'home_score': 2,
            'away_score': 1
          },
          {
            'match_id': 'm-2',
            'round': 1,
            'home': _team('club-alpha-3'),
            'away': _team('club-alpha-4'),
            'status': 'in_progress',
            'scheduled_at': '2026-10-17T10:00:00Z',
            'venue': 'United Park',
            'home_score': 0,
            'away_score': 1
          },
          {
            'match_id': 'm-3',
            'round': 2,
            'home': _team('club-alpha-2'),
            'away': _team('club-alpha-3'),
            'status': 'upcoming',
            'scheduled_at': '2026-10-24T14:00:00Z',
            'venue': 'Rovers Ground',
            'home_score': null,
            'away_score': null
          },
        ]
      };

  static Map<String, dynamic> match(String id) => {
        'match_id': id,
        'sport_id': 'football',
        'league_id': 'league-alpha-1',
        'status': 'in_progress',
        'outcome': null,
        'winner_team_id': null,
        'home': {'team_id': 'club-alpha-3', 'score': 0, 'wickets': null, 'balls': null, 'overs': null, 'fair_play': 0},
        'away': {'team_id': 'club-alpha-4', 'score': 1, 'wickets': null, 'balls': null, 'overs': null, 'fair_play': -1},
        'version': 5,
        'events': [
          {'sequence': 1, 'event_id': 'evt-s1', 'type': 'match_init', 'payload': {}, 'compensated': false, 'compensated_by': null},
          {'sequence': 2, 'event_id': 'evt-s2', 'type': 'match_start', 'payload': {}, 'compensated': false, 'compensated_by': null},
          {'sequence': 3, 'event_id': 'evt-s3', 'type': 'score', 'payload': {'team_id': 'club-alpha-3', 'event_type': 'goal'}, 'compensated': true, 'compensated_by': 'evt-s4'},
          {'sequence': 4, 'event_id': 'evt-s4', 'type': 'compensation', 'payload': {'compensates': 'evt-s3', 'reason': 'Offside on review'}, 'compensated': false, 'compensated_by': null},
          {'sequence': 5, 'event_id': 'evt-s5', 'type': 'score', 'payload': {'team_id': 'club-alpha-4', 'event_type': 'goal'}, 'compensated': false, 'compensated_by': null},
        ],
        'home_name': 'Alpha United',
        'away_name': 'Alpha City',
        'scheduled_at': '2026-10-17T10:00:00Z',
        'venue': 'United Park',
        'scoring_events': ['goal'],
        'pinned_product': products()['products'][0],
      };

  static Map<String, dynamic> standings(String leagueId) => {
        'league_id': leagueId,
        'qualification_spots': 2,
        'table': [
          {'rank': 1, 'team_id': 'club-alpha-1', 'played': 3, 'won': 3, 'drawn': 0, 'lost': 0, 'scored': 7, 'conceded': 2, 'diff': 5, 'points': 9, 'fair_play': 0, 'status': 'clinched', 'resolved_by': null},
          {'rank': 2, 'team_id': 'club-alpha-4', 'played': 3, 'won': 1, 'drawn': 1, 'lost': 1, 'scored': 4, 'conceded': 4, 'diff': 0, 'points': 4, 'fair_play': -1, 'status': 'still_possible', 'resolved_by': 'goal_difference'},
          {'rank': 3, 'team_id': 'club-alpha-2', 'played': 3, 'won': 1, 'drawn': 1, 'lost': 1, 'scored': 3, 'conceded': 4, 'diff': -1, 'points': 4, 'fair_play': 0, 'status': 'still_possible', 'resolved_by': 'goal_difference'},
          {'rank': 4, 'team_id': 'club-alpha-3', 'played': 3, 'won': 0, 'drawn': 0, 'lost': 3, 'scored': 1, 'conceded': 5, 'diff': -4, 'points': 0, 'fair_play': -2, 'status': 'eliminated', 'resolved_by': null},
        ],
        'team_names': _teams,
      };

  static Map<String, dynamic> clubs() => {
        'clubs': _teams.entries
            .map((e) => {'club_id': e.key, 'name': e.value, 'tenant_id': 'tenant-alpha'})
            .toList()
      };

  static Map<String, dynamic> club(String id) => {
        'club_id': id,
        'name': _teams[id] ?? id,
        'tenant_id': 'tenant-alpha',
        'league_id': 'league-alpha-1',
        'profile': {
          'home_venue': {'name': 'Alpha Stadium, Islamabad', 'lat': 33.6844, 'lng': 73.0479},
          'socials': {'instagram': '@alphafc', 'x': '@alphafc', 'website': 'alphafc.pk'},
          'staff': [
            {'role': 'Head coach', 'name': 'Imran Siddiqui'},
            {'role': 'Physio', 'name': 'Sana Malik'},
          ],
          'history': [
            {'season': '2025', 'played': 14, 'won': 9, 'drawn': 3, 'lost': 2},
            {'season': '2024', 'played': 14, 'won': 6, 'drawn': 4, 'lost': 4},
          ],
        },
        'roster': [
          {'player_id': 'player-alpha-1', 'name': 'Ali Khan', 'position': 'gk', 'role': 'captain', 'shirt_number': 1, 'squad_id': id},
          {'player_id': 'player-alpha-3', 'name': 'Bilal Ahmed', 'position': 'df', 'role': 'starter', 'shirt_number': 4, 'squad_id': id},
          {'player_id': 'player-alpha-5', 'name': 'Hamza Raza', 'position': 'mf', 'role': 'vice_captain', 'shirt_number': 8, 'squad_id': id},
          {'player_id': 'player-alpha-7', 'name': 'Usman Tariq', 'position': 'fw', 'role': 'substitute', 'shirt_number': 19, 'squad_id': id},
        ],
      };

  static Map<String, dynamic> products() => {
        'products': [
          {
            'product_id': 'prod-jersey-001',
            'name': 'Alpha FC Home Jersey 2026',
            'category': 'Jerseys',
            'description': 'Breathable match-day jersey with the club crest.',
            'variants': [
              {'variant_id': 'var-jersey-001-S', 'size': 'S', 'color': 'Blue', 'price_minor': 450000, 'currency': 'PKR', 'stock': 5},
              {'variant_id': 'var-jersey-001-M', 'size': 'M', 'color': 'Blue', 'price_minor': 450000, 'currency': 'PKR', 'stock': 1},
              {'variant_id': 'var-jersey-001-L', 'size': 'L', 'color': 'Blue', 'price_minor': 450000, 'currency': 'PKR', 'stock': 0},
            ]
          },
          {
            'product_id': 'prod-scarf-001',
            'name': 'Supporters Scarf',
            'category': 'Accessories',
            'description': 'Knitted scarf in club colours.',
            'variants': [
              {'variant_id': 'var-scarf-001', 'size': 'One size', 'color': 'Blue/Gold', 'price_minor': 150000, 'currency': 'PKR', 'stock': 20},
            ]
          },
        ]
      };
}
