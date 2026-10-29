"""Shared native optional-composition actions; deliberately not a TestCase."""
import json
from pathlib import Path
import tempfile
import unittest
from common import ROOT
import recipient_result_fixture as fixtures
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database

class OptionalResultFollowupFixture(fixtures.RecipientResultFixture):

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'optional-followups.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json', ROOT / 'tests/fixtures/optional-result-followup-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Optional composition', [DeckEntry('Isamaru, Hound of Konda', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)], ['Isamaru, Hound of Konda'])

    def ready(self, s, mana):
        self.add(s.engine, 'Divination', zone='hand', ref='private-recipient-hand')
        self.prepare(s, mana=mana)
        self.checkpoint(s)

    def step(self, s, seat, phase, step):
        from quorune.engine import TURN_STEPS
        index = TURN_STEPS.index((phase, step))
        self.assertGreater(index, 0)
        s.engine.permissions.invalidate_current()
        s.state.pending_decision = None
        s.state.active_player = seat
        s.state.phase, s.state.step = TURN_STEPS[index - 1]
        s.state.phase_index = index - 1
        s.state.priority_passes = []
        s.engine._grant_priority(seat)
        s.engine.pump()
        self.checkpoint(s)
        self.until(s, lambda s: s.state.phase == phase and s.state.step == step)

    def retarget_copy(self, s, targets):
        self.assertEqual('semantic.choice', s.state.pending_decision.kind)
        command = {'action_id': 'choose', 'targets': list(targets)}
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:D', command).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        r = s.act('pilot:A', command)
        self.assertTrue(r.ok, r.summary)

    def cast_face(self, s, card, face, targets=()):
        self.priority(s, 'A')
        current = s.state.cards[card.object_id]
        offered = next((a for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if a.get('card') == current.ref and a.get('face') == face))
        r = s.act('pilot:A', {'action_id': offered['id'], 'face': face, 'targets': list(targets), 'pay': 'auto'})
        self.assertTrue(r.ok, r.summary)

    def optional_counter(self, s, apply):
        self.assertEqual('semantic.choice', s.state.pending_decision.kind)
        self.assertEqual(['A'], s.state.pending_decision.actors)
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:D', {'action_id': 'choose', 'choice': 'put'}).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        s = self.replay(s, load=True)
        r = s.act('pilot:A', {'action_id': 'choose', 'choice': 'put' if apply else 'decline'})
        self.assertTrue(r.ok, r.summary)
        return s

    def finish_turn(self, s):
        turn = s.state.turn_sequence
        for _ in range(100):
            s.engine.pump()
            if s.state.turn_sequence > turn:
                return
            decision = s.state.pending_decision
            principal = s.pending_principals()[0]
            if decision.kind == 'cleanup.discard':
                ctx = s.packet(principal, full=True)['decision']['ctx']
                command = {'action_id': 'discard', 'cards': [r['id'] for r in ctx['hand'][:ctx['count']]]}
            elif decision.kind == 'priority':
                command = {'action_id': 'pass'}
            elif decision.kind == 'combat.attackers':
                command = {'action_id': 'attack', 'attackers': {}}
            elif decision.kind == 'combat.blockers':
                command = {'action_id': 'block', 'blocks': {}}
            else:
                self.fail('Unexpected final-turn decision ' + decision.kind)
            r = s.act(principal, command)
            self.assertTrue(r.ok, r.summary)
        self.fail('Turn did not complete')
