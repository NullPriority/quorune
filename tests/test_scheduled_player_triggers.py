from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses
import test_counter_placement_events as counter_witnesses


class ScheduledPlayerTriggerCompilerTests(unittest.TestCase):
    def test_each_player_and_opponent_results_are_nontargeted_context_bound(self):
        for text in ("At the beginning of each player's upkeep, that player loses 2 life and draws two cards.",
                     "At the beginning of each opponent's upkeep, that player loses 1 life."):
            ir=compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry())
            self.assertEqual('exact',ir.status,ir.material_residuals)
            node=ir.faces[0].nodes[0];self.assertEqual('step.begin',node.event)
            self.assertIsNone(node.target_schema)
            self.assertTrue(all(e['player']=='$context.player' for e in node.effects))
            self.assertIn('trigger.event.scheduled_player_result',node.capability_dependencies)
            self.assertEqual((0,len(text)),(node.span.start,node.span.end))

    def test_unknown_bound_results_and_monarch_schedules_remain_residual(self):
        for text in ("At the beginning of each player's upkeep, that player wins the game.",
                     "At the beginning of the monarch's end step, that player draws a card.",
                     "At the beginning of each player's upkeep, they draw a card for each creature they control."):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry()).status)

    def test_bound_player_damage_shape_requires_exact_player_and_positive_amount(self):
        from quorune.rules.scheduled_player_shapes import scheduled_player_node_capabilities
        effect={'op':'damage','source':'$source','target':'$context.player','amount':1}
        args={'effects':(effect,),'target_schema':None,'mechanic_ids':('scheduled-player-event-result',)}
        self.assertIn('damage.result.player_life',scheduled_player_node_capabilities(**args))
        for changes in ({'target':'$controller'},{'amount':True},{'source':'$target.0'},{'extra':1}):
            self.assertEqual((),scheduled_player_node_capabilities(**{**args,'effects':({**effect,**changes},)}))


class ScheduledPlayerTriggerRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'scheduled.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/scheduled-player-triggers.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Scheduled player',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    resolve=counter_witnesses.CounterPlacementEventRuntimeTests.resolve
    order_triggers=counter_witnesses.CounterPlacementEventRuntimeTests.order_triggers

    def enter_upkeep_from_untap(self,session,seat):
        engine=session.engine;engine.permissions.invalidate_current();session.state.pending_decision=None
        session.state.active_player=seat;session.state.started=True;session.state.phase='beginning';session.state.step='untap'
        session.state.phase_index=0;engine._grant_priority(seat);engine.pump()
        self.checkpoint(session)
        for _ in range(16):
            if session.state.step=='upkeep':break
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('upkeep',session.state.step)

    def test_actual_seizan_upkeep_affects_active_player_not_source_controller(self):
        session=self.session(306001);engine=session.engine
        source=self.add(engine,'Seizan, Perverter of Truth')
        hand=len(session.state.players['B'].zones['hand'])
        self.enter_upkeep_from_untap(session,'B')
        triggers=[i for i in session.state.stack if i.source_object_id==source.object_id]
        self.assertEqual(1,len(triggers));self.assertEqual('A',triggers[0].controller)
        self.assertEqual('B',triggers[0].context['player'])
        self.resolve(session)
        self.assertEqual(38,session.state.players['B'].life);self.assertEqual(40,session.state.players['A'].life)
        self.assertEqual(hand+2,len(session.state.players['B'].zones['hand']))
        private=session.state.cards[session.state.players['B'].zones['hand'][-1]].ref
        for seat in 'ACD':self.assertNotIn(private,str(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_each_opponent_upkeep_filter_excludes_source_controller(self):
        for seat,count in (('A',0),('B',1),('C',1),('D',1)):
            session=self.session(306002+ord(seat));source=self.add(session.engine,'Generic Opponent Upkeep Loss')
            self.enter_upkeep_from_untap(session,seat)
            self.assertEqual(count,sum(i.source_object_id==source.object_id for i in session.state.stack))
            self.resolve(session)
            self.assertEqual(40-count,session.state.players[seat].life);self.replay(session)

    def test_source_controller_substitution_mutant_is_killed_by_seizan(self):
        from quorune.semantic_runtime.values import resolve_semantic_value
        session=self.session(306010);source=self.add(session.engine,'Seizan, Perverter of Truth')
        self.enter_upkeep_from_untap(session,'B')
        def substitute(host,value,item):
            return item.controller if value=='$context.player' else resolve_semantic_value(host,value,item)
        with patch('quorune.semantic_runtime.values.resolve_semantic_value',side_effect=substitute):
            self.resolve(session)
            with self.assertRaises(AssertionError):self.assertEqual(38,session.state.players['B'].life)

    def test_actual_aura_upkeep_uses_recipient_controller_and_keeps_queued_player(self):
        session=self.session(306011);engine=session.engine
        victim=self.add(engine,'Generic Bound Body',seat='B')
        aura=self.add(engine,'Stab Wound',zone='hand')
        treason=self.add(engine,'Generic Scheduled Control Response',zone='hand')
        response_programs=engine.semantics.programs_for_oracle(treason.oracle_id)
        self.assertTrue(response_programs)
        self.assertTrue(all(engine.semantic_program_is_current_trusted(p) for p in response_programs))
        action=self.ready(session,aura,{'B':1,'R':1,'C':6});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[victim.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session);self.replay(session)
        self.enter_upkeep_from_untap(session,'B')
        triggers=[i for i in session.state.stack if i.source_object_id==aura.object_id]
        self.assertEqual(1,len(triggers));self.assertEqual('A',triggers[0].controller)
        while session.pending_principals()[0]!='pilot:A':
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        session.state.players['A'].mana_pool.update({'R':1,'C':2})
        engine.permissions.invalidate_current();session.state.pending_decision=None;engine._grant_priority('A');engine.pump();self.checkpoint(session)
        offered=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        matching=[a for a in offered if a.get('card')==treason.ref]
        self.assertTrue(matching,{'zone':session.state.cards[treason.object_id].zone,'actions':offered,'mana':session.state.players['A'].mana_pool,'phase':session.state.phase})
        action=matching[0]
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[victim.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual('A',session.state.cards[victim.object_id].controller)
        self.assertEqual(38,session.state.players['B'].life);self.assertEqual(40,session.state.players['A'].life)
        self.replay(session,load=True)

    def test_actual_copper_tablet_deals_damage_to_each_active_upkeep_player(self):
        session=self.session(306012);source=self.add(session.engine,'Copper Tablet')
        self.enter_upkeep_from_untap(session,'D')
        self.assertEqual(1,sum(i.source_object_id==source.object_id for i in session.state.stack))
        self.resolve(session)
        self.assertEqual(39,session.state.players['D'].life)
        self.assertEqual(40,session.state.players['A'].life)
        self.assertTrue(any(e.kind=='player_damaged' and e.target=='D' and e.amount==1 for e in session.state.turn_history.events))
        self.replay(session)


if __name__=='__main__':unittest.main()
