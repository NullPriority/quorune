"""Pinned Chalice rules: source counters matter at cast time only."""
from dataclasses import replace
from types import SimpleNamespace
from pathlib import Path
import json,tempfile,unittest
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card,register_generated_programs
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.source_counter_cast_value import SourceCounterCastValueSpec,source_counter_cast_runtime_dependencies,CAPABILITY
from scripts.build_test_database import build_fixture_database
import test_target_recipient_results as recipients
FIXTURE=ROOT/'tests/fixtures/source-counter-cast-value-cards.json'

class SourceCounterCastValueModelTests(unittest.TestCase):
    def test_current_numeric_facts_distinguish_zero_and_missing_information(self):
        spec=SourceCounterCastValueSpec('charge');source=SimpleNamespace(counters={})
        self.assertTrue(spec.matches(source,{'mana_value':0}));self.assertFalse(spec.matches(source,{'mana_value':1}))
        source.counters['charge']=3;self.assertTrue(spec.matches(source,{'mana_value':3}));self.assertFalse(spec.matches(source,{'mana_value':2}))
        for value in (None,True,-1,float('nan'),float('inf')):
            with self.assertRaises(ValueError):spec.matches(source,{'mana_value':value})
    def test_closed_condition_keeps_concrete_runtime_owner_without_annotations(self):
        spec=SourceCounterCastValueSpec('charge');self.assertEqual(spec,SourceCounterCastValueSpec.from_condition(spec.condition()))
        for change in ({'counter':''},{'schema_version':True},{'value':1},{'extra':True}):
            with self.assertRaises(ValueError):SourceCounterCastValueSpec.from_condition({**spec.condition(),**change})
        fields=dict(event='spell.cast',active_zone='battlefield',event_condition=spec.condition())
        self.assertIn(CAPABILITY,source_counter_cast_runtime_dependencies(SimpleNamespace(**fields)))
        for change in ({'event':'spell.cast_or_copy'},{'active_zone':'stack'}):
            with self.assertRaises(ValueError):source_counter_cast_runtime_dependencies(SimpleNamespace(**{**fields,**change}))

    def test_nonmatching_comparison_omission_is_killed(self):
        from unittest.mock import patch
        spec=SourceCounterCastValueSpec('charge');source=SimpleNamespace(counters={'charge':1})
        def witness():self.assertFalse(spec.matches(source,{'mana_value':2}))
        witness()
        with patch.object(SourceCounterCastValueSpec,'matches',return_value=True),self.assertRaises(AssertionError):witness()

class SourceCounterCastValueCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'source-counter.sqlite3'
        build_fixture_database([FIXTURE],path);cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    def test_actual_chalice_has_normal_complete_admission_and_runtime_minimum(self):
        from quorune.semantics import SemanticRegistry
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.binding import bind_card_program_runtime,bind_semantic_program_runtime
        c=self.db.lookup('Chalice of the Void');ir=compile_oracle_card(c,capability_registry=self.registry,capability_profile='commander_review');self.assertEqual('exact',ir.status,ir.material_residuals)
        semantics=SemanticRegistry();register_generated_programs(self.db,semantics,[c],trust_level='provisional',capability_registry=self.registry,capability_profile='commander_review',promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
        p=compile_best_available_card_program(self.db,c,semantic_registry=semantics,capability_registry=self.registry,capability_profile='commander_review');bound=bind_card_program_runtime(p,capability_registry=self.registry,profile='commander_review');self.assertTrue(bound['strict_capability_ready'],bound['blockers'])
        trigger=next(p for p in semantics.programs_for_oracle(c.oracle_id) if CAPABILITY in p.capability_dependencies and p.event=='spell.cast')
        deps=tuple(c for c in trigger.capability_dependencies if c!=CAPABILITY);altered=replace(trigger,coverage=(),handlers=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
        result=bind_semantic_program_runtime(altered,capability_registry=self.registry,profile='commander_review');self.assertFalse(result['strict']);self.assertTrue(any(CAPABILITY in b for b in result['blockers']))

    def test_genuine_source_counter_owner_withdrawal_keeps_unrelated_complete_spells(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id']==CAPABILITY).update(status='blocked',blockers=['counter comparison owner withdrawn'])
        reg=CapabilityRegistry(raw);idx=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        reg.mark_evidence_verified(validate_capability_evidence_index(idx,registry=reg))
        self.assertNotEqual('exact',compile_oracle_card(self.db.lookup('Chalice of the Void'),capability_registry=reg,capability_profile='commander_review').status)
        for name in ('Memnite','Raise the Alarm'):
            self.assertEqual('exact',compile_oracle_card(self.db.lookup(name),capability_registry=reg,capability_profile='commander_review').status)

class SourceCounterCastValueNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'source-counter-native.sqlite3'
        build_fixture_database([FIXTURE],path);cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Source counter comparison',[DeckEntry('Sliver Hivelord',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])
    def cast_x(self,s,source,x,*,reserve=None):
        self.priority(s,'A');action=self.action(s,'A',source);command={'action_id':action['id'],'x':x,'pay':'auto','reserve':reserve or {}}
        self.reject(s,'D',command);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
    def test_actual_chalice_zero_does_not_counter_own_cast_then_counters_zero_value_creature(self):
        s=self.session(2023100);source=self.add(s.engine,'Chalice of the Void',zone='hand');spell=self.add(s.engine,'Memnite',zone='hand')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='counter-zero-private')
        self.assert_closed(s,source);self.prepare(s);self.checkpoint(s);self.cast_x(s,source,0)
        self.assertEqual(1,len(s.state.stack));self.assertIsNone(self.resolve(s));self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.assertEqual(0,s.state.cards[source.object_id].counters.get('charge',0));self.cast_for(s,'A',spell)
        self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[spell.object_id].zone);self.finish(s,private)
    def test_actual_chalice_counters_any_caster_and_ignores_nonmatching_spell(self):
        s=self.session(2023110);source=self.add(s.engine,'Chalice of the Void',zone='hand')
        own=self.add(s.engine,'Shock',zone='hand');foreign=self.add(s.engine,'Shock',seat='B',zone='hand',ref='counter-foreign-shock')
        miss=self.add(s.engine,'Raise the Alarm',seat='B',zone='hand');private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='counter-one-private')
        s.state.players['B'].mana_pool.update({'R':1,'C':1,'W':1});self.prepare(s,mana={'C':2,'R':1});self.checkpoint(s)
        self.cast_x(s,source,1,reserve={'R':1});self.assertIsNone(self.resolve(s));self.assertEqual(1,s.state.cards[source.object_id].counters.get('charge'))
        self.cast_for(s,'A',own,['D']);self.assertIsNone(self.resolve(s));self.cast_for(s,'B',foreign,['D'],reserve={'C':1,'W':1});self.assertIsNone(self.resolve(s))
        self.assertEqual(40,s.state.players['D'].life);self.cast_for(s,'B',miss);self.assertIsNone(self.resolve(s))
        self.assertEqual(2,len([c for c in s.state.cards.values() if c.zone=='battlefield' and c.controller=='B' and not c.is_card_object]));self.finish(s,private)
    def test_actual_counter_change_and_source_departure_do_not_cancel_already_created_trigger(self):
        s=self.session(2023120);source=self.add(s.engine,'Chalice of the Void',zone='hand');tap=self.add(s.engine,'Coretapper')
        spell=self.add(s.engine,'Shock',zone='hand');remove=self.add(s.engine,'Disenchant',zone='hand')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='counter-response-private')
        self.assert_closed(s,source);self.assert_closed(s,tap);self.prepare(s,mana={'C':3,'R':1,'W':1});self.checkpoint(s)
        self.cast_x(s,source,1,reserve={'C':1,'R':1,'W':1});self.assertIsNone(self.resolve(s));self.cast_for(s,'A',spell,['D'],reserve={'C':1,'W':1});self.priority(s,'A')
        action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id']=='activate:'+tap.ref+':ab2')
        result=s.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve_top(s)
        self.assertEqual(3,s.state.cards[source.object_id].counters.get('charge'));self.cast_for(s,'A',remove,[source.ref]);self.resolve_top(s)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.assertIsNone(self.resolve(s))
        self.assertEqual(40,s.state.players['D'].life);self.assertEqual('graveyard',s.state.cards[spell.object_id].zone);self.finish(s,private)

    def test_actual_xx_spell_mana_value_is_twice_announced_x_on_the_stack(self):
        s=self.session(2023130);first=self.add(s.engine,'Chalice of the Void',zone='hand',ref='counter-first-chalice')
        second=self.add(s.engine,'Chalice of the Void',zone='hand',ref='counter-second-chalice')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='counter-xx-private')
        self.prepare(s,mana={'C':6});self.checkpoint(s);self.cast_x(s,first,2,reserve={'C':2});self.assertIsNone(self.resolve(s))
        self.assertEqual(2,s.state.cards[first.object_id].counters.get('charge'));self.cast_x(s,second,1);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[second.object_id].zone);self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));self.finish(s,private)

    def test_actual_matching_uncounterable_spell_triggers_but_still_resolves(self):
        s=self.session(2023140);source=self.add(s.engine,'Chalice of the Void',zone='hand');spell=self.add(s.engine,'Void Rend',seat='B',zone='hand')
        victim=self.add(s.engine,'Grizzly Bears');private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='counter-uncounterable-private')
        s.state.players['B'].mana_pool.update({'W':1,'U':1,'B':1});self.prepare(s,mana={'C':6});self.checkpoint(s)
        self.cast_x(s,source,3);self.assertIsNone(self.resolve(s));self.cast_for(s,'B',spell,[victim.ref])
        self.assertTrue(any(i.kind=='triggered_ability' and i.source_object_id==source.object_id for i in s.state.stack))
        self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[victim.object_id].zone);self.finish(s,private)

    def test_actual_spell_copy_is_not_another_cast_or_counter_trigger(self):
        s=self.session(2023150);source=self.add(s.engine,'Chalice of the Void',zone='hand');copier=self.add(s.engine,'Lithoform Engine')
        spell=self.add(s.engine,'Shock',zone='hand');private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='counter-copy-private')
        self.assert_closed(s,copier);self.prepare(s,mana={'C':5,'R':1});self.checkpoint(s)
        self.cast_x(s,source,1,reserve={'C':3,'R':1});self.assertIsNone(self.resolve(s));self.cast_for(s,'A',spell,['D'],reserve={'C':3})
        original=next(i.ref for i in s.state.stack if i.kind=='spell' and i.card_object_id==spell.object_id)
        self.priority(s,'A');action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id']=='activate:'+copier.ref+':ab2')
        result=s.act('pilot:A',{'action_id':action['id'],'targets':[original],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('semantic.choice',self.resolve(s).kind);self.assertTrue(s.act('pilot:A',{'action_id':'choose','targets':['B']}).ok)
        self.assertEqual(1,sum(i.kind=='triggered_ability' and i.source_object_id==source.object_id for i in s.state.stack))
        self.assertIsNone(self.resolve(s));self.assertEqual(38,s.state.players['B'].life);self.assertEqual(40,s.state.players['D'].life)
        self.assertEqual(2,len(s.engine._current_turn_history('spell_cast')));self.finish(s,private)

    def test_fresh_unconditional_comparison_mutant_is_killed_by_native_nonmatch(self):
        from unittest.mock import patch
        with patch.object(SourceCounterCastValueSpec,'matches',return_value=True),self.assertRaises(AssertionError):
            self.test_actual_chalice_counters_any_caster_and_ignores_nonmatching_spell()
