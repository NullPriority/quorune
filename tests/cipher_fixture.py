"""Original Cipher cards resolve, encode and cast independent combat copies."""
from pathlib import Path
import tempfile
from common import ROOT
import optional_result_followup_fixture as fixtures
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class CipherFixture(fixtures.OptionalResultFollowupFixture):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'cipher.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json', ROOT / 'tests/fixtures/cipher-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Cipher', [DeckEntry('Isamaru, Hound of Konda', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)], ['Isamaru, Hound of Konda'])

    def choose_as(self, s, seat, **fields):
        action = fields.get('choice', 'choose') if s.state.pending_decision.kind == 'selection.exile_cast' else 'choose'
        command = {'action_id': action, **fields}
        before = authoritative_state_hash(s.state)
        wrong = 'D' if seat != 'D' else 'C'
        self.assertFalse(s.act('pilot:' + wrong, command).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        result = s.act('pilot:' + seat, command)
        self.assertTrue(result.ok, result.summary)
