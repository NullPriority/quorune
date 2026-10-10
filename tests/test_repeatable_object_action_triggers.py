from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses


class RepeatableObjectActionCompilerTests(unittest.TestCase):
    def test_sacrifice_another_and_discard_counter_bodies_share_typed_events(self):
        registry=load_default_capability_registry()
        for text,event in (
            ('Whenever you sacrifice another permanent, put a +1/+1 counter on this creature.','permanent.sacrificed'),
            ('Whenever an opponent discards a creature card, put a +1/+1 counter on this creature.','card.discarded'),
        ):
            with self.subTest(text=text):
                ir=compile_oracle_card(query_record(text),capability_registry=registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                node=ir.faces[0].nodes[0]
                self.assertEqual(event,node.event)
                self.assertEqual('place_counters',node.effects[0]['op'])
                self.assertEqual('$source.zone_object',node.effects[0]['card'])
                self.assertIn('trigger.event.normalized_zone_change',node.capability_dependencies)
                self.assertEqual((0,len(text)),(node.span.start,node.span.end))
                if 'another' in text:
                    self.assertIn({'field':'card','op':'ne','value':'$source.ref'},node.event_condition['all'])

    def test_aggregate_limited_and_malformed_action_grammar_remains_residual(self):
        registry=load_default_capability_registry()
        for text in (
            'Whenever you sacrifice one or more permanents, draw a card.',
            'Whenever you sacrifice another permanent, draw a card. This ability triggers only once during your turn.',
            'Whenever you sacrifice creature, draw a card.',
            'Whenever you sacrifice another permanent, you may put a +1/+1 counter on this creature. If you do, draw a card.',
            'Whenever you discard a card for the first time each turn, draw a card.',
            'Whenever you sacrifice another creature with power X or less, draw a card.',
        ):
            with self.subTest(text=text):
                ir=compile_oracle_card(query_record(text),capability_registry=registry,capability_profile='commander_review')
                self.assertNotEqual('exact',ir.status)

    def test_action_counter_dependency_and_parser_omission_fail_closed(self):
        text='Whenever you sacrifice another permanent, put a +1/+1 counter on this creature.'
        record=query_record(text)
        registry=load_default_capability_registry()
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        for capability in ('trigger.event.normalized_zone_change','counter.producer.fixed_event_trigger'):
            value=json.loads(json.dumps(raw))
            next(c for c in value['capabilities'] if c['id']==capability).update(
                status='blocked',blockers=['independent event or counter owner unavailable'])
            self.assertNotEqual('exact',compile_oracle_card(record,capability_registry=CapabilityRegistry(value)).status)
        with patch('quorune.compiler.fixed_public_action_event_bindings._public_sacrifice_spec',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(record,capability_registry=registry).status)


class RepeatableObjectActionRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory()
        path=Path(cls.temporary.name)/'actions.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/repeatable-object-action-triggers.json'],path)
        cls.db=CardDatabase(path)
        cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Repeatable object actions',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close();cls.temporary.cleanup()

    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    resolve=witnesses.BoundEffectProgramRuntimeTests.resolve
    replay=witnesses.BoundEffectProgramRuntimeTests.replay

    def test_actual_sacrifice_cost_queues_one_counter_per_object_and_replays(self):
        session=self.session(297001);engine=session.engine
        observer=self.add(engine,'Gixian Infiltrator')
        enemy=self.add(engine,'Gixian Infiltrator',seat='B',ref='enemy-observer')
        feeder=self.add(engine,'Carrion Feeder')
        bodies=[self.add(engine,'Generic Bound Body',ref='sacrifice-'+str(n)) for n in range(2)]
        program=compile_best_available_card_program(self.db,self.db.lookup('Gixian Infiltrator'),
            semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
        binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'],binding['blockers'])
        action=self.ready(session,feeder,{})
        self.checkpoint(session)
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:B',{'action_id':action['id'],'cost_cards':[bodies[0].ref],'pay':'auto'})
        self.assertFalse(rejected.ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        for index,body in enumerate(bodies):
            if index:
                action=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                            if row.get('source')==feeder.ref)
            result=session.act('pilot:A',{'action_id':action['id'],'cost_cards':[body.ref],'pay':'auto'})
            self.assertTrue(result.ok,result.summary)
            self.resolve(session)
            self.assertEqual('graveyard',body.zone)
            self.assertEqual(index+1,observer.counters.get('+1/+1',0))
            self.assertEqual(index+1,feeder.counters.get('+1/+1',0))
            self.assertEqual({},enemy.counters)
        self.replay(session,load=True)

    def test_pending_sacrifice_trigger_does_not_modify_blinked_source(self):
        session=self.session(297002);engine=session.engine
        observer=self.add(engine,'Gixian Infiltrator')
        feeder=self.add(engine,'Carrion Feeder')
        victim=self.add(engine,'Generic Bound Body')
        blink=self.add(engine,'Cloudshift',zone='hand')
        action=self.ready(session,feeder,{'W':1})
        old_identity=observer.logical_object_id
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'cost_cards':[victim.ref]})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertTrue(any(item.source_object_id==observer.object_id for item in engine.state.stack))
        response=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                      if a.get('card')==blink.ref)
        accepted=session.act('pilot:A',{'action_id':response['id'],'targets':[observer.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        for _ in range(12):
            if not any(item.kind=='spell' for item in engine.state.stack):break
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        self.assertNotEqual(old_identity,observer.logical_object_id)
        self.resolve(session)
        self.assertEqual({},observer.counters)
        self.assertEqual(1,feeder.counters.get('+1/+1'))
        self.replay(session,load=True)

    def test_another_excludes_self_sacrifice_while_foreign_owned_controlled_subject_counts(self):
        session=self.session(297003);engine=session.engine
        observer=self.add(engine,'Gixian Infiltrator')
        feeder=self.add(engine,'Carrion Feeder')
        victim=self.add(engine,'Generic Bound Body',seat='B')
        from quorune.control_effects import gain_control_of_refs
        from quorune.continuous_effect_model import ContinuousEffectDuration
        from quorune.continuous_effect_state import ResolutionEffectSource
        gain_control_of_refs(engine,actor='A',object_refs=(victim.ref,),controller='A',
            duration=ContinuousEffectDuration.ZONE_OBJECT,
            source=ResolutionEffectSource(stack_ref='resolved-borrowed-sacrifice'),
            reason='establish independently owned controlled sacrifice subject')
        action=self.ready(session,feeder,{})
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'cost_cards':[victim.ref]})
        self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        self.assertEqual('graveyard',victim.zone)
        self.assertIn(victim.object_id,engine.state.players['B'].zones['graveyard'])
        self.assertEqual(1,observer.counters.get('+1/+1'))
        action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    if a.get('source')==feeder.ref)
        accepted=session.act('pilot:A',{'action_id':action['id'],'cost_cards':[observer.ref]})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertFalse(any(item.source_object_id==observer.object_id for item in engine.state.stack))
        self.resolve(session)
        self.assertEqual(2,feeder.counters.get('+1/+1'))
        self.replay(session,load=True)

    def test_noncreature_nonland_discard_trigger_uses_replaced_public_result_and_private_choice(self):
        session=self.session(297004);engine=session.engine
        observer=self.add(engine,'Generic Noncreature Nonland Discard Observer')
        self.add(engine,'Dauthi Voidwalker')
        for ident in tuple(engine.state.players['B'].zones['hand']):
            engine.move_card(ident,'library',log=False)
        chosen=self.add(engine,'Generic Bound Growth',seat='B',zone='hand',ref='private-discard-growth')
        land=self.add(engine,'Generic Bound Plains',seat='B',zone='hand',ref='private-discard-land')
        creature=self.add(engine,'Generic Bound Body',seat='B',zone='hand',ref='private-discard-creature')
        spell=self.add(engine,'Generic Bound Discard Loss',zone='hand')
        action=self.ready(session,spell,{'B':3,'C':3})
        self.checkpoint(session)
        hand_before=len(engine.state.players['A'].zones['hand'])
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':['B'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        decision=self.resolve(session)
        self.assertIsNotNone(decision)
        self.assertIn('pilot:B',session.pending_principals())
        self.assertNotIn(chosen.ref,str(session.packet('pilot:C',full=True)))
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:C',{'action_id':'choose','cards':[chosen.ref]})
        self.assertFalse(rejected.ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:B',{'action_id':'choose','cards':[chosen.ref]})
        self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        self.assertEqual('exile',chosen.zone)
        self.assertEqual(1,chosen.counters.get('void'))
        self.assertEqual('hand',land.zone)
        self.assertEqual('hand',creature.zone)
        self.assertEqual(hand_before,len(engine.state.players['A'].zones['hand'])) # Cast one, then draw once.
        self.assertEqual(39,engine.state.players['B'].life)
        self.replay(session,load=True)


if __name__=='__main__':unittest.main()
