"""Canonical real cards and ordinary actions; no TestCase ancestry."""
from pathlib import Path
import re
import tempfile
from common import ROOT
import optional_result_followup_fixture as actions
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class PreparedPowerDamageFixture(actions.OptionalResultFollowupFixture):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory();path = Path(cls.temporary.name) / 'prepared-power.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json', ROOT / 'tests/fixtures/prepared-power-damage-cards.json'], path)
        cls.db = CardDatabase(path);cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Prepared damage', [DeckEntry('Isamaru, Hound of Konda', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)], ['Isamaru, Hound of Konda'])

    def board(self, name, seed, *, fighter='Grizzly Bears', recipient='Wall of Stone'):
        s = self.session(seed)
        spell = self.add(s.engine, name, zone='hand')
        first = self.add(s.engine, fighter, ref='prepared-source-creature')
        second = self.add(s.engine, recipient, seat='B', ref='prepared-recipient-creature')
        self.assert_closed(s, spell)
        cost = self.db.lookup(name, fuzzy=False).mana_cost
        mana = {}
        for symbol in re.findall(r'\{(\d+|[WUBRG])\}', cost):
            key = 'C' if symbol.isdigit() else symbol
            mana[key] = mana.get(key, 0) + (int(symbol) if symbol.isdigit() else 1)
        self.ready(s, mana)
        return s, spell, first, second

    def cast_prepared(self, s, spell, first, second, *, resume=False, **fields):
        self.priority(s, 'A');row = self.action(s, 'A', spell)
        command = {'action_id': row['id'], 'targets': [first.ref, second.ref], 'pay': 'auto', **fields}
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:D', command).ok);self.assertEqual(before, authoritative_state_hash(s.state))
        invalid = {**command, 'targets': [second.ref, first.ref]}
        self.assertFalse(s.act('pilot:A', invalid).ok);self.assertEqual(before, authoritative_state_hash(s.state))
        result = s.act('pilot:A', command);self.assertTrue(result.ok, result.summary)
        return self.replay(s, load=True) if resume else s
