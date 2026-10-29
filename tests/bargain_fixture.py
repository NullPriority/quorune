"""Shared original Bargain actions without test discovery inheritance."""
from pathlib import Path
import tempfile
import optional_result_followup_fixture as fixtures
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class BargainFixture(fixtures.OptionalResultFollowupFixture):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory();path = Path(cls.temporary.name) / 'bargain.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json', ROOT / 'tests/fixtures/bargain-cards.json'], path)
        cls.db = CardDatabase(path);cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Bargain', [DeckEntry('Isamaru, Hound of Konda', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)], ['Isamaru, Hound of Konda'])

    def bargain_action(self, s, source, chosen=True):
        self.priority(s, 'A')
        action = next(row for row in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if row['id'] == 'cast:' + source.ref)
        option = next(row for row in action['cost_options'] if ('+bargain:' in row['id']) is chosen)
        return action, option

    def cast_bargain(self, s, source, price=None, targets=(), *, chosen=True, **fields):
        action, option = self.bargain_action(s, source, chosen)
        command = {'action_id': action['id'], 'cost_option': option['id'], 'targets': list(targets), 'pay': 'auto', **fields}
        if chosen:command['sacrifice_cards'] = [price.ref]
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:D', command).ok);self.assertEqual(before, authoritative_state_hash(s.state))
        result = s.act('pilot:A', command);self.assertTrue(result.ok, result.summary)

    def choose_as(self, s, actor, **fields):
        command = {'action_id': 'choose', **fields};before = authoritative_state_hash(s.state)
        wrong = 'D' if actor != 'D' else 'C'
        self.assertFalse(s.act('pilot:' + wrong, command).ok);self.assertEqual(before, authoritative_state_hash(s.state))
        result = s.act('pilot:' + actor, command);self.assertTrue(result.ok, result.summary)
