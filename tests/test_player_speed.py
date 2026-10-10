from __future__ import annotations
from dataclasses import replace
from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.rules.player_speed_model import player_speed,SpeedIncrease,SPEED_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses
import test_counter_placement_events as counter_witnesses


class PlayerSpeedCompilerTests(unittest.TestCase):
    def test_start_marker_and_max_speed_fixed_stats_keep_independent_closed_scope(self):
        record=replace(query_record('Start your engines!\nMax speed — This creature gets +0/+1 and has vigilance.'),keywords=('Start your engines!',))
        ir=compile_oracle_card(record,capability_registry=load_default_capability_registry());self.assertEqual('exact',ir.status,ir.material_residuals)
        nodes=ir.faces[0].nodes;self.assertTrue(any(n.handlers[0]['handler_id']=='ability.static.start-your-engines.v1' for n in nodes))
        conditional=next(n for n in nodes if n.handlers[0].get('source_condition'))
        self.assertEqual('controller_max_speed',conditional.handlers[0]['source_condition']['kind']);self.assertIn(SPEED_CAPABILITY,conditional.capability_dependencies)

    def test_max_speed_graveyard_and_mana_children_have_live_parent_condition(self):
        for body in ('{3}, Exile this card from your graveyard: Draw a card.','{T}: Add {R}{R}.','Whenever this creature attacks, draw a card.'):
            ir=compile_oracle_card(replace(query_record('Start your engines!\nMax speed — '+body),keywords=('Start your engines!',)),capability_registry=load_default_capability_registry())
            self.assertEqual('exact',ir.status,ir.material_residuals)
            parent=next(n for n in ir.faces[0].nodes if n.template_id=='max-speed-component-scope-v1')
            self.assertEqual({'kind':'controller_speed_equals','designation':'controller_speed','minimum':4},parent.handlers[0]['fragment']['value']['applicability'])
            self.assertTrue(any('current_ability_fragment_required' in n.runtime_coverage for n in ir.faces[0].nodes))

    def test_speed_shapes_and_dependency_omission_reject_boolean_negative_or_open_values(self):
        from types import SimpleNamespace
        from quorune.static_component_scope import StaticComponentApplicabilitySpec
        from quorune.ability_fragments import AbilityFragmentError
        for value in (True,-1,'4'):
            with self.assertRaises(ValueError):player_speed(SimpleNamespace(stats={'speed':value}))
        self.assertIsNone(player_speed(SimpleNamespace(stats={})));self.assertEqual(0,player_speed(SimpleNamespace(stats={'speed':0})))
        for amount in (0,True,-1):
            with self.assertRaises(ValueError):SpeedIncrease('A',amount)
        for value in (3,5,True):
            with self.assertRaises(AbilityFragmentError):StaticComponentApplicabilitySpec('controller_speed_equals','controller_speed',value)
        spec=StaticComponentApplicabilitySpec('controller_speed_equals','controller_speed',4)
        self.assertTrue(spec.applies({'controller_speed':4}));self.assertFalse(spec.applies({'controller_speed':5}))
        data=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));row=next(c for c in data['capabilities'] if c['id']==SPEED_CAPABILITY);row['status']='blocked';row['blockers']=['omitted-player-speed-owner']
        registry=CapabilityRegistry(data);registry.mark_evidence_verified('0'*64)
        self.assertNotEqual('exact',compile_oracle_card(replace(query_record('Start your engines!'),keywords=('Start your engines!',)),capability_registry=registry).status)


class PlayerSpeedRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'speed.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/player-speed-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Player speed',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    resolve=counter_witnesses.CounterPlacementEventRuntimeTests.resolve
    replay=witnesses.BoundEffectProgramRuntimeTests.replay

    def test_actual_start_and_damage_trigger_increment_only_once_own_turn_and_replay(self):
        s=self.session(321001);e=s.engine;starter=self.add(e,'Swiftwing Assailant',zone='hand');shock=self.add(e,'Shock',zone='hand',ref='first-speed-damage');second=self.add(e,'Shock',zone='hand',ref='second-speed-damage')
        action=self.ready(s,starter,{'W':1,'R':6,'C':3});self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        self.assertEqual(1,player_speed(s.state.players['A']))
        for card in (shock,second):
            action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==card.ref)
            accepted=s.act('pilot:A',{'action_id':action['id'],'targets':['B'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
            self.assertEqual(2,player_speed(s.state.players['A']))
        self.assertEqual(36,s.state.players['B'].life);self.replay(s,load=True)

    def test_omitting_speed_life_loss_trigger_mutant_is_killed(self):
        with patch('quorune.rules.player_speed_state.queue_speed_loss_trigger',return_value=None):
            with self.assertRaises(AssertionError):self.test_actual_start_and_damage_trigger_increment_only_once_own_turn_and_replay()

    def test_actual_max_speed_graveyard_ability_is_offered_only_at_four_and_replays(self):
        s=self.session(321002);e=s.engine;source=self.add(e,'Goblin Surveyor',zone='graveyard');probe=self.add(e,'Shock',zone='hand')
        self.ready(s,probe,{'R':1,'C':3})
        self.assertFalse(any(a['id'].startswith('activate:'+source.ref+':') for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']))
        e.state.players['A'].stats['speed']=4;e.permissions.invalidate_current();e.state.pending_decision=None;e._grant_priority('A');e.pump();self.checkpoint(s)
        action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+source.ref+':'))
        count=len(s.state.players['A'].zones['hand']);accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('exile',s.state.cards[source.object_id].zone);self.resolve(s);self.assertEqual(count+1,len(s.state.players['A'].zones['hand']));self.replay(s,load=True)

    def test_actual_max_speed_mana_mode_and_fixed_keyword_follow_controller_speed(self):
        s=self.session(321003);e=s.engine;land=self.add(e,'Muraganda Raceway');body=self.add(e,'Swiftwing Assailant');probe=self.add(e,'Shock',zone='hand')
        self.ready(s,probe,{'R':1});self.assertEqual(1,player_speed(s.state.players['A']))
        self.assertNotIn('vigilance',e._combat_keywords(body))
        from quorune.rules.activation.query import activated_abilities
        self.assertEqual(1,len(activated_abilities(e,land)))
        e.state.players['A'].stats['speed']=4;e.permissions.invalidate_current();e.state.pending_decision=None;e._grant_priority('A');e.pump();self.checkpoint(s)
        self.assertIn('vigilance',e._combat_keywords(body));self.assertEqual(2,len(activated_abilities(e,land)))
        actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(a for a in actions if a['id'].startswith('activate:'+land.ref+':') and '{C}{C}' in str(a))
        before=e.state.players['A'].mana_pool['C'];accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(before+2,e.state.players['A'].mana_pool['C']);self.replay(s,load=True)

    def test_actual_source_bounce_keeps_speed_and_later_damage_still_triggers(self):
        s=self.session(321004);e=s.engine;source=self.add(e,'Swiftwing Assailant');bounce=self.add(e,'Unsummon',zone='hand');damage=self.add(e,'Shock',zone='hand')
        action=self.ready(s,bounce,{'U':1,'R':1});self.assertEqual(1,player_speed(s.state.players['A']));self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        self.assertEqual('hand',s.state.cards[source.object_id].zone);self.assertEqual(1,player_speed(s.state.players['A']))
        action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==damage.ref)
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':['B'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        self.assertEqual(2,player_speed(s.state.players['A']));self.replay(s)

    def test_countered_inherent_trigger_consumes_turn_usage_and_pending_reload_replays(self):
        from quorune.session import CommanderSession
        s=self.session(321005);e=s.engine;self.add(e,'Swiftwing Assailant');first=self.add(e,'Shock',zone='hand',ref='first-countered-speed-damage');second=self.add(e,'Shock',zone='hand',ref='second-countered-speed-damage');counter=self.add(e,'Stifle',seat='B',zone='hand')
        action=self.ready(s,first,{'R':2});s.state.players['B'].mana_pool['U']=2;self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':['C'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        for _ in range(24):
            speed_item=next((i for i in s.state.stack if i.semantic_key=='builtin:player-speed'),None)
            if speed_item is not None and s.state.pending_decision.kind=='priority':break
            accepted=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertIsNotNone(speed_item);self.assertIsNone(speed_item.source_object_id);self.assertEqual(1,player_speed(s.state.players['A']))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'inherent';s.save(path);s=CommanderSession.load(self.db,path)
        for _ in range(8):
            if s.pending_principals()[0]=='pilot:B':break
            accepted=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(accepted.ok,accepted.summary)
        action=next(a for a in s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==counter.ref)
        accepted=s.act('pilot:B',{'action_id':action['id'],'targets':[speed_item.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        self.assertEqual(1,player_speed(s.state.players['A']))
        for _ in range(8):
            if s.pending_principals()[0]=='pilot:A':break
            accepted=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(accepted.ok,accepted.summary)
        action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==second.ref)
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':['C'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        self.assertEqual(1,player_speed(s.state.players['A']));self.replay(s,load=True)

    def test_other_turn_and_self_loss_do_not_trigger_speed(self):
        s=self.session(321006);e=s.engine;self.add(e,'Swiftwing Assailant');self.add(e,'Swiftwing Assailant',seat='B',ref='b-speed-starter')
        spell=self.add(e,'Shock',zone='hand');action=self.ready(s,spell,{'R':1});self.checkpoint(s)
        self.assertEqual(1,player_speed(s.state.players['B']))
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':['A'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        self.assertEqual(1,player_speed(s.state.players['A']));self.assertEqual(1,player_speed(s.state.players['B']));self.replay(s)

    def test_actual_max_speed_damage_replacement_is_inactive_below_four(self):
        for speed in (3,4):
            s=self.session(321007+speed);e=s.engine;self.add(e,'Far Fortune, End Boss');spell=self.add(e,'Shock',zone='hand')
            action=self.ready(s,spell,{'R':1});s.state.players['A'].stats['speed']=speed;e.permissions.invalidate_current();e.state.pending_decision=None;e._grant_priority('A');e.pump();self.checkpoint(s)
            action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==spell.ref)
            accepted=s.act('pilot:A',{'action_id':action['id'],'targets':['B'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
            self.assertEqual(38 if speed==3 else 37,s.state.players['B'].life);self.assertEqual(4,player_speed(s.state.players['A']));self.replay(s)

    def test_actual_simultaneous_opponent_losses_produce_one_inherent_trigger(self):
        s=self.session(321012);e=s.engine;self.add(e,'Swiftwing Assailant');self.add(e,'Syndic of Tithes');target=self.add(e,'Generic Bound Body',seat='B');spell=self.add(e,'Unsummon',zone='hand')
        action=self.ready(s,spell,{'U':1,'W':1});self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        accepted=s.act('pilot:A',{'action_id':'choose','pay':'W'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        self.assertEqual((39,39,39),tuple(s.state.players[p].life for p in 'BCD'))
        self.assertEqual(2,player_speed(s.state.players['A']));self.replay(s,load=True)

    def test_actual_control_change_starts_new_controller_without_moving_old_speed(self):
        s=self.session(321013);e=s.engine;source=self.add(e,'Swiftwing Assailant',seat='B');spell=self.add(e,'Act of Treason',zone='hand')
        action=self.ready(s,spell,{'R':3,'C':2});self.assertEqual(1,player_speed(s.state.players['B']));self.assertIsNone(player_speed(s.state.players['A']));self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        self.assertEqual('A',s.state.cards[source.object_id].controller);self.assertEqual(1,player_speed(s.state.players['A']));self.assertEqual(1,player_speed(s.state.players['B']));self.replay(s,load=True)

    def test_new_own_turn_can_trigger_again_while_absent_speed_still_cannot(self):
        from quorune.rules.player_speed_state import queue_speed_loss_trigger
        from quorune.life_state import LifeTransition
        s=self.session(321014);e=s.engine;e.state.players['A'].stats['speed']=2;e.state.players['A'].stats['speed_trigger_turn']=e.state.turn_sequence
        before=len(e.state.pending_trigger_batches)
        queue_speed_loss_trigger(e,(LifeTransition('B',-1,40,39),));self.assertEqual(before,len(e.state.pending_trigger_batches))
        e.state.turn_sequence+=1;queue_speed_loss_trigger(e,(LifeTransition('B',-1,40,39),));self.assertEqual(before+1,len(e.state.pending_trigger_batches))
        item=e.state.pending_trigger_batches[-1].groups[0].items[0];self.assertIsNone(item.source_object_id);self.assertEqual('A',item.controller)
        e.state.players['A'].stats.pop('speed');e.state.turn_sequence+=1
        queue_speed_loss_trigger(e,(LifeTransition('B',-1,40,39),));self.assertEqual(before+1,len(e.state.pending_trigger_batches))

    def test_zero_resolved_loss_does_not_trigger_and_direct_increase_creates_speed(self):
        from quorune.rules.player_speed_state import queue_speed_loss_trigger,commit_speed_increase
        from quorune.life_state import LifeTransition
        s=self.session(321015);e=s.engine;e.state.players['A'].stats['speed']=1
        queue_speed_loss_trigger(e,(LifeTransition('B',-1,40,40),));self.assertFalse(e.state.pending_trigger_batches)
        e.state.players['A'].stats.pop('speed');self.assertEqual(1,commit_speed_increase(e,SpeedIncrease('A',1)))
        self.assertEqual(1,player_speed(e.state.players['A']))
