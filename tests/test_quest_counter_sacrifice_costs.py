from __future__ import annotations

"""CR 118.3/601.2h/602.2b: remove three counters and sacrifice the same source."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.abilities import parse_activated_abilities
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.preflight import card_semantic_status
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_compound_damage_recipients as controls

NAMES = ('Soul Stair Expedition', 'Khalni Heart Expedition', 'Quest for the Gravelord',
         'Sunspring Expedition', 'Ior Ruin Expedition')


class QuestCounterSacrificeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.CompoundDamageRuntimeTests.session
    add = controls.CompoundDamageRuntimeTests.add
    prepare = controls.CompoundDamageRuntimeTests.prepare
    checkpoint = controls.CompoundDamageRuntimeTests.checkpoint
    cast = controls.CompoundDamageRuntimeTests.cast
    resolve = controls.CompoundDamageRuntimeTests.resolve
    replay = controls.CompoundDamageRuntimeTests.replay
    binding = controls.CompoundDamageRuntimeTests.binding
    actions = controls.CompoundDamageRuntimeTests.actions
    private_replay = controls.CompoundDamageRuntimeTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'quest-costs.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/quest-counter-sacrifice-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Joined source cost witnesses',
            [DeckEntry('Generic Bound Commander', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)],
            ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def activation(self, session, source):
        current = session.state.cards[source.object_id]
        return next(row for row in self.actions(session) if row['id'].startswith('activate:' + current.ref + ':'))

    def activate(self, session, source, targets=()):
        command = {'action_id': self.activation(session, source)['id'], 'targets': list(targets), 'pay': 'auto'}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B', command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act('pilot:A', command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.assertTrue(session.state.stack)

    def test_five_originals_preserve_raw_cost_text_and_bind_both_existing_owners(self):
        for index, name in enumerate(NAMES):
            session = self.session(733010 + index)
            source = self.add(session.engine, name)
            card = self.db.lookup(name)
            ability, = parse_activated_abilities(card_name=card.name, oracle_text=card.oracle_text, keywords=card.keywords)
            self.assertIsNotNone(ability.source_counter_removal_cost)
            self.assertEqual('quest', ability.source_counter_removal_cost.counter_name)
            self.assertEqual(3, ability.source_counter_removal_cost.amount)
            self.assertTrue(ability.sacrifice_source)
            self.assertEqual(card.oracle_text.splitlines()[-1].split(':', 1)[0], ability.cost_text)
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual('exact', ir.status, ir.material_residuals)
            self.assertTrue(self.binding(session, source)['strict_capability_ready'])
            status = card_semantic_status(card, session.engine.semantics, db=self.db,
                capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual('fully_playable', status['status'])

    def test_unbound_antecedents_variable_counts_and_extra_cost_tails_remain_residual(self):
        card = self.db.lookup('Sunspring Expedition')
        for text in (
            'Remove three quest counters from a creature you control and sacrifice it: You gain 8 life.',
            'Remove X quest counters from this enchantment and sacrifice it: You gain 8 life.',
            'Remove three quest counters from this enchantment and sacrifice it and pay 2 life: You gain 8 life.',
            'Remove three quest counters from that enchantment and sacrifice it: You gain 8 life.',
        ):
            changed = replace(card, oracle_text=text)
            self.assertNotEqual('exact', compile_oracle_card(changed, capability_registry=self.registry,
                capability_profile='commander_review').status, text)

    def test_actual_source_cost_requires_three_counters_and_pays_before_life_draw_or_token_result(self):
        for index, name in enumerate(('Sunspring Expedition', 'Ior Ruin Expedition', 'Quest for the Gravelord')):
            session = self.session(733030 + index)
            source = self.add(session.engine, name)
            source.counters['quest'] = 2
            self.prepare(session)
            self.assertFalse(any(row['id'].startswith('activate:' + source.ref + ':') for row in self.actions(session)))
            # Three counters are a separate valid initial position for the paid witness.
            session = self.session(733040 + index)
            source = self.add(session.engine, name)
            source.counters['quest'] = 3
            private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
            self.prepare(session)
            self.checkpoint(session)
            hand = len(session.state.players['A'].zones['hand'])
            self.activate(session, source)
            self.assertEqual(40, session.state.players['A'].life)
            self.assertEqual(hand, len(session.state.players['A'].zones['hand']))
            self.assertFalse(any(card.is_token and card.zone == 'battlefield' for card in session.state.cards.values()))
            session = self.replay(session, load=True)
            self.assertIsNone(self.resolve(session))
            if name == 'Sunspring Expedition':
                self.assertEqual([48, 40, 40, 40], [session.state.players[seat].life for seat in 'ABCD'])
            elif name == 'Ior Ruin Expedition':
                self.assertEqual(hand + 2, len(session.state.players['A'].zones['hand']))
            else:
                token, = [card for card in session.state.cards.values() if card.is_token and card.zone == 'battlefield']
                data = session.engine._effective_card_data(token)
                self.assertEqual('A', token.controller)
                self.assertEqual((5, 5), tuple(session.engine._numeric_stat(token.object_id, field) for field in ('power', 'toughness')))
                self.assertEqual(['B'], list(data['colors']))
                self.assertTrue({'zombie', 'giant'} <= session.engine._type_parts(data['type_line'])[1])
            self.private_replay(session, private)

    def test_fresh_original_admission_kills_omitted_joined_cost_production(self):
        from quorune import abilities
        method = 'test_five_originals_preserve_raw_cost_text_and_bind_both_existing_owners'
        with patch('quorune.abilities._activation_cost_clauses', side_effect=lambda text, name: abilities._split_cost_clauses(text)):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()

    def test_actual_landfall_counter_enables_ior_ruin_then_source_cost_leaves_draw_pending(self):
        session = self.session(733060)
        source = self.add(session.engine, 'Ior Ruin Expedition')
        source.counters['quest'] = 2
        forest = self.add(session.engine, 'Forest', zone='hand')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session)
        self.checkpoint(session)
        action = next(row for row in self.actions(session) if row.get('card') == forest.ref and row['action'] == 'play_land')
        self.assertTrue(session.act('pilot:A', {'action_id': action['id']}).ok)
        pending = self.resolve(session)
        self.assertEqual('semantic.choice', pending.kind)
        session = self.replay(session, load=True)
        before = authoritative_state_hash(session.state)
        command = {'action_id': 'choose', 'choice': 'put'}
        self.assertFalse(session.act('pilot:B', command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act('pilot:A', command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(3, session.state.cards[source.object_id].counters.get('quest', 0))
        hand = len(session.state.players['A'].zones['hand'])
        self.activate(session, source)
        session = self.replay(session, load=True)
        self.assertEqual(hand, len(session.state.players['A'].zones['hand']))
        self.assertIsNone(self.resolve(session))
        self.assertEqual(hand + 2, len(session.state.players['A'].zones['hand']))
        self.private_replay(session, private)

    def test_actual_soul_stair_source_cost_keeps_only_the_surviving_original_graveyard_target(self):
        session = self.session(733070)
        source = self.add(session.engine, 'Soul Stair Expedition')
        source.counters['quest'] = 3
        selected = [self.add(session.engine, 'Generic Bound Body', zone='graveyard', ref='stair-' + str(i)) for i in range(2)]
        foreign = self.add(session.engine, 'Generic Bound Body', seat='B', zone='graveyard', ref='stair-foreign')
        response = self.add(session.engine, 'Cremate', seat='B', zone='hand')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        session.state.players['B'].mana_pool['B'] = 1
        self.prepare(session)
        self.checkpoint(session)
        action = self.activation(session, source)
        before = authoritative_state_hash(session.state)
        for targets in ([selected[0].ref, selected[0].ref], [selected[0].ref, foreign.ref]):
            rejected = session.act('pilot:A', {'action_id': action['id'], 'targets': targets, 'pay': 'auto'})
            self.assertFalse(rejected.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        self.activate(session, source, [card.ref for card in selected])
        self.cast(session, 'B', response, [selected[0].ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('exile', session.state.cards[selected[0].object_id].zone)
        self.assertEqual('hand', session.state.cards[selected[1].object_id].zone)
        self.assertEqual('graveyard', session.state.cards[foreign.object_id].zone)
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.private_replay(session, private)

    def test_actual_khalni_cost_precedes_private_search_and_the_sacrificed_source_cannot_observe_lands(self):
        session = self.session(733080)
        source = self.add(session.engine, 'Khalni Heart Expedition')
        source.counters['quest'] = 3
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session)
        self.checkpoint(session)
        self.activate(session, source)
        pending = self.resolve(session)
        self.assertIsNotNone(pending)
        self.assertEqual(['pilot:A'], session.pending_principals())
        rows = session.packet('pilot:A', full=True)['decision']['ctx']['search_cards']
        chosen = [row['id'] for row in rows[:2]]
        self.assertEqual(2, len(chosen))
        for seat in 'BCD':
            self.assertIsNone(session.packet('pilot:' + seat, full=True)['decision'])
        session = self.replay(session, load=True)
        before = authoritative_state_hash(session.state)
        command = {'action_id': 'choose', 'search_cards': chosen}
        self.assertFalse(session.act('pilot:B', command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertTrue(session.act('pilot:A', command).ok)
        self.assertIsNone(self.resolve(session))
        for ref in chosen:
            card = next(card for card in session.state.cards.values() if card.ref == ref)
            self.assertEqual(('battlefield', 'A', True), (card.zone, card.controller, card.tapped))
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.assertEqual(0, session.state.cards[source.object_id].counters.get('quest', 0))
        self.private_replay(session, private)

    def test_countering_the_actual_ior_ability_does_not_refund_either_source_cost(self):
        session = self.session(733090)
        source = self.add(session.engine, 'Ior Ruin Expedition')
        source.counters['quest'] = 3
        response = self.add(session.engine, 'Stifle', seat='B', zone='hand')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        session.state.players['B'].mana_pool['U'] = 1
        self.prepare(session)
        self.checkpoint(session)
        hand = len(session.state.players['A'].zones['hand'])
        self.activate(session, source)
        ability, = session.state.stack
        self.cast(session, 'B', response, [ability.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.assertEqual(0, session.state.cards[source.object_id].counters.get('quest', 0))
        self.assertEqual(hand, len(session.state.players['A'].zones['hand']))
        self.private_replay(session, private)

    def test_fresh_actual_source_cost_witness_kills_omitted_sacrifice_clause(self):
        from quorune.abilities import _activation_cost_clauses
        method = 'test_actual_source_cost_requires_three_counters_and_pays_before_life_draw_or_token_result'
        def omit_sacrifice(text, name):
            clauses = _activation_cost_clauses(text, name)
            return clauses[:1] if ' and sacrifice it' in text.casefold() else clauses
        with patch('quorune.abilities._activation_cost_clauses', side_effect=omit_sacrifice):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()
