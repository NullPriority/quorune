from __future__ import annotations

from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.engine import TURN_STEPS
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry,CapabilityRegistry
from quorune.enlist_model import EnlistSpec
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses
import test_counter_placement_events as counter_witnesses


class EnlistCompilerTests(unittest.TestCase):
    def test_enlist_has_separate_optional_cost_marker_and_reflexive_power(self):
        registry=load_default_capability_registry();ir=compile_oracle_card(replace(query_record('Enlist'),keywords=('Enlist',)),capability_registry=registry)
        self.assertEqual('exact',ir.status);marker,trigger=ir.faces[0].nodes
        self.assertEqual('printed-enlist-attack-cost-v1',marker.template_id)
        self.assertEqual('printed-enlist-reflexive-growth-v1',trigger.template_id)
        self.assertEqual(1,trigger.effects[0]['power']['schema_version'])
        self.assertEqual('event_object_characteristic',trigger.effects[0]['power']['origin'])
        repeated=compile_oracle_card(replace(query_record('Enlist, enlist'),keywords=('Enlist',)),capability_registry=registry)
        self.assertEqual(4,len(repeated.faces[0].nodes))

    def test_enlist_open_grammar_and_boolean_instance_reject(self):
        registry=load_default_capability_registry()
        for text in ('Enlist 2','Enlist with haste'):
            self.assertNotEqual('exact',compile_oracle_card(replace(query_record(text),keywords=('Enlist',)),capability_registry=registry).status)
        with self.assertRaises(ValueError):EnlistSpec(True)
        value=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));next(c for c in value['capabilities'] if c['id']=='combat.attack.enlist').update(status='blocked',blockers=['missing optional attack cost'])
        blocked=CapabilityRegistry(value);blocked.mark_evidence_verified('0'*64)
        self.assertNotEqual('exact',compile_oracle_card(replace(query_record('Enlist'),keywords=('Enlist',)),capability_registry=blocked).status)


class ExertAttackCompilerTests(unittest.TestCase):
    def test_exert_as_attack_permission_and_result_are_linked_typed_nodes(self):
        from test_bound_effect_programs import record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        text = "You may exert this creature as it attacks. When you do, it gets +1/+2 until end of turn."
        ir = compile_oracle_card(record(text, 'Creature — Human Warrior'), capability_registry=load_default_capability_registry(), capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        permission = next(n for n in ir.faces[0].nodes if n.kind == 'static_ability')
        result = next(n for n in ir.faces[0].nodes if n.kind == 'triggered_ability')
        self.assertEqual('permanent.exerted',result.event)
        self.assertEqual('$source.zone_object',result.effects[0]['card'])
        self.assertEqual((1,2),(result.effects[0]['power'],result.effects[0]['toughness']))
        self.assertEqual('exert_as_attacks',permission.handlers[0]['fragment']['kind'])
        self.assertIn('combat.attack.exert',permission.capability_dependencies)
        self.assertIn('untap.step.exert_player',result.capability_dependencies)

    def test_exert_contract_rejects_open_bodies_malformed_values_and_missing_duration_owner(self):
        from quorune.exert_model import ExertAttackSpec
        from quorune.semantic_runtime.exert import ExertAttackFragmentHandler
        from quorune.ability_fragments import ability_fragment_to_dict
        from test_bound_effect_programs import record
        for text in ('You may exert this creature as it blocks. When you do, draw a card.',
                     'You may exert this creature as it attacks. When you do, draw a card and investigate a mystery.'):
            self.assertNotEqual('exact',compile_oracle_card(record(text,'Creature'),capability_registry=load_default_capability_registry()).status)
        handler=ExertAttackFragmentHandler()
        value={'handler_id':handler.handler_id,'schema_version':1,'event':handler.event,
               'fragment':ability_fragment_to_dict(ExertAttackSpec('constructed:trigger:front:n1'))}
        self.assertEqual('constructed:trigger:front:n1',handler.lower(value,object())[0].result_semantic_key)
        for wrong in ({**value,'unknown':True},{**value,'schema_version':True},
                      {**value,'fragment':{'kind':'exert_as_attacks','spec':{'schema_version':1,'result_semantic_key':''}}}):
            with self.assertRaises(ValueError):handler.lower(wrong,object())
        data=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(cap for cap in data['capabilities'] if cap['id']=='untap.step.exert_player').update(status='blocked',blockers=['missing acting-player duration'])
        registry=CapabilityRegistry(data);registry.mark_evidence_verified('0'*64)
        text='You may exert this creature as it attacks. When you do, draw a card.'
        self.assertNotEqual('exact',compile_oracle_card(record(text,'Creature'),capability_registry=registry).status)


class ExertAttackRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'exert.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/exert-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Exert',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    resolve=witnesses.BoundEffectProgramRuntimeTests.resolve

    def attacks(self,s):
        e=s.engine;e.permissions.invalidate_current();e.state.pending_decision=None;e.state.priority_player=None;e.state.priority_passes=[]
        e.state.started=True;e.state.active_player='A';e.state.phase='combat';e.state.step='declare_attackers'
        e.state.phase_index=TURN_STEPS.index(('combat','declare_attackers'));e._issue_attackers()
        self.assertEqual('combat.attackers',e.state.pending_decision.kind)

    def exert_key(self,s,source):
        rows=s.packet('pilot:A',full=True)['decision']['ctx']['exert']
        return next(row for row in rows if row['value']==source.ref)['options'][0]['value']

    def test_original_entangler_optional_attack_cost_linked_result_and_pending_replay(self):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        s=self.session(358001);e=s.engine;source=self.add(e,'Nef-Crop Entangler');source.acquired_control_turn_count=-1
        card_program=compile_best_available_card_program(self.db,self.db.lookup('Nef-Crop Entangler'),
            semantic_registry=e.semantics,capability_registry=self.registry,capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(card_program,capability_registry=self.registry,profile='commander_review')['strict_capability_ready'])
        self.attacks(s);key=self.exert_key(s,source);self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'},'exert':{source.ref:[key]}})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertTrue(source.tapped);self.assertEqual(['A'],source.annotations.get('exert_next_untap_players'))
        self.assertEqual(1,len(e.state.stack));self.assertEqual('A',e.state.stack[0].controller)
        resumed=self.replay(s,load=True);self.resolve(resumed)
        data=resumed.engine._effective_card_data(resumed.state.cards[source.object_id])
        self.assertEqual((3,3),(int(data['power']),int(data['toughness'])));self.replay(resumed,load=True)

    def test_original_naga_draw_uses_locked_controller_private_projection_and_replay(self):
        s=self.session(358002);e=s.engine;source=self.add(e,'Watchful Naga');source.acquired_control_turn_count=-1
        self.attacks(s);key=self.exert_key(s,source);self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'},'exert':{source.ref:[key]}})
        self.assertTrue(accepted.ok,accepted.summary);self.replay(s,load=True)
        # The pending result retains its controller while the source changes control.
        e.change_control(source.object_id,'C',reason='locked Exert result witness');self.checkpoint(s)
        hands={seat:len(e.state.players[seat].zones['hand']) for seat in 'ABCD'};self.resolve(s)
        self.assertEqual(hands['A']+1,len(e.state.players['A'].zones['hand']))
        for seat in 'BCD':self.assertEqual(hands[seat],len(e.state.players[seat].zones['hand']))
        projection=json.dumps(s.projector._snapshot('pilot:D'),sort_keys=True)
        for seat in 'ABC':
            for object_id in e.state.players[seat].zones['hand']:self.assertNotIn(e.state.cards[object_id].ref,projection)
        self.assertNotIn(source.object_id,projection);self.replay(s,load=True)

    def test_exert_invalid_authority_and_cost_instances_roll_back_and_decline_is_legal(self):
        s=self.session(358003);e=s.engine;source=self.add(e,'Gust Walker');source.acquired_control_turn_count=-1
        other=self.add(e,'Generic Bound Body');other.acquired_control_turn_count=-1
        self.attacks(s);key=self.exert_key(s,source);self.checkpoint(s)
        for principal,selection in [('pilot:B',{source.ref:[key]}),('pilot:A',{other.ref:[key]}),
                                    ('pilot:A',{source.ref:[key,key]}),('pilot:A',{source.ref:['unknown']}),
                                    ('pilot:A',{source.ref:True})]:
            before=authoritative_state_hash(e.state)
            denied=s.act(principal,{'action_id':'attack','attackers':{source.ref:'B'},'exert':selection})
            self.assertFalse(denied.ok);self.assertEqual(before,authoritative_state_hash(e.state))
        accepted=s.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'}})
        self.assertTrue(accepted.ok,accepted.summary);self.assertNotIn('exert_next_untap_players',source.annotations)
        self.assertFalse(e.state.stack);self.replay(s,load=True)

    def test_exert_duration_locks_player_through_control_change_repeated_costs_and_stun(self):
        from quorune.semantic_runtime.untap_steps import current_untap_step_plan
        from quorune.untap_step_coordination import commit_untap_step
        s=self.session(358004);e=s.engine;source=self.add(e,'Gust Walker');source.acquired_control_turn_count=-1
        self.attacks(s);key=self.exert_key(s,source);self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'},'exert':{source.ref:[key]}})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(s);self.replay(s,load=True)
        e.change_control(source.object_id,'B',reason='Exert duration distinction')
        def step(seat):
            e.state.active_player=seat;e.state.phase='beginning';e.state.step='untap'
            e.state.phase_index=TURN_STEPS.index(('beginning','untap'))
            commit_untap_step(e,current_untap_step_plan(e,seat),(),selected_optional_ids=())
        step('B');self.assertFalse(source.tapped);self.assertEqual(['A'],source.annotations['exert_next_untap_players'])
        source.tapped=True;step('A');self.assertTrue(source.tapped);self.assertNotIn('exert_next_untap_players',source.annotations)
        e.change_control(source.object_id,'A',reason='return to Exert player');source.tapped=False
        source.acquired_control_turn_count=-1
        for _ in range(2):
            e._finish_combat_phase();source.tapped=False;self.attacks(s)
            key=self.exert_key(s,source)
            accepted=s.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'},'exert':{source.ref:[key]}})
            self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        source.counters['stun']=1;step('A')
        self.assertTrue(source.tapped);self.assertEqual(1,source.counters['stun']);self.assertNotIn('exert_next_untap_players',source.annotations)
        step('A');self.assertTrue(source.tapped);self.assertNotIn('stun',source.counters)
        step('A');self.assertFalse(source.tapped)

    def test_original_crasher_linked_target_choice_has_authority_and_pending_replay(self):
        s=self.session(358005);e=s.engine;source=self.add(e,'Ahn-Crop Crasher')
        target=self.add(e,'Generic Bound Body',seat='B');self.attacks(s)
        key=self.exert_key(s,source);self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'},'exert':{source.ref:key}})
        self.assertTrue(accepted.ok,accepted.summary)
        decision=self.resolve(s);self.assertEqual('semantic.target',decision.kind)
        self.replay(s,load=True);before=authoritative_state_hash(e.state)
        denied=s.act('pilot:C',{'action_id':'choose','targets':[target.ref]})
        self.assertFalse(denied.ok);self.assertEqual(before,authoritative_state_hash(e.state))
        accepted=s.act('pilot:A',{'action_id':'choose','targets':[target.ref]})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        self.assertFalse(e._can_block(source,target)[0])
        self.replay(s,load=True)

    def test_actual_exert_source_blink_removes_duration_and_invalidates_original_self_result(self):
        s=self.session(358006);e=s.engine;source=self.add(e,'Nef-Crop Entangler');source.acquired_control_turn_count=-1
        blink=self.add(e,'Ephemerate',zone='hand');e.state.players['A'].mana_pool['W']=1
        self.attacks(s);key=self.exert_key(s,source);self.checkpoint(s);identity=source.logical_object_id
        accepted=s.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'},'exert':{source.ref:[key]}})
        self.assertTrue(accepted.ok,accepted.summary)
        action=next(action for action in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if action.get('card')==blink.ref)
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        current=e.state.cards[source.object_id];self.assertNotEqual(identity,current.logical_object_id)
        self.assertNotIn('exert_next_untap_players',current.annotations)
        data=e._effective_card_data(current);self.assertEqual((2,1),(int(data['power']),int(data['toughness'])))
        self.replay(s,load=True)

    def test_current_ability_loss_removes_exert_offer_but_paid_result_and_duration_persist(self):
        from quorune.continuous_effects import ContinuousEffect, ContinuousEffectDuration, ContinuousEffectOrigin, ContinuousObjectIdentity, ContinuousOperation, Layer
        from quorune.continuous_effect_state import commit_continuous_effect
        from quorune.object_predicate import ObjectQuerySpec
        def remove(e,card):
            commit_continuous_effect(e.state,ContinuousEffect(
                effect_id='fixture:remove-exert-ability',source_id='fixture:remove-exert-owner',
                layer=Layer.ABILITY,sublayer='6',timestamp=e._next_zone_timestamp(),
                operations=(ContinuousOperation('remove_all_abilities'),),
                origin=ContinuousEffectOrigin.RESOLUTION,duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                applies=ObjectQuerySpec(zones=('battlefield',)),
                locked_objects=(ContinuousObjectIdentity(object_id=card.object_id,logical_object_id=card.logical_object_id),)))
        s=self.session(358007);e=s.engine;source=self.add(e,'Nef-Crop Entangler');source.acquired_control_turn_count=-1
        remove(e,source);self.attacks(s)
        self.assertFalse(s.packet('pilot:A',full=True)['decision']['ctx'].get('exert'))
        self.checkpoint(s);before=authoritative_state_hash(e.state)
        denied=s.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'},'exert':{source.ref:['unavailable']}})
        self.assertFalse(denied.ok);self.assertEqual(before,authoritative_state_hash(e.state))
        s=self.session(358008);e=s.engine;source=self.add(e,'Nef-Crop Entangler');source.acquired_control_turn_count=-1
        self.attacks(s);key=self.exert_key(s,source);self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'},'exert':{source.ref:[key]}})
        self.assertTrue(accepted.ok,accepted.summary);self.replay(s,load=True)
        remove(e,source);self.checkpoint(s);self.resolve(s)
        self.assertEqual(3,int(e._effective_card_data(source)['power']))
        self.assertEqual(['A'],source.annotations['exert_next_untap_players']);self.replay(s,load=True)

    def test_exert_producer_omission_mutant_is_killed(self):
        with patch('quorune.engine.commit_exert_costs',return_value=None):
            with self.assertRaises(AssertionError):
                self.test_original_entangler_optional_attack_cost_linked_result_and_pending_replay()

    def test_distinct_exert_paragraphs_publish_only_paid_linked_results(self):
        for index in (0,1):
            s=self.session(358010+index);e=s.engine;source=self.add(e,'Generic Exert Dual Result');source.acquired_control_turn_count=-1
            self.attacks(s);row=next(row for row in s.packet('pilot:A',full=True)['decision']['ctx']['exert'] if row['value']==source.ref)
            self.assertEqual(2,row['maximum']);keys=[option['value'] for option in row['options']]
            self.assertEqual(2,len(set(keys)));hands=len(e.state.players['A'].zones['hand']);self.checkpoint(s)
            accepted=s.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'},'exert':{source.ref:[keys[index]]}})
            self.assertTrue(accepted.ok,accepted.summary);self.assertEqual(1,len(e.state.stack))
            self.assertEqual(keys[index],e.state.stack[0].semantic_key);self.resolve(s)
            self.assertEqual(hands+(1 if index==0 else 0),len(e.state.players['A'].zones['hand']))
            data=e._effective_card_data(source);self.assertEqual(2 if index==0 else 3,int(data['power']))
            self.replay(s,load=True)

    def test_exert_duration_validation_is_atomic_and_preserves_other_players(self):
        from quorune.tap_state import expire_exert_untap_restrictions,TapStateError
        s=self.session(358012);e=s.engine;first=self.add(e,'Gust Walker');second=self.add(e,'Nef-Crop Entangler')
        first.annotations['exert_next_untap_players']=['A','C'];second.annotations['exert_next_untap_players']=True
        before=authoritative_state_hash(e.state)
        with self.assertRaises(TapStateError):expire_exert_untap_restrictions(e,'A')
        self.assertEqual(before,authoritative_state_hash(e.state))
        second.annotations['exert_next_untap_players']=['A'];blocked=expire_exert_untap_restrictions(e,'A')
        self.assertEqual({first.object_id,second.object_id},set(blocked))
        self.assertEqual(['C'],first.annotations['exert_next_untap_players']);self.assertNotIn('exert_next_untap_players',second.annotations)


class EnlistRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'enlist.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/enlist-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry();cls.deck=DeckDefinition('Enlist',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    resolve=counter_witnesses.CounterPlacementEventRuntimeTests.resolve

    def attacks(self,s):
        e=s.engine;e.permissions.invalidate_current();e.state.pending_decision=None;e.state.priority_player=None;e.state.priority_passes=[]
        e.state.started=True;e.state.active_player='A';e.state.phase='combat';e.state.step='declare_attackers';e.state.phase_index=TURN_STEPS.index(('combat','declare_attackers'));e._issue_attackers()
        self.assertEqual('combat.attackers',e.state.pending_decision.kind)

    def test_actual_faithbonder_enlists_eligible_body_then_growth_and_replay(self):
        s=self.session(327001);e=s.engine;source=self.add(e,'Benalish Faithbonder');source.acquired_control_turn_count=-1
        body=self.add(e,'Generic Bound Body');body.acquired_control_turn_count=-1
        self.attacks(s);self.checkpoint(s)
        packet=s.packet('pilot:A',full=True)['decision'];self.assertTrue(packet['ctx']['enlist'])
        result=s.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'},'enlist':{source.ref:body.ref}});self.assertTrue(result.ok,result.summary)
        self.assertFalse(e.state.cards[source.object_id].tapped);self.assertTrue(e.state.cards[body.object_id].tapped);self.assertEqual(1,len(e.state.stack))
        self.resolve(s);data=e._effective_card_data(e.state.cards[source.object_id]);self.assertEqual(3,int(data['power']));self.assertEqual(3,int(data['toughness']));self.replay(s,load=True)

    def test_enlist_wrong_principal_self_attacking_or_sick_source_rejects_atomically(self):
        s=self.session(327002);e=s.engine;source=self.add(e,'Benalish Faithbonder');source.acquired_control_turn_count=-1
        body=self.add(e,'Generic Bound Body');body.acquired_control_turn_count=-1;sick=self.add(e,'Generic Bound Body',ref='sick-enlist-body');sick.acquired_control_turn_count=e.state.players['A'].turns_begun
        self.attacks(s);self.checkpoint(s)
        for principal,attackers,enlisted in [('pilot:B',{source.ref:'B'},body.ref),('pilot:A',{source.ref:'B'},source.ref),('pilot:A',{source.ref:'B',body.ref:'B'},body.ref),('pilot:A',{source.ref:'B'},sick.ref)]:
            before=authoritative_state_hash(e.state);result=s.act(principal,{'action_id':'attack','attackers':attackers,'enlist':{source.ref:enlisted}})
            self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(e.state))
        result=s.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'}});self.assertTrue(result.ok,result.summary)
        self.assertFalse(e.state.cards[body.object_id].tapped);self.assertFalse(e.state.stack);self.replay(s,load=True)

    def test_omitting_enlist_trigger_mutant_is_killed(self):
        with patch('quorune.engine.enlist_trigger_items',return_value=[]):
            with self.assertRaises(AssertionError):self.test_actual_faithbonder_enlists_eligible_body_then_growth_and_replay()

    def test_actual_enlisted_power_is_live_at_resolution_and_departure_uses_lki(self):
        for leave in (False,True):
            s=self.session(327003 if not leave else 327004);e=s.engine;source=self.add(e,'Benalish Faithbonder');source.acquired_control_turn_count=-1
            body=self.add(e,'Generic Bound Body');body.acquired_control_turn_count=-1;growth=self.add(e,'Giant Growth',zone='hand');bounce=self.add(e,'Unsummon',zone='hand')
            e.state.players['A'].mana_pool.update({'G':2,'U':2});self.attacks(s);self.checkpoint(s)
            result=s.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'},'enlist':{source.ref:body.ref}});self.assertTrue(result.ok,result.summary)
            action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==growth.ref)
            result=s.act('pilot:A',{'action_id':action['id'],'targets':[body.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
            while any(item.card_object_id==growth.object_id for item in e.state.stack):
                result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
            if leave:
                action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==bounce.ref)
                result=s.act('pilot:A',{'action_id':action['id'],'targets':[body.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
            self.resolve(s);data=e._effective_card_data(e.state.cards[source.object_id]);self.assertEqual(6,int(data['power']))
            if leave:self.assertEqual('hand',e.state.cards[body.object_id].zone)
            self.replay(s,load=True)

    def test_actual_attacker_blink_invalidates_reflexive_growth_and_replays(self):
        s=self.session(327005);e=s.engine;source=self.add(e,'Benalish Faithbonder');source.acquired_control_turn_count=-1;body=self.add(e,'Generic Bound Body');body.acquired_control_turn_count=-1;blink=self.add(e,'Ephemerate',zone='hand')
        e.state.players['A'].mana_pool['W']=2;self.attacks(s);self.checkpoint(s)
        result=s.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'},'enlist':{source.ref:body.ref}});self.assertTrue(result.ok,result.summary)
        action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==blink.ref)
        result=s.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        self.assertEqual(1,int(e._effective_card_data(e.state.cards[source.object_id])['power']));self.replay(s,load=True)

    def test_two_actual_attackers_cannot_enlist_same_donor_and_haste_allows_new_donor(self):
        s=self.session(327006);e=s.engine;one=self.add(e,'Benalish Faithbonder',ref='enlist-first');two=self.add(e,'Coalition Warbrute');one.acquired_control_turn_count=-1;two.acquired_control_turn_count=-1
        donor=self.add(e,'Generic Bound Body');donor.acquired_control_turn_count=e.state.players['A'].turns_begun;donor.temporary_keywords.append('Haste')
        self.attacks(s);self.checkpoint(s);before=authoritative_state_hash(e.state)
        result=s.act('pilot:A',{'action_id':'attack','attackers':{one.ref:'B',two.ref:'B'},'enlist':{one.ref:donor.ref,two.ref:donor.ref}})
        self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(e.state))
        result=s.act('pilot:A',{'action_id':'attack','attackers':{one.ref:'B',two.ref:'B'},'enlist':{one.ref:donor.ref}});self.assertTrue(result.ok,result.summary);self.resolve(s)
        self.assertEqual(3,int(e._effective_card_data(e.state.cards[one.object_id])['power']));self.assertEqual(3,int(e._effective_card_data(e.state.cards[two.object_id])['power']));self.replay(s,load=True)

    def test_model_negative_enlisted_power_uses_zero_result(self):
        from quorune.scalar_effect_amount_model import ScalarEffectAmountSpec,ScalarAmountOrigin,SCALAR_REFERENCE_CONTEXT
        from quorune.scalar_effect_amounts import resolve_scalar_effect_amount
        from quorune.model import StackItem
        s=self.session(327007);e=s.engine;donor=self.add(e,'Generic Bound Body')
        snapshot={'ref':donor.ref,'object_id':donor.object_id,'logical_object_id':donor.logical_object_id,'zone':'battlefield','power':-1,'toughness':3,'mana_value':1}
        item=StackItem('model','model','triggered_ability','A','Signed scalar',context={SCALAR_REFERENCE_CONTEXT:{ScalarAmountOrigin.EVENT_OBJECT.value:snapshot}})
        signed=ScalarEffectAmountSpec(ScalarAmountOrigin.EVENT_OBJECT,characteristic='power')
        with patch('quorune.scalar_effect_amounts._snapshot',return_value=snapshot):
            self.assertEqual(0,resolve_scalar_effect_amount(e,signed.to_dict(),item))

    def test_actual_negative_power_donor_gives_zero_boost_and_replays(self):
        s=self.session(327008);e=s.engine;source=self.add(e,'Benalish Faithbonder');source.acquired_control_turn_count=-1;donor=self.add(e,'Generic Bound Body');donor.acquired_control_turn_count=-1;spell=self.add(e,'Disfigure',zone='hand',ref='negative-first');second=self.add(e,'Disfigure',zone='hand',ref='negative-second')
        e.state.players['A'].mana_pool['B']=2
        # Use an actual printed debuff before constructing the attack boundary.
        e.permissions.invalidate_current();e.state.pending_decision=None;e.state.started=True;e.state.active_player='A';e.state.phase='precombat_main';e.state.step='main';e._grant_priority('A');e.pump();self.checkpoint(s)
        for card in (spell,second):
            action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==card.ref)
            result=s.act('pilot:A',{'action_id':action['id'],'targets':[donor.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        self.assertEqual(-2,int(e._effective_card_data(e.state.cards[donor.object_id])['power']));self.replay(s,load=True)
        self.attacks(s);self.checkpoint(s)
        result=s.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'},'enlist':{source.ref:donor.ref}});self.assertTrue(result.ok,result.summary);self.resolve(s)
        self.assertEqual(1,int(e._effective_card_data(e.state.cards[source.object_id])['power']));self.replay(s,load=True)

    def test_actual_pending_enlist_trigger_save_load_keeps_paid_tap_and_original_power_reference(self):
        s=self.session(327009);e=s.engine;source=self.add(e,'Benalish Faithbonder');source.acquired_control_turn_count=-1;donor=self.add(e,'Generic Bound Body');donor.acquired_control_turn_count=-1
        self.attacks(s);self.checkpoint(s)
        result=s.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'},'enlist':{source.ref:donor.ref}});self.assertTrue(result.ok,result.summary)
        resumed=self.replay(s,load=True);self.assertTrue(resumed.state.cards[donor.object_id].tapped)
        self.assertEqual(1,int(resumed.engine._effective_card_data(resumed.state.cards[source.object_id])['power']))
        self.resolve(resumed);self.assertEqual(3,int(resumed.engine._effective_card_data(resumed.state.cards[source.object_id])['power']));self.replay(resumed,load=True)
