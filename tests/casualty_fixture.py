"""Canonical ordinary actions shared by the original Casualty witnesses."""
from pathlib import Path
import tempfile
import optional_result_followup_fixture as fixtures
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class CasualtyFixture(fixtures.OptionalResultFollowupFixture):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory();path = Path(cls.temporary.name) / 'casualty.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json', ROOT / 'tests/fixtures/casualty-cards.json'], path)
        cls.db = CardDatabase(path);cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Casualty', [DeckEntry('Isamaru, Hound of Konda', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)], ['Isamaru, Hound of Konda'])

    def casualty_action(self, s, source):
        self.priority(s, 'A')
        action = next(row for row in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if row['id'] == 'cast:' + source.ref)
        option = next(row for row in action['cost_options'] if '+casualty:' in row['id'])
        return action, option

    def cast_casualty(self, s, source, creature, targets=(), **fields):
        action, option = self.casualty_action(s, source)
        command = {'action_id': action['id'], 'cost_option': option['id'], 'sacrifice_cards': [creature.ref], 'targets': list(targets), 'pay': 'auto', **fields}
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:D', command).ok);self.assertEqual(before, authoritative_state_hash(s.state))
        result = s.act('pilot:A', command);self.assertTrue(result.ok, result.summary)

    def choose_copy(self, s, targets):
        self.assertEqual('semantic.storm', s.state.pending_decision.kind)
        command = {'action_id': 'choose', 'copy_targets': [list(targets)]}
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:D', command).ok);self.assertEqual(before, authoritative_state_hash(s.state))
        result = s.act('pilot:A', command);self.assertTrue(result.ok, result.summary)
