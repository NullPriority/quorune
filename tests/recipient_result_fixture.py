"""Shared native recipient-result actions; deliberately not a TestCase."""
import json
from pathlib import Path
import tempfile
from common import ROOT
import inherited_composition_fixture as actions
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database

class RecipientResultFixture(actions.InheritedNativeFixture):

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'recipient-result.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json', ROOT / 'tests/fixtures/recipient-result-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Recipient result', [DeckEntry('Isamaru, Hound of Konda', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)], ['Isamaru, Hound of Konda'])

    def activate(self, s, seat, source, targets=()):
        self.priority(s, seat)
        current = s.state.cards[source.object_id]
        action = next((a for a in s.packet('pilot:' + seat, full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:' + current.ref + ':')))
        r = s.act('pilot:' + seat, {'action_id': action['id'], 'targets': list(targets), 'pay': 'auto'})
        self.assertTrue(r.ok, r.summary)

    def private(self, s):
        for seat in 'BCD':
            self.assertNotIn('private-recipient-hand', json.dumps(s.packet('pilot:' + seat, full=True)))

    def optional(self, s, apply):
        self.assertEqual('semantic.choice', s.state.pending_decision.kind)
        self.assertEqual(['A'], s.state.pending_decision.actors)
        self.private(s)
        before = authoritative_state_hash(s.state)
        for principal, choice in (('pilot:B', 'apply'), ('pilot:A', 'some')):
            self.assertFalse(s.act(principal, {'action_id': 'choose', 'choice': choice}).ok)
            self.assertEqual(before, authoritative_state_hash(s.state))
        s = self.replay(s, load=True)
        r = s.act('pilot:A', {'action_id': 'choose', 'choice': 'apply' if apply else 'decline'})
        self.assertTrue(r.ok, r.summary)
        return s

    def insects(self, s):
        return [c for c in s.state.cards.values() if c.is_token and c.zone == 'battlefield' and (c.controller == 'A') and ('insect' in str(s.engine._effective_card_data(c).get('type_line', '')).casefold())]

    def hydras(self, s):
        return [c for c in s.state.cards.values() if c.is_token and c.zone == 'battlefield' and (c.controller == 'A') and ('hydra' in str(s.engine._effective_card_data(c).get('type_line', '')).casefold())]
