from __future__ import annotations

from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.semantics import SemanticRegistry
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.rules.transform_capability_shapes import source_transform_node_capabilities
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses
import test_counter_placement_events as counter_witnesses


class ActivatedTransformCompilerTests(unittest.TestCase):
    def test_fixed_self_transform_has_same_generic_owner_and_count_snapshot(self):
        registry=load_default_capability_registry();record=query_record('{6}: Transform this creature.')
        ir=compile_oracle_card(record,capability_registry=registry)
        self.assertEqual('exact',ir.status);node=ir.faces[0].nodes[0]
        self.assertEqual(('permanent.transform.face_change',),source_transform_node_capabilities(effects=node.effects,target_schema=None,mechanic_ids=node.mechanics))
        self.assertEqual('$source.zone_object',node.effects[0]['card'])
        self.assertEqual('$source.transform_count',node.effects[0]['expected_transform_count'])

    def test_targeted_and_unrepresented_transform_variants_stay_residual(self):
        registry=load_default_capability_registry()
        for text in ('{6}: Transform target creature.','{6}: Transform this creature, then take an extra turn.','{6}: Convert this creature.'):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry).status)
        effect={'op':'transform','card':'$source.zone_object','expected_transform_count':'$source.transform_count'}
        for change in ({'card':'$target.0'},{'expected_transform_count':True},{'extra':1}):
            self.assertFalse(source_transform_node_capabilities(effects=({**effect,**change},),target_schema=None,mechanic_ids=('fixed-activated-source-transform',)))
        data=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));next(c for c in data['capabilities'] if c['id']=='permanent.transform.face_change').update(status='blocked',blockers=['missing transform owner'])
        registry=CapabilityRegistry(data);registry.mark_evidence_verified('0'*64)
        self.assertNotEqual('exact',compile_oracle_card(query_record('{6}: Transform this creature.'),capability_registry=registry).status)

    def test_copied_keyword_snapshot_is_not_reinterpreted_as_original_selected_face(self):
        from types import SimpleNamespace
        from quorune.rules.current_face_keywords import current_printed_face_keywords
        base={'keywords':['Flying','Defender']}
        record=SimpleNamespace(faces=({'name':'front'},{'name':'back'}))
        for values in ({'annotations':{'copy_overrides':{}},'is_token':False,'object_kind':'card'},
                       {'annotations':{},'is_token':True,'object_kind':'token'},
                       {'annotations':{},'is_token':False,'object_kind':'spell_copy'}):
            card=SimpleNamespace(zone='battlefield',**values)
            self.assertEqual(base['keywords'],current_printed_face_keywords(None,card,record,base))


class ActivatedTransformRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'activated-transform.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/activated-self-transform-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Activated transform',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    def add(self,engine,name,**kwargs):
        card=witnesses.BoundEffectProgramRuntimeTests.add(self,engine,name,**kwargs)
        record=self.db.by_oracle_id(card.oracle_id)
        if record.faces and card.zone=='battlefield':card.active_face=str(record.faces[0]['name'])
        return card
    def ready(self,session,source,mana):
        record=self.db.by_oracle_id(source.oracle_id)
        program=compile_best_available_card_program(self.db,record,semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
        binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'],binding['blockers'])
        for ability in program.abilities:session.engine.semantics.put(ability)
        return witnesses.BoundEffectProgramRuntimeTests.ready(self,session,source,mana)
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    resolve=counter_witnesses.CounterPlacementEventRuntimeTests.resolve

    def activation(self,s,card):
        return next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+card.ref+':') and 'Transform' in a['label'])

    def test_actual_ulvenwald_transform_preserves_object_and_uses_back_mana_ability(self):
        s=self.session(326001);e=s.engine;source=self.add(e,'Ulvenwald Captive // Ulvenwald Abomination');source.acquired_control_turn_count=-1;source.counters['+1/+1']=1;source.marked_damage=1
        self.ready(s,source,{'G':3,'C':6});self.checkpoint(s);identity=source.logical_object_id;timestamp=source.zone_timestamp
        action=self.activation(s,source);before=authoritative_state_hash(e.state)
        wrong=s.act('pilot:B',{'action_id':action['id'],'pay':'auto'});self.assertFalse(wrong.ok);self.assertEqual(before,authoritative_state_hash(e.state))
        result=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.assertEqual(0,e.state.stack[-1].context.get('source_transform_count'));self.resolve(s)
        current=e.state.cards[source.object_id];self.assertEqual('Ulvenwald Abomination',current.active_face)
        self.assertEqual(identity,current.logical_object_id);self.assertEqual(timestamp,current.zone_timestamp);self.assertEqual(1,current.counters['+1/+1']);self.assertEqual(1,current.marked_damage)
        action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+current.ref+':') and 'Add {C}{C}' in a['label'])
        count=e.state.players['A'].mana_pool['C'];result=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(count+2,e.state.players['A'].mana_pool['C']);self.replay(s,load=True)

    def test_two_actual_gargoyle_activations_transform_only_once_since_stack_placement(self):
        s=self.session(326002);e=s.engine;source=self.add(e,'Thraben Gargoyle // Stonewing Antagonizer')
        self.ready(s,source,{'C':12});self.checkpoint(s)
        for _ in range(2):
            result=s.act('pilot:A',{'action_id':self.activation(s,source)['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(2,len(e.state.stack));self.resolve(s);current=e.state.cards[source.object_id]
        self.assertEqual('Stonewing Antagonizer',current.active_face);self.assertEqual(1,current.transform_count);self.assertIn('flying',e._combat_keywords(current));self.replay(s,load=True)

    def test_omitting_activation_transform_count_mutant_is_killed(self):
        with patch('quorune.rules.activation.commit.activation_transform_context',return_value={}):
            with self.assertRaises(AssertionError):self.test_actual_ulvenwald_transform_preserves_object_and_uses_back_mana_ability()

    def test_actual_gossipmonger_source_and_selected_tap_costs_transform_same_object(self):
        s=self.session(326003);e=s.engine;source=self.add(e,'Town Gossipmonger // Incited Rabble');source.acquired_control_turn_count=-1;body=self.add(e,'Generic Bound Body')
        self.ready(s,source,{});self.checkpoint(s);action=self.activation(s,source)
        before=authoritative_state_hash(e.state)
        rejected=s.act('pilot:A',{'action_id':action['id'],'cost_cards':[source.ref],'pay':'auto'});self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(e.state))
        result=s.act('pilot:A',{'action_id':action['id'],'cost_cards':[body.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertTrue(e.state.cards[source.object_id].tapped);self.assertTrue(e.state.cards[body.object_id].tapped)
        self.resolve(s);current=e.state.cards[source.object_id];self.assertEqual('Incited Rabble',current.active_face);self.assertTrue(current.tapped);self.replay(s,load=True)

    def test_actual_source_blink_before_activation_resolution_does_not_transform_new_object(self):
        s=self.session(326004);e=s.engine;source=self.add(e,'Thraben Gargoyle // Stonewing Antagonizer');blink=self.add(e,'Ephemerate',zone='hand')
        self.ready(s,source,{'W':2,'C':7});self.checkpoint(s);old_identity=source.logical_object_id
        result=s.act('pilot:A',{'action_id':self.activation(s,source)['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==blink.ref)
        result=s.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        current=e.state.cards[source.object_id];self.assertNotEqual(old_identity,current.logical_object_id);self.assertEqual('Thraben Gargoyle',current.active_face);self.assertEqual(0,current.transform_count);self.replay(s,load=True)

    def test_actual_control_change_keeps_original_activation_controller_and_source_binding(self):
        s=self.session(326005);e=s.engine;source=self.add(e,'Thraben Gargoyle // Stonewing Antagonizer',seat='B');control=self.add(e,'Word of Seizing',zone='hand')
        self.ready(s,control,{'R':2,'C':4});e.state.players['B'].mana_pool['C']=7
        e.permissions.invalidate_current();e.state.pending_decision=None;e._grant_priority('B');e.pump();self.checkpoint(s)
        action=next(a for a in s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+source.ref+':'))
        result=s.act('pilot:B',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        for _ in range(8):
            if s.pending_principals()[0]=='pilot:A':break
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==control.ref)
        result=s.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.resolve(s);self.assertEqual('A',e.state.cards[source.object_id].controller);self.assertEqual('Stonewing Antagonizer',e.state.cards[source.object_id].active_face);self.replay(s,load=True)

    def test_actual_gargoyle_cast_then_transform_uses_offered_front_and_new_flying_face(self):
        s=self.session(326006);e=s.engine;source=self.add(e,'Thraben Gargoyle // Stonewing Antagonizer',zone='hand')
        action=self.ready(s,source,{'C':9});self.checkpoint(s)
        result=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        current=e.state.cards[source.object_id];self.assertEqual('Thraben Gargoyle',current.active_face);self.assertIn('defender',e._combat_keywords(current))
        result=s.act('pilot:A',{'action_id':self.activation(s,current)['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        current=e.state.cards[source.object_id];self.assertEqual('Stonewing Antagonizer',current.active_face);self.assertIn('flying',e._combat_keywords(current));self.assertNotIn('defender',e._combat_keywords(current));self.replay(s,load=True)

    def test_omitting_selected_face_keyword_filter_mutant_is_killed(self):
        with patch('quorune.rules.current_face_keywords.current_printed_face_keywords',side_effect=lambda host,card,record,base:base.get('keywords',[])):
            with self.assertRaises(AssertionError):self.test_actual_gargoyle_cast_then_transform_uses_offered_front_and_new_flying_face()
