from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.activation_usage import commit_trigger_usage, trigger_usage_available
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card, generated_programs
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.trigger_limits import TriggerLimitSpec
from quorune.semantics import SemanticProgram
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses
import test_counter_placement_events as counter_witnesses


class LimitedEventTriggerCompilerTests(unittest.TestCase):
    def test_closed_limit_preserves_span_and_dependency(self):
        text='Whenever one or more +1/+1 counters are put on this creature, draw a card. This ability triggers only once each turn.'
        ir=compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry())
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node=ir.faces[0].nodes[0]
        self.assertEqual(TriggerLimitSpec().to_dict(),node.trigger_limit)
        self.assertEqual((0,len(text)),(node.span.start,node.span.end))
        self.assertEqual(text,node.text)
        self.assertIn('trigger.usage.once_per_turn',node.capability_dependencies)
        self.assertIn('current_ability_fragment_required',node.runtime_coverage)

    def test_unsupported_action_limits_and_departures_remain_residual(self):
        for text in (
            'Whenever you gain life, draw a card. Do this only once each turn.',
            'Whenever a creature dies, draw a card. This ability triggers only once each turn.',
            'Whenever you gain life, you win the game. This ability triggers only once each turn.',
            'Whenever you gain life, draw a card. This ability triggers only once during your turn.',
        ):
            with self.subTest(text=text):
                self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry()).status)

    def test_missing_usage_capability_keeps_trigger_residual(self):
        from quorune.rules.capabilities import CapabilityRegistry
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        raw['capabilities']=[c for c in raw['capabilities'] if c['id']!='trigger.usage.once_per_turn']
        registry=CapabilityRegistry(raw)
        registry.mark_evidence_verified(load_default_capability_registry().evidence_fingerprint)
        text='Whenever one or more +1/+1 counters are put on this creature, draw a card. This ability triggers only once each turn.'
        ir=compile_oracle_card(query_record(text),capability_registry=registry)
        self.assertNotEqual('exact',ir.status)
        self.assertTrue(any('trigger.usage.once_per_turn' in str(r.blockers) for r in ir.material_residuals))

    def test_codec_rejects_malformed_limit_and_preserves_old_shape(self):
        for value in ({'schema_version':True,'kind':'once_per_turn'}, {'schema_version':2,'kind':'once_per_turn'},
                      {'schema_version':1,'kind':'once_per_game'}, {'schema_version':1,'kind':'once_per_turn','extra':1}):
            with self.assertRaises(ValueError):TriggerLimitSpec.from_dict(value)
        old=SemanticProgram('old','old').to_dict()
        self.assertNotIn('trigger_limit',old)
        self.assertEqual(old,SemanticProgram.from_dict(old).to_dict())
        old['trigger_limit']='once_per_turn'
        with self.assertRaises(ValueError):SemanticProgram.from_dict(old)
        old['trigger_limit']=TriggerLimitSpec().to_dict();old['active_zone']='battlefield';old['event']='life.gained'
        with self.assertRaises(ValueError):SemanticProgram.from_dict(old)


class LimitedEventTriggerRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'limits.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/counter-placement-event-cards.json',ROOT/'tests/fixtures/limited-event-triggers.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Trigger limits',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    order_triggers=counter_witnesses.CounterPlacementEventRuntimeTests.order_triggers
    resolve=counter_witnesses.CounterPlacementEventRuntimeTests.resolve

    def cast(self,session,spell,*,targets=()):
        actions=session.packet(session.pending_principals()[0],full=True)['decision']['ctx']['legal']['actions']
        action=next(a for a in actions if a.get('card')==spell.ref)
        result=session.act(session.pending_principals()[0],{'action_id':action['id'],'targets':list(targets),'pay':'auto'})
        self.assertTrue(result.ok,result.summary)

    def test_actual_counter_events_draw_once_and_replay(self):
        session=self.session(301001);engine=session.engine
        observer=self.add(engine,'Dusk Legion Duelist')
        spells=[self.add(engine,'Generic Counter Event Two',zone='hand',ref='counter-'+str(i)) for i in range(2)]
        self.ready(session,spells[0],{});self.checkpoint(session)
        hand=len(session.state.players['A'].zones['hand'])
        before=authoritative_state_hash(session.state)
        denied=session.act('pilot:B',{'action_id':'cast:'+spells[0].ref,'targets':[observer.ref],'pay':'auto'})
        self.assertFalse(denied.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.cast(session,spells[0],targets=[observer.ref]);self.resolve(session)
        self.assertEqual(hand,len(session.state.players['A'].zones['hand']))
        self.cast(session,spells[1],targets=[observer.ref]);self.resolve(session)
        self.assertEqual(hand-1,len(session.state.players['A'].zones['hand']))
        self.assertEqual(4,session.state.cards[observer.object_id].counters['+1/+1'])
        resumed=self.replay(session,load=True)
        self.assertEqual(session.state.cards[observer.object_id].annotations,resumed.state.cards[observer.object_id].annotations)

    def test_source_usage_continuity_is_per_ability_and_global_turn(self):
        source=SimpleNamespace(annotations={})
        commit_trigger_usage(source,ability_id='trigger:front:1',turn_sequence=4)
        self.assertFalse(trigger_usage_available(source,ability_id='trigger:front:1',turn_sequence=4))
        self.assertTrue(trigger_usage_available(source,ability_id='trigger:front:2',turn_sequence=4))
        self.assertTrue(trigger_usage_available(source,ability_id='trigger:front:1',turn_sequence=5))
        source.annotations['once_per_turn_triggers']={'trigger:front:1':True}
        with self.assertRaises(ValueError):trigger_usage_available(source,ability_id='trigger:front:1',turn_sequence=4)

    def test_actual_blink_renews_limit_but_distinct_sources_keep_their_own_usage(self):
        session=self.session(301003);engine=session.engine
        first=self.add(engine,'Dusk Legion Duelist',ref='first-observer')
        other=self.add(engine,'Dusk Legion Duelist',ref='other-observer')
        spells=[self.add(engine,'Generic Counter Event Two',zone='hand',ref='counter-'+str(i)) for i in range(3)]
        blink=self.add(engine,'Cloudshift',zone='hand')
        self.ready(session,spells[0],{'W':1});self.checkpoint(session)
        initial=first.logical_object_id;hand=len(session.state.players['A'].zones['hand'])
        self.cast(session,spells[0],targets=[first.ref]);self.resolve(session)
        self.cast(session,spells[1],targets=[other.ref]);self.resolve(session)
        self.assertEqual(hand,len(session.state.players['A'].zones['hand']))
        self.cast(session,blink,targets=[first.ref]);self.resolve(session)
        self.assertNotEqual(initial,session.state.cards[first.object_id].logical_object_id)
        self.cast(session,spells[2],targets=[first.ref]);self.resolve(session)
        self.assertEqual(hand-1,len(session.state.players['A'].zones['hand']))
        self.replay(session)

    def test_trigger_multiplier_does_not_bypass_limit_but_stack_copy_resolves(self):
        session=self.session(301004);engine=session.engine
        observer=self.add(engine,'Dusk Legion Duelist')
        throne=self.add(engine,'Roaming Throne')
        throne.annotations['chosen_creature_type']='Vampire'
        throne.annotations['chosen_creature_type_adds_subtype']=True
        resonator=self.add(engine,'Strionic Resonator')
        spell=self.add(engine,'Generic Counter Event Two',zone='hand')
        self.ready(session,spell,{'C':2});self.checkpoint(session)
        hand=len(session.state.players['A'].zones['hand'])
        self.cast(session,spell,targets=[observer.ref])
        for _ in range(12):
            if any(i.kind=='triggered_ability' and i.source_object_id==observer.object_id for i in session.state.stack):break
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        triggers=[i for i in session.state.stack if i.kind=='triggered_ability' and i.source_object_id==observer.object_id]
        self.assertEqual(1,len(triggers))
        while session.pending_principals()[0]!='pilot:A':
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(a for a in actions if a['id'].startswith('activate:'+resonator.ref+':'))
        self.assertIn(triggers[0].ref,action['target_schema']['legal_refs'])
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[triggers[0].ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(hand+1,len(session.state.players['A'].zones['hand']))
        self.replay(session)

    def test_countered_first_trigger_still_consumes_allowance(self):
        session=self.session(301005);engine=session.engine
        observer=self.add(engine,'Dusk Legion Duelist')
        spells=[self.add(engine,'Generic Counter Event Two',zone='hand',ref='counter-'+str(i)) for i in range(2)]
        stifle=self.add(engine,'Stifle',zone='hand')
        self.ready(session,spells[0],{'U':1});self.checkpoint(session)
        self.cast(session,spells[0],targets=[observer.ref])
        for _ in range(12):
            triggers=[i for i in session.state.stack if i.kind=='triggered_ability' and i.source_object_id==observer.object_id]
            if triggers:break
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(1,len(triggers))
        while session.pending_principals()[0]!='pilot:A':
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.cast(session,stifle,targets=[triggers[0].ref]);self.resolve(session)
        before=len(session.state.players['A'].zones['hand'])
        self.cast(session,spells[1],targets=[observer.ref]);self.resolve(session)
        self.assertEqual(before-1,len(session.state.players['A'].zones['hand']))
        self.replay(session)

    def test_control_change_and_next_players_turn_use_same_object_allowance(self):
        from quorune.control_effects import change_control
        from quorune.model import TurnEntry
        session=self.session(301006);engine=session.engine
        observer=self.add(engine,'Dusk Legion Duelist')
        spells=[self.add(engine,'Generic Counter Event Two',zone='hand',ref='counter-'+str(i)) for i in range(3)]
        self.ready(session,spells[0],{});self.checkpoint(session)
        self.cast(session,spells[0],targets=[observer.ref]);self.resolve(session);self.replay(session)
        identity=observer.logical_object_id
        change_control(engine,observer.object_id,'B')
        self.checkpoint(session)
        bhand=len(session.state.players['B'].zones['hand'])
        self.cast(session,spells[1],targets=[observer.ref]);self.resolve(session)
        self.assertEqual(bhand,len(session.state.players['B'].zones['hand']))
        self.assertEqual(identity,session.state.cards[observer.object_id].logical_object_id)
        self.replay(session)
        engine._begin_turn(TurnEntry(turn_id='limit-next-turn',player='B'))
        third=session.state.cards[spells[2].object_id]
        engine.state.players['A'].zones['hand'].remove(third.object_id)
        third.owner='B';third.controller='B';engine.state.players['B'].zones['hand'].append(third.object_id)
        engine.permissions.invalidate_current();engine.state.pending_decision=None
        engine.state.phase='precombat_main';engine.state.step='main';engine._grant_priority('B');engine.pump()
        self.checkpoint(session)
        hand=len(session.state.players['B'].zones['hand'])
        self.cast(session,third,targets=[observer.ref]);self.resolve(session)
        self.assertEqual(hand,len(session.state.players['B'].zones['hand']))
        self.replay(session)

    def test_ability_loss_and_restoration_do_not_clear_consumed_use(self):
        session=self.session(301007);engine=session.engine
        observer=self.add(engine,'Dusk Legion Duelist')
        spells=[self.add(engine,'Generic Counter Event Two',zone='hand',ref='counter-'+str(i)) for i in range(3)]
        frog=self.add(engine,'Turn to Frog',zone='hand')
        self.ready(session,spells[0],{'U':1,'C':1});self.checkpoint(session)
        self.cast(session,spells[0],targets=[observer.ref]);self.resolve(session)
        usage=deepcopy(session.state.cards[observer.object_id].annotations['once_per_turn_triggers'])
        self.cast(session,frog,targets=[observer.ref]);self.resolve(session)
        before=len(session.state.players['A'].zones['hand'])
        self.cast(session,spells[1],targets=[observer.ref]);self.resolve(session)
        self.assertEqual(before-1,len(session.state.players['A'].zones['hand']))
        self.assertEqual(usage,session.state.cards[observer.object_id].annotations['once_per_turn_triggers'])
        self.replay(session)
        # Remove the temporary layer-six effect as an owner diagnostic; replay the
        # subsequent action from this explicit checkpoint, not across setup mutation.
        engine.state.continuous_effects.clear();engine.permissions.invalidate_current()
        engine.state.pending_decision=None;engine._grant_priority('A');engine.pump();self.checkpoint(session)
        before=len(session.state.players['A'].zones['hand'])
        self.cast(session,spells[2],targets=[observer.ref]);self.resolve(session)
        self.assertEqual(before-1,len(session.state.players['A'].zones['hand']))
        self.replay(session)

    def test_missing_usage_mutant_is_killed_by_second_real_event(self):
        session=self.session(301002);engine=session.engine
        observer=self.add(engine,'Dusk Legion Duelist')
        spells=[self.add(engine,'Generic Counter Event Two',zone='hand',ref='counter-'+str(i)) for i in range(2)]
        self.ready(session,spells[0],{})
        self.cast(session,spells[0],targets=[observer.ref]);self.resolve(session)
        with patch('quorune.trigger_discovery.trigger_usage_available',return_value=True),patch('quorune.trigger_discovery.commit_trigger_usage'):
            before=len(session.state.players['A'].zones['hand'])
            self.cast(session,spells[1],targets=[observer.ref]);self.resolve(session)
            with self.assertRaises(AssertionError):self.assertEqual(before-1,len(session.state.players['A'].zones['hand']))

    def test_false_intervening_condition_does_not_consume_limit(self):
        session=self.session(301008);engine=session.engine
        observer=self.add(engine,'Generic Limited Life Observer')
        gains=[self.add(engine,'Generic Limited Life Gain',zone='hand',ref='gain-'+str(i)) for i in range(2)]
        self.ready(session,gains[0],{'W':2});self.checkpoint(session)
        self.cast(session,gains[0]);self.resolve(session)
        self.assertNotIn('once_per_turn_triggers',session.state.cards[observer.object_id].annotations)
        self.replay(session)
        self.add(engine,'Generic Bound Body',ref='condition-body');self.checkpoint(session)
        before=len(session.state.players['A'].zones['hand'])
        self.cast(session,gains[1]);self.resolve(session)
        self.assertEqual(before,len(session.state.players['A'].zones['hand']))
        self.assertIn('once_per_turn_triggers',session.state.cards[observer.object_id].annotations)
        self.replay(session)


if __name__=='__main__':unittest.main()
