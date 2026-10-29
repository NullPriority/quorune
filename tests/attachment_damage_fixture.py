"""Native attached-damage actions, without TestCase ancestry."""
from pathlib import Path
import tempfile
from common import ROOT
import optional_result_followup_fixture as actions
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class AttachmentDamageFixture(actions.OptionalResultFollowupFixture):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'attachment-damage.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
                               ROOT / 'tests/fixtures/attachment-damage-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Attached damage', [DeckEntry('Isamaru, Hound of Konda', 1, 'commander'),
                                 DeckEntry('Generic Bound Plains', 30)], ['Isamaru, Hound of Konda'])

    def attach_cast(self, s, aura, body):
        self.cast(s, aura.controller, aura, [body.ref])
        self.settle(s)
        self.assertEqual(body.object_id, s.state.cards[aura.object_id].attached_to)

    def attack_for(self, s, seat, assignments):
        self.until(s, lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind == 'combat.attackers')
        command = {'action_id': 'attack', 'attackers': {body.ref: recipient for body, recipient in assignments}}
        before = authoritative_state_hash(s.state)
        foreign = 'D' if seat != 'D' else 'C'
        self.assertFalse(s.act('pilot:' + foreign, command).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        result = s.act('pilot:' + seat, command)
        self.assertTrue(result.ok, result.summary)

    def choose_as(self, s, seat, **fields):
        command = {'action_id': 'choose', **fields}
        before = authoritative_state_hash(s.state)
        foreign = 'D' if seat != 'D' else 'C'
        self.assertFalse(s.act('pilot:' + foreign, command).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        result = s.act('pilot:' + seat, command)
        self.assertTrue(result.ok, result.summary)

    def damage_pending(self, s, source):
        self.until(s, lambda s: self.trigger_count(s, source) > 0)

    def ping(self, s, seat, source, target):
        self.activate(s, seat, source, [target])
