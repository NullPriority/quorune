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
from quorune.engine import TURN_STEPS
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.oracle_ir import compile_oracle_card
from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from quorune.continuous_effect_model import Layer, ContinuousOperation, ContinuousEffectDuration
from quorune.continuous_effect_state import (
    ResolutionContinuousComponent, ResolutionEffectSource, create_resolution_continuous_effect_components,
)
from quorune.compiler.untap_step_templates import static_untap_step_handler
from quorune.semantic_runtime.untap_steps import StaticUntapStepParticipationHandler, current_untap_step_plan
from quorune.semantic_runtime.context import SemanticNodeError
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_spree_mode_costs as spree
import test_public_condition_surfaces as surfaces


CAP='untap.step.static_participation'
TEXT="Nonbasic lands don't untap during their controllers' untap steps."


class FixedUntapParticipationCompilerTests(unittest.TestCase):
    def test_fixed_subjects_preserve_complete_spans_typed_queries_and_current_ability_presence(self):
        cases=(TEXT,"Legendary creatures don't untap during their controllers' untap steps.",
            "Mercenaries don't untap during their controllers' untap steps.",
            "Creatures with power 3 or greater don't untap during their controllers' untap steps.",
            "Creatures with power 1 or less don't untap during their controllers' untap steps.",
            "Creatures with ice counters on them don't untap during their controllers' untap steps.",
            "Untap all green and/or blue creatures you control during each other player's untap step.",
            "Untap this creature during each other player's untap step.",
            "Enchanted artifact doesn't untap during its controller's untap step.")
        for text in cases:
            with self.subTest(text=text):
                ir=compile_oracle_card(record(text,'Creature'),capability_registry=load_default_capability_registry())
                self.assertEqual('exact',ir.status,ir.material_residuals)
                node,=ir.faces[0].nodes
                self.assertEqual((text,0,len(text)),(node.text,node.span.start,node.span.end))
                self.assertIn(CAP,node.capability_dependencies)
                self.assertIn(CURRENT_ABILITY_FRAGMENT_COVERAGE,node.runtime_coverage)
        descriptor=static_untap_step_handler(cases[2],source_name='Fixture')[1]
        predicate=StaticUntapStepParticipationHandler().validate(descriptor).predicate
        self.assertEqual(('mercenary',),predicate.subtypes_all)
        self.assertEqual((),predicate.types_all)

    def test_unrepresented_qualifiers_malformed_descriptors_and_producer_omission_fail_closed(self):
        for text in (TEXT.replace('Nonbasic lands','Lands with an activated ability'),
                TEXT.replace('Nonbasic lands','Nonwhite creatures with power 3 or greater'),
                TEXT.replace('Nonbasic lands','Creatures with power X or greater'),
                TEXT.replace('Nonbasic lands','Creatures of the chosen type'),
                "Untap this creature during each other player's untap step if you control a Desert."):
            self.assertNotEqual('exact',compile_oracle_card(record(text,'Creature'),
                capability_registry=load_default_capability_registry()).status)
        descriptor=static_untap_step_handler(TEXT,source_name='Fixture')[1]
        for key,value in (('unknown',True),('event','damage'),('schema_version',2)):
            with self.subTest(key=key),self.assertRaises(SemanticNodeError):
                StaticUntapStepParticipationHandler().validate({**descriptor,key:value})
        with patch('quorune.compiler.untap_step_templates._fixed_untap_query_subject',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(record(TEXT,'Enchantment'),
                capability_registry=load_default_capability_registry()).status)

    def test_each_declared_untap_dependency_blocks_complete_admission(self):
        for missing in (CAP,'permanent.untap.effect','state_query.permanent.public_state_predicate'):
            raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
            next(r for r in raw['capabilities'] if r['id']==missing).update(status='blocked',blockers=['Untap dependency witness'])
            with self.subTest(missing=missing):
                self.assertNotEqual('exact',compile_oracle_card(record(TEXT,'Enchantment'),
                    capability_registry=CapabilityRegistry(raw)).status)


class FixedUntapParticipationRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=spree.SpreeModeCostRuntimeTests.session
    add=spree.SpreeModeCostRuntimeTests.add
    checkpoint=spree.SpreeModeCostRuntimeTests.checkpoint
    replay=spree.SpreeModeCostRuntimeTests.replay
    assert_closed=spree.SpreeModeCostRuntimeTests.assert_closed
    reject=spree.SpreeModeCostRuntimeTests.reject
    resolve=spree.SpreeModeCostRuntimeTests.resolve
    prepare=spree.SpreeModeCostRuntimeTests.prepare
    cast=surfaces.PublicConditionSurfaceRuntimeTests.cast

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        path=Path(cls.temp.name)/'fixed-untap-participation.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/fixed-untap-participation-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Fixed untap participation',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close();cls.temp.cleanup()

    def end_step(self,session,active='A'):
        e=session.engine
        e.state.phase_index=TURN_STEPS.index(('ending','end_step'))
        e.state.phase='ending';e.state.step='end_step';e.state.active_player=active
        e.state.last_normal_turn_player=active
        if e.state.current_turn is not None:e.state.current_turn=replace(e.state.current_turn,player=active)
        e.state.priority_passes=[];e.permissions.invalidate_current();e.state.pending_decision=None
        e._grant_priority(active);e.pump()

    def next_turn(self,session,active='A'):
        seats='ABCD';start=seats.index(active)
        for seat in seats[start:]+seats[:start]:
            result=session.act('pilot:'+seat,{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)

    def effect(self,session,card,layer,sublayer,*operations):
        create_resolution_continuous_effect_components(session.engine,
            source=ResolutionEffectSource(stack_ref='S-untap-current-'+card.ref),targets=(card,),components=(
                ResolutionContinuousComponent(layer,sublayer,operations),),duration=ContinuousEffectDuration.ZONE_OBJECT)

    def test_actual_back_to_basics_preserves_basic_mana_and_blocks_nonbasic_untap(self):
        session=self.session(50232001)
        source=self.add(session.engine,'Back to Basics',ref='actual-back-to-basics')
        self.assert_closed(session,source)
        island=self.add(session.engine,'Island',seat='B',ref='basic-untaps')
        desert=self.add(session.engine,'Sunscorched Desert',seat='B',ref='nonbasic-stays-tapped')
        foreign=self.add(session.engine,'Island',seat='C',ref='foreign-stays-tapped')
        hidden=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand',ref='untap-private-hand')
        self.effect(session,desert,Layer.TYPE,'4',ContinuousOperation('add_types',('island',),field='subtypes'))
        for card in (island,desert,foreign):card.tapped=True
        self.end_step(session);self.checkpoint(session)
        self.reject(session,'pass','D')
        self.next_turn(session)
        self.assertEqual(('B','upkeep'),(session.state.active_player,session.state.step))
        self.assertFalse(session.state.cards[island.object_id].tapped)
        self.assertTrue(session.state.cards[desert.object_id].tapped)
        self.assertTrue(session.state.cards[foreign.object_id].tapped)
        for seat in 'ABD':self.assertNotIn(hidden.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_humility_removes_a_creatures_additional_untap_before_next_step(self):
        session=self.session(50232002)
        source=self.add(session.engine,'Thousand Moons Infantry',ref='ability-removed-extra-untap')
        self.assert_closed(session,source)
        self.add(session.engine,'Humility',ref='humility-removes-untap-ability')
        source.tapped=True
        self.end_step(session);self.checkpoint(session)
        self.next_turn(session)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assertEqual('upkeep',session.state.step)
        self.replay(session,load=True)

    def test_all_twelve_original_cards_have_complete_trusted_bindings(self):
        session=self.session(50232003)
        for name in ('Back to Basics','Arena of the Ancients','Root Cage','Inertia Bubble',"Bender's Waterskin",
                'Endbringer','Thousand Moons Infantry','Murkfiend Liege','Rimescale Dragon','Meekstone','Marble Titan','Juntu Stakes'):
            with self.subTest(name=name):self.assert_closed(session,self.add(session.engine,name))

    def test_all_three_self_untappers_use_other_turns_and_current_controller(self):
        from quorune.control_effects import change_control
        for name in ("Bender's Waterskin",'Endbringer','Thousand Moons Infantry'):
            with self.subTest(name=name):
                session=self.session(50232004)
                source=self.add(session.engine,name,ref='original-self-extra-untap')
                other=self.add(session.engine,'Grizzly Bears',ref='no-extra-untap')
                change_control(session.engine,source.object_id,'C',reason='Current additional untap controller')
                source.tapped=other.tapped=True
                self.end_step(session);self.checkpoint(session)
                self.next_turn(session)
                self.assertFalse(session.state.cards[source.object_id].tapped)
                self.assertTrue(session.state.cards[other.object_id].tapped)
                self.assertEqual(('A','C'),(session.state.cards[source.object_id].owner,session.state.cards[source.object_id].controller))
                self.replay(session,load=True)

    def test_actual_inertia_bubble_does_not_block_other_turn_mana_untap(self):
        session=self.session(50232005)
        source=self.add(session.engine,"Bender's Waterskin",seat='B',ref='inertia-extra-mana')
        aura=self.add(session.engine,'Inertia Bubble',zone='hand',ref='foreign-untap-aura')
        spell=self.add(session.engine,'Unsummon',seat='B',zone='hand',ref='newly-untapped-mana-spell')
        body=self.add(session.engine,'Grizzly Bears',ref='mana-result-target')
        self.prepare(session,mana={'U':10,'C':10});self.checkpoint(session)
        self.cast(session,aura,(source.ref,))
        self.assertEqual(source.object_id,session.state.cards[aura.object_id].attached_to)
        self.replay(session,load=True)
        source.tapped=True
        self.assertIn(source.object_id,current_untap_step_plan(session.engine,'B').prohibited_object_ids)
        self.assertNotIn(source.object_id,current_untap_step_plan(session.engine,'A').prohibited_object_ids)
        self.end_step(session,active='D');self.checkpoint(session)
        self.next_turn(session,active='D')
        self.assertEqual(('A','upkeep'),(session.state.active_player,session.state.step))
        self.assertFalse(session.state.cards[source.object_id].tapped)
        result=session.act('pilot:A',{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.cast(session,spell,(body.ref,),actor='B')
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assertEqual('hand',session.state.cards[body.object_id].zone)
        self.replay(session,load=True)

    def test_numeric_originals_use_current_power_counters_and_inclusive_thresholds(self):
        for name in ('Meekstone','Marble Titan','Juntu Stakes'):
            with self.subTest(name=name):
                session=self.session(50232006)
                source=self.add(session.engine,name);self.assert_closed(session,source)
                blocked=self.add(session.engine,'Wandering Ones',seat='B',ref='power-boundary-blocked')
                allowed=self.add(session.engine,'Grizzly Bears',seat='B',ref='power-boundary-allowed')
                artifact=self.add(session.engine,"Bender's Waterskin",seat='B',ref='noncreature-power-negative')
                if name!='Juntu Stakes':blocked.counters['+1/+1']=2
                for card in (blocked,allowed,artifact):card.tapped=True
                self.end_step(session);self.checkpoint(session)
                self.next_turn(session)
                self.assertTrue(session.state.cards[blocked.object_id].tapped)
                self.assertFalse(session.state.cards[allowed.object_id].tapped)
                self.assertFalse(session.state.cards[artifact.object_id].tapped)
                self.replay(session,load=True)

    def test_arena_and_root_cage_preserve_supertype_and_noncreature_subtype_subjects(self):
        for name in ('Arena of the Ancients','Root Cage'):
            with self.subTest(name=name):
                session=self.session(50232007)
                source=self.add(session.engine,name);self.assert_closed(session,source)
                blocked=self.add(session.engine,'Grizzly Bears',seat='B',ref='typed-untap-blocked')
                allowed=self.add(session.engine,'Wandering Ones',seat='B',ref='typed-untap-allowed')
                artifact=self.add(session.engine,"Bender's Waterskin",seat='B',ref='typed-artifact-control')
                self.effect(session,blocked,Layer.TYPE,'4',ContinuousOperation('add_types',
                    ('legendary',) if name=='Arena of the Ancients' else ('mercenary',),
                    field='supertypes' if name=='Arena of the Ancients' else 'subtypes'))
                self.effect(session,artifact,Layer.TYPE,'4',ContinuousOperation('add_types',
                    ('legendary',) if name=='Arena of the Ancients' else ('mercenary',),
                    field='supertypes' if name=='Arena of the Ancients' else 'subtypes'))
                if name=='Root Cage':
                    self.effect(session,artifact,Layer.TYPE,'4',ContinuousOperation('add_types',('kindred',),field='card_types'))
                for card in (blocked,allowed,artifact):card.tapped=True
                self.end_step(session);self.checkpoint(session)
                self.next_turn(session)
                self.assertTrue(session.state.cards[blocked.object_id].tapped)
                self.assertFalse(session.state.cards[allowed.object_id].tapped)
                self.assertEqual(name=='Root Cage',session.state.cards[artifact.object_id].tapped)
                self.replay(session,load=True)

    def test_actual_rimescale_activation_adds_ice_and_blocks_only_current_creatures(self):
        session=self.session(50232008)
        source=self.add(session.engine,'Rimescale Dragon',ref='actual-ice-source');self.assert_closed(session,source)
        self.add(session.engine,'Snow-Covered Island',ref='actual-snow-mana')
        body=self.add(session.engine,'Grizzly Bears',seat='B',ref='actual-ice-target')
        artifact=self.add(session.engine,'Icy Manipulator',seat='B',ref='ice-artifact-negative')
        artifact.counters['ice']=1;artifact.tapped=True
        self.prepare(session,mana={'C':2});self.checkpoint(session)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        ability=next(row for row in actions if row.get('action')=='activate' and row.get('source')==source.ref)
        result=session.act('pilot:A',{'action_id':ability['id'],'targets':[body.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual(1,session.state.cards[body.object_id].counters['ice'])
        self.assertTrue(session.state.cards[body.object_id].tapped)
        self.replay(session,load=True)
        self.end_step(session);self.checkpoint(session);self.next_turn(session)
        self.assertTrue(session.state.cards[body.object_id].tapped)
        self.assertEqual(1,session.state.cards[body.object_id].counters['ice'])
        self.assertFalse(session.state.cards[artifact.object_id].tapped)
        self.replay(session,load=True)

    def test_murkfiend_color_union_is_current_mandatory_and_duplicate_stun_attempts_are_not_added(self):
        session=self.session(50232009)
        sources=[self.add(session.engine,'Murkfiend Liege',ref='color-untapper-'+str(i)) for i in range(2)]
        for source in sources:self.assert_closed(session,source)
        green=self.add(session.engine,'Grizzly Bears',ref='green-stun-untap')
        blue=self.add(session.engine,'Wandering Ones',ref='blue-extra-untap')
        white=self.add(session.engine,'Generic Bound Body',ref='white-extra-negative')
        foreign=self.add(session.engine,'Wandering Ones',seat='C',ref='foreign-blue-negative')
        active=self.add(session.engine,'Generic Bound Body',seat='B',ref='active-white-untap')
        green.counters['stun']=2
        for card in (*sources,green,blue,white,foreign,active):card.tapped=True
        self.end_step(session);self.checkpoint(session);self.next_turn(session)
        self.assertTrue(session.state.cards[green.object_id].tapped)
        self.assertEqual(1,session.state.cards[green.object_id].counters['stun'])
        for card in (*sources,blue,active):self.assertFalse(session.state.cards[card.object_id].tapped)
        for card in (white,foreign):self.assertTrue(session.state.cards[card.object_id].tapped)
        self.replay(session,load=True)

    def test_copied_additional_untap_survives_original_departure_and_replays(self):
        session=self.session(50232010)
        source=self.add(session.engine,'Thousand Moons Infantry',ref='copy-untap-original')
        spell=self.add(session.engine,'Generic Entry One Copy',zone='hand',ref='copy-untap-spell')
        bounce=self.add(session.engine,'Unsummon',zone='hand',ref='copy-untap-bounce')
        for card in (source,spell,bounce):self.assert_closed(session,card)
        self.prepare(session,mana={'W':1,'U':1});self.checkpoint(session)
        self.cast(session,spell,(source.ref,))
        token,=[card for card in session.state.cards.values() if card.is_token and card.zone=='battlefield']
        self.cast(session,bounce,(source.ref,))
        self.replay(session,load=True)
        token.tapped=True
        self.end_step(session,active='B');self.checkpoint(session);self.next_turn(session,active='B')
        self.assertEqual(('C','upkeep'),(session.state.active_player,session.state.step))
        self.assertFalse(session.state.cards[token.object_id].tapped)
        self.assertEqual('hand',session.state.cards[source.object_id].zone)
        self.replay(session,load=True)

    def test_additional_untap_triggers_wait_for_upkeep_and_resolve_from_loaded_programs(self):
        session=self.session(50232011)
        source=self.add(session.engine,'Murkfiend Liege',ref='held-extra-untap-source')
        observer=self.add(session.engine,'Wake Thrasher',ref='held-extra-untap-observer')
        for card in (source,observer):self.assert_closed(session,card);card.tapped=True
        self.end_step(session);self.checkpoint(session);self.next_turn(session)
        self.assertEqual(('B','upkeep'),(session.state.active_player,session.state.step))
        self.assertFalse(session.state.cards[source.object_id].tapped)
        self.assertFalse(session.state.cards[observer.object_id].tapped)
        self.assertEqual('trigger.order',session.state.pending_decision.kind)
        self.assertFalse(session.state.stack)
        self.assertEqual('2',session.engine._effective_card_data(session.state.cards[observer.object_id])['power'])
        session=self.replay(session,load=True)
        packet=session.packet('pilot:A',full=True)['decision']
        refs=[row['id'] for row in packet['ctx']['triggers']]
        self.assertEqual(2,len(refs))
        self.reject(session,'order','B',triggers=refs)
        result=session.act('pilot:A',{'action_id':'order','triggers':refs})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual(2,len(session.state.stack))
        self.assertEqual({'A'},{item.controller for item in session.state.stack})
        self.assertIsNone(self.resolve(session))
        self.assertEqual('4',session.engine._effective_card_data(session.state.cards[observer.object_id])['power'])
        self.replay(session,load=True)

    def test_native_untap_participation_omission_is_killed(self):
        with patch.object(StaticUntapStepParticipationHandler,'lower',return_value=()):
            with self.assertRaises(AssertionError):
                self.test_actual_back_to_basics_preserves_basic_mana_and_blocks_nonbasic_untap()


if __name__=='__main__':
    unittest.main()
