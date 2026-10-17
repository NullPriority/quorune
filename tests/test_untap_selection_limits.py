from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.engine import TURN_STEPS
from quorune.errors import GameRuleError
from quorune.oracle_ir import compile_oracle_card
from quorune.untap_selection import validate_selection
from quorune.choice_forms import build_action_form
from quorune.continuous_effect_model import ContinuousOperation, Layer
from quorune.continuous_effect_state import (
    ResolutionContinuousComponent, ResolutionEffectSource, create_resolution_continuous_effect_components,
)
from quorune.optional_untap import OptionalUntapContinuation
from quorune.untap_step import UntapStepPlan, UntapSelectionConstraint, plan_untap_step
from quorune.compiler.untap_selection_templates import fixed_untap_selection_limit_handler
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.untap_steps import (
    current_untap_step_plan, FixedUntapSelectionLimitHandler, UntapStepSourceContext,
)
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.tap_state import prohibit_next_player_untap
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_spree_mode_costs as spree


TEXT = "As long as this artifact is untapped, players can't untap more than two permanents during their untap steps."
CAP = 'untap.step.fixed_selection_limit'


class UntapSelectionLimitCompilerTests(unittest.TestCase):
    def test_source_spanned_fixed_scopes_and_unsupported_neighbors(self):
        for text in (TEXT, "Players can't untap more than one artifact during their untap steps.",
                     "Players can't untap more than one nonbasic land during their untap steps.",
                     "You can't untap more than one land during your untap step."):
            ir = compile_oracle_card(record(text,'Artifact'), capability_registry=load_default_capability_registry(),
                capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
            node, = ir.faces[0].nodes
            self.assertEqual(text,node.text)
            self.assertEqual((0,len(text)),(node.span.start,node.span.end))
            self.assertIn(CAP,node.capability_dependencies)
        for text in (TEXT.replace('two','X'),TEXT.replace('permanents','cards'),
                     TEXT.replace('their untap steps','each other player\'s untap step')):
            self.assertNotEqual('exact',compile_oracle_card(record(text,'Artifact'),
                capability_registry=load_default_capability_registry()).status)
        with patch('quorune.compiler.untap_selection_templates.fixed_untap_selection_limit_handler',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(record(TEXT,'Artifact'),
                capability_registry=load_default_capability_registry()).status)

    def test_overlapping_limits_preserve_different_maximal_sizes_and_optional_choices(self):
        schema={'legal_refs':['ANIMATED','LAND','CREATURE','OPTIONAL'],
            'subset_maxima':[{'refs':['ANIMATED','LAND'],'maximum':1},
                             {'refs':['ANIMATED','CREATURE'],'maximum':1}],
            'maximal_refs':['ANIMATED','LAND','CREATURE']}
        for refs in (['ANIMATED'],['LAND','CREATURE'],['ANIMATED','OPTIONAL']):
            validate_selection(schema,refs)
        for refs in ([],['LAND'],['ANIMATED','LAND'],['ANIMATED','ANIMATED'],['UNKNOWN']):
            with self.subTest(refs=refs),self.assertRaises(GameRuleError):validate_selection(schema,refs)
        form=build_action_form({'id':'choose','action':'choose','choice_schema':{'refs':{
            'type':'object_ref_array','legal_refs':schema['legal_refs'],'subset_maxima':schema['subset_maxima'],
            'maximal_refs':schema['maximal_refs'],'distinct':True}}},decision_kind='untap.optional_source',context={})
        field,=form['fields']
        self.assertEqual(schema['subset_maxima'],field['subset_maxima'])
        self.assertEqual(schema['maximal_refs'],field['maximal_refs'])

    def test_historical_optional_continuation_omits_new_empty_limits_and_round_trips(self):
        frame=OptionalUntapContinuation('A',1,0,UntapStepPlan('A',(),(),optional_object_ids=('body',)),
            (),('body',),())
        wire=frame.to_dict()
        self.assertNotIn('selection_limits',wire['plan'])
        self.assertEqual(wire,OptionalUntapContinuation.from_dict(wire).to_dict())
        with self.assertRaises(GameRuleError):
            replace(frame,plan=replace(frame.plan,selection_limits=(UntapSelectionConstraint(('body',),1),)))

    def test_fine_limit_descriptor_is_closed_and_zero_prohibits_only_current_matches(self):
        from test_typed_untap_step_participation import _row
        descriptor=fixed_untap_selection_limit_handler(TEXT,source_name='Fixture')[1]
        handler=FixedUntapSelectionLimitHandler()
        for key,value in (('maximum',True),('maximum',-1),('maximum',11),('kind','additional')):
            bad=deepcopy(descriptor);bad['instruction'][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(SemanticNodeError):handler.validate(bad)
        bad=deepcopy(descriptor);bad['unknown']=True
        with self.assertRaises(SemanticNodeError):handler.validate(bad)
        bad=deepcopy(descriptor);bad['condition']['turn_relation']='other_player'
        with self.assertRaises(SemanticNodeError):handler.validate(bad)
        zero=fixed_untap_selection_limit_handler(
            "Players can't untap more than 0 lands during their untap steps.",source_name='Fixture')[1]
        context=UntapStepSourceContext('source','S1','A',False,'zero')
        plan=plan_untap_step('A',(_row('land',controller='A',card_type='land'),
            _row('other',controller='B',card_type='land')),handler.lower(zero,context))
        self.assertEqual(('land',),plan.prohibited_object_ids)
        self.assertEqual((),plan.selection_limits)


class UntapSelectionLimitRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=spree.SpreeModeCostRuntimeTests.session
    add=spree.SpreeModeCostRuntimeTests.add
    checkpoint=spree.SpreeModeCostRuntimeTests.checkpoint
    replay=spree.SpreeModeCostRuntimeTests.replay
    assert_closed=spree.SpreeModeCostRuntimeTests.assert_closed
    reject=spree.SpreeModeCostRuntimeTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'untap-limits.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/untap-selection-limit-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Untap limits',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def enter(self,session,active='A'):
        e=session.engine;e.permissions.invalidate_current();e.state.pending_decision=None
        e.state.priority_player=None;e.state.priority_passes=[];e.state.active_player=active
        e.state.phase_index=TURN_STEPS.index(('beginning','untap'));e._enter_step()

    def choose(self,session,refs,actor='A'):
        result=session.act('pilot:'+actor,{'action_id':'choose','choices':{'refs':list(refs)}})
        self.assertTrue(result.ok,result.summary)

    def test_actual_static_orb_choice_is_private_atomic_loaded_and_replayed(self):
        session=self.session(50231001)
        orb=self.add(session.engine,'Static Orb',ref='actual-static-orb');self.assert_closed(session,orb)
        cards=[self.add(session.engine,'Generic Bound Plains',ref='orb-land-'+str(i)) for i in range(3)]
        for card in cards:card.tapped=True
        hidden=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand',ref='untap-private-c')
        self.enter(session)
        self.assertEqual('untap.optional_source',session.state.pending_decision.kind)
        self.assertEqual('untap',session.state.step)
        self.assertIsNone(session.state.priority_player)
        packet=session.packet('pilot:A',full=True)['decision']
        field,=packet['legal_actions'][0]['form']['fields']
        self.assertTrue(field['subset_maxima']);self.assertTrue(field['maximal_refs'])
        self.assertTrue(all(card.tapped for card in cards))
        self.reject(session,'choose','B',choices={'refs':[cards[0].ref,cards[1].ref]})
        for refs in ([],[cards[0].ref],[c.ref for c in cards],[cards[0].ref]*2,['unknown']):
            self.reject(session,'choose',choices={'refs':refs})
        for actor in 'BCD':self.assertIsNone(session.packet('pilot:'+actor,full=True)['decision'])
        self.checkpoint(session);session=self.replay(session,load=True)
        self.choose(session,[cards[0].ref,cards[2].ref])
        self.assertEqual([False,True,False],[session.state.cards[c.object_id].tapped for c in cards])
        self.assertEqual('upkeep',session.state.step)
        for actor in 'ABD':self.assertNotIn(hidden.ref,json.dumps(session.packet('pilot:'+actor,full=True)))
        self.replay(session,load=True)

    def test_tapped_winter_orb_is_checked_before_any_permanent_untaps(self):
        session=self.session(50231002)
        orb=self.add(session.engine,'Winter Orb',ref='tapped-winter-orb');self.assert_closed(session,orb);orb.tapped=True
        cards=[self.add(session.engine,'Generic Bound Plains',ref='winter-land-'+str(i)) for i in range(3)]
        for card in cards:card.tapped=True
        self.enter(session)
        self.assertFalse(orb.tapped)
        self.assertTrue(all(not card.tapped for card in cards))
        self.assertEqual('upkeep',session.state.step)

    def test_actual_smoke_and_winter_orb_allow_both_maximal_overlapping_sets(self):
        for selection in (('animated',),('land','creature')):
            with self.subTest(selection=selection):
                session=self.session(50231003)
                for name in ('Smoke','Winter Orb'):
                    source=self.add(session.engine,name);self.assert_closed(session,source)
                cards={key:self.add(session.engine,name,ref=key) for key,name in (
                    ('animated','Generic Limit Animated Land'),('land','Generic Bound Plains'),
                    ('creature','Generic Bound Body'))}
                for card in cards.values():card.tapped=True
                self.enter(session)
                self.reject(session,'choose',choices={'refs':['land']})
                self.checkpoint(session)
                self.choose(session,selection)
                self.assertEqual({key:key not in selection for key in cards},
                    {key:session.state.cards[card.object_id].tapped for key,card in cards.items()})
                self.replay(session,load=True)

    def test_actual_artifact_creature_and_nonbasic_scopes_exclude_other_permanents(self):
        for name,limited_name,unaffected_name in (
            ('Imi Statue','Generic Limit Artifact','Generic Bound Body'),
            ('Damping Field','Generic Limit Artifact','Generic Bound Plains'),
            ('Stoic Angel','Generic Bound Body','Generic Bound Plains'),
            ('Winter Moon','Generic Limit Nonbasic Land','Generic Bound Plains')):
            with self.subTest(name=name):
                session=self.session(50231004)
                source=self.add(session.engine,name,seat='C');self.assert_closed(session,source)
                limited=[self.add(session.engine,limited_name,ref='limited-'+str(i)) for i in range(2)]
                other=self.add(session.engine,unaffected_name,ref='unaffected')
                foreign=self.add(session.engine,limited_name,seat='B',ref='foreign')
                for card in (*limited,other,foreign):card.tapped=True
                self.enter(session)
                schema=session.state.pending_decision.payload_by_actor['A']['legal_actions'][0]['choice_schema']['refs']
                self.assertEqual({card.ref for card in limited},set(schema['legal_refs']))
                self.choose(session,[limited[1].ref])
                self.assertTrue(session.state.cards[limited[0].object_id].tapped)
                self.assertFalse(session.state.cards[limited[1].object_id].tapped)
                self.assertFalse(session.state.cards[other.object_id].tapped)
                self.assertTrue(session.state.cards[foreign.object_id].tapped)

    def test_selected_stun_uses_a_slot_and_unselected_stun_is_preserved(self):
        session=self.session(50231005)
        self.assert_closed(session,self.add(session.engine,'Winter Orb'))
        cards=[self.add(session.engine,'Generic Bound Plains',ref='stunned-'+str(i)) for i in range(3)]
        for card in cards:card.tapped=True;card.counters['stun']=2
        self.enter(session);self.checkpoint(session)
        self.choose(session,[cards[1].ref])
        self.assertEqual([2,1,2],[session.state.cards[c.object_id].counters['stun'] for c in cards])
        self.assertTrue(all(session.state.cards[c.object_id].tapped for c in cards))
        self.replay(session,load=True)

    def test_prohibited_and_exerted_subjects_do_not_use_slots_and_expire_once(self):
        session=self.session(50231006)
        self.add(session.engine,'Static Orb')
        cards=[self.add(session.engine,'Generic Bound Plains',ref='blocked-'+str(i)) for i in range(4)]
        for card in cards:card.tapped=True;card.counters['stun']=1
        cards[0].annotations['does_not_untap_next']=True
        prohibit_next_player_untap(session.engine,cards[1],actor='A')
        self.enter(session)
        self.assertEqual('upkeep',session.state.step)
        self.assertEqual([1,1,0,0],[session.state.cards[c.object_id].counters.get('stun',0) for c in cards])
        self.assertNotIn('does_not_untap_next',cards[0].annotations)
        self.assertEqual({},cards[1].annotations)

    def test_optional_sources_share_the_limit_without_forcing_optional_untaps(self):
        for selected in (('mandatory',),('optional',)):
            with self.subTest(selected=selected):
                session=self.session(50231007)
                self.add(session.engine,'Imi Statue')
                mandatory=self.add(session.engine,'Generic Limit Artifact',ref='mandatory')
                optional=self.add(session.engine,'Generic Limit Optional Artifact',ref='optional')
                mandatory.tapped=optional.tapped=True
                self.assert_closed(session,optional)
                self.enter(session);self.reject(session,'choose',choices={'refs':[]})
                self.checkpoint(session);self.choose(session,selected)
                self.assertEqual('mandatory' not in selected,session.state.cards[mandatory.object_id].tapped)
                self.assertEqual('optional' not in selected,session.state.cards[optional.object_id].tapped)
                self.replay(session,load=True)

    def test_current_source_control_ability_removal_and_duplicate_limits(self):
        session=self.session(50231008)
        source=self.add(session.engine,'Mungha Wurm',ref='own-limits')
        self.assert_closed(session,source)
        a=[self.add(session.engine,'Generic Bound Plains',ref='a-'+str(i)) for i in range(2)]
        b=[self.add(session.engine,'Generic Bound Plains',seat='B',ref='b-'+str(i)) for i in range(2)]
        for card in (*a,*b):card.tapped=True
        self.assertTrue(current_untap_step_plan(session.engine,'A').selection_limits)
        self.assertFalse(current_untap_step_plan(session.engine,'B').selection_limits)
        from quorune.control_effects import change_control
        change_control(session.engine,source.object_id,'B',reason='Current untap limit controller')
        self.assertFalse(current_untap_step_plan(session.engine,'A').selection_limits)
        self.assertTrue(current_untap_step_plan(session.engine,'B').selection_limits)
        humility=self.add(session.engine,'Humility');self.assert_closed(session,humility)
        self.assertFalse(current_untap_step_plan(session.engine,'B').selection_limits)
        session.engine.move_card(humility.object_id,'graveyard',log=False)
        duplicate=self.add(session.engine,'Mungha Wurm',seat='B',ref='duplicate-limit')
        plan=current_untap_step_plan(session.engine,'B')
        self.assertEqual(1,len(plan.selection_limits));self.assertEqual(1,plan.selection_limits[0].maximum)
        self.enter(session,'B');self.choose(session,[b[1].ref],actor='B')
        self.assertTrue(session.state.cards[b[0].object_id].tapped)
        self.assertFalse(session.state.cards[b[1].object_id].tapped)
        self.assertTrue(all(session.state.cards[c.object_id].tapped for c in a))

    def test_stale_source_or_subject_and_malformed_retained_fields_reject_atomically(self):
        for mutate in ('counter','control','source','unknown','boolean','trigger','plan_boolean','snapshot_boolean'):
            with self.subTest(mutate=mutate):
                session=self.session(50231009)
                source=self.add(session.engine,'Winter Orb',ref='stale-source')
                cards=[self.add(session.engine,'Generic Bound Plains',ref='stale-'+str(i)) for i in range(2)]
                for card in cards:card.tapped=True
                self.enter(session)
                frame=session.state.pending_decision.continuation['untap_selection']
                if mutate=='counter':cards[0].counters['stun']=1
                elif mutate=='control':session.engine.change_control(cards[0].object_id,'B',reason='Stale choice')
                elif mutate=='source':source.tapped=True
                elif mutate=='unknown':frame['unknown']=True
                elif mutate=='boolean':frame['schema_version']=True
                elif mutate=='plan_boolean':frame['plan']['selection_limits'][0]['maximum']=True
                elif mutate=='snapshot_boolean':
                    row=next(row for row in frame['snapshot'] if row['object_id']==cards[0].object_id)
                    row['tapped']=1
                else:frame['waiting_triggers']=[{'kind':'spell'}]
                self.reject(session,'choose',choices={'refs':[cards[0].ref]})

    def test_pre_turn_boundary_replay_holds_untap_triggers_until_upkeep(self):
        session=self.session(50231010)
        self.add(session.engine,'Winter Orb')
        observer=self.add(session.engine,'Wake Thrasher',seat='B',ref='untap-observer')
        self.assert_closed(session,observer)
        cards=[self.add(session.engine,'Generic Bound Plains',seat='B',ref='next-turn-'+str(i)) for i in range(2)]
        for card in cards:card.tapped=True
        e=session.engine;e.state.phase_index=TURN_STEPS.index(('ending','end_step'))
        e.state.phase='ending';e.state.step='end_step';e.state.active_player='A'
        e.state.priority_passes=[];e.permissions.invalidate_current();e.state.pending_decision=None
        e._grant_priority('A');e.pump();self.checkpoint(session)
        for seat in 'ABCD':
            result=session.act('pilot:'+seat,{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('B',session.state.active_player)
        self.assertEqual('untap',session.state.step);self.assertIsNone(session.state.priority_player)
        self.assertFalse(session.state.stack)
        session=self.replay(session,load=True)
        self.choose(session,[cards[0].ref],actor='B')
        self.assertEqual('upkeep',session.state.step)
        self.assertEqual(1,len(session.state.stack))
        self.assertEqual('B',session.state.stack[0].controller)
        spree.SpreeModeCostRuntimeTests.resolve(self,session)
        self.assertEqual('2',session.engine._effective_card_data(session.state.cards[observer.object_id])['power'])
        self.replay(session,load=True)

    def test_runtime_limit_omission_is_killed_by_the_actual_native_untap_boundary(self):
        def witness():
            session=self.session(50231011)
            self.assert_closed(session,self.add(session.engine,'Static Orb'))
            cards=[self.add(session.engine,'Generic Bound Plains',ref='mutation-'+str(i)) for i in range(3)]
            for card in cards:card.tapped=True
            self.enter(session)
            self.assertEqual('untap',session.state.step)
            self.assertEqual('untap.optional_source',session.state.pending_decision.kind)
            self.assertTrue(all(card.tapped for card in cards))
        witness()
        with patch('quorune.semantic_runtime.untap_steps.FixedUntapSelectionLimitHandler.lower',return_value=()):
            with self.assertRaises(AssertionError):witness()

    def test_current_type_changes_recompute_scopes_and_manual_mode_retains_tapped_state(self):
        session=self.session(50231012)
        self.add(session.engine,'Smoke')
        body=self.add(session.engine,'Generic Bound Body',ref='scope-body')
        land=self.add(session.engine,'Generic Bound Plains',ref='animated-now')
        body.tapped=land.tapped=True
        plan=current_untap_step_plan(session.engine,'A')
        self.assertEqual((body.object_id,),plan.selection_limits[0].object_ids)
        create_resolution_continuous_effect_components(session.engine,
            source=ResolutionEffectSource(stack_ref='S-untap-scope'),targets=(land,),components=(
                ResolutionContinuousComponent(Layer.TYPE,'4',(
                    ContinuousOperation('add_types',('creature',),field='card_types'),)),
                ResolutionContinuousComponent(Layer.POWER_TOUGHNESS,'7b',(
                    ContinuousOperation('set_power_toughness',(2,2)),))))
        plan=current_untap_step_plan(session.engine,'A')
        self.assertEqual({body.object_id,land.object_id},set(plan.selection_limits[0].object_ids))
        session.state.config.auto_untap=False
        self.enter(session)
        self.assertTrue(body.tapped);self.assertTrue(land.tapped)
        self.assertEqual('upkeep',session.state.step)
        self.assertTrue(session.state.pending_decision is None
            or session.state.pending_decision.kind != 'untap.optional_source')


if __name__=='__main__':unittest.main()
