from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card,generated_programs
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.closed_effect_program_shapes import closed_effect_program_runtime_dependencies,CLOSED_EFFECT_PROGRAM_CAPABILITY as CLOSED
from quorune.card_programs.modal_catalog_equivalence import shadowed_reviewed_modal_catalog_keys
from quorune.compiler.activated_ability_catalog import with_activated_ability_catalog
from quorune.semantics import SemanticRegistry
from quorune.util import stable_json
from scripts.build_test_database import build_fixture_database
import test_all_kind_counter_doubling as controls
from quorune.rules.evidence_storage import read_evidence_document

NAMES=('Cankerbloom','Insidious Fungus')
MODAL='choice.modal.fixed_nonrepeating'


class ModalActivationAdmissionTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.AllKindCounterTests.session
    add=controls.AllKindCounterTests.add
    prepare=controls.AllKindCounterTests.prepare
    checkpoint=controls.AllKindCounterTests.checkpoint
    resolve=controls.AllKindCounterTests.resolve
    replay=controls.AllKindCounterTests.replay
    binding=controls.AllKindCounterTests.binding
    actions=controls.AllKindCounterTests.actions
    cast=controls.AllKindCounterTests.cast
    reject=controls.AllKindCounterTests.reject
    private_replay=controls.AllKindCounterTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'modal-admission.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/modal-activation-admission-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Complete modal activations',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def activation(self,s,source):
        return next((a for a in self.actions(s) if a['id'].startswith('activate:'+source.ref+':')),None)

    def programs(self,name):
        record=self.db.lookup(name)
        current=tuple(generated_programs(self.db,record,trust_level='trusted',capability_registry=self.registry,capability_profile='commander_review'))
        old=tuple(with_activated_ability_catalog(record,SemanticRegistry().programs_for_oracle(record.oracle_id),reference_programs=current))
        return record,current,old

    def test_ordered_modal_scope_uses_the_branch_and_requires_every_sibling_owner(self):
        _record,current,_old=self.programs('Insidious Fungus');program=current[0]
        self.assertEqual([],program.effects)
        self.assertIn(CLOSED,closed_effect_program_runtime_dependencies(program))
        self.assertIn(CLOSED,closed_effect_program_runtime_dependencies(replace(program,coverage=[v for v in program.coverage if v!='closed-effect-program'])))
        for effects in ([],[{'op':'draw','player':'$controller','count':1,'private':True}],[{'op':'unknown'}]*2):
            schema=deepcopy(program.target_schema);schema['modes']['mode_3']['effects']=effects
            with self.assertRaises(ValueError):closed_effect_program_runtime_dependencies(replace(program,target_schema=schema))
        schema=deepcopy(program.target_schema);schema['modes']['mode_1']['effects']=[{'op':'unknown'}]
        with self.assertRaises(ValueError):closed_effect_program_runtime_dependencies(replace(program,target_schema=schema))
        for effects in ([],[{'op':'draw','player':'$controller','count':1}],list(program.target_schema['modes']['mode_3']['effects'])*5):
            plain=replace(program,effects=effects,target_schema=None,coverage=['closed-effect-program'])
            with self.assertRaises(ValueError):closed_effect_program_runtime_dependencies(plain)

    def test_current_catalog_equivalence_is_source_cost_body_bound_and_never_mutates_snapshots(self):
        record,current,old=self.programs('Cankerbloom');self.assertEqual(1,len(current));self.assertEqual(1,len(old))
        before=stable_json([p.to_dict() for p in (*current,*old)])
        self.assertEqual({old[0].key},shadowed_reviewed_modal_catalog_keys(record,current,old))
        self.assertEqual(before,stable_json([p.to_dict() for p in (*current,*old)]))
        for key in ('source_oracle_hash','source_rulings_hash'):
            changed=replace(old[0],provenance={**old[0].provenance,key:'0'*64})
            self.assertEqual(set(),shadowed_reviewed_modal_catalog_keys(record,current,(changed,)))
        handlers=deepcopy(old[0].handlers);handlers[0]['ability']['mana']={'GENERIC':True}
        with self.assertRaises(ValueError):replace(old[0],handlers=handlers)
        for field,value in (('sacrifice_source',False),('cost_text','{0}'),('oracle_line','unknown source')):
            handlers=deepcopy(old[0].handlers);handlers[0]['ability'][field]=value
            changed=replace(old[0],handlers=handlers)
            self.assertEqual(set(),shadowed_reviewed_modal_catalog_keys(record,current,(changed,)))
        for update in ({'mode_count':True},{'min_modes':0},{'unknown':1}):
            schema={**deepcopy(old[0].target_schema),**update};handlers=deepcopy(old[0].handlers);handlers[0]['ability']['target_schema']=schema
            changed=replace(old[0],target_schema=schema,handlers=handlers)
            self.assertEqual(set(),shadowed_reviewed_modal_catalog_keys(record,current,(changed,)))
        schema=deepcopy(old[0].target_schema);schema['modes']['destroy_artifact']['effects']=[{'op':'draw','player':'$controller','count':1}]
        handlers=deepcopy(old[0].handlers);handlers[0]['ability']['target_schema']=schema
        self.assertEqual(set(),shadowed_reviewed_modal_catalog_keys(record,current,(replace(old[0],target_schema=schema,handlers=handlers),)))

    def test_both_originals_have_normal_strict_admission_and_independent_mode_scope_minima(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.session(797000)
        for name in NAMES:
            source=self.add(s.engine,name);bound=self.binding(s,source)
            self.assertTrue(bound['strict_capability_ready'],(name,bound['blockers']));self.assertEqual('capability_closed',bound['trust_basis'])
            self.assertEqual('exact',compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review').status)
            program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if p.event=='activate' and p.target_schema)
            for cap in (MODAL,*([CLOSED] if name=='Insidious Fungus' else [])):
                deps=tuple(c for c in program.capability_dependencies if c!=cap)
                changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(result['strict'])
                if cap==CLOSED:self.assertIn('capability:undeclared_runtime_dependency:'+CLOSED,result['blockers'])

    def test_original_canker_all_three_modes_keep_target_and_sacrifice_authority(self):
        for index in (1,2,3):
            s=self.session(797010+index);source=self.add(s.engine,'Cankerbloom')
            target=self.add(s.engine,'Sol Ring' if index!=2 else 'Glorious Anthem',seat='B');target.counters['charge']=1
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(s,mana={'C':1});self.checkpoint(s);action=self.activation(s,source);self.assertIsNotNone(action)
            self.assertIn('mode_3',action['target_schema']['legal_modes'])
            wrong='mode_2' if index!=2 else 'mode_1';self.assertNotIn(wrong,action['target_schema']['legal_modes'])
            self.reject(s,{'action_id':action['id'],'modes':[wrong],'targets':[target.ref],'pay':'auto'})
            command={'action_id':action['id'],'modes':['mode_'+str(index)],'targets':[target.ref] if index!=3 else [],'pay':'auto'}
            self.reject(s,command,'pilot:D');result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
            self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
            s=self.replay(s,load=True);pending=self.resolve(s)
            if index==3:
                self.assertIsNotNone(pending);self.assertEqual('semantic.choice',pending.kind)
                s=self.replay(s,load=True);result=s.act('pilot:A',{'action_id':'choose','objects':[target.ref]});self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
                self.assertEqual(2,s.state.cards[target.object_id].counters['charge'])
            else:self.assertIsNone(pending);self.assertEqual('graveyard',s.state.cards[target.object_id].zone)
            self.private_replay(s,private)

    def test_original_fungus_all_modes_and_private_draw_then_optional_tapped_land(self):
        for index,enter in ((1,False),(2,False),(3,False),(3,True)):
            s=self.session(797020+index+enter);source=self.add(s.engine,'Insidious Fungus')
            target=self.add(s.engine,'Sol Ring' if index!=2 else 'Glorious Anthem',seat='B')
            forest=self.add(s.engine,'Forest',zone='library',ref='fungus-drawn-land')
            library=s.state.players['A'].zones['library'];library.remove(forest.object_id);library.append(forest.object_id)
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(s,mana={'C':2});self.checkpoint(s);action=self.activation(s,source);self.assertIsNotNone(action)
            hand=len(s.state.players['A'].zones['hand'])
            result=s.act('pilot:A',{'action_id':action['id'],'modes':['mode_'+str(index)],'targets':[target.ref] if index!=3 else [],'pay':'auto'})
            self.assertTrue(result.ok,result.summary);self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
            s=self.replay(s,load=True);pending=self.resolve(s)
            if index==3:
                self.assertIsNotNone(pending);self.assertEqual('hand',s.state.cards[forest.object_id].zone)
                self.assertEqual(hand+1,len(s.state.players['A'].zones['hand']))
                self.reject(s,{'action_id':'choose','card':forest.ref if enter else None},'pilot:B')
                s=self.replay(s,load=True);result=s.act('pilot:A',{'action_id':'choose','card':forest.ref if enter else None})
                self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
                self.assertEqual('battlefield' if enter else 'hand',s.state.cards[forest.object_id].zone)
                if enter:self.assertTrue(s.state.cards[forest.object_id].tapped)
            else:self.assertIsNone(pending);self.assertEqual('graveyard',s.state.cards[target.object_id].zone)
            self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));self.private_replay(s,private)

    def test_fresh_scope_and_catalog_precedence_omissions_are_killed(self):
        method='test_both_originals_have_normal_strict_admission_and_independent_mode_scope_minima'
        with patch('quorune.card_programs.modal_catalog_equivalence.shadowed_reviewed_modal_catalog_keys',return_value=set()):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        def old_scope(program):
            if 'closed-effect-program' in program.coverage and not 2<=len(program.effects)<=8:raise ValueError('Closed ordered programs require two to eight effects')
            return (CLOSED,) if 'closed-effect-program' in program.coverage else ()
        with patch('quorune.card_programs.runtime_requirements.closed_effect_program_runtime_dependencies',side_effect=old_scope):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_countered_modal_activation_keeps_paid_costs_and_executes_no_other_mode(self):
        for index,name in enumerate(NAMES):
            s=self.session(797040+index);source=self.add(s.engine,name)
            stifle=self.add(s.engine,'Stifle',seat='B',zone='hand')
            target=self.add(s.engine,'Sol Ring',seat='B');target.counters['charge']=2
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            s.state.players['B'].mana_pool['U']=1;self.prepare(s,mana={'C':1+index});self.checkpoint(s)
            action=self.activation(s,source);self.assertIsNotNone(action)
            result=s.act('pilot:A',{'action_id':action['id'],'modes':['mode_3'],'targets':[],'pay':'auto'})
            self.assertTrue(result.ok,result.summary);self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
            hand=len(s.state.players['A'].zones['hand']);library=len(s.state.players['A'].zones['library'])
            self.cast(s,'B',stifle,[s.state.stack[-1].ref]);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
            self.assertEqual(hand,len(s.state.players['A'].zones['hand']));self.assertEqual(library,len(s.state.players['A'].zones['library']))
            self.assertEqual(2,s.state.cards[target.object_id].counters['charge'])
            self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));self.private_replay(s,private)

    def test_reviewed_snapshot_serialization_retains_legacy_mode_ids(self):
        from quorune.semantics import SemanticProgram
        record,current,old=self.programs('Cankerbloom');raw=deepcopy(old[0].to_dict())
        restored=SemanticProgram.from_dict(raw)
        self.assertEqual(stable_json(raw),stable_json(restored.to_dict()))
        self.assertEqual({'destroy_artifact','destroy_enchantment','proliferate'},set(restored.target_schema['modes']))
        saved=stable_json(restored.to_dict());self.assertEqual({restored.key},shadowed_reviewed_modal_catalog_keys(record,current,(restored,)))
        self.assertEqual(saved,stable_json(restored.to_dict()))

    def test_verified_ordered_and_mode_dependency_withdrawal_rejects_source(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        declaration=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        known=set(discover_tests(ROOT).values())
        for cap,name in ((CLOSED,'Insidious Fungus'),(MODAL,'Cankerbloom')):
            raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
            next(c for c in raw['capabilities'] if c['id']==cap).update(status='blocked',blockers=['withdrawn modal scope authority'],dependency_fail_closed_status='not_run')
            registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=declaration,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
            blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
