from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from quorune.compiler.public_state_queries import fixed_public_state_condition
from quorune.compiler.public_state_queries import compose_public_conditions, _query_count_condition
from quorune.oracle_ir import compile_oracle_card
from quorune.continuous_conditions import FixedPublicStateConditionSpec, FixedPublicStateConditionKind
from quorune.engine import TURN_STEPS
from quorune.model import CombatState
from quorune.continuous_effect_model import Layer, ContinuousOperation
from quorune.continuous_effect_state import (
    ResolutionContinuousComponent, ResolutionEffectSource, create_resolution_continuous_effect_components,
)
from test_granted_skulk import record
from scripts.build_test_database import build_fixture_database
import test_spree_mode_costs as spree
import test_public_condition_surfaces as surfaces


class LandConditionQuantityCompilerTests(unittest.TestCase):
    def test_land_subtypes_snow_and_boolean_branches_use_existing_closed_values(self):
        for name in ('Island','Desert','Power-Plant',"Urza's"):
            value=fixed_public_state_condition('you control a '+name,source_name='Condition Observer')
            self.assertIsNotNone(value)
            self.assertEqual(('land',),value.quantity.query.types_all)
            self.assertEqual((name.casefold(),),value.quantity.query.subtypes_all)
        snow=fixed_public_state_condition('you control a snow land',source_name='Condition Observer')
        self.assertEqual(('snow',),snow.quantity.query.supertypes_all)
        value=fixed_public_state_condition('you control another Merfolk or an Island',source_name="Kumena's Speaker")
        self.assertEqual(FixedPublicStateConditionKind.ANY,value.kind)
        left,right=value.terms
        self.assertTrue(left.quantity.exclude_source)
        self.assertEqual(('merfolk',),left.quantity.query.subtypes_all)
        self.assertEqual((),left.quantity.query.types_all)
        self.assertFalse(right.quantity.exclude_source)
        self.assertEqual(('island',),right.quantity.query.subtypes_all)
        self.assertEqual(value.to_dict(),FixedPublicStateConditionSpec.from_dict(value.to_dict()).to_dict())

    def test_whole_source_span_nearest_unsupported_terms_and_producer_omissions(self):
        text='This creature gets +1/+1 as long as you control another Merfolk or an Island.'
        ir=compile_oracle_card(record(text,'Creature — Merfolk'),capability_registry=load_default_capability_registry())
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node,=ir.faces[0].nodes
        self.assertEqual(text,node.text)
        self.assertEqual((0,len(text)),(node.span.start,node.span.end))
        for bad in (text.replace('an Island','a stickered permanent'),
                    text.replace('an Island','a chosen card in your hand'),
                    text.replace('+1/+1','+X/+X')):
            self.assertNotEqual('exact',compile_oracle_card(record(bad,'Creature'),
                capability_registry=load_default_capability_registry()).status)
        with patch('quorune.compiler.public_condition_surfaces.fixed_condition_subject_extension',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(record(text,'Creature'),
                capability_registry=load_default_capability_registry()).status)

    def test_current_graveyard_card_queries_are_card_only_and_legacy_decoding_stays_exact(self):
        for text in ('there is a Desert card in your graveyard','a creature card is in your graveyard',
                     'there are seven or more cards in your graveyard','there are no cards in your graveyard'):
            value=fixed_public_state_condition(text,source_name='Card-only observer')
            self.assertIsNotNone(value)
            self.assertIs(value.quantity.query.token,False)
            self.assertEqual(2,value.quantity.schema_version)
        legacy=FixedPublicStateConditionSpec(
            FixedPublicStateConditionKind.CONTROLLER_GRAVEYARD_CARD_COUNT_AT_LEAST,amount=7)
        self.assertEqual(legacy.to_dict(),FixedPublicStateConditionSpec.from_dict(legacy.to_dict()).to_dict())


class LandConditionQuantityRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = spree.SpreeModeCostRuntimeTests.session
    add = spree.SpreeModeCostRuntimeTests.add
    prepare = spree.SpreeModeCostRuntimeTests.prepare
    checkpoint = spree.SpreeModeCostRuntimeTests.checkpoint
    resolve = spree.SpreeModeCostRuntimeTests.resolve
    replay = spree.SpreeModeCostRuntimeTests.replay
    assert_closed = spree.SpreeModeCostRuntimeTests.assert_closed
    reject = spree.SpreeModeCostRuntimeTests.reject
    cast = surfaces.PublicConditionSurfaceRuntimeTests.cast
    stats = surfaces.PublicConditionSurfaceRuntimeTests.stats
    produce_mana = surfaces.PublicConditionSurfaceRuntimeTests.produce_mana

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'land-condition-quantities.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/land-condition-quantity-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Land conditions', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def test_actual_camel_and_naga_graveyard_desert_grant_fixed_keywords_once(self):
        session=self.session(20531001)
        camel=self.add(session.engine,'Solitary Camel',ref='actual-desert-camel')
        naga=self.add(session.engine,'Sidewinder Naga',ref='actual-desert-naga')
        for source in (camel,naga):self.assert_closed(session,source)
        self.assertNotIn('lifelink',session.engine._combat_keywords(camel))
        self.assertNotIn('trample',session.engine._combat_keywords(naga))
        self.add(session.engine,'Sunscorched Desert',zone='graveyard',ref='own-desert-in-graveyard')
        self.assertIn('lifelink',session.engine._combat_keywords(camel))
        self.assertIn('trample',session.engine._combat_keywords(naga))
        self.assertEqual('4',session.engine._effective_card_data(naga)['power'])
        self.add(session.engine,'Sunscorched Desert',ref='own-desert-on-battlefield')
        self.assertEqual('4',session.engine._effective_card_data(naga)['power'])

    def test_desert_token_can_satisfy_battlefield_branch_but_is_not_a_graveyard_card(self):
        session=self.session(20531002)
        camel=self.add(session.engine,'Solitary Camel',ref='desert-card-only-observer')
        self.assert_closed(session,camel)
        ref,=session.engine.create_token('A',name='Desert token card-negative',characteristics={
            'type_line':'Token Land — Desert','colors':[],'keywords':[]})
        token=session.engine._resolve_object('A',ref,zones={'battlefield'})
        self.assertIn('lifelink',session.engine._combat_keywords(camel))
        session.engine.move_card(token.object_id,'graveyard',log=False)
        self.assertNotIn('lifelink',session.engine._combat_keywords(camel))

    def test_actual_desert_lifelink_damage_and_native_graveyard_exile_preserve_draw_privacy_and_replay(self):
        session=self.session(20531003)
        camel=self.add(session.engine,'Solitary Camel',ref='actual-lifelink-attacker')
        naga=self.add(session.engine,'Sidewinder Naga',ref='actual-trample-observer')
        desert=self.add(session.engine,'Sunscorched Desert',zone='graveyard',ref='actual-desert-to-exile')
        swamp=self.add(session.engine,'Swamp',seat='B',ref='cremate-native-mana')
        spell=self.add(session.engine,'Cremate',seat='B',zone='hand',ref='remove-desert-after-damage')
        for source in (camel,naga,spell):self.assert_closed(session,source)
        camel.acquired_control_turn_count=-1
        self.prepare(session);self.checkpoint(session)
        for _ in range(24):
            if session.state.pending_decision.kind=='combat.attackers':break
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        result=session.act('pilot:A',{'action_id':'attack','attackers':{camel.ref:'B'}})
        self.assertTrue(result.ok,result.summary)
        before=(session.state.players['A'].life,session.state.players['B'].life)
        for _ in range(32):
            if session.state.players['B'].life<before[1]:break
            decision=session.state.pending_decision
            if decision.kind=='combat.blockers':
                response={'action_id':'block','blocks':{}}
            elif decision.kind=='combat.damage':
                response={'action_id':'assign_damage','assignments':[{'source':camel.ref,'target':'B','amount':3}]}
            else:response={'action_id':'pass'}
            result=session.act(session.pending_principals()[0],response)
            self.assertTrue(result.ok,result.summary)
        self.assertEqual((before[0]+3,before[1]-3),(session.state.players['A'].life,session.state.players['B'].life))
        result=session.act('pilot:A',{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.produce_mana(session,swamp,actor='B')
        hand=set(session.state.players['B'].zones['hand'])
        self.reject(session,'cast:'+spell.ref,'C',targets=[desert.ref],pay='auto')
        self.cast(session,spell,(desert.ref,),actor='B')
        drawn,=set(session.state.players['B'].zones['hand'])-hand
        ref=session.state.cards[drawn].ref
        for actor in 'ACD':self.assertNotIn(ref,json.dumps(session.packet('pilot:'+actor,full=True)))
        self.assertNotIn('lifelink',session.engine._combat_keywords(session.state.cards[camel.object_id]))
        self.assertNotIn('trample',session.engine._combat_keywords(session.state.cards[naga.object_id]))
        self.assertEqual(('3','2'),self.stats(session,naga))
        self.assertEqual(before[0]+3,session.state.players['A'].life)
        self.replay(session,load=True)

    def test_actual_speaker_loses_island_bonus_and_dies_to_already_marked_native_damage(self):
        session=self.session(20531004)
        speaker=self.add(session.engine,"Kumena's Speaker",ref='actual-marked-speaker')
        self.assert_closed(session,speaker)
        self.add(session.engine,'Glacial Fortress',ref='blue-mana-is-not-island')
        self.add(session.engine,'Tundra',seat='C',ref='foreign-island-negative')
        self.assertEqual(('1','1'),self.stats(session,speaker))
        own=self.add(session.engine,'Tundra',ref='actual-own-island-dual')
        self.assertEqual(('2','2'),self.stats(session,speaker))
        pinger=self.add(session.engine,'Prodigal Sorcerer',seat='B',ref='actual-one-damage-source')
        pinger.acquired_control_turn_count=-1
        bounce=self.add(session.engine,'Boomerang',zone='hand',ref='return-only-own-island')
        self.prepare(session,actor='B',active='A')
        session.state.players['A'].mana_pool['U']=2
        self.checkpoint(session)
        action=next(row for row in session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
            if row.get('action')=='activate' and row.get('source')==pinger.ref)
        result=session.act('pilot:B',{'action_id':action['id'],'targets':[speaker.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual(1,session.state.cards[speaker.object_id].marked_damage)
        session=self.replay(session,load=True)
        self.cast(session,bounce,(own.ref,))
        self.assertEqual('graveyard',session.state.cards[speaker.object_id].zone)
        self.assertEqual('hand',session.state.cards[own.object_id].zone)
        self.replay(session,load=True)

    def test_speaker_counts_kindred_merfolk_and_self_as_island_in_distinct_branches(self):
        session=self.session(20531005)
        speaker=self.add(session.engine,"Kumena's Speaker",ref='branch-specific-other-source')
        self.add(session.engine,'Merrow Commerce',seat='B',ref='foreign-kindred-negative')
        self.assertEqual(('1','1'),self.stats(session,speaker))
        own=self.add(session.engine,'Merrow Commerce',ref='own-kindred-positive')
        self.assertEqual(('2','2'),self.stats(session,speaker))
        session.engine.move_card(own.object_id,'graveyard',log=False)
        self.assertEqual(('1','1'),self.stats(session,speaker))
        create_resolution_continuous_effect_components(session.engine,
            source=ResolutionEffectSource(stack_ref='S-self-island-boundary'),targets=(speaker,),components=(
                ResolutionContinuousComponent(Layer.TYPE,'4',(
                    ContinuousOperation('add_types',('land',),field='card_types'),
                    ContinuousOperation('add_types',('island',),field='subtypes'),)),))
        self.assertEqual(('2','2'),self.stats(session,speaker))

    def test_actual_snow_devil_reads_aura_controller_land_and_enchanted_blocker_first_strike(self):
        for snow_controller in ('A','B'):
            with self.subTest(snow_controller=snow_controller):
                session=self.session(20531006)
                body=self.add(session.engine,'Grizzly Bears',seat='B',ref='foreign-enchanted-blocker')
                attacker=self.add(session.engine,'Grizzly Bears',seat='C',ref='snow-combat-attacker')
                aura=self.add(session.engine,'Snow Devil',zone='hand',ref='actual-snow-aura')
                self.assert_closed(session,aura)
                self.add(session.engine,'Snow-Covered Forest',seat=snow_controller,ref='controller-snow-land')
                self.prepare(session,mana={'C':1,'U':1});self.checkpoint(session)
                self.cast(session,aura,(body.ref,))
                self.assertIn('flying',session.engine._combat_keywords(session.state.cards[body.object_id]))
                self.assertNotIn('first strike',session.engine._combat_keywords(session.state.cards[body.object_id]))
                self.replay(session,load=True)
                e=session.engine;e.permissions.invalidate_current();e.state.pending_decision=None
                e.state.priority_player=None;e.state.priority_passes=[];e.state.active_player='C'
                e.state.phase_index=TURN_STEPS.index(('combat','declare_blockers'))
                e.state.phase='combat';e.state.step='declare_blockers';attacker.attacking='B'
                e.state.combat=CombatState(attackers_declared=True,attackers={attacker.object_id:'B'},defending_players=['B'])
                e._begin_blocker_decisions();self.checkpoint(session)
                result=session.act('pilot:B',{'action_id':'block','blocks':{body.ref:attacker.ref}})
                self.assertTrue(result.ok,result.summary)
                self.assertEqual(snow_controller=='A','first strike' in e._combat_keywords(e.state.cards[body.object_id]))
                if snow_controller=='B':
                    self.replay(session,load=True)
                    continue
                for _ in range(24):
                    if e.state.cards[attacker.object_id].zone=='graveyard':break
                    if e.state.pending_decision.kind=='combat.damage':
                        response={'action_id':'assign_damage','assignments':[{'source':body.ref,'target':attacker.ref,'amount':2}]}
                    else:response={'action_id':'pass'}
                    result=session.act(session.pending_principals()[0],response)
                    self.assertTrue(result.ok,result.summary)
                self.assertEqual('graveyard',e.state.cards[attacker.object_id].zone)
                self.assertEqual('battlefield',e.state.cards[body.object_id].zone)
                self.assertEqual(0,e.state.cards[body.object_id].marked_damage)
                self.replay(session,load=True)

    def test_land_conditions_follow_current_controller_and_ignore_foreign_or_hand_deserts(self):
        session=self.session(20531007)
        camel=self.add(session.engine,'Solitary Camel',ref='controller-relative-camel')
        naga=self.add(session.engine,'Sidewinder Naga',ref='controller-relative-naga')
        self.add(session.engine,'Sunscorched Desert',seat='B',ref='other-controller-desert')
        foreign=self.add(session.engine,'Sunscorched Desert',seat='C',zone='graveyard',ref='other-graveyard-desert')
        self.add(session.engine,'Sunscorched Desert',zone='hand',ref='own-hand-desert-negative')
        self.assertNotIn('lifelink',session.engine._combat_keywords(camel))
        self.assertEqual(('3','2'),self.stats(session,naga))
        from quorune.control_effects import change_control
        change_control(session.engine,camel.object_id,'B',reason='Current Desert controller')
        change_control(session.engine,naga.object_id,'C',reason='Current graveyard Desert controller')
        self.assertIn('lifelink',session.engine._combat_keywords(camel))
        self.assertIn('trample',session.engine._combat_keywords(naga))
        self.assertEqual(('4','2'),self.stats(session,naga))
        session.engine.move_card(foreign.object_id,'hand',log=False)
        self.assertEqual(('3','2'),self.stats(session,naga))
        self.checkpoint(session);self.replay(session,load=True)

    def test_card_only_and_branch_specific_exclusion_mutants_are_killed_by_native_controls(self):
        def omit_card_only(**values):
            query=values['query']
            if query.zones==('graveyard',):values={**values,'query':replace(query,token=None)}
            return _query_count_condition(**values)

        with patch('quorune.compiler.public_state_queries._query_count_condition',side_effect=omit_card_only):
            with self.assertRaises(AssertionError):
                self.test_desert_token_can_satisfy_battlefield_branch_but_is_not_a_graveyard_card()

        def exclude_source_from_both(text,**values):
            condition=compose_public_conditions(text,**values)
            if condition is not None and condition.kind is FixedPublicStateConditionKind.ANY:
                condition=replace(condition,terms=tuple(replace(term,quantity=replace(term.quantity,exclude_source=True))
                    if term.quantity is not None else term for term in condition.terms))
            return condition

        with patch('quorune.compiler.public_state_queries.compose_public_conditions',side_effect=exclude_source_from_both):
            with self.assertRaises(AssertionError):
                self.test_speaker_counts_kindred_merfolk_and_self_as_island_in_distinct_branches()


if __name__=='__main__':
    unittest.main()
