from __future__ import annotations

"""Pinned CR 702.37e: a fixed nonmana turn-up price is a special action."""

import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from quorune.oracle_ir import compile_oracle_card
from quorune.nonmana_morph_model import FixedNonmanaMorphSpec, FixedMorphObjectCost, MorphObjectCostKind
from quorune.morph import FixedManaMorphSpec, MorphError, face_down_method_handler_descriptor
from quorune.semantic_runtime.morph import FixedManaMorphHandler, default_fixed_mana_morph_registry
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs import bind_card_program_runtime
from scripts.build_test_database import build_fixture_database
import test_spree_mode_costs as actions
import test_self_public_zone_casting as zone_actions


FIXTURE = ROOT / 'tests/fixtures/nonmana-morph-cards.json'


class FixedNonmanaMorphModelTests(unittest.TestCase):
    def test_nonmana_cost_codec_is_closed_and_historical_mana_handler_rejects_it(self):
        for cost in (FixedMorphObjectCost(MorphObjectCostKind.LIFE, 5),
                FixedMorphObjectCost(MorphObjectCostKind.REVEAL, color='U'),
                FixedMorphObjectCost(MorphObjectCostKind.DISCARD, subtype='zombie'),
                FixedMorphObjectCost(MorphObjectCostKind.RETURN, 2, subtype='island')):
            with self.subTest(cost=cost):
                spec = FixedNonmanaMorphSpec(cost)
                self.assertEqual(spec, FixedManaMorphSpec.from_dict(spec.to_dict()))
                descriptor = face_down_method_handler_descriptor(spec)
                self.assertEqual((spec,), default_fixed_mana_morph_registry().lower(descriptor, None))
                descriptor['handler_id'] = FixedManaMorphHandler().handler_id
                with self.assertRaises(SemanticNodeError):
                    FixedManaMorphHandler().validate(descriptor)
                for change in ({'unknown': True}, {'schema_version': True}, {'requirements': []},
                        {'requirements': dict(spec.requirements_dict, GENERIC=True)},
                        {'nonmana_cost': dict(cost.to_dict(), count=True)}):
                    with self.assertRaises(MorphError):
                        FixedManaMorphSpec.from_dict({**spec.to_dict(), **change})
        for kind, values in ((MorphObjectCostKind.REVEAL, {'color':'C'}),
                (MorphObjectCostKind.RETURN, {'count':3, 'subtype':'island'}),
                (MorphObjectCostKind.DISCARD, {'subtype':'inventedsubtype'})):
            with self.assertRaises(ValueError):
                FixedMorphObjectCost(kind, **values)

    def test_compiler_nonmana_grammar_and_dependency_removal_fail_closed(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.compiler import nonmana_morph_nodes
        registry = load_default_capability_registry()
        def compile(text, caps=registry):
            card = replace(actions.record(text, 'Creature — Zombie'), keywords=('Morph',), power='3', toughness='4')
            return compile_oracle_card(card, capability_registry=caps, capability_profile='commander_review')
        for text in ('Morph—Pay 5 life.', 'Morph—Reveal a white card in your hand.',
                'Morph—Discard a Zombie card.', "Morph—Return two Islands you control to their owner's hand."):
            with self.subTest(text=text):
                ir = compile(text)
                self.assertEqual('exact', ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual(text, node.text)
                self.assertEqual((0, len(text)), (node.span.start, node.span.end))
                self.assertTrue(node.handlers[0]['requires_complete_card_program'])
                with mock.patch.object(nonmana_morph_nodes, 'fixed_nonmana_morph_spec', return_value=None):
                    self.assertNotEqual('exact', compile(text).status)
        for text in ('Morph—Pay X life.', 'Morph—Reveal a colorless card in your hand.',
                'Morph—Discard two cards.', 'Morph—Sacrifice another creature.',
                'Morph—Pay 5 life and discard a card.', 'Morph—Pay 5 life.\nRewrite history.'):
            self.assertNotEqual('exact', compile(text).status)
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(row for row in raw['capabilities'] if row['id']=='casting.morph.fixed_nonmana').update(
            status='blocked', blockers=['Independent nonmana owner removal'])
        self.assertNotEqual('exact', compile('Morph—Pay 5 life.', CapabilityRegistry(raw)).status)


class FixedNonmanaMorphRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.SpreeModeCostRuntimeTests.session
    add = actions.SpreeModeCostRuntimeTests.add
    checkpoint = actions.SpreeModeCostRuntimeTests.checkpoint
    resolve = actions.SpreeModeCostRuntimeTests.resolve
    replay = actions.SpreeModeCostRuntimeTests.replay
    assert_closed = actions.SpreeModeCostRuntimeTests.assert_closed
    reject = actions.SpreeModeCostRuntimeTests.reject
    cast = zone_actions.SelfPublicZoneRuntimeTests.cast

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'nonmana-morph.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/fixed-resolution-payment-cards.json', FIXTURE], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Nonmana Morph witness', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def prepare(self, session, **kwargs):
        session.state.config.semantic_policy = 'trusted_only'
        return actions.SpreeModeCostRuntimeTests.prepare(self, session, **kwargs)

    def offers(self, session):
        return session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']

    def cast_face_down(self, session, source):
        self.assert_closed(session, source)
        self.prepare(session, mana={'C':3})
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id':'cast-morph:' + source.ref, 'pay':'auto'})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)
        self.assertTrue(session.state.cards[source.object_id].face_down)
        return 'turn-face-up:' + source.ref

    def test_actual_zombie_cutthroat_pays_life_to_turn_face_up_without_a_stack_item(self):
        session = self.session(70237051)
        engine = session.engine
        source = self.add(engine, 'Zombie Cutthroat', zone='hand', ref='nonmana-morph-zombie')
        private = self.add(engine, 'Generic Bound Growth', seat='D', zone='hand', ref='nonmana-morph-private')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 3})
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': 'cast-morph:' + source.ref, 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        for viewer in 'BCD':
            self.assertNotIn('Zombie Cutthroat', json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.assertNotIn(private.ref, json.dumps(session.packet('pilot:B', full=True)))
        session = self.replay(session, load=True)
        self.resolve(session)
        current = session.state.cards[source.object_id]
        self.assertTrue(current.face_down)
        incarnation = current.logical_object_id
        action_id = 'turn-face-up:' + current.ref
        offered = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if row['id'] == action_id)
        self.assertEqual(0, sum(offered['requirements'].values()))
        self.reject(session, action_id, 'B')
        life_before = session.state.players['A'].life
        result = session.act('pilot:A', {'action_id': action_id})
        self.assertTrue(result.ok, result.summary)
        current = session.state.cards[source.object_id]
        self.assertFalse(current.face_down)
        self.assertEqual(incarnation, current.logical_object_id)
        self.assertEqual(life_before - 5, session.state.players['A'].life)
        self.assertEqual('A', session.state.priority_player)
        self.assertFalse(session.state.stack)
        self.assertEqual(1, len(session.engine._current_turn_history('spell_cast')))
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        for viewer in 'BCD':
            self.assertIn('Zombie Cutthroat', json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.replay(session, load=True)

    def test_life_payment_omission_is_killed_by_actual_native_consequence(self):
        with mock.patch('quorune.rules.nonmana_morph_costs.pay_life_cost'):
            with self.assertRaisesRegex(AssertionError, '35 != 40'):
                self.test_actual_zombie_cutthroat_pays_life_to_turn_face_up_without_a_stack_item()

    def test_all_ten_originals_admit_normally_and_unknown_siblings_remain_residual(self):
        names = ('Putrid Raptor','Gathan Raiders','Raven Guild Initiate','Watcher of the Roost',
            'Temur Charger','Zombie Cutthroat',"Dragon's Eye Savants",'Ruthless Ripper','Fathom Seer','Horde Ambusher')
        session = self.session(70237052)
        for index, name in enumerate(names):
            with self.subTest(name=name):
                source = self.add(session.engine, name, zone='hand', ref='nonmana-original-' + str(index))
                self.assert_closed(session, source)
        for name in ('Gift of Doom', 'Skirk Volcanist'):
            program = compile_best_available_card_program(self.db, self.db.lookup(name),
                semantic_registry=session.engine.semantics, capability_registry=self.registry, capability_profile='commander_review')
            self.assertFalse(bind_card_program_runtime(program, capability_registry=self.registry,
                profile='commander_review')['strict_capability_ready'])

    def test_actual_discard_cost_uses_current_subtype_and_discards_before_turn_up(self):
        for index, name in enumerate(('Putrid Raptor', 'Gathan Raiders')):
            with self.subTest(name=name):
                session = self.session(70237053 + index)
                source = self.add(session.engine, name, zone='hand', ref='nonmana-discard-source')
                zombie = self.add(session.engine, 'Walking Corpse', zone='hand', ref='nonmana-zombie-payment')
                wrong = self.add(session.engine, 'Storm Crow', zone='hand', ref='nonmana-wrong-subtype')
                foreign = self.add(session.engine, 'Walking Corpse', zone='hand', seat='B', ref='nonmana-foreign-card')
                action = self.cast_face_down(session, source)
                schema = next(row for row in self.offers(session) if row['id']==action)['choice_schema']['discard_cards']
                self.assertIn(zombie.ref, schema['items']['enum'])
                self.assertNotIn(foreign.ref, schema['items']['enum'])
                if name == 'Putrid Raptor':
                    self.assertNotIn(wrong.ref, schema['items']['enum'])
                    self.reject(session, action, discard_cards=[wrong.ref])
                for refs in ([], [zombie.ref, zombie.ref], [foreign.ref], ['missing']):
                    self.reject(session, action, discard_cards=refs)
                result = session.act('pilot:A', {'action_id':action, 'discard_cards':[zombie.ref]})
                self.assertTrue(result.ok, result.summary)
                self.assertEqual('graveyard', session.state.cards[zombie.object_id].zone)
                self.assertFalse(session.state.cards[source.object_id].face_down)
                self.assertFalse(session.state.stack)
                self.assertEqual('A', session.state.priority_player)
                self.replay(session, load=True)

    def test_actual_bird_return_uses_controller_and_returns_to_physical_owner(self):
        session = self.session(70237055)
        source = self.add(session.engine, 'Raven Guild Initiate', zone='hand', ref='nonmana-bird-source')
        bird = self.add(session.engine, 'Storm Crow', seat='B', ref='nonmana-borrowed-bird')
        bird.owner = 'C'
        session.engine.state.players['B'].zones['battlefield'].remove(bird.object_id)
        session.engine.state.players['A'].zones['battlefield'].append(bird.object_id)
        bird.controller = 'A'
        wrong = self.add(session.engine, 'Walking Corpse', ref='nonmana-not-bird')
        action = self.cast_face_down(session, source)
        self.reject(session, action, return_cards=[wrong.ref])
        incarnation = session.state.cards[source.object_id].logical_object_id
        result = session.act('pilot:A', {'action_id':action, 'return_cards':[bird.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIn(bird.object_id, session.state.players['C'].zones['hand'])
        self.assertNotIn(bird.object_id, session.state.players['A'].zones['hand'])
        current = session.state.cards[source.object_id]
        self.assertFalse(current.face_down)
        self.assertEqual(incarnation, current.logical_object_id)
        self.assertFalse(session.state.stack)
        self.replay(session, load=True)

    def test_actual_fathom_seer_returns_two_distinct_islands_then_draws_privately(self):
        session = self.session(70237056)
        source = self.add(session.engine, 'Fathom Seer', zone='hand', ref='nonmana-fathom')
        islands = [self.add(session.engine, 'Island', ref='nonmana-island-' + str(i)) for i in range(2)]
        foreign = self.add(session.engine, 'Island', seat='B', ref='nonmana-foreign-island')
        action = self.cast_face_down(session, source)
        for refs in ([islands[0].ref], [islands[0].ref]*2, [islands[0].ref, foreign.ref]):
            self.reject(session, action, return_cards=refs)
        hand_before = set(session.state.players['A'].zones['hand'])
        with mock.patch.object(session.engine, '_move_cards_simultaneously', wraps=session.engine._move_cards_simultaneously) as movement:
            result = session.act('pilot:A', {'action_id':action, 'return_cards':[c.ref for c in islands]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, movement.call_count)
        self.assertTrue(all(session.state.cards[c.object_id].zone=='hand' for c in islands))
        self.assertEqual(1, len(session.state.stack))
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual(len(hand_before)+4, len(session.state.players['A'].zones['hand']))
        drawn = set(session.state.players['A'].zones['hand']) - hand_before - {c.object_id for c in islands}
        self.assertEqual(2, len(drawn))
        for seat in 'BCD':
            packet = json.dumps(session.packet('pilot:'+seat, full=True))
            for oid in drawn:
                self.assertNotIn(session.state.cards[oid].ref, packet)
        self.replay(session)

    def test_actual_reveal_prices_publish_only_one_eligible_card_and_execute_face_up_triggers(self):
        cases = [('Watcher of the Roost','Elite Vanguard',None), ('Temur Charger','Llanowar Elves','creature'),
            ("Dragon's Eye Savants",'Merfolk of the Pearl Trident','B'), ('Ruthless Ripper','Walking Corpse','B'),
            ('Horde Ambusher','Raging Goblin','creature')]
        for index, (name, color_card, target) in enumerate(cases):
            with self.subTest(name=name):
                session = self.session(70237060 + index)
                source = self.add(session.engine, name, zone='hand', ref='nonmana-reveal-source')
                payment = self.add(session.engine, color_card, zone='hand', ref='nonmana-reveal-payment')
                private = self.add(session.engine, 'Generic Bound Growth', zone='hand', seat='B', ref='nonmana-opponent-private')
                other = self.add(session.engine, 'Generic Bound Plains', zone='hand', ref='nonmana-other-private')
                body = self.add(session.engine, 'Generic Bound Body', seat='B', ref='nonmana-faceup-target')
                action = self.cast_face_down(session, source)
                self.reject(session, action, reveal_cards=[other.ref])
                self.reject(session, action, reveal_cards=[private.ref])
                for seat in 'BCD':
                    packet = json.dumps(session.packet('pilot:'+seat, full=True))
                    self.assertNotIn(payment.ref, packet)
                    self.assertNotIn(other.ref, packet)
                result = session.act('pilot:A', {'action_id':action, 'reveal_cards':[payment.ref]})
                self.assertTrue(result.ok, result.summary)
                self.assertFalse(session.state.cards[source.object_id].face_down)
                self.assertEqual('hand', session.state.cards[payment.object_id].zone)
                for seat in 'BCD':
                    packet = json.dumps(session.packet('pilot:'+seat, full=True))
                    self.assertIn(payment.ref, packet)
                    self.assertNotIn(other.ref, packet)
                if target is not None:
                    self.assertEqual('semantic.target', session.state.pending_decision.kind)
                    session = self.replay(session, load=True)
                    result = session.act('pilot:A', {'action_id':'choose', 'targets':[body.ref if target=='creature' else target]})
                    self.assertTrue(result.ok, result.summary)
                self.assertEqual(1, len(session.state.stack))
                self.resolve(session)
                if name == 'Watcher of the Roost':
                    self.assertEqual(42, session.state.players['A'].life)
                elif name == 'Ruthless Ripper':
                    self.assertEqual(38, session.state.players['B'].life)
                    self.assertEqual(40, session.state.players['A'].life)
                elif name == 'Temur Charger':
                    self.assertIn('trample', session.engine._combat_keywords(session.state.cards[body.object_id]))
                elif name == "Dragon's Eye Savants":
                    self.assertIn(private.ref, json.dumps(session.packet('pilot:A', full=True)))
                    for seat in 'CD':
                        self.assertNotIn(private.ref, json.dumps(session.packet('pilot:'+seat, full=True)))
                self.replay(session, load=True)

    def test_life_shortfall_and_prospective_ability_removal_suppress_turn_up(self):
        session = self.session(70237070)
        source = self.add(session.engine, 'Zombie Cutthroat', zone='hand', ref='nonmana-unaffordable')
        session.state.players['A'].life = 4
        action = self.cast_face_down(session, source)
        self.assertNotIn(action, [row['id'] for row in self.offers(session)])
        self.reject(session, action)
        self.replay(session)
        session = self.session(70237071)
        source = self.add(session.engine, 'Zombie Cutthroat', zone='hand', ref='nonmana-humble')
        humble = self.add(session.engine, 'Humble', zone='hand', ref='nonmana-humble-spell')
        for i in range(2):
            self.add(session.engine, 'Generic Bound Plains', ref='nonmana-humble-payment-' + str(i))
        action = self.cast_face_down(session, source)
        self.cast(session, 'A', humble, [source.ref])
        self.resolve(session)
        self.assertNotIn(action, [row['id'] for row in self.offers(session)])
        self.reject(session, action)
        self.assertTrue(session.state.cards[source.object_id].face_down)
        self.replay(session)

    def test_native_discard_replacement_suspends_before_reveal_then_resumes_after_load(self):
        session = self.session(70237072)
        source = self.add(session.engine, 'Putrid Raptor', zone='hand', ref='nonmana-replaced-discard')
        payment = self.add(session.engine, 'Walking Corpse', zone='hand', ref='nonmana-replaced-zombie')
        self.add(session.engine, 'Generic Payment Destination Replacement', seat='B', ref='nonmana-exile-study')
        self.add(session.engine, 'Generic Payment Competing Replacement', seat='C', ref='nonmana-exile-review')
        action = self.cast_face_down(session, source)
        result = session.act('pilot:A', {'action_id':action, 'discard_cards':[payment.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('replacement.order', session.state.pending_decision.kind)
        self.assertTrue(session.state.cards[source.object_id].face_down)
        self.assertEqual('hand', session.state.cards[payment.object_id].zone)
        for seat in 'BCD':
            self.assertNotIn(payment.ref, json.dumps(session.packet('pilot:'+seat, full=True)))
        session = self.replay(session, load=True)
        options = session.packet('pilot:A', full=True)['decision']['ctx']['options']
        self.reject(session, 'choose', 'B', replacement=options[0]['id'])
        result = session.act('pilot:A', {'action_id':'choose', 'replacement':options[0]['id']})
        self.assertTrue(result.ok, result.summary)
        current = session.state.cards[payment.object_id]
        self.assertEqual('exile', current.zone)
        self.assertEqual(1, sum(current.counters.values()))
        self.assertFalse(session.state.cards[source.object_id].face_down)
        self.assertFalse(session.state.stack)
        self.assertEqual('A', session.state.priority_player)
        self.replay(session, load=True)

    def test_current_type_can_return_source_without_a_spurious_turn_face_up_occurrence(self):
        from quorune.continuous_effect_model import Layer
        from quorune.continuous_effect_state import create_resolution_continuous_effect, ResolutionEffectSource
        from quorune.continuous_effects import ContinuousOperation
        session = self.session(70237073)
        source = self.add(session.engine, 'Raven Guild Initiate', zone='hand', ref='nonmana-self-return')
        action = self.cast_face_down(session, source)
        self.assertNotIn(action, [r['id'] for r in self.offers(session)])
        current = session.state.cards[source.object_id]
        old_incarnation = current.logical_object_id
        create_resolution_continuous_effect(session.engine,
            source=ResolutionEffectSource(stack_ref='fixture:nonmana-current-type'), targets=(current,),
            layer=Layer.TYPE, sublayer='4', operations=(ContinuousOperation('add_types', ['Bird'], field='subtypes'),))
        self.prepare(session)
        self.checkpoint(session)
        self.assertIn(action, [r['id'] for r in self.offers(session)])
        result = session.act('pilot:A', {'action_id':action, 'return_cards':[current.ref]})
        self.assertTrue(result.ok, result.summary)
        current = session.state.cards[source.object_id]
        self.assertEqual('hand', current.zone)
        self.assertFalse(current.face_down)
        self.assertNotEqual(old_incarnation, current.logical_object_id)
        self.assertFalse(any(event.code=='permanent.turn_face_up' for event in session.state.events))
        self.assertFalse(session.state.stack)
        self.assertEqual('A', session.state.priority_player)
        self.replay(session, load=True)

    def test_stale_selected_return_resource_and_internal_journal_attempts_roll_back(self):
        from quorune.rules.morph_actions import commit_turn_face_up
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        session = self.session(70237074)
        source = self.add(session.engine, 'Raven Guild Initiate', zone='hand', ref='nonmana-stale-return')
        bird = self.add(session.engine, 'Storm Crow', ref='nonmana-stale-bird')
        action = self.cast_face_down(session, source)
        offer = next(r for r in self.offers(session) if r['id']==action)
        self.reject(session, action, return_cards=[bird.ref], _mana_replacement_selections={'forged': ['x']})
        session.engine.move_card(bird.object_id, 'hand', log=False)
        session.engine.move_card(bird.object_id, 'battlefield', controller='B', log=False)
        before = authoritative_state_hash(session.state)
        with self.assertRaises(GameRuleError):
            commit_turn_face_up(session.engine, seat='A', response={**offer, 'return_cards':[bird.ref]})
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertTrue(session.state.cards[source.object_id].face_down)

    def test_native_island_mana_can_be_floated_before_paying_the_return_cost(self):
        session = self.session(70237075)
        source = self.add(session.engine, 'Fathom Seer', zone='hand', ref='nonmana-floated-seer')
        lands = [self.add(session.engine, 'Island', ref='nonmana-floated-island-' + str(i)) for i in range(2)]
        action = self.cast_face_down(session, source)
        for card in lands:
            mana = next(row for row in self.offers(session) if row.get('action')=='activate' and row.get('source')==card.ref)
            result = session.act('pilot:A', {'action_id':mana['id']})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual(2, session.state.players['A'].mana_pool['U'])
        result = session.act('pilot:A', {'action_id':action, 'return_cards':[card.ref for card in lands]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(2, session.state.players['A'].mana_pool['U'])
        self.assertTrue(all(session.state.cards[card.object_id].zone=='hand' for card in lands))
        self.resolve(session)
        self.replay(session, load=True)

    def test_native_horde_ambusher_face_up_trigger_prohibits_the_chosen_blocker(self):
        session = self.session(70237076)
        source = self.add(session.engine, 'Horde Ambusher', zone='hand', ref='nonmana-horde-blocking')
        payment = self.add(session.engine, 'Raging Goblin', zone='hand', ref='nonmana-horde-red')
        blocker = self.add(session.engine, 'Generic Bound Body', seat='B', ref='nonmana-horde-blocker')
        control = self.add(session.engine, 'Generic Bound Body', seat='B', ref='nonmana-horde-control')
        action = self.cast_face_down(session, source)
        self.assertTrue(session.engine._can_block(session.state.cards[source.object_id], blocker)[0])
        result = session.act('pilot:A', {'action_id':action, 'reveal_cards':[payment.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('semantic.target', session.state.pending_decision.kind)
        result = session.act('pilot:A', {'action_id':'choose', 'targets':[blocker.ref]})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)
        self.assertFalse(session.engine._can_block(session.state.cards[source.object_id], session.state.cards[blocker.object_id])[0])
        self.assertTrue(session.engine._can_block(session.state.cards[source.object_id], session.state.cards[control.object_id])[0])
        self.replay(session, load=True)
