"""Independent printed target occurrences reuse the existing spell composer."""
import unittest
from types import SimpleNamespace
from quorune.oracle_ir import compile_oracle_card,generated_programs
from quorune.rules.capabilities import load_default_capability_registry
from test_granted_skulk import record


class IndependentTargetClauseCompilerTests(unittest.TestCase):
    def test_independent_bounded_target_sets_do_not_become_one_shared_set(self):
        from test_public_query_effect_amounts import query_amount_record,_NoRulingsDatabase
        from quorune.card_programs import compile_card_program,bind_card_program_runtime
        text=('Target opponent loses 1 life and you gain 1 life.\n'
            'Up to one target creature gets +1/+1 until end of turn.\n'
            'Up to one target creature gets -1/-1 until end of turn.')
        registry=load_default_capability_registry()
        program=compile_card_program(_NoRulingsDatabase(),query_amount_record(text,suffix=365211711),
            capability_registry=registry,capability_profile='commander_review',trust_level='trusted')
        self.assertEqual(3,len(program.abilities[0].target_schema['groups']))
        bound=bind_card_program_runtime(program,capability_registry=registry,profile='commander_review')
        self.assertTrue(bound['strict_capability_ready'],bound['blockers'])
        from copy import deepcopy
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        ability=program.abilities[0];effects=deepcopy(ability.effects)
        self.assertIn('cards',effects[1]);self.assertIn('cards',effects[2])
        effects[2]['cards']=effects[1]['cards']
        bad=replace(ability,effects=effects)
        bound=bind_semantic_program_runtime(bad,capability_registry=registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertTrue(any('component owners and target roles' in b for b in bound['blockers']))

    def program(self,text):
        registry=load_default_capability_registry();card=record(text,'Instant')
        ir=compile_oracle_card(card,capability_registry=registry)
        self.assertEqual('exact',ir.status,ir.material_residuals)
        programs=generated_programs(SimpleNamespace(rulings=lambda _:()),card,capability_registry=registry)
        self.assertEqual(1,len(programs));return ir,programs[0]

    def test_single_line_another_clause_preserves_distinct_roles_and_exact_source_spans(self):
        text='Target creature gets +1/+1 until end of turn. Another target creature gets -1/-1 until end of turn.'
        ir,program=self.program(text)
        self.assertEqual(2,len(ir.faces[0].nodes));self.assertTrue(program.target_schema.get('globally_distinct',False))
        self.assertEqual(['$target_group.clause_0.0','$target_group.clause_1.0'],[e['card'] for e in program.effects])
        self.assertEqual([(1,1),(-1,-1)],[(e['power'],e['toughness']) for e in program.effects])
        for node in ir.faces[0].nodes:self.assertEqual(node.text,text[node.span.start:node.span.end])
        self.assertEqual(['clause_0','clause_1'],[g['id'] for g in program.target_schema['groups']])

    def test_unqualified_target_occurrences_remain_separate_and_allow_the_same_object(self):
        text='Target creature gets +3/+0 until end of turn. Target creature gets +0/+3 until end of turn.'
        _ir,program=self.program(text)
        self.assertEqual(2,len(program.target_schema['groups']));self.assertFalse(program.target_schema.get('globally_distinct',False))
        self.assertEqual(['$target_group.clause_0.0','$target_group.clause_1.0'],[e['card'] for e in program.effects])

    def test_target_source_audit_keeps_previous_clause_distinctness_and_rejects_forged_context(self):
        from dataclasses import replace
        from quorune.compiler.target_effect_corpus_assurance import TargetEffectCorpusCollector,TargetEffectAssuranceError
        from quorune.compiler.program_composition import DISTINCT_CLAUSE_TARGET_COVERAGE
        text='Target creature gets +1/+1 until end of turn. Another target creature gets -1/-1 until end of turn.'
        card=record(text,'Instant');ir,_program=self.program(text)
        collector=TargetEffectCorpusCollector();collector.observe(card,ir);self.assertEqual(2,len(collector._observations))
        face=ir.faces[0];first,second=face.nodes
        changed=replace(second,runtime_coverage=tuple(v for v in second.runtime_coverage if v!=DISTINCT_CLAUSE_TARGET_COVERAGE))
        with self.assertRaisesRegex(TargetEffectAssuranceError,'differs from source lowering'):
            TargetEffectCorpusCollector().observe(card,replace(ir,faces=(replace(face,nodes=(first,changed)),)))
        changed=replace(first,runtime_coverage=(*first.runtime_coverage,DISTINCT_CLAUSE_TARGET_COVERAGE))
        with self.assertRaisesRegex(TargetEffectAssuranceError,'does not match its complete printed source'):
            TargetEffectCorpusCollector().observe(card,replace(ir,faces=(replace(face,nodes=(changed,second)),)))

    def test_stat_and_counter_source_keeps_leaf_rejection_and_complete_independent_composition(self):
        from quorune.compiler.fixed_target_effect_sequences import fixed_target_effect_sequence_template,fixed_target_characteristics_effect_template
        text='Target creature gets +1/+1 until end of turn. Put a +1/+1 counter on target creature.'
        self.assertIsNone(fixed_target_characteristics_effect_template(text))
        self.assertIsNone(fixed_target_effect_sequence_template(text,card_name='Generic Source'))
        ir,program=self.program(text)
        self.assertEqual(2,len(ir.faces[0].nodes));self.assertEqual(2,len(program.target_schema['groups']))
        self.assertFalse(program.target_schema.get('globally_distinct',False))
        self.assertEqual(2,len(program.effects));self.assertEqual((1,1),(program.effects[0]['power'],program.effects[0]['toughness']))

    def test_unrepresented_sibling_choices_and_ambiguous_another_chains_stay_residual(self):
        registry=load_default_capability_registry()
        for text in ('Target creature gets +1/+1 until end of turn. Another target creature gets -1/-1 until end of turn. Rewrite history.',
                'Target creature gets +1/+1 until end of turn. Another target creature gets -1/-1 until end of turn. Another target creature gains flying until end of turn.'):
            self.assertNotEqual('exact',compile_oracle_card(record(text,'Instant'),capability_registry=registry).status)

    def test_existing_composition_certificate_rejects_a_dropped_distinct_constraint_or_marker(self):
        from dataclasses import replace
        from copy import deepcopy
        from quorune.compiler.program_composition import is_closed_composed_spell_effect_program,DISTINCT_CLAUSE_TARGET_COVERAGE
        from quorune.compiler.program_generation import _closed_effect_recognizers
        _,program=self.program('Target creature gets +1/+1 until end of turn. Another target creature gets -1/-1 until end of turn.')
        closed=lambda p:is_closed_composed_spell_effect_program(p,component_recognizers=_closed_effect_recognizers())
        self.assertTrue(closed(program))
        self.assertFalse(closed(replace(program,target_schema={k:v for k,v in program.target_schema.items() if k!='globally_distinct'})))
        provenance=deepcopy(program.provenance)
        for component in provenance['components']:component['coverage']=[v for v in component['coverage'] if v!=DISTINCT_CLAUSE_TARGET_COVERAGE]
        self.assertFalse(closed(replace(program,provenance=provenance)))

    def test_another_role_marker_omission_mutation_is_killed(self):
        from unittest.mock import patch
        import quorune.compiler.independent_target_rows as owner
        original=owner.independent_target_source_rows
        def omitted(*args,**kwargs):
            rows,_spans=original(*args,**kwargs);return rows,set()
        with patch.object(owner,'independent_target_source_rows',side_effect=omitted):
            result=unittest.TestResult()
            IndependentTargetClauseCompilerTests('test_single_line_another_clause_preserves_distinct_roles_and_exact_source_spans').run(result)
        self.assertFalse(result.errors,result.errors);self.assertTrue(result.failures)


import json,tempfile
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database


class IndependentTargetClauseNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'independent-targets.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/independent-target-clause-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Independent target clauses',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',14)],['Isamaru, Hound of Konda'])

    def reject(self,s,seat,command):
        before=authoritative_state_hash(s.state);result=s.act('pilot:'+seat,command)
        self.assertFalse(result.ok,result.summary);self.assertEqual(before,authoritative_state_hash(s.state))

    def stats(self,s,card):return tuple(s.engine._numeric_stat(card.object_id,k) for k in ('power','toughness'))

    def test_actual_consume_strength_offers_distinct_roles_and_applies_only_each_roles_scalar_result(self):
        s=self.session(115360121);spell=self.add(s.engine,'Consume Strength',zone='hand')
        first=self.add(s.engine,'Phyrexian Walker',seat='B',ref='independent-positive')
        second=self.add(s.engine,'Phyrexian Walker',seat='D',ref='independent-negative')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='independent-private')
        self.assert_closed(s,spell);self.prepare(s,mana={'B':1,'G':1,'C':1});self.checkpoint(s)
        action=next(r for r in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if r['id']=='cast:'+spell.ref)
        self.assertEqual(2,len(action['target_schema']['groups']));self.assertTrue(action['target_schema']['globally_distinct'])
        self.reject(s,'A',{'action_id':action['id'],'targets':[first.ref,first.ref],'pay':'auto'})
        self.reject(s,'C',{'action_id':action['id'],'targets':[first.ref,second.ref],'pay':'auto'})
        self.cast(s,'A',spell,[first.ref,second.ref]);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual((2,5),self.stats(s,first));self.assertEqual((-2,1),self.stats(s,second))
        for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.replay(s,load=True)

    def test_actual_martial_glory_keeps_same_object_legal_and_partial_blink_is_local_to_its_role(self):
        for same in (True,False):
            with self.subTest(same=same):
                s=self.session(115360130+int(same));spell=self.add(s.engine,'Martial Glory',zone='hand')
                first=self.add(s.engine,'Phyrexian Walker',seat='B',ref='independent-martial-first')
                second=first if same else self.add(s.engine,'Phyrexian Walker',seat='D',ref='independent-martial-second')
                blink=self.add(s.engine,'Cloudshift',seat='B',zone='hand');self.add(s.engine,'Plains',seat='B')
                self.assert_closed(s,spell);self.assert_closed(s,blink)
                self.prepare(s,mana={'R':1,'W':1});self.checkpoint(s);self.cast(s,'A',spell,[first.ref,second.ref])
                if not same:self.cast(s,'B',blink,[first.ref]);self.resolve_top(s)
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual((3,6) if same else (0,3),self.stats(s,first))
                if not same:self.assertEqual((0,6),self.stats(s,second))
                self.replay(s,load=True)

    def test_actual_copy_rebinds_separate_roles_and_preserves_original_per_group_counts(self):
        s=self.session(115360140);spell=self.add(s.engine,'Martial Glory',zone='hand')
        copy=self.add(s.engine,'Reverberate',seat='B',zone='hand');s.state.players['B'].mana_pool.update({'R':2})
        targets={seat:self.add(s.engine,'Phyrexian Walker',seat=seat,ref='independent-copy-'+seat) for seat in 'ACD'}
        self.assert_closed(s,spell);self.assert_closed(s,copy)
        self.prepare(s,mana={'R':1,'W':1});self.checkpoint(s)
        self.cast(s,'A',spell,[targets['A'].ref,targets['A'].ref]);self.cast(s,'B',copy,[s.state.stack[-1].ref])
        decision=self.resolve(s);self.assertEqual('semantic.choice',decision.kind)
        self.reject(s,'B',{'action_id':'choose','targets':[targets['C'].ref]})
        self.reject(s,'A',{'action_id':'choose','targets':[targets[p].ref for p in 'CD']})
        s=self.replay(s,load=True);result=s.act('pilot:B',{'action_id':'choose','targets':[targets[p].ref for p in 'CD']});self.assertTrue(result.ok,result.summary)
        self.resolve_top(s);self.assertEqual((3,3),self.stats(s,targets['C']));self.assertEqual((0,6),self.stats(s,targets['D']))
        self.assertEqual((0,3),self.stats(s,targets['A']));self.assertIsNone(self.resolve(s))
        self.assertEqual((3,6),self.stats(s,targets['A']));self.replay(s,load=True)
