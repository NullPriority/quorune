"""Native source Explore action helpers; deliberately not a TestCase."""
from pathlib import Path
import tempfile
from common import ROOT
import optional_result_followup_fixture as actions
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class SourceExploreFixture(actions.OptionalResultFollowupFixture):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'source-explore.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
                               ROOT / 'tests/fixtures/source-explore-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Source Explore', [DeckEntry('Isamaru, Hound of Konda', 1, 'commander'),
                                 DeckEntry('Generic Bound Plains', 30)], ['Isamaru, Hound of Konda'])

    def empty_library(self, s, seat='A'):
        for oid in list(s.state.players[seat].zones['library']):
            s.engine.move_card(oid, 'exile', reason='Prepared empty library')

    def explore_choice(self, s, seat, choice):
        from quorune.record import authoritative_state_hash
        self.assertEqual('semantic.choice', s.state.pending_decision.kind)
        self.assertEqual([seat], s.state.pending_decision.actors)
        command = {'action_id': 'choose', 'choice': choice}
        before = authoritative_state_hash(s.state)
        foreign = 'B' if seat == 'A' else 'A'
        self.assertFalse(s.act('pilot:' + foreign, command).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        self.assertFalse(s.act('pilot:' + seat, {'action_id': 'choose', 'choice': 'unrepresented'}).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        s = self.replay(s, load=True)
        r = s.act('pilot:' + seat, command)
        self.assertTrue(r.ok, r.summary)
        return s
