from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quorune.ability_source_information import ability_source_information_version
from quorune.characteristic_evaluation import type_parts
from quorune.compiler.counter_templates import targeted_counter_effect_template
from quorune.model import CardInstance,GameConfig,GameState,StackItem
from quorune.errors import GameRuleError
from quorune.target_source import (
    TARGETING_SOURCE_CONTEXT,ensure_trigger_targeting_source,pin_targeting_source_departures,stack_targeting_source,
)
from quorune.targets import TargetGroup
from quorune.target_predicates import target_predicate_matches
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_life_gain_prohibitions as native


class AbilitySourceCounterModelTests(unittest.TestCase):
    def test_new_source_information_marker_is_optional_strict_and_legacy_hash_neutral(self):
        state=GameState('source-info',GameConfig(),{},{},{},{},[],None,None)
        self.assertEqual(1,state.to_dict()['ability_source_information_version'])
        raw=state.to_dict();raw.pop('ability_source_information_version')
        legacy=GameState.from_dict(raw)
        self.assertIsNone(legacy.ability_source_information_version)
        self.assertEqual(raw,legacy.to_dict())
        for version in (True,False,0,2,1.0,'1',{}):
            with self.subTest(version=version),self.assertRaises(ValueError):
                GameState.from_dict({**raw,'ability_source_information_version':version})
        for version in (None,1):self.assertEqual(version,ability_source_information_version(version))

    def host(self, *, version=1,kind='activated_ability'):
        source=CardInstance(object_id='source',ref='source',oracle_id='source-model',printed_name='Source',
            owner='A',controller='B',zone='battlefield')
        item=StackItem(stack_id='item',ref='item',kind=kind,controller='B',label='Ability',
            source_object_id=source.object_id,context={'source_logical_object_id':source.logical_object_id})
        facts={'type_line':'Artifact','colors':[]}
        state=SimpleNamespace(cards={source.object_id:source},stack=[item],pending_trigger_batches=[],
            ability_source_information_version=version)
        host=SimpleNamespace(state=state,semantics=SimpleNamespace(get=lambda key:None),
            _effective_card_data=lambda card:dict(facts))
        return host,source,item,facts

    def test_nontargeted_ability_source_uses_current_then_actual_departure_information(self):
        host,source,item,facts=self.host()
        ensure_trigger_targeting_source(host,item)
        self.assertIn(TARGETING_SOURCE_CONTEXT,item.context)
        self.assertEqual(frozenset({'artifact'}),stack_targeting_source(host,item).card_types)
        facts['type_line']='Creature'
        self.assertEqual(frozenset({'creature'}),stack_targeting_source(host,item).card_types)
        pin_targeting_source_departures(host,[source])
        source.zone='graveyard';source.zone_change_counter+=1
        facts['type_line']='Artifact'
        self.assertEqual(frozenset({'creature'}),stack_targeting_source(host,item).card_types)
        source.zone='battlefield';source.zone_change_counter+=1
        self.assertEqual(frozenset({'creature'}),stack_targeting_source(host,item).card_types)

    def test_legacy_nontargeted_ability_frames_remain_unmodified_and_malformed_facts_fail(self):
        host,source,item,facts=self.host(version=None)
        before=deepcopy(item.to_dict());ensure_trigger_targeting_source(host,item)
        self.assertEqual(before,item.to_dict())
        source.zone='graveyard';source.zone_change_counter+=1
        pin_targeting_source_departures(host,[source])
        self.assertEqual(before,item.to_dict())
        self.assertIsNone(stack_targeting_source(host,item).card_types)
        current,source,item,facts=self.host();ensure_trigger_targeting_source(current,item)
        item.context[TARGETING_SOURCE_CONTEXT]['logical_object_id']='other-incarnation'
        with self.assertRaises(GameRuleError):stack_targeting_source(current,item)

    def test_source_type_counter_domains_distinguish_ability_kind_and_known_source_facts(self):
        forms=(('Counter target activated ability from an artifact source.','activated_artifact_source'),
            ('Counter target activated or triggered ability from a noncreature source.','noncreature_ability_source'))
        for text,predicate in forms:
            template=targeted_counter_effect_template(text);self.assertIsNotNone(template)
            self.assertEqual(predicate,template.target_schema['predicate'])
            group=TargetGroup.from_mapping(template.target_schema)
            for kind in ('activated_ability','triggered_ability','spell'):
                item=StackItem(stack_id='item',ref='item',kind=kind,controller='B',label='Counter domain')
                for types in (None,frozenset({'artifact'}),frozenset({'creature'}),frozenset({'artifact','creature'})):
                    row={'zone':'stack','category':'spell' if kind=='spell' else 'ability','stack_item':item,'ability_source_types':types}
                    expected=(kind=='activated_ability' and types is not None and 'artifact' in types) if predicate=='activated_artifact_source' else (
                        kind in {'activated_ability','triggered_ability'} and types is not None and 'creature' not in types)
                    with self.subTest(predicate=predicate,kind=kind,types=types):
                        self.assertEqual(expected,target_predicate_matches(SimpleNamespace(),group,row,
                            types=set(),supertypes=set(),colors=set(),derived={}))

    def test_source_information_capture_omission_and_malformed_filter_are_rejected(self):
        from quorune.ability_source_information import ability_source_filter_dependencies,ABILITY_SOURCE_INFORMATION_CAPABILITY
        method='test_nontargeted_ability_source_uses_current_then_actual_departure_information'
        with patch('quorune.ability_source_information.complete_ability_source_information',return_value=False):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        template=targeted_counter_effect_template('Counter target activated ability from an artifact source.')
        self.assertEqual((ABILITY_SOURCE_INFORMATION_CAPABILITY,),ability_source_filter_dependencies(template.target_schema))
        for fields in ({'count':True},{'source_exclusion':False},{'categories':['spell']},{'extra':1}):
            with self.assertRaises(ValueError):ability_source_filter_dependencies({**template.target_schema,**fields})

    def test_source_information_scan_preserves_unrelated_structured_predicates(self):
        from quorune.ability_source_information import ability_source_filter_dependencies
        from quorune.card_programs.descriptor_requirements import typed_result_amount_runtime_dependencies
        for predicate in (None,{},[],{'field':'keywords','value':'flying'},['artifact'],'other_predicate'):
            with self.subTest(predicate=predicate):
                value={'predicate':predicate}
                self.assertEqual((),ability_source_filter_dependencies(value))
                self.assertEqual((),typed_result_amount_runtime_dependencies(value))

    def test_adventure_permission_and_face_offer_versions_reject_stale_or_legacy_facts(self):
        from quorune.adventure import ADVENTURE_CONTEXT,ADVENTURE_PERMISSION,adventure_cast_permission,adventure_resolution_destination,complete_adventure_resolution
        from quorune.cast_face_offers import cast_face_offers_version
        state=GameState('face-info',GameConfig(),{},{},{},{},[],None,None)
        raw=state.to_dict();self.assertEqual(1,raw['cast_face_offers_version'])
        raw.pop('cast_face_offers_version');raw.pop('ability_source_information_version')
        legacy=GameState.from_dict(raw)
        self.assertEqual(raw,legacy.to_dict())
        self.assertIsNone(legacy.cast_face_offers_version)
        for value in (True,False,0,2,1.0,'1'):
            with self.assertRaises(ValueError):cast_face_offers_version(value)
        card=CardInstance(object_id='adventurer',ref='adventurer',oracle_id='model:adventure',printed_name='Normal',owner='A',controller='B',zone='exile')
        item=StackItem(stack_id='adventure',ref='adventure',kind='spell',controller='B',label='Adventure',
            card_object_id=card.object_id,chosen_face='Alternative',context={ADVENTURE_CONTEXT:{
                'schema_version':1,'adventure_face':'Alternative','normal_face':'Normal'}})
        self.assertEqual('exile',adventure_resolution_destination(item,'graveyard'))
        complete_adventure_resolution(item,card)
        record=SimpleNamespace(layout='adventure',faces=({'name':'Normal'},{'name':'Alternative'}))
        host=SimpleNamespace(card_record=lambda source:record)
        self.assertTrue(adventure_cast_permission(host,'B',card))
        self.assertFalse(adventure_cast_permission(host,'A',card))
        card.active_face='Alternative';self.assertFalse(adventure_cast_permission(host,'B',card))
        card.active_face=None;card.zone_change_counter+=1
        self.assertFalse(adventure_cast_permission(host,'B',card))
        card.annotations.clear();item.kind='spell_copy';complete_adventure_resolution(item,card)
        self.assertNotIn(ADVENTURE_PERMISSION,card.annotations)

    def test_adventure_resolution_omission_and_malformed_context_are_detected(self):
        from quorune.adventure import adventure_resolution_destination
        method='test_adventure_permission_and_face_offer_versions_reject_stale_or_legacy_facts'
        with patch('quorune.adventure.adventure_resolution_destination',side_effect=lambda item,destination:destination):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        for raw in ({}, {'schema_version':True,'adventure_face':'Alternative','normal_face':'Normal'},
                {'schema_version':1,'adventure_face':'Same','normal_face':'Same'}):
            item=StackItem(stack_id='invalid',ref='invalid',kind='spell',controller='A',label='Invalid',
                chosen_face='Alternative',context={'adventure_resolution':raw})
            with self.assertRaises(ValueError):adventure_resolution_destination(item,'graveyard')


class AbilitySourceCounterRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=native.LifeGainProhibitionRuntimeTests.session
    add=native.LifeGainProhibitionRuntimeTests.add
    prepare=native.LifeGainProhibitionRuntimeTests.prepare
    checkpoint=native.LifeGainProhibitionRuntimeTests.checkpoint
    resolve=native.LifeGainProhibitionRuntimeTests.resolve
    replay=native.LifeGainProhibitionRuntimeTests.replay
    private_replay=native.LifeGainProhibitionRuntimeTests.private_replay
    priority=native.LifeGainProhibitionRuntimeTests.priority
    actions=native.LifeGainProhibitionRuntimeTests.actions
    cast=native.LifeGainProhibitionRuntimeTests.cast
    reject=native.LifeGainProhibitionRuntimeTests.reject
    binding=native.LifeGainProhibitionRuntimeTests.binding
    resume=native.LifeGainProhibitionRuntimeTests.resume
    settle=native.LifeGainProhibitionRuntimeTests.settle

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'ability-source-counter.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/ability-source-counter-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Ability source information',[
            DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def fixture(self,seed,*,counter='Rust'):
        s=self.session(seed);spell=self.add(s.engine,counter,zone='hand')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['B'].mana_pool.update({'C':8,'U':3,'G':3})
        return s,spell,private

    def activate(self,s,source,*,needle='draw',targets=()):
        self.priority(s,'B')
        rows=[row for row in self.actions(s,'B') if row['id'].startswith('activate:'+source.ref+':')]
        row=next((row for row in rows if needle.casefold() in json.dumps(row).casefold()),None)
        self.assertIsNotNone(row,rows)
        command={'action_id':row['id'],'targets':list(targets),'pay':'auto'}
        self.reject(s,command,seat='D')
        result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary)

    def counter(self,s,spell,target,*,adventure=False):
        self.priority(s,'A')
        rows=self.actions(s)
        row=next((row for row in rows if row.get('card')==spell.ref and (
            not adventure or row.get('face')=='Dissonant Wave')),None)
        self.assertIsNotNone(row,[{key:value for key,value in row.items() if key in {'id','card','face','label'}} for row in rows])
        self.assertTrue(self.binding(s,spell)['strict_capability_ready'])
        command={'action_id':row['id'],'targets':[target],'pay':'manual',
            'payment':{'C':2,'G':1} if adventure else {'G':1}}
        if adventure:command['face']='Dissonant Wave'
        self.reject(s,command,seat='D')
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def test_real_rust_counters_sacrificed_nontargeted_artifact_ability_without_draw(self):
        s,spell,private=self.fixture(113700300)
        source=self.add(s.engine,'Mind Stone',seat='B')
        self.prepare(s,mana={'G':1});self.checkpoint(s)
        before=set(s.state.players['B'].zones['hand'])
        self.activate(s,source)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        ability=s.state.stack[-1]
        self.assertIn(TARGETING_SOURCE_CONTEXT,ability.context)
        self.assertIn('artifact',stack_targeting_source(s.engine,ability).card_types)
        self.counter(s,spell,ability.ref)
        s=self.resume(s);self.assertIsNone(self.settle(s))
        self.assertEqual(before,set(s.state.players['B'].zones['hand']))
        self.assertEqual('graveyard',s.state.cards[spell.object_id].zone)
        self.private_replay(s,private)

    def test_rust_revalidation_uses_changed_source_then_departure_not_printed_artifact(self):
        s,spell,private=self.fixture(113700310)
        source=self.add(s.engine,'Rod of Ruin',seat='B')
        self.add(s.engine,'Vedalken Orrery')
        change=self.add(s.engine,'Song of the Dryads',zone='hand')
        bounce=self.add(s.engine,'Boomerang',zone='hand')
        self.prepare(s,mana={'C':2,'G':2,'U':2});self.checkpoint(s)
        self.activate(s,source,needle='damage',targets=['A'])
        ability=s.state.stack[-1]
        self.counter(s,spell,ability.ref)
        self.cast(s,change,[source.ref],payment={'C':2,'G':1})
        while len(s.state.stack)>2:
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(frozenset({'land'}),stack_targeting_source(s.engine,ability).card_types)
        self.cast(s,bounce,[source.ref],payment={'U':2})
        while len(s.state.stack)>2:
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('hand',s.state.cards[source.object_id].zone)
        self.assertEqual(frozenset({'land'}),stack_targeting_source(s.engine,ability).card_types)
        s=self.resume(s);self.assertIsNone(self.settle(s))
        self.assertEqual(39,s.state.players['A'].life)
        self.assertEqual('graveyard',s.state.cards[spell.object_id].zone)
        self.private_replay(s,private)

    def test_actual_adventure_counters_nontargeted_departed_artifact_source_and_casts_creature(self):
        s,spell,private=self.fixture(113700320,counter='Emerald Dragon // Dissonant Wave')
        source=self.add(s.engine,'Mind Stone',seat='B')
        self.prepare(s,mana={'C':6,'G':3});self.checkpoint(s)
        before=set(s.state.players['B'].zones['hand'])
        self.activate(s,source)
        self.counter(s,spell,s.state.stack[-1].ref,adventure=True)
        s=self.resume(s);self.assertIsNone(self.settle(s))
        self.assertEqual(before,set(s.state.players['B'].zones['hand']))
        self.assertEqual('exile',s.state.cards[spell.object_id].zone)
        self.priority(s,'A')
        rows=[row for row in self.actions(s) if row.get('card')==spell.ref]
        self.assertTrue(rows,{'actions':self.actions(s),'annotations':s.state.cards[spell.object_id].annotations})
        row=next(row for row in rows if row.get('face')=='Emerald Dragon')
        self.assertFalse(any(row.get('face')=='Dissonant Wave' for row in rows))
        self.reject(s,{'action_id':row['id'],'face':'Dissonant Wave','targets':[],
            'pay':'manual','payment':{'C':2,'G':1}})
        command={'action_id':row['id'],'face':'Emerald Dragon','targets':[],
            'cost_option':row['cost_options'][0]['id'],'pay':'manual','payment':{'C':4,'G':2}}
        self.reject(s,command,seat='D')
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.settle(s))
        self.assertEqual('battlefield',s.state.cards[spell.object_id].zone)
        self.assertEqual((4,4),(s.engine._numeric_stat(spell.object_id,'power'),s.engine._numeric_stat(spell.object_id,'toughness')))
        self.assertTrue({'flying','trample'}<=s.engine._combat_keywords(s.state.cards[spell.object_id]))
        self.private_replay(s,private)

    def test_rust_rejects_artifact_trigger_and_adventure_can_counter_that_departed_source_trigger(self):
        for i,adventure in enumerate((False,True)):
            with self.subTest(adventure=adventure):
                s,spell,private=self.fixture(113700340+i,counter='Emerald Dragon // Dissonant Wave' if adventure else 'Rust')
                source=self.add(s.engine,'Implement of Examination',seat='B')
                self.prepare(s,mana={'C':2,'G':1});self.checkpoint(s)
                before=set(s.state.players['B'].zones['hand'])
                self.activate(s,source)
                self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
                trigger=next(item for item in s.state.stack if item.kind=='triggered_ability')
                self.assertIn('artifact',stack_targeting_source(s.engine,trigger).card_types)
                if adventure:self.counter(s,spell,trigger.ref,adventure=True)
                else:
                    self.priority(s,'A')
                    self.reject(s,{'action_id':'cast:'+spell.ref,'targets':[trigger.ref],'pay':'manual','payment':{'G':1}})
                s=self.resume(s);self.assertIsNone(self.settle(s))
                drawn=set(s.state.players['B'].zones['hand'])-before
                self.assertEqual(1 if adventure else 2,len(drawn))
                for object_id in drawn:
                    for seat in 'ACD':self.assertNotIn(s.state.cards[object_id].ref,json.dumps(s.packet('pilot:'+seat,full=True)))
                self.private_replay(s,private)

    def test_countered_adventure_receives_neither_exile_nor_normal_face_permission(self):
        from quorune.adventure import ADVENTURE_PERMISSION
        s,spell,private=self.fixture(113700350,counter='Emerald Dragon // Dissonant Wave')
        source=self.add(s.engine,'Mind Stone',seat='B')
        response=self.add(s.engine,'Counterspell',seat='B',zone='hand')
        self.prepare(s,mana={'C':2,'G':1});self.checkpoint(s)
        self.activate(s,source)
        self.counter(s,spell,s.state.stack[-1].ref,adventure=True)
        original=s.state.stack[-1].ref
        self.cast(s,response,[original],seat='B',payment={'U':2})
        self.assertIsNone(self.settle(s))
        self.assertEqual('graveyard',s.state.cards[spell.object_id].zone)
        self.assertNotIn(ADVENTURE_PERMISSION,s.state.cards[spell.object_id].annotations)
        self.private_replay(s,private)

    def test_legacy_absent_marker_replays_nontargeted_ability_without_adding_source_book(self):
        s=self.session(113700330)
        s.state.ability_source_information_version=None
        source=self.add(s.engine,'Mind Stone',seat='B')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['B'].mana_pool['C']=1
        self.prepare(s,mana={});self.checkpoint(s)
        self.activate(s,source)
        self.assertNotIn(TARGETING_SOURCE_CONTEXT,s.state.stack[-1].context)
        s=self.resume(s)
        self.assertIsNone(s.state.ability_source_information_version)
        self.assertNotIn('ability_source_information_version',s.state.to_dict())
        self.assertIsNone(self.settle(s))
        self.private_replay(s,private)

    def test_creature_source_abilities_are_rejected_without_payment_and_still_deal_damage(self):
        for i,adventure in enumerate((False,True)):
            with self.subTest(adventure=adventure):
                s,spell,private=self.fixture(113700360+i,counter='Emerald Dragon // Dissonant Wave' if adventure else 'Rust')
                source=self.add(s.engine,'Prodigal Pyromancer',seat='B')
                source.acquired_control_turn_count=-1
                mana={'C':2,'G':1} if adventure else {'G':1}
                self.prepare(s,mana=mana);self.checkpoint(s)
                self.activate(s,source,needle='damage',targets=['A'])
                self.priority(s,'A')
                self.assertFalse(any(row.get('card')==spell.ref and (not adventure or row.get('face')=='Dissonant Wave')
                    for row in self.actions(s)))
                command={'action_id':'cast:'+spell.ref+(':face:Dissonant Wave' if adventure else ''),
                    'targets':[s.state.stack[-1].ref],'pay':'manual','payment':mana}
                if adventure:command['face']='Dissonant Wave'
                self.reject(s,command)
                s=self.resume(s);self.assertIsNone(self.settle(s))
                self.assertEqual(39,s.state.players['A'].life)
                self.assertEqual('hand',s.state.cards[spell.object_id].zone)
                for symbol,amount in mana.items():self.assertEqual(amount,s.state.players['A'].mana_pool[symbol])
                self.private_replay(s,private)

    def test_all_illegal_adventure_targets_give_no_exile_permission(self):
        from quorune.adventure import ADVENTURE_PERMISSION
        s,spell,private=self.fixture(113700370,counter='Emerald Dragon // Dissonant Wave')
        source=self.add(s.engine,'Rod of Ruin',seat='B')
        response=self.add(s.engine,'Rust',seat='B',zone='hand')
        self.prepare(s,mana={'C':2,'G':1});self.checkpoint(s)
        self.activate(s,source,needle='damage',targets=['A'])
        original=s.state.stack[-1].ref
        self.counter(s,spell,original,adventure=True)
        self.cast(s,response,[original],seat='B',payment={'G':1})
        s=self.resume(s);self.assertIsNone(self.settle(s))
        self.assertEqual(40,s.state.players['A'].life)
        self.assertEqual('graveyard',s.state.cards[spell.object_id].zone)
        self.assertNotIn(ADVENTURE_PERMISSION,s.state.cards[spell.object_id].annotations)
        self.private_replay(s,private)

    def test_source_and_adventure_authority_withdrawals_are_specific_rules_blocks(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from quorune.oracle_ir import compile_oracle_card
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        tests=set(discover_tests(ROOT).values())
        for cap in ('target.ability_source.current_or_lki','casting.lifecycle.adventure','stack.counter.effect','target.revalidate_resolution'):
            changed=deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id']==cap).update(status='blocked',
                blockers=['withdrawn source-filter or Adventure authority'],dependency_fail_closed_status='not_run')
            registry=CapabilityRegistry(changed)
            index=build_index(registry_value=changed,declaration_source=declarations,discovered_test_ids=tests)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            names=('Emerald Dragon // Dissonant Wave',) if cap=='casting.lifecycle.adventure' else (
                'Rust','Emerald Dragon // Dissonant Wave')
            for name in names:
                ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
                blockers=[blocker for residual in ir.material_residuals for blocker in residual.blockers]
                self.assertTrue(any('status:'+cap+':blocked' in blocker for blocker in blockers),blockers)
                self.assertFalse(any('evidence_index:unverified' in blocker for blocker in blockers),blockers)

    def test_source_and_adventure_programs_require_declared_intrinsic_authority(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        for i,name in enumerate(('Rust','Emerald Dragon // Dissonant Wave')):
            s,card,private=self.fixture(113700380+i,counter=name)
            program=next(program for program in s.engine.semantics.programs_for_oracle(card.oracle_id)
                if any(effect.get('op')=='counter_stack_target' for effect in program.effects))
            required=['target.ability_source.current_or_lki']
            if i:required.append('casting.lifecycle.adventure')
            for cap in required:
                dependencies=tuple(value for value in program.capability_dependencies if value!=cap)
                missing=replace(program,capability_dependencies=dependencies,
                    capability_closure=self.registry.closure(dependencies,profile='commander_review').to_dict())
                bound=bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(bound['strict'])
                self.assertTrue(any(cap in blocker for blocker in bound['blockers']),bound['blockers'])


if __name__=='__main__':unittest.main()
