"""Ordinary real-card actions for fixed power multiples and source controls."""
from pathlib import Path
import re
import tempfile
import source_counter_pronoun_fixture as actions
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class CreaturePowerMultipleFixture(actions.SourceCounterPronounFixture):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory();path = Path(cls.temporary.name) / 'power-multiples.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json', ROOT / 'tests/fixtures/creature-power-multiple-cards.json'], path)
        cls.db = CardDatabase(path);cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Fixed power multiples', [DeckEntry('Isamaru, Hound of Konda', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)], ['Isamaru, Hound of Konda'])

    def multiple_board(self, name, seed, *, fighter='Grizzly Bears', recipient='Wall of Stone', discount=False):
        s = self.session(seed);spell = self.add(s.engine, name, zone='hand')
        source = self.add(s.engine, fighter, ref='multiple-source-creature')
        target = self.add(s.engine, recipient, seat='B', ref='multiple-recipient-creature')
        if self.db.lookup(recipient, fuzzy=False).loyalty is not None:
            target.counters['loyalty'] = int(self.db.lookup(recipient, fuzzy=False).loyalty)
            target.annotations['loyalty_initialized'] = True
        if name == 'Punishing Punch':
            self.add(s.engine, 'Grizzly Bears', zone='graveyard', ref='price-creature-one')
            self.add(s.engine, 'Merfolk of the Pearl Trident' if discount else 'Sol Ring', zone='graveyard', ref='price-second-card')
        generic = sum(int(n) for n in re.findall(r'\{(\d+)\}', self.db.lookup(name, fuzzy=False).mana_cost))
        self.assert_closed(s, spell);self.ready(s, {'C': generic, 'G': 1})
        return s, spell, source, target

    def cast_multiple(self, s, spell, source, target):
        self.priority(s, 'A');row = self.action(s, 'A', spell)
        command = {'action_id': row['id'], 'targets': [source.ref, target.ref], 'pay': 'auto'}
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:D', command).ok);self.assertEqual(before, authoritative_state_hash(s.state))
        self.assertFalse(s.act('pilot:A', {**command, 'targets': [target.ref, source.ref]}).ok);self.assertEqual(before, authoritative_state_hash(s.state))
        result = s.act('pilot:A', command);self.assertTrue(result.ok, result.summary)
