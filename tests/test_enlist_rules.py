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
