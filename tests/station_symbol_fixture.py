"""Original Station cards cross thresholds through real Station activations."""
from pathlib import Path
import tempfile
from common import ROOT
import optional_result_followup_fixture as actions
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.station import station_cost_choice
from scripts.build_test_database import build_fixture_database


class StationSymbolFixture(actions.OptionalResultFollowupFixture):
    def choose(self, s, seat='A', **fields):
        command = {'action_id': 'choose', **fields}
        before = authoritative_state_hash(s.state)
        foreign = 'D' if seat != 'D' else 'C'
        self.assertFalse(s.act('pilot:' + foreign, command).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        r = s.act('pilot:' + seat, command)
        self.assertTrue(r.ok, r.summary)

    def animate(self, s, source, workers, minimum, stats, keywords=('flying',)):
        for worker in workers:
            if s.state.cards[source.object_id].counters.get('charge', 0) >= minimum:
                break
            self.station(s, source, worker)
        self.assertGreaterEqual(s.state.cards[source.object_id].counters.get('charge', 0), minimum)
        self.assertIn('creature', s.engine._effective_card_data(s.state.cards[source.object_id])['type_line'].casefold())
        self.assertEqual(stats, tuple(s.engine._numeric_stat(source.object_id, stat) for stat in ('power', 'toughness')))
        self.assertTrue(set(keywords) <= s.engine._combat_keywords(s.state.cards[source.object_id]))

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'station-symbols.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
                               ROOT / 'tests/fixtures/station-symbol-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Station symbols', [DeckEntry('Isamaru, Hound of Konda', 1, 'commander'),
                                 DeckEntry('Generic Bound Plains', 30)], ['Isamaru, Hound of Konda'])

    def station(self, s, source, creature, *, pending=False):
        self.priority(s, 'A')
        current = s.state.cards[source.object_id]
        ability = next(a for a in s.engine._activated_abilities(current) if station_cost_choice(a) is not None)
        identifier = 'activate:' + current.ref + ':' + ability.ability_id
        offered = next(a for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if a['id'] == identifier)
        self.assertIn(creature.ref, offered['cost_summary']['choose_cost'][0]['legal_refs'])
        command = {'action_id': identifier, 'cost_cards': [creature.ref]}
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:D', command).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        self.assertFalse(s.act('pilot:A', {'action_id': identifier, 'cost_cards': [source.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        r = s.act('pilot:A', command)
        self.assertTrue(r.ok, r.summary)
        self.assertTrue(s.state.cards[creature.object_id].tapped)
        if not pending:
            self.settle(s)
