from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.attached_control import AttachedControlSpec
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses


class StaticAttachedControlCompilerTests(unittest.TestCase):
    def test_exact_attached_control_descriptor_and_source_span(self):
        text='You control enchanted creature.'
        ir=compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry())
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node=ir.faces[0].nodes[0]
        self.assertEqual(AttachedControlSpec().to_descriptor(),dict(node.handlers[0]))
        self.assertEqual((0,len(text)),(node.span.start,node.span.end))
        self.assertIn('continuous.control.attached_source',node.capability_dependencies)

    def test_malformed_and_conditional_static_control_remain_closed(self):
        for value in ({'schema_version':True}, {**AttachedControlSpec().to_descriptor(),'controller':'owner'},
                      {**AttachedControlSpec().to_descriptor(),'extra':1}):
            with self.assertRaises(ValueError):AttachedControlSpec.from_descriptor(value)
        for text in ('You control enchanted creature as long as it is tapped.','You control enchanted player.',
                     'You control all creatures enchanted player controls.'):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry()).status)

    def test_static_controller_dependencies_and_cycle_follow_timestamp_order(self):
        from quorune.attached_control import layer_two_controller_map
        from quorune.continuous_effect_model import ContinuousEffect,ContinuousEffectOrigin,ContinuousEffectRelation,ContinuousObjectIdentity,ContinuousOperation,Layer
        cards={name:SimpleNamespace(object_id=name,logical_object_id=name+'@0',zone='battlefield',controller=controller) for name,controller in [('one','A'),('two','B'),('body','C')]}
        def effect(source,target,time):
            return ContinuousEffect(effect_id=source+'-control',source_id=source,layer=Layer.CONTROL,sublayer='2',timestamp=time,
                operations=(ContinuousOperation('set_controller',cards[source].controller),),origin=ContinuousEffectOrigin.STATIC_ABILITY,
                relation=ContinuousEffectRelation.SOURCE_ATTACHED_TO_OBJECT,related_object=ContinuousObjectIdentity(target,target+'@0'))
        host=SimpleNamespace(state=SimpleNamespace(cards=cards))
        chain=(effect('one','body',1),effect('two','one',2))
        self.assertEqual({'one':'B','body':'B'},layer_two_controller_map(host,(),chain))
        cycle=(effect('one','two',1),effect('two','one',2))
        self.assertEqual({'one':'A','two':'A'},layer_two_controller_map(host,(),cycle))
        self.assertEqual(layer_two_controller_map(host,(),cycle),layer_two_controller_map(host,(),tuple(reversed(cycle))))
        from quorune.continuous_effect_model import ContinuousEffectDuration
        origins=tuple(ContinuousEffect(effect_id='control-origin:'+card.logical_object_id,source_id=card.object_id,layer=Layer.CONTROL,
            sublayer='2',timestamp=0,operations=(ContinuousOperation('set_controller',card.controller),),origin=ContinuousEffectOrigin.RESOLUTION,
            duration=ContinuousEffectDuration.ZONE_OBJECT,locked_objects=(ContinuousObjectIdentity(card.object_id,card.logical_object_id),)) for card in cards.values())
        cards['one'].controller='B';cards['two'].controller='A'
        self.assertEqual({'one':'A','two':'A','body':'C'},layer_two_controller_map(host,origins,cycle))


class StaticAttachedControlRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'aura-control.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/static-attached-control.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Static control',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    resolve=witnesses.BoundEffectProgramRuntimeTests.resolve
    replay=witnesses.BoundEffectProgramRuntimeTests.replay

    def cast(self,session,spell,target):
        for _ in range(8):
            if session.pending_principals()[0]=='pilot:'+spell.owner:break
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        principal=session.pending_principals()[0]
        action=next(a for a in session.packet(principal,full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==spell.ref)
        self.assertIn(target.ref,action['target_schema']['legal_refs'])
        accepted=session.act(principal,{'action_id':action['id'],'targets':[target.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)

    def test_actual_aura_cast_controls_recipient_and_bounce_restores_initial_custody(self):
        from quorune.control_effects import change_control
        session=self.session(303001);engine=session.engine
        body=self.add(engine,'Generic Bound Mentor',seat='B')
        self.add(engine,'Generic Bound Body',ref='mentor-target')
        change_control(engine,body.object_id,'C')
        aura=self.add(engine,'Mind Control',zone='hand')
        bounce=self.add(engine,'Generic Aura Control Bounce',zone='hand')
        action=self.ready(session,aura,{'U':3,'C':4});self.checkpoint(session)
        self.assertIn(body.ref,action['target_schema']['legal_refs'])
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:B',{'action_id':action['id'],'targets':[body.ref],'pay':'auto'})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.cast(session,aura,body)
        self.assertEqual('A',session.state.cards[body.object_id].controller)
        self.assertEqual(body.object_id,session.state.cards[aura.object_id].attached_to)
        self.assertEqual('B',session.state.cards[body.object_id].owner)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(a['id'].startswith('activate:'+body.ref+':') for a in actions))
        self.assertEqual(session.state.players['A'].turns_begun,session.state.cards[body.object_id].acquired_control_turn_count)
        self.replay(session,load=True)
        self.cast(session,bounce,aura)
        self.assertEqual('C',session.state.cards[body.object_id].controller)
        self.assertEqual('hand',session.state.cards[aura.object_id].zone)
        self.assertEqual(body.object_id,session.state.players['C'].zones['battlefield'][-1])
        self.replay(session)

    def test_static_source_omission_mutant_is_killed_by_actual_aura_cast(self):
        session=self.session(303002);engine=session.engine
        body=self.add(engine,'Generic Bound Body',seat='B')
        aura=self.add(engine,'Mind Control',zone='hand')
        self.ready(session,aura,{'U':2,'C':3})
        with patch('quorune.control_effects.static_attachment_control_effects',return_value=()):
            self.cast(session,aura,body)
            with self.assertRaises(AssertionError):self.assertEqual('A',session.state.cards[body.object_id].controller)

    def priority(self,session,seat):
        session.engine.permissions.invalidate_current();session.state.pending_decision=None
        session.state.active_player=seat;session.state.phase='precombat_main';session.state.step='main'
        session.engine._grant_priority(seat);session.engine.pump()

    def test_overlapping_static_auras_and_later_resolution_share_timestamp_order(self):
        session=self.session(303003);engine=session.engine
        body=self.add(engine,'Generic Bound Body',seat='B')
        a=self.add(engine,'Mind Control',zone='hand',ref='a-control')
        c=self.add(engine,'Mind Control',zone='hand',seat='C',ref='c-control')
        bounces=[self.add(engine,'Generic Aura Control Bounce',zone='hand',ref='bounce-'+str(i)) for i in range(2)]
        treason=self.add(engine,'Act of Treason',seat='D',zone='hand')
        self.ready(session,a,{'U':4,'C':3});self.checkpoint(session)
        self.cast(session,a,body);self.replay(session)
        session.state.players['C'].mana_pool.update({'U':2,'C':3});self.priority(session,'C');self.checkpoint(session)
        self.cast(session,c,body);self.assertEqual('C',session.state.cards[body.object_id].controller);self.replay(session)
        session.state.players['D'].mana_pool.update({'R':1,'C':2});self.priority(session,'D');self.checkpoint(session)
        self.cast(session,treason,body);self.assertEqual('D',session.state.cards[body.object_id].controller);self.replay(session)
        self.priority(session,'A');self.checkpoint(session)
        self.cast(session,bounces[0],c)
        self.assertEqual('D',session.state.cards[body.object_id].controller);self.replay(session)
        from quorune.continuous_effect_state import expire_end_of_turn_continuous_effects
        expire_end_of_turn_continuous_effects(session.state)
        engine._stabilize();session.state.players['A'].mana_pool['U']=1;self.priority(session,'A');self.checkpoint(session)
        self.assertEqual('A',session.state.cards[body.object_id].controller)
        self.cast(session,bounces[1],a)
        self.assertEqual('B',session.state.cards[body.object_id].controller);self.replay(session)

    def test_controlling_the_aura_changes_its_recipient_control_and_restores_chain(self):
        session=self.session(303004);engine=session.engine
        body=self.add(engine,'Generic Bound Body',seat='B')
        aura=self.add(engine,'Mind Control',zone='hand',ref='inner-control')
        outer=self.add(engine,'Steal Enchantment',zone='hand',seat='C')
        bounce=self.add(engine,'Generic Aura Control Bounce',zone='hand',seat='C')
        self.ready(session,aura,{'U':2,'C':3});self.checkpoint(session)
        self.cast(session,aura,body);self.replay(session)
        session.state.players['C'].mana_pool.update({'U':3});self.priority(session,'C');self.checkpoint(session)
        self.cast(session,outer,aura)
        self.assertEqual('C',session.state.cards[aura.object_id].controller)
        self.assertEqual('C',session.state.cards[body.object_id].controller)
        self.assertEqual('A',session.state.cards[aura.object_id].owner)
        self.replay(session,load=True)
        self.cast(session,bounce,outer)
        self.assertEqual('A',session.state.cards[aura.object_id].controller)
        self.assertEqual('A',session.state.cards[body.object_id].controller)
        self.replay(session)

    def test_losing_aura_ability_restores_control_and_blink_breaks_attachment(self):
        session=self.session(303005);engine=session.engine
        body=self.add(engine,'Generic Bound Body',seat='B')
        aura=self.add(engine,'Mind Control',zone='hand')
        song=self.add(engine,'Generic Aura Ability Loss',zone='hand')
        self.ready(session,aura,{'U':4,'G':1,'C':8});self.checkpoint(session)
        self.cast(session,aura,body)
        self.cast(session,song,aura)
        self.assertEqual('B',session.state.cards[body.object_id].controller,
            {'body_identity':session.state.cards[body.object_id].logical_object_id,'aura_zone':session.state.cards[aura.object_id].zone,
             'aura_attached':session.state.cards[aura.object_id].attached_to,'journal':[e.to_dict() for e in session.state.continuous_effects]})
        self.assertEqual('graveyard',session.state.cards[song.object_id].zone)
        self.assertEqual('graveyard',session.state.cards[aura.object_id].zone)
        self.replay(session)

    def test_later_layer_aura_ability_loss_keeps_prior_layer_two_control(self):
        from quorune.continuous_effect_model import ContinuousEffect,ContinuousEffectOrigin,ContinuousEffectDuration,ContinuousObjectIdentity,ContinuousOperation,Layer
        from quorune.continuous_effect_state import commit_continuous_effect
        session=self.session(303007);engine=session.engine
        body=self.add(engine,'Generic Bound Body',seat='B');aura=self.add(engine,'Mind Control',zone='hand')
        self.ready(session,aura,{'U':2,'C':3});self.cast(session,aura,body)
        effect=ContinuousEffect(effect_id='diagnostic-aura-ability-loss',source_id='diagnostic-stack',layer=Layer.ABILITY,sublayer='6',
            timestamp=engine._next_zone_timestamp(),operations=(ContinuousOperation('remove_all_abilities'),),
            origin=ContinuousEffectOrigin.RESOLUTION,duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
            locked_objects=(ContinuousObjectIdentity(aura.object_id,session.state.cards[aura.object_id].logical_object_id),))
        commit_continuous_effect(session.state,effect);engine._stabilize()
        self.assertEqual('A',session.state.cards[body.object_id].controller)
        self.assertEqual(body.object_id,session.state.cards[aura.object_id].attached_to)
        self.assertEqual('battlefield',session.state.cards[aura.object_id].zone)

    def test_phased_out_recipient_keeps_control_history_and_relation(self):
        session=self.session(303008);engine=session.engine
        body=self.add(engine,'Generic Bound Body',seat='B');aura=self.add(engine,'Mind Control',zone='hand')
        self.ready(session,aura,{'U':2,'C':3});self.cast(session,aura,body)
        acquired=session.state.cards[body.object_id].acquired_control_timestamp
        session.state.cards[body.object_id].phased_out=True;session.state.cards[aura.object_id].phased_out=True
        engine._stabilize()
        self.assertEqual('A',session.state.cards[body.object_id].controller)
        self.assertEqual(acquired,session.state.cards[body.object_id].acquired_control_timestamp)
        session.state.cards[body.object_id].phased_out=False;session.state.cards[aura.object_id].phased_out=False
        engine._stabilize()
        self.assertEqual('A',session.state.cards[body.object_id].controller)
        self.assertEqual(acquired,session.state.cards[body.object_id].acquired_control_timestamp)

    def test_original_aura_with_unsupported_sibling_stays_out_of_runtime(self):
        c=self.db.lookup('Take Possession')
        ir=compile_oracle_card(c,capability_registry=self.registry,capability_profile='commander_review')
        self.assertNotEqual('exact',ir.status)
        self.assertTrue(any(n.exact and n.handlers and n.handlers[0].get('handler_id')=='continuous.control.attached-source.v1' for f in ir.faces for n in f.nodes))
        program=compile_best_available_card_program(self.db,c,semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
        self.assertFalse(bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict_capability_ready'])

    def test_departing_aura_owner_restores_recipient_in_surviving_game(self):
        session=self.session(303009);engine=session.engine
        body=self.add(engine,'Generic Bound Body',seat='B');aura=self.add(engine,'Mind Control',zone='hand')
        self.ready(session,aura,{'U':2,'C':3});self.checkpoint(session);self.cast(session,aura,body)
        self.assertEqual('A',session.state.cards[body.object_id].controller)
        result=session.act('pilot:A',{'action_id':'concede','choices':{'confirm_concede':True}});self.assertTrue(result.ok,result.summary)
        self.assertEqual('B',session.state.cards[body.object_id].controller)
        self.assertEqual('outside',session.state.cards[aura.object_id].zone)
        self.assertEqual(['B','C','D'],engine.active_seats);self.replay(session)

    def test_actual_recipient_blink_returns_under_printed_controller_without_old_control(self):
        session=self.session(303006);engine=session.engine
        body=self.add(engine,'Generic Bound Body',seat='B')
        aura=self.add(engine,'Mind Control',zone='hand')
        blink=self.add(engine,'Cloudshift',zone='hand')
        self.ready(session,aura,{'U':2,'W':1,'C':8});self.checkpoint(session)
        self.cast(session,aura,body);identity=session.state.cards[body.object_id].logical_object_id
        self.cast(session,blink,body)
        self.assertNotEqual(identity,session.state.cards[body.object_id].logical_object_id)
        self.assertEqual('A',session.state.cards[body.object_id].controller)
        self.assertFalse(any(e.effect_id=='control-origin:'+session.state.cards[body.object_id].logical_object_id for e in session.state.continuous_effects))
        self.assertEqual('graveyard',session.state.cards[aura.object_id].zone)
        self.replay(session)


if __name__=='__main__':unittest.main()
