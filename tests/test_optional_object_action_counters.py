from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.semantics import SemanticRegistry
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses
import test_counter_placement_events as counter_witnesses


class OptionalObjectActionCounterCompilerTests(unittest.TestCase):
    def test_optional_public_action_counter_uses_existing_choice_owner(self):
        for text,event in (
            ('Whenever a player sacrifices a creature, you may put a +1/+1 counter on this creature.','permanent.sacrificed'),
            ('Whenever an opponent discards a card, you may put a quest counter on this enchantment.','card.discarded')):
            with self.subTest(text=text):
                ir=compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry())
                self.assertEqual('exact',ir.status,ir.material_residuals)
                node=ir.faces[0].nodes[0];self.assertEqual(event,node.event)
                self.assertEqual('offer_optional_counter_placement',node.effects[0]['op'])
                self.assertIn('counter.producer.optional_fixed_event_trigger',node.capability_dependencies)
                self.assertEqual((0,len(text)),(node.span.start,node.span.end))

    def test_optional_public_action_omission_mutant_is_killed(self):
        text='Whenever a player sacrifices a creature, you may put a +1/+1 counter on this creature.'
        with patch('quorune.compiler.fixed_counter_trigger_nodes.OPTIONAL_COUNTER_PLACEMENT_OPERATION','place_counters'):
            ir=compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry())
            with self.assertRaises(AssertionError):
                self.assertEqual('offer_optional_counter_placement',ir.faces[0].nodes[0].effects[0]['op'])


class OptionalObjectActionCounterRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'optional.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/repeatable-object-action-triggers.json',ROOT/'tests/fixtures/optional-object-action-counters.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Optional action counters',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    order_triggers=counter_witnesses.CounterPlacementEventRuntimeTests.order_triggers
    resolve=counter_witnesses.CounterPlacementEventRuntimeTests.resolve

    def finish_to_choice(self,session):
        for _ in range(36):
            pending=session.state.pending_decision
            if pending is not None and pending.kind=='trigger.order':self.order_triggers(session);continue
            if pending is not None and pending.kind=='semantic.choice':return pending
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.fail('Optional counter choice was not issued')

    def test_actual_sacrifice_can_decline_then_place_replaced_counter_and_replay(self):
        session=self.session(302001);engine=session.engine
        observer=self.add(engine,'Mortician Beetle');feeder=self.add(engine,'Carrion Feeder')
        self.add(engine,'Hardened Scales')
        victims=[self.add(engine,'Generic Bound Body',ref='victim-'+str(i)) for i in range(2)]
        action=self.ready(session,feeder,{});self.checkpoint(session)
        for index,victim in enumerate(victims):
            if index:action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('source')==feeder.ref)
            accepted=session.act('pilot:A',{'action_id':action['id'],'cost_cards':[victim.ref]});self.assertTrue(accepted.ok,accepted.summary)
            self.finish_to_choice(session)
            self.assertEqual(('pilot:A',),tuple(session.pending_principals()))
            for seat in 'BCD':self.assertIsNone(session.packet('pilot:'+seat,full=True)['decision'])
            before=authoritative_state_hash(session.state)
            denied=session.act('pilot:B',{'action_id':'choose','choice':'put'});self.assertFalse(denied.ok)
            self.assertEqual(before,authoritative_state_hash(session.state))
            resumed=self.replay(session,load=True)
            response=resumed.act('pilot:A',{'action_id':'choose','choice':'decline' if index==0 else 'put'})
            self.assertTrue(response.ok,response.summary);self.resolve(resumed);self.replay(resumed)
            self.assertEqual(0 if index==0 else 2,resumed.state.cards[observer.object_id].counters.get('+1/+1',0))
            session=resumed;engine=session.engine

    def test_replaced_discard_retains_optional_counter_choice_and_private_replay(self):
        session=self.session(302002);engine=session.engine
        observer=self.add(engine,'Generic Optional Discard Counter Observer')
        self.add(engine,'Dauthi Voidwalker')
        for ident in tuple(engine.state.players['B'].zones['hand']):engine.move_card(ident,'library',log=False)
        chosen=self.add(engine,'Generic Bound Growth',seat='B',zone='hand',ref='private-discard')
        spell=self.add(engine,'Generic Bound Discard Loss',zone='hand')
        action=self.ready(session,spell,{'B':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':['B'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        pending=self.resolve(session);self.assertEqual('choice.apnap',pending.kind)
        for seat in 'ACD':self.assertNotIn(chosen.ref,str(session.packet('pilot:'+seat,full=True)))
        accepted=session.act('pilot:B',{'action_id':'choose','cards':[chosen.ref]});self.assertTrue(accepted.ok,accepted.summary)
        self.finish_to_choice(session)
        self.assertEqual('exile',session.state.cards[chosen.object_id].zone)
        self.assertEqual(('pilot:A',),tuple(session.pending_principals()))
        resumed=self.replay(session,load=True)
        accepted=resumed.act('pilot:A',{'action_id':'choose','choice':'put'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(resumed)
        self.assertEqual(1,resumed.state.cards[observer.object_id].counters['quest']);self.replay(resumed)

    def test_original_discard_carrier_sibling_still_blocks_whole_card(self):
        c=self.db.lookup('Quest for the Nihil Stone')
        ir=compile_oracle_card(c,capability_registry=self.registry,capability_profile='commander_review')
        self.assertNotEqual('exact',ir.status)
        self.assertTrue(any(n.exact and n.event=='card.discarded' for f in ir.faces for n in f.nodes))
        program=compile_best_available_card_program(self.db,c,semantic_registry=SemanticRegistry(),
            capability_registry=self.registry,capability_profile='commander_review')
        self.assertFalse(bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict_capability_ready'])


if __name__=='__main__':unittest.main()
