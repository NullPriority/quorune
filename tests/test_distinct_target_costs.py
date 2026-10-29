"""Pinned Hinata/Thaumaturge rulings count distinct objects, not occurrences."""

from dataclasses import replace
from itertools import combinations
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from quorune.object_predicate import ObjectQuerySpec
from quorune.targeted_spell_costs import (TargetedSpellCostSpec,TargetedSpellCostSource,
    TargetedCostSubject,validate_targeted_spell_cost_fact)
from quorune.compiler.targeted_spell_costs import targeted_spell_cost_template
from quorune.rules.casting.targeted_costs import target_cost_fact,_preview_option_rank
from quorune.rules.casting.distinct_target_costs import distinct_target_tax_assignments
from common import ROOT
from test_qualified_zone_event_queries import query_record
import test_job_select as actions
import test_public_target_spell_costs as source_actions
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.targeted_spell_costs import DISTINCT_TARGET_COST_CAPABILITY
from scripts.build_test_database import build_fixture_database


class DistinctTargetCostModelTests(unittest.TestCase):
    def source(self,key='count',*,generic=-1,kind='all',refs=()):
        spec=TargetedSpellCostSpec(TargetedCostSubject.DISTINCT_TARGETS,generic,5,
            caster_relation='source_controller',spell_predicate=ObjectQuerySpec(),counted_targets=kind)
        return TargetedSpellCostSource('provider','provider:1','A',key,spec,refs)

    def test_distinct_counts_ignore_repetition_and_creatures_ignore_other_target_kinds(self):
        targets=('creature:1','creature:1','player:B','artifact:1','spell:1')
        all_targets=self.source(generic=1)
        creatures=self.source('creature',kind='creatures',refs=('creature:1',))
        self.assertEqual(4,all_targets.adjustment(targets));self.assertEqual(-1,creatures.adjustment(targets))
        self.assertEqual(0,all_targets.adjustment(()));self.assertEqual(0,creatures.adjustment(('spell:1','artifact:1')))
        old=TargetedSpellCostSource('old','old:1','A','once',
            TargetedSpellCostSpec(TargetedCostSubject.SOURCE,2),('creature:1','artifact:1'))
        fact=target_cost_fact((all_targets,creatures,old),targets)
        self.assertEqual((3,6,1),(fact['schema_version'],fact['generic'],fact['generic_reduction']))
        self.assertEqual(6,validate_targeted_spell_cost_fact(fact))
        self.assertEqual(2,old.adjustment(targets))

    def test_version_five_is_closed_and_old_modifiers_and_facts_remain_exact(self):
        current=self.source().spec;self.assertEqual(current,TargetedSpellCostSpec.from_dict(current.to_dict()))
        for update in ({'schema_version':1},{'counted_targets':'occurrences'},{'counted_targets':None},
                       {'generic':0},{'caster_relation':'owner'},{'spell_predicate':ObjectQuerySpec(zones=('hand',)).to_dict()}, {'unknown':True}):
            with self.assertRaises((TypeError,ValueError)):
                TargetedSpellCostSpec.from_dict({**current.to_dict(),**update})
        old_source=TargetedSpellCostSource('old','old:1','A','once',TargetedSpellCostSpec(TargetedCostSubject.SOURCE,2),('target',))
        expected={'schema_version':1,'generic':2,'sources':[{'component_id':'once','source_ref':'old','source_identity':'old:1','controller':'A','generic':2}]}
        self.assertEqual(json.dumps(expected),json.dumps(target_cost_fact((old_source,),('target','target'))))
        for bad in ({'schema_version':[]},{'generic_reduction':-1},{'generic':3}):
            with self.assertRaises((TypeError,ValueError)):
                validate_targeted_spell_cost_fact({**target_cost_fact((self.source(),),('x',)),**bad})

    def test_count_witnesses_match_exhaustive_independent_amounts_and_owner_constraints(self):
        refs=tuple('target:'+str(index) for index in range(7));creatures=frozenset(refs[:4]);once_refs=frozenset(refs[2:5])
        sources=(self.source('all',generic=1),self.source('creatures',generic=-2,kind='creatures',refs=tuple(creatures)),
            TargetedSpellCostSource('old','old:1','A','once',TargetedSpellCostSpec(TargetedCostSubject.SOURCE,3),tuple(once_refs)))
        snapshots={ref:{'owner':'A' if index%2 else 'B','controller':'C'} for index,ref in enumerate(refs)}
        host=SimpleNamespace(_target_candidate_map=lambda seat,plan,**kwargs:{group.group_id:refs for group in plan.groups},_target_snapshot=lambda ref:snapshots[ref])
        for same_owner in (False,True):
            schema={'zones':['battlefield'],'categories':['permanent'],'up_to':5,'same_owner':same_owner}
            expected=set();expected_refs=set()
            for size in range(6):
                for chosen in combinations(refs,size):
                    if same_owner and chosen and len({snapshots[ref]['owner'] for ref in chosen})!=1:continue
                    expected.add((size,size+3*bool(set(chosen)&once_refs),2*len(set(chosen)&creatures)))
                    expected_refs.update(chosen)
            with patch('quorune.rules.casting.distinct_target_costs.spell_targeting_source',return_value=None):
                rows=tuple(distinct_target_tax_assignments(host,'A',SimpleNamespace(ref='spell'),schema,sources))
            actual={(count,target_cost_fact(sources,chosen)['generic'],target_cost_fact(sources,chosen)['generic_reduction'])
                for chosen,_grouped,count in rows}
            self.assertEqual(expected,actual)
            self.assertEqual(expected_refs,{ref for chosen,_groups,_count in rows for ref in chosen})

    def test_fixed_count_witnesses_keep_each_reference_without_enumerating_subsets(self):
        refs=tuple('object:'+str(index) for index in range(30))
        host=SimpleNamespace(_target_candidate_map=lambda seat,plan,**kwargs:{group.group_id:refs for group in plan.groups},
            _target_snapshot=lambda ref:{'owner':'A','controller':'A'})
        sources=(self.source(generic=1),self.source('creatures',kind='creatures',refs=refs[:15]))
        schema={'zones':['battlefield'],'categories':['permanent'],'up_to':30}
        with patch('quorune.rules.casting.distinct_target_costs.spell_targeting_source',return_value=None):
            rows=tuple(distinct_target_tax_assignments(host,'A',SimpleNamespace(ref='spell'),schema,sources,9))
        self.assertEqual(set(refs),{ref for chosen,_groups,_count in rows for ref in chosen})
        self.assertTrue(all(count==9 and len(set(chosen))==9 for chosen,_groups,count in rows))
        self.assertLess(len(rows),600)

    def test_counted_x_preview_prefers_supported_maximum_over_zero_target_cheapest_quote(self):
        def option(maximum,generic):
            return SimpleNamespace(to_dict=lambda:{'requirements':{'GENERIC':generic,'U':1},
                'targeted_cost':{'schema_version':3},'choice_schema':{'x':{'maximum':maximum}}})
        self.assertLess(_preview_option_rank(option(3,3)),_preview_option_rank(option(0,0)))

    def test_count_modifier_grammar_preserves_caster_and_spell_domain(self):
        cases=(('Spells you cast cost {1} less to cast for each target.','all',-1,'source_controller'),
            ('Spells your opponents cast cost {1} more to cast for each target.','all',1,'source_opponents'),
            ('Each instant and sorcery spell you cast costs {1} less to cast for each creature it targets.','creatures',-1,'source_controller'))
        for text,kind,amount,relation in cases:
            result=targeted_spell_cost_template(text,source_name='Generic Source')
            self.assertIsNotNone(result)
            spec=TargetedSpellCostSpec.from_dict(result[1]['modifier'])
            self.assertEqual((5,kind,amount,relation),(spec.schema_version,spec.counted_targets,spec.generic,spec.caster_relation))
            if kind=='creatures':self.assertEqual(('instant','sorcery'),spec.spell_predicate.types_any)


class DistinctTargetCostCompilerTests(unittest.TestCase):
    def test_complete_clauses_bind_count_kind_and_reject_unrepresented_counting(self):
        registry=load_default_capability_registry()
        for text in ('Spells you cast cost {1} less to cast for each target.',
                     'Spells your opponents cast cost {1} more to cast for each target.',
                     'Each instant and sorcery spell you cast costs {1} less to cast for each creature it targets.'):
            ir=compile_oracle_card(query_record(text),capability_registry=registry)
            self.assertEqual('exact',ir.status,ir.material_residuals)
            node,=ir.faces[0].nodes
            self.assertEqual(text,query_record(text).oracle_text[node.span.start:node.span.end])
            self.assertEqual(5,node.handlers[0]['modifier']['schema_version'])
            self.assertIn(DISTINCT_TARGET_COST_CAPABILITY,node.capability_dependencies)
        for text in ('Spells you cast cost {X} less to cast for each target.',
                     'Spells you cast cost {U} less to cast for each target.',
                     'Spells you cast cost {1} less to cast for each target beyond the second.',
                     'Spells you cast cost {1} less to cast for each word target in its text.'):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry).status)

    def test_missing_distinct_or_base_capability_fails_closed_with_unrelated_control(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        discovered=set(discover_tests(ROOT).values())
        record=query_record('Spells you cast cost {1} less to cast for each target.')
        for omitted in (DISTINCT_TARGET_COST_CAPABILITY,'casting.cost.targeted_fixed_generic','variant.monarch.designate'):
            value=deepcopy(raw)
            next(row for row in value['capabilities'] if row['id']==omitted).update(status='blocked',blockers=['distinct target owner omitted'])
            registry=CapabilityRegistry(value)
            index=build_index(registry_value=value,declaration_source=declarations,discovered_test_ids=discovered)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            self.assertEqual(omitted=='variant.monarch.designate',compile_oracle_card(record,capability_registry=registry).status=='exact',omitted)


class DistinctTargetCostNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=actions.JobSelectRuntimeTests.session
    add=actions.JobSelectRuntimeTests.add
    prepare=actions.JobSelectRuntimeTests.prepare
    cast=actions.JobSelectRuntimeTests.cast
    checkpoint=actions.JobSelectRuntimeTests.checkpoint
    resolve=actions.JobSelectRuntimeTests.resolve
    replay=actions.JobSelectRuntimeTests.replay
    priority=source_actions.PublicTargetSpellCostNativeTests.priority
    cast_reserved=source_actions.PublicTargetSpellCostNativeTests.cast_reserved

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'distinct-targets.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/distinct-target-cost-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Distinct target costs',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',24)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def action(self,session,seat,card):
        packet=session.packet('pilot:'+seat,full=True)
        self.assertIsNotNone(packet['decision'],'Expected an issued decision for the casting principal')
        offered=next((row for row in packet['decision']['ctx']['legal']['actions']
            if row['id']=='cast:'+card.ref),None)
        self.assertIsNotNone(offered,'Supported legal cast must be advertised: '+card.printed_name)
        return offered

    def test_actual_hinata_reduction_counts_distinct_objects_after_other_increases(self):
        session=self.session(115_909_501)
        source=self.add(session.engine,'Hinata, Dawn-Crowned',zone='hand')
        self.add(session.engine,'Sphere of Resistance',seat='D')
        first=self.add(session.engine,'Memnite');second=self.add(session.engine,'Goblin Piker',seat='B')
        spell=self.add(session.engine,'Common Bond',zone='hand')
        private=self.add(session.engine,'Twiddle',seat='C',zone='hand')
        self.prepare(session,mana={'C':2,'U':1,'R':1,'W':2,'G':1});self.checkpoint(session)
        self.cast_reserved(session,source,reserve={'G':1,'W':1});self.assertIsNone(self.resolve(session))
        offered=self.action(session,'A',spell)
        unchanged=authoritative_state_hash(session.state)
        command={'action_id':offered['id'],'targets':[first.ref,second.ref],'pay':'auto'}
        self.assertFalse(session.act('pilot:C',command).ok)
        self.assertFalse(session.act('pilot:A',{**command,'targets':[first.ref,first.ref]}).ok)
        self.assertEqual(unchanged,authoritative_state_hash(session.state))
        for seat in 'ABD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        accepted=session.act('pilot:A',command);self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        for card in (first,second):self.assertEqual(1,session.state.cards[card.object_id].counters.get('+1/+1',0))
        self.replay(session,load=True)

    def test_actual_hinata_opponent_tax_counts_a_repeated_target_once_and_player_targets(self):
        session=self.session(115_909_502)
        self.add(session.engine,'Hinata, Dawn-Crowned')
        first=self.add(session.engine,'Memnite',seat='A');first.owner='C'
        second=self.add(session.engine,'Goblin Piker',seat='A')
        spell=self.add(session.engine,'Common Bond',seat='B',zone='hand')
        shock=self.add(session.engine,'Shock',seat='D',zone='hand')
        session.state.players['D'].mana_pool['R']=1
        self.prepare(session,actor='B',active='B',mana={'C':2,'G':1,'W':1});self.checkpoint(session)
        offered=self.action(session,'B',spell);unchanged=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':offered['id'],'targets':[first.ref,second.ref],'pay':'auto'}).ok)
        self.assertEqual(unchanged,authoritative_state_hash(session.state))
        accepted=session.act('pilot:B',{'action_id':offered['id'],'targets':[first.ref,first.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertEqual(0,sum(session.state.players['B'].mana_pool.values()))
        self.priority(session,'D')
        self.assertNotIn('cast:'+shock.ref,[row['id'] for row in session.packet('pilot:D',full=True)['decision']['ctx']['legal']['actions']])
        self.assertIsNone(self.resolve(session));self.assertEqual(2,session.state.cards[first.object_id].counters.get('+1/+1',0))
        self.replay(session,load=True)

    def test_actual_hinata_advertises_and_pays_nine_target_x_with_only_colored_mana(self):
        session=self.session(115_909_503)
        source=self.add(session.engine,'Hinata, Dawn-Crowned',zone='hand')
        spell=self.add(session.engine,'Gridlock',zone='hand')
        targets=[self.add(session.engine,'Memnite',seat='B',ref='hinata-x-'+str(index)) for index in range(9)]
        self.add(session.engine,'Sol Ring',seat='D')
        self.prepare(session,mana={'C':1,'U':2,'R':1,'W':1});self.checkpoint(session)
        self.cast_reserved(session,source,reserve={'U':1});self.assertIsNone(self.resolve(session))
        offered=self.action(session,'A',spell)
        maximum=offered['cost_options'][0]['choice_schema']['x']['maximum']
        self.assertGreaterEqual(maximum,9)
        accepted=session.act('pilot:A',{'action_id':offered['id'],'x':9,'targets':[card.ref for card in targets],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertTrue(all(session.state.cards[card.object_id].tapped for card in targets));self.replay(session,load=True)

    def test_actual_thaumaturge_counts_only_creatures_and_instant_sorcery_spells(self):
        session=self.session(115_909_504)
        source=self.add(session.engine,'Battlefield Thaumaturge',zone='hand')
        gridlock=self.add(session.engine,'Gridlock',zone='hand')
        aura=self.add(session.engine,'Oakenform',zone='hand')
        elf=self.add(session.engine,'Llanowar Elves',zone='hand')
        targets=[self.add(session.engine,'Memnite',seat='B',ref='thaum-x-'+str(index)) for index in range(9)]
        artifact=self.add(session.engine,'Sol Ring',seat='D')
        self.prepare(session,mana={'C':1,'U':2,'G':1});self.checkpoint(session)
        self.cast_reserved(session,source,reserve={'U':1,'G':1});self.assertIsNone(self.resolve(session))
        self.assertNotIn('cast:'+aura.ref,[row['id'] for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']])
        self.cast(session,'A',elf);self.assertIsNone(self.resolve(session))
        offered=self.action(session,'A',gridlock);self.assertGreaterEqual(offered['cost_options'][0]['choice_schema']['x']['maximum'],9)
        unchanged=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',{'action_id':offered['id'],'x':9,'targets':[card.ref for card in targets[:8]]+[artifact.ref],'pay':'auto'}).ok)
        self.assertEqual(unchanged,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'action_id':offered['id'],'x':9,'targets':[source.ref]+[card.ref for card in targets[:8]],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertEqual(2,len(session.state.stack))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertIn('hexproof',session.engine._combat_keywords(session.state.cards[source.object_id]))
        self.assertTrue(session.state.cards[source.object_id].tapped);self.replay(session,load=True)

    def test_actual_hinata_opponent_x_tax_keeps_the_largest_supported_positive_x(self):
        session=self.session(115_909_506)
        self.add(session.engine,'Hinata, Dawn-Crowned',seat='A')
        spell=self.add(session.engine,'Gridlock',seat='B',zone='hand')
        targets=[self.add(session.engine,'Memnite',seat='C',ref='opponent-x-'+str(index)) for index in range(9)]
        self.prepare(session,actor='B',active='B',mana={'C':18,'U':1});self.checkpoint(session)
        offered=self.action(session,'B',spell)
        self.assertEqual(9,offered['cost_options'][0]['choice_schema']['x']['maximum'])
        accepted=session.act('pilot:B',{'action_id':offered['id'],'x':9,'targets':[card.ref for card in targets],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertEqual(0,sum(session.state.players['B'].mana_pool.values()))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertTrue(all(session.state.cards[card.object_id].tapped for card in targets));self.replay(session,load=True)

    def test_actual_strive_increases_precede_thaumaturge_reductions_and_keep_colored_fees(self):
        session=self.session(115_909_505)
        source=self.add(session.engine,'Battlefield Thaumaturge',zone='hand')
        target=self.add(session.engine,'Memnite')
        spell=self.add(session.engine,"Ajani's Presence",zone='hand')
        self.prepare(session,mana={'C':1,'U':1,'W':2});self.checkpoint(session)
        self.cast_reserved(session,source,reserve={'W':2});self.assertIsNone(self.resolve(session))
        offered=self.action(session,'A',spell)
        self.assertTrue(any(row.get('target_count')==2 for row in offered['cost_options'][0]['target_costs']['prices']))
        accepted=session.act('pilot:A',{'action_id':offered['id'],'targets':[source.ref,target.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.assertIsNone(self.resolve(session));self.assertIn('indestructible',session.engine._combat_keywords(session.state.cards[target.object_id]))
        self.replay(session,load=True)

    def test_occurrence_count_mutant_is_killed_by_actual_hinata_repeated_target(self):
        original=TargetedSpellCostSource.adjustment
        def occurrences(value,targets):
            if value.spec.schema_version==5:
                return value.spec.generic*sum(value.matches_target(ref) for ref in targets)
            return original(value,targets)
        with patch.object(TargetedSpellCostSource,'adjustment',occurrences),self.assertRaises(AssertionError):
            self.test_actual_hinata_opponent_tax_counts_a_repeated_target_once_and_player_targets()


if __name__=='__main__':unittest.main()
