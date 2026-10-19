from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.compiler.public_state_queries import fixed_public_state_condition
from quorune.resolution_conditions import resolution_condition_is_closed
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_spree_mode_costs as actions


class GraveyardResolutionConditionTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.SpreeModeCostRuntimeTests.session
    add = actions.SpreeModeCostRuntimeTests.add
    prepare = actions.SpreeModeCostRuntimeTests.prepare
    checkpoint = actions.SpreeModeCostRuntimeTests.checkpoint
    replay = actions.SpreeModeCostRuntimeTests.replay
    resolve = actions.SpreeModeCostRuntimeTests.resolve
    reject = actions.SpreeModeCostRuntimeTests.reject
    assert_closed = actions.SpreeModeCostRuntimeTests.assert_closed

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'graveyard-resolution.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/graveyard-resolution-condition-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Graveyard conditions', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 20)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def test_four_originals_and_card_only_condition_keep_complete_admission(self):
        for name in ('Demoralize', 'Calculated Dismissal', 'Unholy Hunger', "Gideon's Phalanx"):
            card = self.db.lookup(name)
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
            with self.subTest(name=name):
                self.assertEqual('exact', ir.status, ir.material_residuals)
                for face in ir.faces:
                    for node in face.nodes:
                        self.assertEqual(node.text, card.oracle_text[node.span.start:node.span.end])
                session = self.session(60824000)
                self.assert_closed(session, self.add(session.engine, name, zone='hand'))
        condition = fixed_public_state_condition(
            'there are two or more instant and/or sorcery cards in your graveyard', source_name='Fixture')
        self.assertEqual(2, condition.quantity.schema_version)
        self.assertIs(condition.quantity.query.token, False)
        self.assertTrue(resolution_condition_is_closed(condition))

    def offered(self, session, card):
        return next((row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if row['id'] == 'cast:' + session.state.cards[card.object_id].ref), None)

    def test_legacy_card_queries_remain_closed_but_other_v2_quantities_stay_excluded(self):
        condition = fixed_public_state_condition('there are seven or more cards in your graveyard', source_name='Fixture')
        quantity = condition.quantity
        legacy = replace(quantity, schema_version=1, query=replace(quantity.query, token=None))
        self.assertTrue(resolution_condition_is_closed(replace(condition, quantity=legacy)))
        for changed in (replace(quantity, multiplier=2), replace(quantity, offset=1),
                replace(quantity, query=replace(quantity.query, token=None)),
                replace(quantity, query=replace(quantity.query, token=True)), replace(quantity, exclude_source=True)):
            self.assertFalse(resolution_condition_is_closed(replace(condition, quantity=changed)))
        for text in ('this creature entered this turn', 'this creature has a +1/+1 counter on it'):
            source_bound = fixed_public_state_condition(text, source_name='Fixture')
            self.assertIsNotNone(source_bound)
            self.assertFalse(resolution_condition_is_closed(source_bound))

    def test_original_unholy_hunger_reads_resolution_time_own_cards_with_private_replay(self):
        session = self.session(60824001)
        spell = self.add(session.engine, 'Unholy Hunger', zone='hand')
        response = self.add(session.engine, 'Generic Bound Growth', zone='hand', ref='spell-mastery-response')
        self.add(session.engine, 'Generic Bound Growth', zone='graveyard', ref='spell-mastery-first-card')
        target = self.add(session.engine, 'Generic Bound Body', seat='B', ref='unholy-target')
        friendly = self.add(session.engine, 'Generic Bound Body', ref='mastery-response-target')
        self.add(session.engine, 'Forest', ref='mastery-response-mana')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='mastery-private')
        self.prepare(session, mana={'C':3, 'B':2})
        self.checkpoint(session)
        offer = self.offered(session, spell)
        self.assertIsNotNone(offer, 'The previously supported conditional original must remain castable')
        self.assert_closed(session, spell)
        self.reject(session, offer['id'], 'B', targets=[target.ref], pay='auto')
        self.reject(session, offer['id'], targets=[target.ref], pay='manual', payment={'C':3, 'B':1})
        accepted = session.act('pilot:A', {'action_id':offer['id'], 'targets':[target.ref], 'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        session = self.replay(session, load=True)
        self.assertEqual(1, len(session.state.players['A'].zones['graveyard']))
        response_offer = self.offered(session, response)
        self.assertIsNotNone(response_offer)
        accepted = session.act('pilot:A', {'action_id':response_offer['id'], 'targets':[friendly.ref], 'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual(42, session.state.players['A'].life)
        self.assertEqual('graveyard', session.state.cards[target.object_id].zone)
        self.assertEqual('graveyard', session.state.cards[spell.object_id].zone)
        self.assertEqual('graveyard', session.state.cards[response.object_id].zone)
        self.assertEqual('battlefield', session.state.cards[friendly.object_id].zone)
        for viewer in 'ABD':
            self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.replay(session, load=True)

    def test_card_only_consumer_omission_is_killed_by_native_original(self):
        with patch('quorune.resolution_conditions._card_only_graveyard_count', return_value=False):
            with self.assertRaises(AssertionError):
                type(self)('test_original_unholy_hunger_reads_resolution_time_own_cards_with_private_replay').test_original_unholy_hunger_reads_resolution_time_own_cards_with_private_replay()

    def test_original_unholy_hunger_excludes_opponent_cards_and_resolving_spell(self):
        session = self.session(60824002)
        spell = self.add(session.engine, 'Unholy Hunger', zone='hand')
        self.add(session.engine, 'Generic Bound Growth', zone='graveyard', ref='one-own-mastery-card')
        for index in range(2):
            self.add(session.engine, 'Generic Bound Growth', seat='B', zone='graveyard', ref='foreign-mastery-' + str(index))
        target = self.add(session.engine, 'Generic Bound Body', seat='C', ref='false-mastery-target')
        self.prepare(session, mana={'C':3, 'B':2})
        self.checkpoint(session)
        self.assert_closed(session, spell)
        accepted = session.act('pilot:A', {'action_id':'cast:' + spell.ref, 'targets':[target.ref], 'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual('graveyard', session.state.cards[target.object_id].zone)
        self.assertEqual(40, session.state.players['A'].life)
        self.assertEqual(2, len(session.state.players['A'].zones['graveyard']))
        self.replay(session, load=True)
