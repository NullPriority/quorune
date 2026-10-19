from __future__ import annotations

from pathlib import Path
from copy import deepcopy
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.oracle_ir import compile_oracle_card
from quorune.object_query import exact_numeric_characteristic
from quorune.engine import TURN_STEPS
from quorune.rules.activation.query import activated_abilities
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.conditional_color_grants import ConditionalColorGrantHandler
from quorune.continuous_effect_model import Layer
from test_granted_skulk import record
from scripts.build_test_database import build_fixture_database
import test_spree_mode_costs as spree


TEXT = ('Threshold — As long as there are seven or more cards in your graveyard, '
    'this creature gets +1/+1, is black, and has "{2}{B}, {T}: Destroy target blue creature."')
CAPABILITY = 'continuous.ability.public_state_color_grant'


class ConditionalColorGrantCompilerTests(unittest.TestCase):
    def test_complete_source_span_and_independent_child_capabilities(self):
        ir = compile_oracle_card(record(TEXT, 'Creature — Human'),
            capability_registry=load_default_capability_registry(), capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        outer, child = ir.faces[0].nodes
        self.assertEqual(TEXT, outer.text)
        self.assertEqual((0, len(TEXT)), (outer.span.start, outer.span.end))
        self.assertEqual('granted_activated_ability', child.kind)
        self.assertIn(CAPABILITY, outer.capability_dependencies)
        self.assertTrue(set(child.capability_dependencies).issubset(outer.capability_dependencies))
        descriptor, = outer.handlers
        self.assertEqual(['B'], descriptor['set_colors'])
        self.assertEqual(1, descriptor['modifier']['power'])
        self.assertEqual(1, len(descriptor['modifier']['add_ability_fragments']))

    def test_unrepresented_compounds_and_missing_color_producer_remain_residual(self):
        for text in (TEXT.replace('is black', 'is black and red'),
                     TEXT.replace('is black', 'is black in addition to its other colors'),
                     TEXT.replace('+1/+1', '+X/+X'),
                     TEXT.replace('seven or more cards in your graveyard', 'this creature is blue'),
                     TEXT.replace('Destroy target blue creature.', 'Take an unrepresented extra phase.')):
            with self.subTest(text=text):
                self.assertNotEqual('exact', compile_oracle_card(record(text, 'Creature'),
                    capability_registry=load_default_capability_registry()).status)
        with patch('quorune.compiler.conditional_color_grants.conditional_color_grant_shell', return_value=None):
            self.assertNotEqual('exact', compile_oracle_card(record(TEXT, 'Creature'),
                capability_registry=load_default_capability_registry()).status)

    def test_descriptor_rejects_unknown_colors_versions_and_recipients(self):
        ir = compile_oracle_card(record(TEXT, 'Creature'), capability_registry=load_default_capability_registry())
        descriptor, = ir.faces[0].nodes[0].handlers
        handler = ConditionalColorGrantHandler()
        self.assertTrue(handler.may_affect_static_components(descriptor))
        for field, value in (('set_colors', []), ('set_colors', ['B','R']), ('set_colors', ['black']),
                             ('set_colors', 'B'), ('schema_version', True), ('unknown', 1)):
            bad = deepcopy(descriptor); bad[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(SemanticNodeError):
                handler.validate(bad)
        bad = deepcopy(descriptor); bad['target']['kind'] = 'attached'
        with self.assertRaises(SemanticNodeError): handler.validate(bad)

    def test_color_grant_and_child_capability_omissions_each_block_complete_admission(self):
        for missing in (CAPABILITY,'continuous.ability.fixed_public_state_grant','permanent.destroy.effect'):
            raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
            row=next(row for row in raw['capabilities'] if row['id']==missing)
            row.update(status='blocked',blockers=['Conditional color grant omission witness'])
            with self.subTest(missing=missing):
                self.assertNotEqual('exact',compile_oracle_card(record(TEXT,'Creature'),
                    capability_registry=CapabilityRegistry(raw)).status)


class ConditionalColorGrantRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = spree.SpreeModeCostRuntimeTests.session
    add = spree.SpreeModeCostRuntimeTests.add
    prepare = spree.SpreeModeCostRuntimeTests.prepare
    checkpoint = spree.SpreeModeCostRuntimeTests.checkpoint
    resolve = spree.SpreeModeCostRuntimeTests.resolve
    replay = spree.SpreeModeCostRuntimeTests.replay
    assert_closed = spree.SpreeModeCostRuntimeTests.assert_closed
    reject = spree.SpreeModeCostRuntimeTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'conditional-color-grants.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/conditional-color-grant-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Conditional color grants', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def graveyard(self, session, count, actor='A', prefix='threshold'):
        return [self.add(session.engine, 'Generic Bound Plains', seat=actor, zone='graveyard',
            ref=prefix + '-' + str(i)) for i in range(count)]

    def test_actual_possessed_aven_keeps_flying_and_replaces_color_at_seven(self):
        session = self.session(61351001)
        source = self.add(session.engine, 'Possessed Aven', ref='actual-threshold-aven')
        self.assert_closed(session, source)
        self.graveyard(session, 6)
        low = session.engine._effective_card_data(source)
        self.assertEqual(['U'], low['colors'])
        self.assertEqual(('3', '3'), (low['power'], low['toughness']))
        self.graveyard(session, 1, prefix='seventh')
        high = session.engine._effective_card_data(source)
        self.assertEqual(['B'], high['colors'])
        self.assertEqual(('4', '4'), (high['power'], high['toughness']))
        self.assertIn('Flying', high['keywords'])

    def test_all_actual_color_qualified_activations_use_current_legal_targets(self):
        for name, target_name, expected in (
            ('Possessed Aven','Wandering Ones','B'),
            ('Possessed Centaur','Grizzly Bears','B'),
            ('Possessed Nomad','Elite Vanguard','B'),
            ('Possessed Barbarian','Goblin Piker','B'),
            ('Repentant Vampire','Scathe Zombies','W')):
            with self.subTest(name=name):
                session = self.session(61351002)
                source = self.add(session.engine,name,ref='qualified-source')
                self.assert_closed(session,source)
                target = self.add(session.engine,target_name,seat='B',ref='qualified-target')
                wrong = self.add(session.engine,'Scathe Zombies' if target_name!='Scathe Zombies'
                    else 'Wandering Ones',seat='C',ref='wrong-color')
                source.acquired_control_turn_count = -1
                self.graveyard(session,6)
                self.assertFalse(activated_abilities(session.engine,source))
                self.graveyard(session,1,prefix='seventh')
                self.assertEqual([expected],session.engine._effective_card_data(source)['colors'])
                self.prepare(session,mana={'C':2,'B':1})
                action = next(row for row in session.engine._priority_action_hints('A')['actions']
                    if row['id'].startswith('activate:'+source.ref+':'))
                self.assertIn(target.ref,action['target_schema']['legal_refs'])
                self.assertNotIn(wrong.ref,action['target_schema']['legal_refs'])
                self.checkpoint(session)
                self.reject(session,action['id'],'B',targets=[target.ref],pay='auto')
                self.reject(session,action['id'],targets=[wrong.ref],pay='auto')
                result = session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'})
                self.assertTrue(result.ok,result.summary)
                self.assertIsNone(self.resolve(session))
                self.assertEqual('graveyard',session.state.cards[target.object_id].zone)
                self.assertTrue(session.state.cards[source.object_id].tapped)
                self.replay(session,load=True)

    def test_actual_cremate_ends_threshold_after_activation_without_removing_the_stack_ability(self):
        session = self.session(61351003)
        source = self.add(session.engine,'Possessed Aven',ref='live-threshold-source')
        target = self.add(session.engine,'Wandering Ones',seat='B',ref='independent-stack-target')
        cards = self.graveyard(session,7)
        spell = self.add(session.engine,'Cremate',seat='B',zone='hand',ref='actual-threshold-cremate')
        private = self.add(session.engine,'Generic Bound Body',seat='C',zone='hand',ref='foreign-private-hand')
        for card in (source,spell):self.assert_closed(session,card)
        source.acquired_control_turn_count = -1
        self.prepare(session,mana={'C':2,'B':1})
        session.state.players['B'].mana_pool['B']=1
        action = next(row for row in session.engine._priority_action_hints('A')['actions']
            if row['id'].startswith('activate:'+source.ref+':'))
        self.checkpoint(session)
        result = session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        session = self.replay(session,load=True)
        before_hand = set(session.state.players['B'].zones['hand'])
        self.reject(session,'cast:'+spell.ref,'D',targets=[cards[0].ref],pay='auto')
        result = session.act('pilot:A',{'action_id':'pass'})
        self.assertTrue(result.ok,result.summary)
        result = session.act('pilot:B',{'action_id':'cast:'+spell.ref,'targets':[cards[0].ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session))
        after_hand = set(session.state.players['B'].zones['hand'])
        self.assertEqual(len(before_hand),len(after_hand))
        drawn, = after_hand - before_hand
        drawn_ref = session.state.cards[drawn].ref
        self.assertIn(drawn_ref,json.dumps(session.packet('pilot:B',full=True)))
        for actor in 'ACD':self.assertNotIn(drawn_ref,json.dumps(session.packet('pilot:'+actor,full=True)))
        self.assertEqual('exile',session.state.cards[cards[0].object_id].zone)
        self.assertEqual('graveyard',session.state.cards[target.object_id].zone)
        # B's spell goes to B's graveyard, leaving A below threshold.
        current=session.state.cards[source.object_id]
        self.assertEqual(['U'],session.engine._effective_card_data(current)['colors'])
        self.assertFalse(activated_abilities(session.engine,current))
        for actor in 'ABD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+actor,full=True)))
        self.replay(session,load=True)

    def test_started_layer_five_effect_survives_humility_and_layer_six_uses_timestamps(self):
        for older_humility in (True,False):
            with self.subTest(older_humility=older_humility):
                session = self.session(61351004)
                if older_humility:self.add(session.engine,'Humility',ref='ability-removal')
                source = self.add(session.engine,'Possessed Aven',ref='layer-five-start')
                self.graveyard(session,7)
                if not older_humility:self.add(session.engine,'Humility',ref='ability-removal')
                source.counters['+1/+1']=2
                data = session.engine._effective_card_data(source)
                self.assertEqual(['B'],data['colors'])
                self.assertEqual(('2','2'),(data['power'],data['toughness']))
                self.assertEqual((4,4),tuple(exact_numeric_characteristic(source,data,stat)
                    for stat in ('power','toughness')))
                self.assertEqual(older_humility,bool(activated_abilities(session.engine,source)))
                self.assertNotIn('flying',session.engine._combat_keywords(source))

    def test_current_control_uses_new_controllers_graveyard_and_removal_restores_printed_values(self):
        session = self.session(61351005)
        source = self.add(session.engine,'Possessed Centaur',ref='controller-relative-threshold')
        self.graveyard(session,7)
        b_cards = self.graveyard(session,6,actor='B',prefix='b-graveyard')
        self.assertEqual(['B'],session.engine._effective_card_data(source)['colors'])
        from quorune.control_effects import change_control
        change_control(session.engine,source.object_id,'B',reason='Conditional color controller')
        self.assertEqual(['G'],session.engine._effective_card_data(source)['colors'])
        self.graveyard(session,1,actor='B',prefix='b-seventh')
        self.assertEqual(['B'],session.engine._effective_card_data(source)['colors'])
        session.engine.move_card(b_cards[0].object_id,'hand',log=False)
        data=session.engine._effective_card_data(source)
        self.assertEqual(['G'],data['colors'])
        self.assertEqual(('3','3'),(data['power'],data['toughness']))
        self.assertFalse(activated_abilities(session.engine,source))

    def test_actual_wayward_angel_upkeep_trigger_retains_controller_after_threshold_ends(self):
        session = self.session(61351006)
        source = self.add(session.engine,'Wayward Angel',ref='actual-threshold-upkeep')
        self.assert_closed(session,source)
        cards=self.graveyard(session,7)
        other=self.add(session.engine,'Generic Bound Body',ref='sacrifice-other-body')
        data=session.engine._effective_card_data(source)
        self.assertEqual(['B'],data['colors'])
        self.assertEqual(('7','7'),(data['power'],data['toughness']))
        self.assertIn('trample',session.engine._combat_keywords(source))
        e=session.engine;e.permissions.invalidate_current();e.state.pending_decision=None
        e.state.started=True;e.state.active_player='A'
        e.state.phase_index=TURN_STEPS.index(('beginning','upkeep'));e._enter_step();e.pump()
        self.assertEqual(1,len(session.state.stack))
        e.move_card(cards[0].object_id,'hand',log=False)
        self.assertNotIn('trample',e._combat_keywords(source))
        self.checkpoint(session)
        decision=self.resolve(session)
        self.assertIsNotNone(decision)
        self.assertEqual(['A'],decision.actors)
        self.reject(session,'choose','C',cards=[other.ref])
        result=session.act('pilot:A',{'action_id':'choose','cards':[other.ref]})
        self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[other.object_id].zone)
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
        self.replay(session,load=True)

    def test_actual_copy_inherits_the_conditional_component_after_original_departure(self):
        session=self.session(61351007)
        source=self.add(session.engine,'Possessed Aven',ref='copied-conditional-source')
        self.graveyard(session,7)
        spell=self.add(session.engine,'Generic Entry One Copy',zone='hand',ref='copy-conditional-spell')
        bounce=self.add(session.engine,'Unsummon',zone='hand',ref='return-conditional-original')
        for card in (source,spell,bounce):self.assert_closed(session,card)
        self.prepare(session,mana={'W':1,'U':1});self.checkpoint(session)
        result=session.act('pilot:A',{'action_id':'cast:'+spell.ref,'targets':[source.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        copied,=[card for card in session.state.cards.values() if card.zone=='battlefield' and card.is_token]
        result=session.act('pilot:A',{'action_id':'cast:'+bounce.ref,'targets':[source.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[source.object_id].zone)
        data=session.engine._effective_card_data(copied)
        self.assertEqual(['B'],data['colors'])
        self.assertEqual(('4','4'),(data['power'],data['toughness']))
        self.assertEqual(1,len(activated_abilities(session.engine,copied)))
        self.assertIn('flying',session.engine._combat_keywords(copied))
        self.replay(session,load=True)

    def test_runtime_color_and_early_source_participation_omissions_are_killed(self):
        from quorune.semantic_runtime.continuous_components import ContinuousEffectComponentRegistry
        original=ContinuousEffectComponentRegistry.source_component_layer

        def witness():
            session=self.session(61351008)
            source=self.add(session.engine,'Possessed Aven',ref='mutation-color-source')
            self.graveyard(session,7)
            self.add(session.engine,'Humility',ref='mutation-remove-abilities')
            self.assertEqual(['B'],session.engine._effective_card_data(source)['colors'])
            self.assertEqual('2',session.engine._effective_card_data(source)['power'])

        witness()
        with patch.object(ConditionalColorGrantHandler,'lower',return_value=()):
            with self.assertRaises(AssertionError):witness()

        def skip_early(registry,descriptor):
            if descriptor.get('handler_id')==ConditionalColorGrantHandler().handler_id:return Layer.ABILITY
            return original(registry,descriptor)

        with patch.object(ContinuousEffectComponentRegistry,'source_component_layer',new=skip_early):
            with self.assertRaises(AssertionError):witness()

    def test_losing_threshold_revalidates_an_already_offered_activation_before_payment(self):
        session=self.session(61351009)
        source=self.add(session.engine,'Possessed Aven',ref='offered-live-grant')
        source.acquired_control_turn_count=-1
        target=self.add(session.engine,'Wandering Ones',seat='B',ref='offered-live-target')
        cards=self.graveyard(session,7)
        self.prepare(session,mana={'C':2,'B':1})
        action=next(row for row in session.engine._priority_action_hints('A')['actions']
            if row['id'].startswith('activate:'+source.ref+':'))
        session.engine.move_card(cards[0].object_id,'hand',log=False)
        self.reject(session,action['id'],targets=[target.ref],pay='auto')
        self.assertFalse(session.state.cards[source.object_id].tapped)
        self.assertFalse(any(row['id'].startswith('activate:'+source.ref+':')
            for row in session.engine._priority_action_hints('A')['actions']))


if __name__ == '__main__':
    unittest.main()
