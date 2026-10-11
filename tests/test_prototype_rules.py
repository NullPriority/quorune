from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.prototype_model import PrototypeSpec,PROTOTYPE_ANNOTATION
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.rules.prototype import compiled_prototype_spec
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses
import test_counter_placement_events as counter_witnesses


class PrototypeCompilerTests(unittest.TestCase):
    def test_fixed_prototype_compiles_closed_alternate_characteristics_and_full_admission(self):
        registry=load_default_capability_registry()
        record=replace(query_record('Prototype {1}{B} — 1/1\nDeathtouch'),keywords=('Prototype','Deathtouch'))
        ir=compile_oracle_card(record,capability_registry=registry)
        self.assertEqual('exact',ir.status)
        node=ir.faces[0].nodes[0]
        self.assertEqual('all',node.active_zone)
        self.assertEqual({'mana_cost':'{1}{B}','mana_value':2,'colors':['B'],'power':'1','toughness':'1'},PrototypeSpec.from_dict(node.handlers[0]['prototype']).characteristic_values())
        self.assertIs(node.handlers[0]['requires_complete_card_program'],True)

    def test_prototype_open_grammar_boolean_and_missing_dependency_fail_closed(self):
        registry=load_default_capability_registry()
        for text in ('Prototype {X}{B} — 1/1','Prototype {1}{B} — */*','Prototype {1}{B} — 1/1 with haste'):
            self.assertNotEqual('exact',compile_oracle_card(replace(query_record(text),keywords=('Prototype',)),capability_registry=registry).status)
        for values in ({'mana_cost':'{X}'},{'power':True},{'toughness':-1},{'schema_version':True}):
            with self.assertRaises(ValueError):PrototypeSpec(**{'mana_cost':'{1}{B}','power':1,'toughness':1,**values})
        data=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in data['capabilities'] if r['id']=='casting.prototype.characteristics').update(status='blocked',blockers=['missing selected characteristic owner'])
        registry=CapabilityRegistry(data);registry.mark_evidence_verified('0'*64)
        self.assertNotEqual('exact',compile_oracle_card(replace(query_record('Prototype {1}{B} — 1/1'),keywords=('Prototype',)),capability_registry=registry).status)


class PrototypeRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'prototype.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/prototype-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Prototype',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    resolve=counter_witnesses.CounterPlacementEventRuntimeTests.resolve

    def action(self,s,card,prototype=False):
        wanted='cast-prototype:'+card.ref if prototype else 'cast:'+card.ref
        actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        matches=[a for a in actions if a['id']==wanted]
        self.assertTrue(matches,(wanted,[(a['id'],a.get('cost')) for a in actions]))
        return matches[0]

    def test_actual_prototype_offer_cost_color_size_persist_to_battlefield_and_replay(self):
        s=self.session(324001);e=s.engine;source=self.add(e,'Blitz Automaton',zone='hand')
        self.ready(s,source,{'R':2,'C':8});self.checkpoint(s)
        ordinary=self.action(s,source);prototype=self.action(s,source,True)
        self.assertEqual('{7}',ordinary['cost']);self.assertEqual('{2}{R}',prototype['cost'])
        before=authoritative_state_hash(e.state)
        wrong=s.act('pilot:B',{'action_id':prototype['id'],'pay':'auto'});self.assertFalse(wrong.ok);self.assertEqual(before,authoritative_state_hash(e.state))
        result=s.act('pilot:A',{'action_id':prototype['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        data=e._effective_card_data(e.state.cards[source.object_id])
        self.assertEqual((3,['R'],'3','2'),(data['mana_value'],data['colors'],data['power'],data['toughness']))
        self.assertEqual('stack',e.state.cards[source.object_id].zone)
        self.resolve(s);data=e._effective_card_data(e.state.cards[source.object_id])
        self.assertEqual((3,['R'],'3','2'),(data['mana_value'],data['colors'],data['power'],data['toughness']))
        self.assertIn('haste',e._combat_keywords(e.state.cards[source.object_id]));self.replay(s,load=True)

    def test_actual_prototype_blink_restores_printed_characteristics_and_replays(self):
        s=self.session(324002);e=s.engine;source=self.add(e,'Blitz Automaton',zone='hand');blink=self.add(e,'Ephemerate',zone='hand')
        self.ready(s,source,{'R':2,'W':3,'C':8});self.checkpoint(s)
        result=s.act('pilot:A',{'action_id':self.action(s,source,True)['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        result=s.act('pilot:A',{'action_id':self.action(s,blink)['id'],'targets':[source.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        current=e.state.cards[source.object_id];data=e._effective_card_data(current)
        self.assertEqual((7,[],'6','4'),(data['mana_value'],data['colors'],data['power'],data['toughness']))
        self.assertNotIn(PROTOTYPE_ANNOTATION,current.annotations);self.replay(s,load=True)

    def test_omitting_prototype_characteristics_mutant_is_killed(self):
        with patch('quorune.rules.prototype.prototype_characteristic_values',return_value={}):
            with self.assertRaises(AssertionError):self.test_actual_prototype_offer_cost_color_size_persist_to_battlefield_and_replay()

    def test_actual_prototype_token_copy_retains_alternate_copiable_values_and_replay(self):
        s=self.session(324003);e=s.engine;source=self.add(e,'Blitz Automaton',zone='hand');copy_spell=self.add(e,'Cackling Counterpart',zone='hand')
        self.ready(s,source,{'R':2,'U':4,'C':10});self.checkpoint(s)
        result=s.act('pilot:A',{'action_id':self.action(s,source,True)['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        before=set(e.state.cards)
        result=s.act('pilot:A',{'action_id':self.action(s,copy_spell)['id'],'targets':[source.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        copies=[card for oid,card in e.state.cards.items() if oid not in before and card.is_token and card.zone=='battlefield']
        self.assertEqual(1,len(copies));copy=copies[0];data=e._effective_card_data(copy)
        self.assertEqual(('{2}{R}',3,['R'],'3','2'),(data['mana_cost'],data['mana_value'],data['colors'],data['power'],data['toughness']))
        self.assertIn('haste',e._combat_keywords(copy));self.replay(s,load=True)

    def test_actual_countered_prototype_restores_graveyard_values_and_replay(self):
        s=self.session(324004);e=s.engine;source=self.add(e,'Blitz Automaton',zone='hand');counter=self.add(e,'Counterspell',seat='B',zone='hand')
        self.ready(s,source,{'R':2,'C':8});e.state.players['B'].mana_pool.update({'U':2});self.checkpoint(s)
        result=s.act('pilot:A',{'action_id':self.action(s,source,True)['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        target=e.state.stack[-1].ref
        for _ in range(8):
            if s.pending_principals()[0]=='pilot:B':break
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        action=next(a for a in s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==counter.ref)
        result=s.act('pilot:B',{'action_id':action['id'],'targets':[target],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        current=e.state.cards[source.object_id];self.assertEqual('graveyard',current.zone);data=e._effective_card_data(current)
        self.assertEqual((7,[],'6','4'),(data['mana_value'],data['colors'],data['power'],data['toughness']))
        self.assertNotIn(PROTOTYPE_ANNOTATION,current.annotations);self.replay(s,load=True)

    def test_actual_black_prototype_uses_color_cost_reduction_and_preserves_deathtouch(self):
        s=self.session(324005);e=s.engine;source=self.add(e,'Goring Warplow',zone='hand');self.add(e,'Jet Medallion')
        self.ready(s,source,{'B':2,'C':8});self.checkpoint(s)
        normal=self.action(s,source);prototype=self.action(s,source,True)
        self.assertEqual(6,normal['cost_options'][0]['requirements']['GENERIC'])
        self.assertEqual(0,prototype['cost_options'][0]['requirements']['GENERIC'])
        self.assertEqual(1,prototype['cost_options'][0]['requirements']['B'])
        result=s.act('pilot:A',{'action_id':prototype['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        current=e.state.cards[source.object_id];data=e._effective_card_data(current)
        self.assertEqual((2,['B'],'1','1'),(data['mana_value'],data['colors'],data['power'],data['toughness']))
        self.assertIn('deathtouch',e._combat_keywords(current));self.replay(s,load=True)

    def test_canonical_free_permission_combines_with_prototype_and_replays(self):
        from quorune.impulse_access import grant_temporary_cast_permission
        from quorune.impulse_access_model import TemporaryCastPermissionGrant,ImpulseAccessDuration
        s=self.session(324006);e=s.engine;source=self.add(e,'Blitz Automaton',zone='exile')
        grant=TemporaryCastPermissionGrant('A',ImpulseAccessDuration.END_OF_TURN,None,True,'canonical Prototype alternate-cost composition')
        grant_temporary_cast_permission(e,card=source,grant=grant,reason='canonical permission boundary')
        self.ready(s,source,{});self.checkpoint(s)
        normal=self.action(s,source);prototype=self.action(s,source,True)
        self.assertTrue(all(not any(o['requirements'].values()) for o in prototype['cost_options']))
        self.assertTrue(all(not any(o['requirements'].values()) for o in normal['cost_options']))
        result=s.act('pilot:A',{'action_id':prototype['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        data=e._effective_card_data(e.state.cards[source.object_id]);self.assertEqual((3,['R'],'3','2'),(data['mana_value'],data['colors'],data['power'],data['toughness']))
        self.replay(s,load=True)

    def test_canonical_spell_copy_of_prototype_keeps_selected_values_through_resolution(self):
        s=self.session(324007);e=s.engine;source=self.add(e,'Blitz Automaton',zone='hand')
        self.ready(s,source,{'R':2,'C':8});self.checkpoint(s)
        result=s.act('pilot:A',{'action_id':self.action(s,source,True)['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        item=e.state.stack[-1]
        copied=e._copy_stack_item(controller='B',target=item,targets=(),target_groups={},reason='canonical Prototype spell-copy owner diagnostic')
        copy_object=e.state.cards[copied.card_object_id];data=e._effective_card_data(copy_object)
        self.assertEqual((3,['R'],'3','2'),(data['mana_value'],data['colors'],data['power'],data['toughness']))
        self.resolve(s)
        self.assertEqual('battlefield',copy_object.zone);self.assertTrue(copy_object.is_token)
        data=e._effective_card_data(copy_object)
        self.assertEqual((3,['R'],'3','2'),(data['mana_value'],data['colors'],data['power'],data['toughness']))

    def test_unsupported_prototype_request_and_underpayment_reject_before_mutation(self):
        s=self.session(324008);e=s.engine;source=self.add(e,'Blitz Automaton',zone='hand');ordinary=self.add(e,'Generic Bound Body',zone='hand')
        self.ready(s,source,{'R':1,'W':1,'C':2});self.checkpoint(s)
        prototype=self.action(s,source,True);before=authoritative_state_hash(e.state)
        result=s.act('pilot:A',{'action_id':prototype['id'],'pay':'manual','payment':{'R':1,'C':1}})
        self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(e.state))
        result=s.act('pilot:A',{'action_id':self.action(s,ordinary)['id'],'cast_method':'prototype','pay':'auto'})
        self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(e.state))
        result=s.act('pilot:A',{'action_id':prototype['id'],'pay':'manual','payment':{'R':1,'C':2}})
        self.assertTrue(result.ok,result.summary);self.resolve(s);self.replay(s,load=True)

    def test_actual_combat_thresher_keeps_entry_draw_double_strike_and_private_replay(self):
        s=self.session(324009);e=s.engine;source=self.add(e,'Combat Thresher',zone='hand')
        self.ready(s,source,{'W':2,'C':8});self.checkpoint(s)
        count=len(e.state.players['A'].zones['hand'])
        result=s.act('pilot:A',{'action_id':self.action(s,source,True)['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        current=e.state.cards[source.object_id];data=e._effective_card_data(current)
        self.assertEqual((3,['W'],'1','1'),(data['mana_value'],data['colors'],data['power'],data['toughness']))
        self.assertEqual(count,len(e.state.players['A'].zones['hand']))
        self.assertIn('double strike',e._combat_keywords(current));self.replay(s,load=True)

    def test_model_zone_permission_uses_selected_prototype_color_before_cast_source_check(self):
        from quorune.rules.casting.face_queries import candidate_zone_cast_permission
        from quorune.rules.casting.proposal import _resolve_cast_source
        from quorune.rules.casting.model import CastProposalRequest
        s=self.session(324010);e=s.engine;source=self.add(e,'Blitz Automaton',zone='exile');probe=self.add(e,'Unsummon',zone='hand');self.add(e,'Generic Bound Body')
        self.ready(s,probe,{'R':1,'U':1,'C':2})
        def only_red(seat,card):
            return card.ref==source.ref and 'R' in e._effective_card_data(card)['colors']
        with patch.object(e,'_compiled_zone_cast_permission',side_effect=only_red):
            self.assertTrue(candidate_zone_cast_permission(e,'A',source))
            request=CastProposalRequest.from_submission('A',{'card':source.ref,'from':'exile','cast_method':'prototype','pay':'auto'})
            resolved=_resolve_cast_source(e,request)
            self.assertEqual(source.object_id,resolved.object_id)

    def test_model_stale_prototype_commit_rejects_before_payment(self):
        from quorune.rules.casting.proposal import build_cast_proposal
        from quorune.rules.casting.model import CastProposalRequest,CastProposalError
        from quorune.rules.casting.commit import commit_cast
        s=self.session(324011);e=s.engine;source=self.add(e,'Blitz Automaton',zone='hand')
        self.ready(s,source,{'R':2,'C':8})
        request=CastProposalRequest.from_submission('A',{'card':source.ref,'from':'hand','cast_method':'prototype','pay':'auto'})
        proposal=build_cast_proposal(e,request)
        before=authoritative_state_hash(e.state)
        with patch('quorune.rules.prototype.compiled_prototype_spec',return_value=None):
            with self.assertRaises(CastProposalError):commit_cast(e,proposal,{'pay':'auto'})
        self.assertEqual(before,authoritative_state_hash(e.state))
