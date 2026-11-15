"""CR 605.3b/608.2c: counter depletion is a post-mana resolution result."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT,packet_without_decision_capability
from quorune.carddb import CardDatabase
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs import bind_card_program_runtime
from quorune.deck import DeckDefinition,DeckEntry
from quorune.fixed_mana_results import FixedManaResultSpec,COUNTER_DEPLETION_RESULT_CAPABILITY as CAP
from quorune.fixed_mana_abilities import compile_fixed_activated_mana_ability,FixedActivatedManaAbilitySpec
from quorune.abilities import parse_activated_abilities
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.oracle_ir import compile_oracle_card
from scripts.build_test_database import build_fixture_database
import test_fixed_mana_abilities as actions
from inherited_composition_fixture import InheritedNativeFixture

LANDS=('Hickory Woodlot','Saprazzan Skerry','Peat Bog','Sandstone Needle','Remote Farm','Gemstone Mine')


class DepletingManaModelTests(unittest.TestCase):
    def test_counter_depletion_codec_preserves_historical_results_and_rejects_mismatched_cost(self):
        old={'schema_version':1,'kind':'damage_controller','amount':1}
        self.assertEqual(old,FixedManaResultSpec.from_dict(old).to_dict())
        result=FixedManaResultSpec('sacrifice_if_no_counters',schema_version=2,counter_name='mining')
        self.assertEqual(result,FixedManaResultSpec.from_dict(result.to_dict()))
        for bad in ({**result.to_dict(),'schema_version':1},{**result.to_dict(),'counter_name':None},
                    {**result.to_dict(),'amount':True},{**result.to_dict(),'counter_name':'Mining'},
                    {**old,'counter_name':'mining'}):
            with self.subTest(value=bad),self.assertRaises(ValueError):FixedManaResultSpec.from_dict(bad)
        text='{T}, Remove a mining counter from this land: Add one mana of any color. If there are no mining counters on this land, sacrifice it.'
        ability=parse_activated_abilities(card_name='Typed Mana Land',oracle_text=text)[0]
        spec=compile_fixed_activated_mana_ability(ability,source_name='Typed Mana Land')
        self.assertIsNotNone(spec);self.assertEqual(result,spec.result)
        malformed=deepcopy(spec.to_dict());malformed['result']['counter_name']='charge'
        with self.assertRaises(ValueError):FixedActivatedManaAbilitySpec.from_dict(malformed)
        for changed in (text.replace('no mining','no charge'),text+' Draw a card.',text.replace('sacrifice it','destroy it')):
            ability=parse_activated_abilities(card_name='Typed Mana Land',oracle_text=changed)[0]
            self.assertIsNone(compile_fixed_activated_mana_ability(ability,source_name='Typed Mana Land'))


class DepletingManaNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=actions.FixedManaResultRuntimeTests.session
    add=actions.FixedManaResultRuntimeTests.add
    ready=actions.FixedManaResultRuntimeTests.ready
    checkpoint=actions.FixedManaResultRuntimeTests.checkpoint
    replay=actions.FixedManaResultRuntimeTests.replay
    resolve=InheritedNativeFixture.resolve

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'depleting.sqlite3'
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[row for row in base['cards'] if row['name']=='Generic Bound Plains'];base['rulings']=[]
        basic=Path(cls.temporary.name)/'basic.json';basic.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([basic,ROOT/'tests/fixtures/depleting-mana-source-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Depleting mana sources',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def finish(self,s,private):
        for seat in 'ABCD':
            packet=json.dumps(packet_without_decision_capability(s.packet('pilot:'+seat,full=True)))
            if seat=='C':self.assertIn(private.ref,packet)
            else:self.assertNotIn(private.ref,packet)
        self.replay(s,load=True)

    def scene(self,name,count):
        s=self.session(932000+LANDS.index(name)*10+count);land=self.add(s.engine,name)
        key='mining' if name=='Gemstone Mine' else 'depletion'
        land.counters={key:count,'charge':1};land.tapped=False;land.owner='D'
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='depleting-private-hand')
        self.ready(s,land);self.checkpoint(s)
        return s,land,key,private

    def test_six_complete_original_lands_enter_with_exact_counters_and_tap_status(self):
        for index,name in enumerate(LANDS):
            with self.subTest(name=name):
                s=self.session(933000+index);land=self.add(s.engine,name,zone='hand')
                self.ready(s,land);self.checkpoint(s)
                program=compile_best_available_card_program(self.db,self.db.lookup(name,fuzzy=False),semantic_registry=s.engine.semantics,
                    capability_registry=self.registry,capability_profile='commander_review')
                self.assertTrue(bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict_capability_ready'])
                row=next(row for row in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    if row.get('action')=='play_land' and row.get('card')==land.ref)
                accepted=s.act('pilot:A',{'action_id':row['id']});self.assertTrue(accepted.ok,accepted.summary)
                self.assertEqual('battlefield',land.zone);mine=name=='Gemstone Mine'
                self.assertEqual({'mining':3} if mine else {'depletion':2},land.counters)
                self.assertEqual(not mine,land.tapped);self.replay(s,load=True)

    def test_explicit_counter_payment_precedes_mana_and_only_last_counter_sacrifices_to_owner(self):
        for name in LANDS:
            for count in (1,2):
                with self.subTest(name=name,count=count):
                    s,land,key,private=self.scene(name,count)
                    row=next(row for row in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                        if row.get('source')==land.ref and row.get('action')=='activate')
                    command={'action_id':row['id'],**({'mana_output':{'G':1}} if name=='Gemstone Mine' else {})}
                    before=authoritative_state_hash(s.state);self.assertFalse(s.act('pilot:B',command).ok)
                    self.assertEqual(before,authoritative_state_hash(s.state))
                    accepted=s.act('pilot:A',command);self.assertTrue(accepted.ok,accepted.summary)
                    self.assertEqual(1 if name=='Gemstone Mine' else 2,sum(s.state.players['A'].mana_pool.values()))
                    if count==1:
                        self.assertEqual('graveyard',land.zone);self.assertIn(land.object_id,s.state.players['D'].zones['graveyard'])
                    else:self.assertEqual('battlefield',land.zone);self.assertEqual(1,land.counters[key])
                    self.assertFalse(s.state.stack);self.finish(s,private)

    def test_external_counter_removal_does_not_sacrifice_or_offer_exhausted_mana(self):
        from quorune.counter_removal import CounterRemoval,plan_counter_removals,commit_counter_removals
        s,land,key,private=self.scene('Gemstone Mine',1)
        commit_counter_removals(s.engine,plan_counter_removals(s.engine,(CounterRemoval(land.object_id,key,1),)))
        self.assertEqual('battlefield',land.zone);self.assertEqual({'charge':1},land.counters)
        self.ready(s,land);self.checkpoint(s)
        rows=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row.get('source')==land.ref and row.get('action')=='activate' for row in rows))
        self.finish(s,private)

    def test_depletion_capability_blocker_and_omitted_result_mutant_are_detected(self):
        data=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(row for row in data['capabilities'] if row['id']==CAP).update(status='blocked',blockers=['missing result owner'])
        registry=CapabilityRegistry(data);registry.mark_evidence_verified('0'*64)
        ir=compile_oracle_card(self.db.lookup('Gemstone Mine',fuzzy=False),capability_registry=registry)
        self.assertNotEqual('exact',ir.status)
        from quorune import mana_ability_runtime
        original=mana_ability_runtime._mana_result_effects
        s,land,key,private=self.scene('Gemstone Mine',1)
        row=next(row for row in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if row.get('source')==land.ref)
        with patch.object(mana_ability_runtime,'_mana_result_effects',side_effect=lambda ability:tuple(
                effect for effect in original(ability) if effect['op']!='sacrifice_if_no_counters')):
            accepted=s.act('pilot:A',{'action_id':row['id'],'mana_output':{'G':1}})
        self.assertTrue(accepted.ok,accepted.summary)
        with self.assertRaises(AssertionError):self.assertEqual('graveyard',land.zone)

    def test_actual_automatic_payment_removes_the_last_named_counter_before_mana_then_sacrifices_to_its_owner(self):
        for name in ('Hickory Woodlot','Gemstone Mine'):
            with self.subTest(name=name):
                s,land,key,private=self.scene(name,1)
                body=self.add(s.engine,'Grizzly Bears',ref='auto-depleting-target')
                spell=self.add(s.engine,'Giant Growth',zone='hand',ref='auto-depleting-spell')
                s.state.config.semantic_policy='trusted_only';self.ready(s,land);self.checkpoint(s)
                self.assertEqual(1,s.state.mana_source_counter_payment_version);self.assertEqual(1,s.state.mana_cost_order_version)
                command={'action_id':'cast:'+spell.ref,'targets':[body.ref],'pay':'auto'}
                before=authoritative_state_hash(s.state);self.assertFalse(s.act('pilot:D',command).ok)
                self.assertEqual(before,authoritative_state_hash(s.state))
                observed=[];original=s.engine._add_mana_to_pool
                def add_mana(*args,**kwargs):
                    observed.append((land.zone,dict(land.counters)));return original(*args,**kwargs)
                with patch.object(s.engine,'_add_mana_to_pool',side_effect=add_mana):
                    result=s.act('pilot:A',command)
                self.assertTrue(result.ok,result.summary);self.assertEqual([('battlefield',{'charge':1})],observed)
                self.assertIn(land.object_id,s.state.players['D'].zones['graveyard'])
                self.assertEqual(1 if name=='Hickory Woodlot' else 0,s.state.players['A'].mana_pool['G'])
                self.assertIsNone(self.resolve(s));self.assertEqual(5,s.engine._numeric_stat(body.object_id,'power'))
                self.finish(s,private)

    def test_actual_exhausted_automatic_source_cannot_offer_or_accept_a_spell_and_keeps_the_other_counter(self):
        for name in ('Hickory Woodlot','Gemstone Mine'):
            with self.subTest(name=name):
                s,land,key,private=self.scene(name,0)
                body=self.add(s.engine,'Grizzly Bears',ref='exhausted-depleting-target')
                spell=self.add(s.engine,'Giant Growth',zone='hand',ref='exhausted-depleting-spell')
                s.state.config.semantic_policy='trusted_only';self.ready(s,land);self.checkpoint(s)
                actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                self.assertFalse(any(row['id']=='cast:'+spell.ref for row in actions))
                before=authoritative_state_hash(s.state)
                result=s.act('pilot:A',{'action_id':'cast:'+spell.ref,'targets':[body.ref],'pay':'auto'})
                self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(s.state))
                self.assertEqual('battlefield',land.zone);self.assertFalse(land.tapped);self.assertEqual(1,land.counters['charge'])
                self.finish(s,private)
