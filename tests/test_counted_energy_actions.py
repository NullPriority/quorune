"""Native resolution-time counts for player energy placement."""

import json
import tempfile
import unittest
from pathlib import Path

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class CountedEnergyActionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        path = Path(cls.temporary.name) / 'counted-energy.sqlite3'
        build_fixture_database([
            ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/counted-energy-cards.json',
        ], path)
        cls.db = CardDatabase(path)
        cls.addClassCleanup(cls.db.close)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Counted energy originals', [
            DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 20),
        ], ['Generic Bound Commander'])

    def test_original_robobrain_counts_after_response_and_handles_zero(self):
        for expected_energy in (1, 0):
            with self.subTest(expected_energy=expected_energy):
                session = self.session(107_014_001 + expected_energy)
                robot = self.add(session.engine, 'Robobrain War Mind', zone='hand')
                response = self.add(session.engine, 'Unsummon', seat='B', zone='hand')
                ring = self.add(session.engine, 'Sol Ring')
                opponent = self.add(session.engine, 'Ornithopter', seat='C', ref='opposing-thopter')
                private = self.add(session.engine, 'Generic Bound Body', seat='C',
                                   zone='hand', ref='private-energy-control')
                removed = (self.add(session.engine, 'Ornithopter', ref='friendly-thopter')
                           if expected_energy else robot)
                for card in (robot, response, ring, opponent, removed):
                    self.assert_closed(session, card)
                self.prepare(session, mana={'C': 3, 'U': 1})
                session.state.players['B'].mana_pool['U'] = 1
                self.checkpoint(session)

                self.cast(session, 'A', robot)
                self.resolve_top(session)
                self.assertEqual('battlefield', session.state.cards[robot.object_id].zone)
                self.assertEqual(1, len(session.state.stack))
                self.assertEqual(0, session.state.players['A'].energy)

                self.priority(session, 'B')
                offer = self.action(session, 'B', session.state.cards[response.object_id])
                before = authoritative_state_hash(session.state)
                rejected = session.act('pilot:B', {
                    'action_id': offer['id'], 'targets': [ring.ref], 'pay': 'auto',
                })
                self.assertFalse(rejected.ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                self.cast_for(session, 'B', response, [removed.ref])
                self.resolve_top(session)
                self.assertEqual('hand', session.state.cards[removed.object_id].zone)
                self.assertEqual(1, len(session.state.stack))

                session = self.replay(session, load=True)
                self.assertIsNone(self.resolve(session))
                self.assertEqual(expected_energy, session.state.players['A'].energy)
                self.assertEqual([0, 0, 0], [session.state.players[seat].energy for seat in 'BCD'])
                energy_placements = [event.details['placed'] for event in session.state.events
                                     if event.code == 'counter.add' and event.details['counter'] == 'energy']
                self.assertEqual([expected_energy] if expected_energy else [], energy_placements)
                for viewer in 'ABD':
                    self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
                self.assertIn(private.ref, json.dumps(session.packet('pilot:C', full=True)))
                self.replay(session)

    def test_original_scurry_counts_creatures_after_creating_gremlins(self):
        session = self.session(107_014_003)
        scurry = self.add(session.engine, 'Scurry of Gremlins', zone='hand')
        self.add(session.engine, 'Generic Bound Body', ref='friendly-creature')
        self.add(session.engine, 'Ornithopter', seat='C', ref='opposing-creature')
        self.assert_closed(session, scurry)
        self.prepare(session, mana={'C': 2, 'R': 1, 'W': 1})
        self.checkpoint(session)

        self.cast(session, 'A', scurry)
        self.assertIsNone(self.resolve(session))
        tokens = [card for card in session.state.cards.values()
                  if card.object_kind == 'token' and card.zone == 'battlefield']
        self.assertEqual(['A', 'A'], [card.controller for card in tokens])
        self.assertEqual(3, session.state.players['A'].energy)
        self.replay(session)
