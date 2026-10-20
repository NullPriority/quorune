"""CR122.1/122.2/611.3: any-kind presence and individual counter totals."""
from __future__ import annotations

from dataclasses import replace
import itertools
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.object_predicate import PermanentStatePredicateSpec,ObjectQuerySpec,ObjectQueryError
from quorune.object_query import ObjectQueryResult,object_matches_query
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.oracle_ir import compile_oracle_card
from quorune.card_programs.binding import bind_semantic_program_runtime
from test_granted_skulk import record
from scripts.build_test_database import build_fixture_database
import test_scalar_mill_wording as controls


class PublicAnyCounterModelTests(unittest.TestCase):
    def test_public_and_direct_queries_preserve_named_and_any_counter_boundaries(self):
        from quorune.compiler.direct_target import direct_permanent_target_spec
        from quorune.compiler.public_state_queries import fixed_characteristic_battlefield_query_subject
        any_target=direct_permanent_target_spec('target creature with a counter on it')
        self.assertEqual(1,any_target.state_predicate.minimum_total_counter_count)
        named=direct_permanent_target_spec('target creature with a charge counter on it')
        self.assertEqual('charge',named.state_predicate.counter_name)
        self.assertNotIn('minimum_total_counter_count',named.state_predicate.to_dict())
        relation,query,_=fixed_characteristic_battlefield_query_subject('Each creature you control with a counter on it')
        self.assertEqual('source_controller',relation)
        self.assertEqual(any_target.state_predicate,query.state_predicate)
        for text in ('target creature with a counter on it that entered last turn',
                'target creature that entered this turn with a counter on it',
                'target creature with a charge counter on it with a counter on it'):
            self.assertIsNone(direct_permanent_target_spec(text),text)
        raw=query.state_predicate.to_dict()
        for changes in ({'minimum_total_counter_count':True},{'minimum_total_counter_count':-1},{'future':True}):
            with self.assertRaises(ObjectQueryError):PermanentStatePredicateSpec.from_dict({**raw,**changes})

    def test_total_source_anthem_is_explicit_and_keeps_named_counter_descriptor(self):
        from quorune.compiler.continuous_templates import fixed_power_toughness_anthem_handler
        from quorune.semantic_runtime.continuous_components import FixedQueryPowerToughnessAnthemHandler
        compiled=fixed_power_toughness_anthem_handler('Other Humans you control get +1/+1 for each counter on this creature.')
        self.assertEqual('continuous.anthem.source-counter-total-query.v4',compiled[1]['handler_id'])
        self.assertIn('quantity_expression.public_counter_total',compiled[2])
        handler=FixedQueryPowerToughnessAnthemHandler(handler_id=compiled[1]['handler_id'],schema_version=4)
        self.assertTrue(handler.validate(compiled[1]).source_counter_total)
        for value in (False,0,None,'all'):
            with self.assertRaises(ValueError):handler.validate({**compiled[1],
                'modifier':{**compiled[1]['modifier'],'source_counter_total':value}})
        named=fixed_power_toughness_anthem_handler('Other Humans you control get +1/+1 for each charge counter on this creature.')
        self.assertEqual('continuous.anthem.source-counter-query.v3',named[1]['handler_id'])
        self.assertEqual('charge',named[1]['modifier']['source_counter'])
        self.assertNotIn('source_counter_total',named[1]['modifier'])

    def test_capability_withdrawal_preserves_any_counter_and_total_residuals(self):
        for cap,text in (('state_query.permanent.any_counter',"Defender\nAs long as this creature has a counter on it, it can attack as though it didn't have defender."),
                ('quantity_expression.public_counter_total',"Defender\nAs long as there are two or more counters among creatures you control, this creature can attack as though it didn't have defender.")):
            raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
            next(r for r in raw['capabilities'] if r['id']==cap).update(status='blocked',blockers=['withdrawn counter query'])
            ir=compile_oracle_card(record(text,'Creature'),capability_registry=CapabilityRegistry(raw),
                capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status);self.assertTrue(ir.material_residuals)

    def test_any_counter_presence_uses_positive_individual_counts_not_names(self):
        state=PermanentStatePredicateSpec(minimum_total_counter_count=1)
        query=ObjectQuerySpec(zones=('battlefield',),types_all=('creature',),state_predicate=state)
        self.assertEqual(state,PermanentStatePredicateSpec.from_dict(state.to_dict()))
        for charge,lore,stun in itertools.product(range(3),repeat=3):
            row=ObjectQueryResult(object_id='object',ref='C1',printed_name='Generic',owner='A',
                controller='A',zone='battlefield',types=('creature',),counters={'charge':charge,'lore':lore,'stun':stun})
            self.assertEqual(charge+lore+stun>0,object_matches_query(row,query))
        for amount in (True,-1,1.5):
            row=replace(row,counters={'charge':amount})
            with self.assertRaises(ValueError):object_matches_query(row,query)
        for minimum in (True,0,-1,1.5):
            with self.assertRaises(ObjectQueryError):PermanentStatePredicateSpec(minimum_total_counter_count=minimum)

    def test_all_counter_reduction_sums_individual_counters_over_matched_rows(self):
        from quorune.characteristic_fragments import CharacteristicQuantityReduction,CharacteristicQuantitySpec,CharacteristicQuantityScope
        from quorune.public_quantity_reductions import reduce_public_quantity
        def row(index,counters):
            return ObjectQueryResult(object_id=str(index),ref='C'+str(index),printed_name='Generic',
                owner='A',controller='A',zone='battlefield',counters=counters)
        rows=[(row(1,{'charge':2,'lore':1}),{},'card'),(row(2,{'charge':1,'stun':2}),{},'card')]
        self.assertEqual(6,reduce_public_quantity(rows,CharacteristicQuantityReduction.TOTAL_COUNTERS))
        self.assertEqual(0,reduce_public_quantity([],CharacteristicQuantityReduction.TOTAL_COUNTERS))
        for query in (None,'not-a-query',ObjectQuerySpec(zones=('hand',))):
            with self.assertRaises(ValueError):CharacteristicQuantitySpec(
                scope=CharacteristicQuantityScope.CONTROLLER_ZONE,schema_version=2,
                reduction=CharacteristicQuantityReduction.TOTAL_COUNTERS,query=query)


class PublicAnyCounterRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.ScalarMillWordingRuntimeTests.session
    add=controls.ScalarMillWordingRuntimeTests.add
    prepare=controls.ScalarMillWordingRuntimeTests.prepare
    checkpoint=controls.ScalarMillWordingRuntimeTests.checkpoint
    cast=controls.ScalarMillWordingRuntimeTests.cast
    resolve=controls.ScalarMillWordingRuntimeTests.resolve
    replay=controls.ScalarMillWordingRuntimeTests.replay
    until=controls.ScalarMillWordingRuntimeTests.until
    binding=controls.ScalarMillWordingRuntimeTests.binding

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'any-counters.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/public-any-counter-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Any counters',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',20)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def private_replay(self,session,private):
        for seat in 'ABD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.assertIn(private.ref,json.dumps(session.packet('pilot:C',full=True)))
        return self.replay(session,load=True)

    def attackers(self,session):
        self.until(session,lambda s:s.state.active_player=='A' and s.state.pending_decision is not None
            and s.state.pending_decision.kind=='combat.attackers')
        return {r['id'] for r in session.packet('pilot:A',full=True)['decision']['ctx']['candidates']}

    def test_original_demon_wall_attacks_with_a_charge_counter_and_keeps_defender(self):
        session=self.session(72102001);source=self.add(session.engine,'Demon Wall')
        source.counters['charge']=1;source.acquired_control_turn_count=-1
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session);self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.assertIn(source.ref,self.attackers(session))
        self.assertIn('Defender',session.engine._effective_card_data(source)['keywords'])
        command={'action_id':'attack','attackers':{source.ref:'B'}};before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        session=self.replay(session,load=True)
        self.until(session,lambda s:s.state.active_player=='A' and s.state.phase=='postcombat_main')
        self.assertEqual(40-session.engine._numeric_stat(source.object_id,'power'),session.state.players['B'].life)
        self.private_replay(session,private)

    def test_original_backstreet_bruiser_counts_two_counters_on_one_controlled_creature(self):
        session=self.session(72102002);source=self.add(session.engine,'Backstreet Bruiser')
        source.acquired_control_turn_count=-1
        body=self.add(session.engine,'Generic Bound Body');body.counters['charge']=2;body.acquired_control_turn_count=-1
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session);self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.assertIn(source.ref,self.attackers(session))
        result=session.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'}})
        self.assertTrue(result.ok,result.summary);self.private_replay(session,private)

    def test_original_rishkar_grants_green_mana_to_a_charge_counter_creature(self):
        session=self.session(72102003);source=self.add(session.engine,'Rishkar, Peema Renegade')
        body=self.add(session.engine,'Generic Bound Body');body.counters['charge']=1;body.acquired_control_turn_count=-1
        spell=self.add(session.engine,'Generic Bound Growth',zone='hand',ref='any-counter-funded-growth')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session);self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertTrue(any(r.get('card')==spell.ref for r in actions))
        self.cast(session,'A',spell,[body.ref])
        self.assertTrue(session.state.cards[body.object_id].tapped)
        self.assertIsNone(self.resolve(session))
        self.assertFalse(session.state.cards[body.object_id].tapped)
        self.assertEqual(4,session.engine._numeric_stat(body.object_id,'power'))
        self.assertEqual('graveyard',session.state.cards[spell.object_id].zone)
        self.private_replay(session,private)

    def test_original_razorfin_returns_a_charge_counter_creature_and_rejects_zero(self):
        session=self.session(72102004);source=self.add(session.engine,'Razorfin Abolisher')
        source.acquired_control_turn_count=-1
        body=self.add(session.engine,'Generic Bound Body');body.counters['charge']=1
        zero=self.add(session.engine,'Generic Bound Body',seat='B',ref='zero-counter-target')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'U':1,'C':1});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(r for r in actions if str(r['id']).startswith('activate:'+source.ref+':'))
        self.assertIn(body.ref,action['target_schema']['legal_refs'])
        self.assertNotIn(zero.ref,action['target_schema']['legal_refs'])
        before=authoritative_state_hash(session.state)
        bad=session.act('pilot:A',{'action_id':action['id'],'targets':[zero.ref]})
        self.assertFalse(bad.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':action['id'],'targets':[body.ref]})
        self.assertTrue(result.ok,result.summary);session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[body.object_id].zone)
        self.assertEqual({},session.state.cards[body.object_id].counters)
        self.assertEqual('battlefield',session.state.cards[zero.object_id].zone)
        self.private_replay(session,private)

    def test_any_counter_matching_omission_is_killed_by_a_fresh_native_original(self):
        method='test_original_demon_wall_attacks_with_a_charge_counter_and_keeps_defender'
        with patch('quorune.object_predicate.permanent_counter_total',return_value=0):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_distinct_counter_names_mutant_is_killed_by_a_fresh_original_total(self):
        method='test_original_backstreet_bruiser_counts_two_counters_on_one_controlled_creature'
        with patch('quorune.public_quantity_reductions.permanent_counter_total',side_effect=lambda value:len(value)):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_actual_last_counter_removal_ends_demon_wall_attack_permission(self):
        session=self.session(72102005);source=self.add(session.engine,'Demon Wall')
        source.counters['charge']=1;source.acquired_control_turn_count=-1
        body=self.add(session.engine,'Generic Bound Body');body.acquired_control_turn_count=-1
        removal=self.add(session.engine,'Generic Any Counter Removal',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'C':1});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.cast(session,'A',removal,[source.ref]);self.assertIsNone(self.resolve(session))
        self.assertNotIn(source.ref,self.attackers(session))
        before=authoritative_state_hash(session.state)
        result=session.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'}})
        self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.private_replay(session,private)

    def test_original_backstreet_total_preserves_controller_type_and_individual_count(self):
        for index,mode in enumerate(('one','opponent','artifact','split','cancelled')):
            session=self.session(72102020+index);source=self.add(session.engine,'Backstreet Bruiser')
            source.acquired_control_turn_count=-1
            body=self.add(session.engine,'Generic Bound Body');body.acquired_control_turn_count=-1
            if mode=='one':body.counters['charge']=1
            if mode=='opponent':self.add(session.engine,'Generic Bound Body',seat='B',ref='foreign-total').counters['charge']=2
            if mode=='artifact':self.add(session.engine,'Generic Any Counter Artifact').counters['charge']=2
            if mode=='split':source.counters['charge']=1;body.counters['lore']=1
            if mode=='cancelled':body.counters.update({'+1/+1':1,'-1/-1':1})
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(session);self.checkpoint(session)
            if mode=='cancelled':self.assertFalse(session.state.cards[body.object_id].counters)
            self.assertTrue(self.binding(session,source)['strict_capability_ready'])
            self.assertEqual(mode=='split',source.ref in self.attackers(session))
            before=authoritative_state_hash(session.state)
            result=session.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'}})
            self.assertEqual(mode=='split',result.ok,result.summary)
            if not result.ok:self.assertEqual(before,authoritative_state_hash(session.state))
            self.private_replay(session,private)

    def test_razorfin_revalidates_last_counter_after_actual_response_and_pending_load(self):
        session=self.session(72102006);source=self.add(session.engine,'Razorfin Abolisher')
        source.acquired_control_turn_count=-1
        body=self.add(session.engine,'Generic Bound Body');body.counters['charge']=1
        removal=self.add(session.engine,'Generic Any Counter Removal',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'U':1,'C':2});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        action=next(r for r in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if str(r['id']).startswith('activate:'+source.ref+':'))
        result=session.act('pilot:A',{'action_id':action['id'],'targets':[body.ref]})
        self.assertTrue(result.ok,result.summary)
        self.cast(session,'A',removal,[body.ref]);session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[body.object_id].zone)
        self.assertFalse(session.state.cards[body.object_id].counters)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.private_replay(session,private)

    def test_original_kyler_counts_all_individual_source_counters_and_actual_human_entry(self):
        session=self.session(72102007);source=self.add(session.engine,'Kyler, Sigardian Emissary')
        source.counters.update({'charge':2,'lore':1})
        body=self.add(session.engine,'Generic Bound Body')
        enemy=self.add(session.engine,'Generic Bound Body',seat='B',ref='foreign-human')
        late=self.add(session.engine,'Generic Bound Body',zone='hand',ref='later-human')
        removal=self.add(session.engine,'Generic Any Counter Removal',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'W':1,'C':1});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.assertEqual(5,session.engine._numeric_stat(body.object_id,'power'))
        self.assertEqual(2,session.engine._numeric_stat(enemy.object_id,'power'))
        self.cast(session,'A',late);self.assertIsNone(self.resolve(session))
        self.assertEqual(1,session.state.cards[source.object_id].counters.get('+1/+1'))
        self.assertEqual(6,session.engine._numeric_stat(body.object_id,'power'))
        self.assertEqual(6,session.engine._numeric_stat(late.object_id,'power'))
        self.cast(session,'A',removal,[source.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual(5,session.engine._numeric_stat(body.object_id,'power'))
        self.private_replay(session,private)

    def test_original_rishkar_preserves_sickness_and_loses_grant_after_actual_counter_removal(self):
        for index,mode in enumerate(('sick','removed')):
            session=self.session(72102040+index);source=self.add(session.engine,'Rishkar, Peema Renegade')
            body=self.add(session.engine,'Generic Bound Body');body.counters['charge']=1
            body.acquired_control_turn_count=session.state.players['A'].turns_begun if mode=='sick' else -1
            spell=self.add(session.engine,'Generic Bound Growth',zone='hand',ref='unfunded-green-spell')
            removal=self.add(session.engine,'Generic Any Counter Removal',zone='hand')
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(session,mana={'C':1} if mode=='removed' else None);self.checkpoint(session)
            self.assertTrue(self.binding(session,source)['strict_capability_ready'])
            if mode=='removed':self.cast(session,'A',removal,[body.ref]);self.assertIsNone(self.resolve(session))
            actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            self.assertFalse(any(r.get('card')==spell.ref for r in actions))
            before=authoritative_state_hash(session.state)
            result=session.act('pilot:A',{'action_id':'cast:'+spell.ref,'pay':'auto','targets':[body.ref]})
            self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(session.state))
            self.private_replay(session,private)

    def test_original_acolyte_grants_actual_ward_for_a_charge_counter(self):
        session=self.session(72102008);source=self.add(session.engine,'Cathedral Acolyte')
        body=self.add(session.engine,'Generic Bound Body');body.counters['charge']=1
        removal=self.add(session.engine,'Unsummon',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,actor='B',mana={'U':1});self.checkpoint(session)
        for card in (source,removal):self.assertTrue(self.binding(session,card)['strict_capability_ready'])
        self.cast(session,'B',removal,[body.ref]);decision=self.resolve(session)
        self.assertIsNotNone(decision);self.assertEqual('semantic.choice',decision.kind)
        self.assertEqual(['B'],list(decision.actors));session=self.replay(session,load=True)
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',{'action_id':'choose','pay':False}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:B',{'action_id':'choose','pay':False})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[body.object_id].zone)
        self.assertEqual('graveyard',session.state.cards[removal.object_id].zone)
        self.private_replay(session,private)

    def test_original_baxter_locks_counter_qualified_recipients_until_end_of_turn(self):
        session=self.session(72102009);source=self.add(session.engine,'Baxter, Fly in the Ointment',zone='hand')
        body=self.add(session.engine,'Generic Bound Body');body.counters['charge']=1
        late=self.add(session.engine,'Generic Bound Body',ref='late-counter-creature')
        enemy=self.add(session.engine,'Generic Bound Body',seat='B',ref='enemy-counter-creature');enemy.counters['charge']=1
        remove=self.add(session.engine,'Generic Any Counter Removal',zone='hand')
        add=self.add(session.engine,'Generic Any Counter Charge',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'U':1,'C':5});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session))
        def flying(card):return 'Flying' in session.engine._effective_card_data(session.state.cards[card.object_id])['keywords']
        self.assertTrue(flying(body));self.assertFalse(flying(late));self.assertFalse(flying(enemy))
        self.cast(session,'A',remove,[body.ref]);self.assertIsNone(self.resolve(session))
        self.assertFalse(session.state.cards[body.object_id].counters);self.assertTrue(flying(body))
        self.cast(session,'A',add,[late.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual(1,session.state.cards[late.object_id].counters.get('charge'))
        self.assertFalse(flying(late))
        self.until(session,lambda s:s.state.active_player=='A' and s.state.pending_decision is not None
            and s.state.pending_decision.kind=='combat.attackers')
        result=session.act('pilot:A',{'action_id':'attack','attackers':{}})
        self.assertTrue(result.ok,result.summary)
        self.until(session,lambda s:s.state.active_player=='B' and s.state.phase=='precombat_main')
        self.assertFalse(flying(body));self.assertFalse(flying(late))
        self.private_replay(session,private)

    def test_executable_counter_query_and_total_reject_missing_capability_dependencies(self):
        for index,name,cap in ((0,'Demon Wall','state_query.permanent.any_counter'),
                (1,'Backstreet Bruiser','quantity_expression.public_counter_total'),
                (2,'Razorfin Abolisher','state_query.permanent.any_counter')):
            session=self.session(72102090+index);source=self.add(session.engine,name)
            program=next(p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
                if cap in p.capability_dependencies and (name!='Razorfin Abolisher' or p.target_schema is not None))
            self.assertTrue(bind_semantic_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict'])
            dependencies=tuple(c for c in program.capability_dependencies if c!=cap)
            missing=replace(program,capability_dependencies=dependencies,
                capability_closure=self.registry.closure(dependencies,profile='commander_review').to_dict() if dependencies else None)
            bound=bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])

    def test_source_total_omission_is_killed_by_a_fresh_original_kyler(self):
        method='test_original_kyler_counts_all_individual_source_counters_and_actual_human_entry'
        with patch('quorune.semantic_runtime.continuous_components.permanent_counter_total',return_value=0):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_last_counter_loss_after_declaration_does_not_undo_original_demon_attack(self):
        session=self.session(72102010);source=self.add(session.engine,'Demon Wall')
        source.counters['charge']=1;source.acquired_control_turn_count=-1
        self.add(session.engine,'Generic Bound Plains',ref='counter-response-land')
        removal=self.add(session.engine,'Generic Any Counter Removal',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session);self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.assertIn(source.ref,self.attackers(session))
        result=session.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'}})
        self.assertTrue(result.ok,result.summary)
        self.cast(session,'A',removal,[source.ref]);self.assertIsNone(self.resolve(session))
        self.assertFalse(session.state.cards[source.object_id].counters)
        self.assertEqual('B',session.state.cards[source.object_id].attacking)
        session=self.replay(session,load=True)
        self.until(session,lambda s:s.state.active_player=='A' and s.state.phase=='postcombat_main')
        self.assertEqual(37,session.state.players['B'].life)
        self.private_replay(session,private)
