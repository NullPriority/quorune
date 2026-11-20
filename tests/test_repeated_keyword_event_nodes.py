"""Pinned CR702.46b: two Soulshift instances, two targets, separate resolution."""
import unittest
from dataclasses import replace
from unittest.mock import patch
from quorune.compiler.ir_model import SourceSpan
from quorune.compiler.repeated_keyword_event_nodes import numeric_keyword_clause_parts, repeated_numeric_keyword_nodes


class RepeatedNumericKeywordCompilerTests(unittest.TestCase):
    def lower(self,material='Soulshift 4, soulshift 4'):
        clauses=numeric_keyword_clause_parts(material)
        return repeated_numeric_keyword_nodes(node_id='front:n1',line=material,material_line=material,
            span=SourceSpan(start=7,end=7+len(material),line=2),mechanics=tuple(s.keyword for _,_,_,s in clauses),
            capability_registry=None,capability_profile='commander_review',residuals=[])

    def test_repeated_and_mixed_numeric_clauses_retain_each_owned_specification(self):
        parts=numeric_keyword_clause_parts('Soulshift 4, soulshift 4')
        self.assertEqual([('soulshift',4),('soulshift',4)],[(s.keyword,s.amount) for _,_,_,s in parts])
        parts=numeric_keyword_clause_parts('Annihilator 2, mobilize 1')
        self.assertEqual([('annihilator',2),('mobilize',1)],[(s.keyword,s.amount) for _,_,_,s in parts])

    def test_two_nodes_keep_distinct_source_spans_and_missing_authority_is_not_bypassed(self):
        material='Soulshift 4, soulshift 4';nodes=self.lower(material)
        self.assertEqual(2,len(nodes));self.assertEqual(2,len({n.node_id for n in nodes}))
        for node,(text,start,end,_) in zip(nodes,numeric_keyword_clause_parts(material),strict=True):
            self.assertEqual(text,material[node.span.start-7:node.span.end-7]);self.assertEqual((7+start,7+end,2),(node.span.start,node.span.end,node.span.line))
            self.assertEqual('permanent.graveyard.self',node.event);self.assertEqual(1,node.target_schema['count'])
            self.assertTrue(node.lowerable);self.assertFalse(node.exact);self.assertTrue(node.residual_ids)

    def test_unowned_zero_variable_empty_and_material_riders_remain_unrepresented(self):
        for bad in ('Soulshift 4','Soulshift 4,','Soulshift 4,, Soulshift 4','Soulshift 4, ,Soulshift 4',
                'Soulshift 0, Soulshift 4','Soulshift X, Soulshift 4','Soulshift 4, draw a card.',
                'Soulshift 4, flying','Cascade, cascade','Soulshift 4, Soulshift 4. Draw a card.'):
            self.assertIsNone(numeric_keyword_clause_parts(bad),bad)

    def test_registered_identity_keeps_numeric_instances_and_preserves_existing_identity_contract(self):
        from quorune.compiler.generated_program_identity import generated_ability_id
        def identity(node,kind='triggered_ability'):
            return generated_ability_id(kind=kind,face_id='front',line=2,static_declaration=False,node_id=node)
        self.assertEqual(['trigger:front:n2:numeric-instance:1','trigger:front:n2:numeric-instance:2'],
            [identity('front:n2:numeric-instance:'+str(i)) for i in (1,2)])
        self.assertEqual('trigger:front:n2',identity('front:n2'))
        self.assertEqual('trigger:front:n2:myriad:2',identity('front:n2:myriad:2'))
        self.assertEqual('ability:ab2:typecycling-instance:2',identity('front:n2:typecycling-instance:2','activated_ability'))

    def test_fresh_instance_identity_collapse_mutant_is_killed_after_positive_two_instances(self):
        self.assertEqual(2,len({n.node_id for n in self.lower()}))
        import quorune.compiler.repeated_keyword_event_nodes as owner
        original=owner.fixed_keyword_event_effect_node
        def collapsed(**kwargs):return replace(original(**kwargs),node_id='collapsed')
        with patch.object(owner,'fixed_keyword_event_effect_node',side_effect=collapsed):
            with self.assertRaises(AssertionError):self.assertEqual(2,len({n.node_id for n in self.lower()}))




from pathlib import Path
import json,tempfile
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_turn_life_loss_recovery as recovery_helpers


class RepeatedNumericKeywordNativeTests(InheritedNativeFixture,unittest.TestCase):
    start=recovery_helpers.TurnLifeLossRecoveryNativeTests.start
    reject=recovery_helpers.TurnLifeLossRecoveryNativeTests.reject
    finish=recovery_helpers.TurnLifeLossRecoveryNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();folder=Path(cls.temporary.name)
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[r for r in base['cards'] if r['name']=='Generic Bound Plains'];base['rulings']=[]
        basic=folder/'basic.json';basic.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([basic,ROOT/'tests/fixtures/repeated-keyword-event-cards.json'],folder/'repeated.sqlite3')
        cls.db=CardDatabase(folder/'repeated.sqlite3');cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Repeated numeric abilities',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def test_full_original_closes_normally_as_two_distinct_targeted_trigger_programs(self):
        s,_=self.start(723000);source=self.add(s.engine,'Forked-Branch Garami',zone='hand');self.assert_closed(s,source)
        ir=compile_oracle_card(self.db.lookup('Forked-Branch Garami'),capability_registry=self.registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        nodes=[n for f in ir.faces for n in f.nodes if n.event=='permanent.graveyard.self']
        self.assertEqual(2,len(nodes));self.assertEqual(2,len({n.node_id for n in nodes}))
        self.assertEqual([1,1],[n.target_schema['count'] for n in nodes])
        programs=[p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if p.event=='permanent.graveyard.self']
        self.assertEqual(2,len(programs));self.assertEqual(2,len({p.ability_id for p in programs}))

    def death_world(self,seed,response=None):
        s,private=self.start(seed);source=self.add(s.engine,'Forked-Branch Garami',zone='hand')
        small=self.add(s.engine,'Lantern Kami',zone='graveyard',ref='first-spirit')
        second=self.add(s.engine,'Kami of Ancient Law',zone='graveyard',ref='second-spirit')
        foreign=self.add(s.engine,'Lantern Kami',seat='B',zone='graveyard',ref='foreign-spirit')
        nonspirit=self.add(s.engine,'Memnite',zone='graveyard')
        kill=self.add(s.engine,'Doom Blade',zone='hand')
        for card in (source,small,second,foreign,nonspirit,kill):self.assert_closed(s,card)
        if response is not None:
            self.add(s.engine,response,seat='B',zone='hand' if response=='Stifle' else 'battlefield',ref='garami-response')
        s.state.players['B'].mana_pool.update({'U':1})
        self.prepare(s,mana={'G':2,'C':4,'B':1});self.checkpoint(s)
        before=sum(s.state.players['A'].mana_pool.values());self.cast(s,'A',source);self.assertIsNone(self.resolve(s))
        self.assertEqual(before-5,sum(s.state.players['A'].mana_pool.values()));self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.cast(s,'A',kill,[source.ref]);self.assertEqual('trigger.order',self.resolve(s).kind)
        refs=[item.ref for batch in s.state.pending_trigger_batches for item in batch.items]
        self.assertEqual(2,len(refs));self.assertEqual(2,len(set(refs)))
        command={'action_id':'order','triggers':refs};self.reject(s,'D',command)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('semantic.target',s.state.pending_decision.kind)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        return s,private,source,small,second,foreign,nonspirit

    def target_instances(self,s,source,small,second,foreign,nonspirit,*,same=False):
        for card in (small,small if same else second):
            decision=s.packet('pilot:A',full=True)['decision'];self.assertEqual('semantic.target',s.state.pending_decision.kind)
            schema=decision['legal_actions'][0]['target_schema'];legal=schema['legal_refs']
            self.assertEqual(1,schema['count']);self.assertIn(small.ref,legal);self.assertIn(second.ref,legal)
            for excluded in (source,foreign,nonspirit):
                self.assertNotIn(excluded.ref,legal);self.reject(s,'A',{'action_id':'choose','targets':[excluded.ref]})
            command={'action_id':'choose','targets':[card.ref]};self.reject(s,'D',command)
            result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        items=[i for i in s.state.stack if i.source_object_id==source.object_id]
        self.assertEqual(2,len(items));self.assertEqual(2,len({i.semantic_key for i in items}))

    def actual_returns(self,seed,*,same=False):
        s,private,source,small,second,foreign,nonspirit=self.death_world(seed)
        self.target_instances(s,source,small,second,foreign,nonspirit,same=same)
        s=self.replay(s,load=True)
        for _ in range(1 if same else 2):
            self.assertEqual('semantic.choice',self.resolve(s).kind)
            command={'action_id':'choose','choice':'apply'};self.reject(s,'B',command)
            result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s))
        self.assertEqual('hand',s.state.cards[small.object_id].zone)
        self.assertEqual('graveyard' if same else 'hand',s.state.cards[second.object_id].zone)
        for card in (source,foreign,nonspirit):self.assertEqual('graveyard',s.state.cards[card.object_id].zone)
        self.finish(s,private)

    def test_actual_five_mana_cast_death_and_two_distinct_returns_preserve_principal_and_replay(self):
        self.actual_returns(723001)

    def test_actual_two_instances_can_choose_same_target_and_second_fizzles(self):
        self.actual_returns(723002,same=True)

    def test_actual_independent_optional_results_allow_declining_only_one_instance(self):
        s,private,source,small,second,foreign,nonspirit=self.death_world(723010)
        self.target_instances(s,source,small,second,foreign,nonspirit)
        for choice in ('decline','apply'):
            self.assertEqual('semantic.choice',self.resolve(s).kind)
            result=s.act('pilot:A',{'action_id':'choose','choice':choice});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual(['graveyard','hand'],sorted(s.state.cards[c.object_id].zone for c in (small,second)))
        self.finish(s,private)

    def test_actual_stifle_counters_one_instance_and_leaves_the_other_independent(self):
        s,private,source,small,second,foreign,nonspirit=self.death_world(723011,response='Stifle')
        self.target_instances(s,source,small,second,foreign,nonspirit)
        response=next(c for c in s.state.cards.values() if c.ref=='garami-response');self.assert_closed(s,response)
        self.cast(s,'B',response,[s.state.stack[-1].ref]);self.resolve_top(s)
        self.assertEqual(1,len([i for i in s.state.stack if i.source_object_id==source.object_id]))
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        result=s.act('pilot:A',{'action_id':'choose','choice':'apply'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual(['graveyard','hand'],sorted(s.state.cards[c.object_id].zone for c in (small,second)))
        self.finish(s,private)

    def test_actual_graveyard_exile_response_invalidates_both_targets_without_optional_choices(self):
        s,private,source,small,second,foreign,nonspirit=self.death_world(723012,response="Tormod's Crypt")
        self.target_instances(s,source,small,second,foreign,nonspirit)
        response=next(c for c in s.state.cards.values() if c.ref=='garami-response');self.assert_closed(s,response)
        self.priority(s,'B');offered=next(a for a in s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+response.ref+':'))
        command={'action_id':offered['id'],'targets':['A'],'pay':'auto'};self.reject(s,'D',command)
        result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s))
        for card in (source,small,second,nonspirit):self.assertEqual('exile',s.state.cards[card.object_id].zone)
        self.assertEqual('graveyard',s.state.cards[foreign.object_id].zone);self.finish(s,private)

    def test_actual_return_fault_restores_pending_choice_zone_events_and_retries_once(self):
        from quorune import return_to_hand as owner
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        s,private,source,small,second,foreign,nonspirit=self.death_world(723013)
        self.target_instances(s,source,small,second,foreign,nonspirit)
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        original=owner.return_graveyard_card_to_owner_hand;observed=[]
        def fault(*args,**kwargs):
            result=original(*args,**kwargs);observed.append(sum(s.state.cards[c.object_id].zone=='hand' for c in (small,second)))
            raise GameRuleError('Injected post-Soulshift-return failure')
        before=authoritative_state_hash(s.state);command={'action_id':'choose','choice':'apply'}
        with patch.object(owner,'return_graveyard_card_to_owner_hand',side_effect=fault):
            result=s.act('pilot:A',command);self.assertFalse(result.ok);self.assertIn('Injected post-Soulshift',result.summary)
        self.assertEqual([1],observed);self.assertEqual(before,authoritative_state_hash(s.state))
        for card in (small,second):self.assertEqual('graveyard',s.state.cards[card.object_id].zone)
        s=self.replay(s,load=True);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        for card in (small,second):self.assertEqual('hand',s.state.cards[card.object_id].zone)
        self.finish(s,private)

    def test_fresh_native_registration_identity_collapse_is_killed_before_two_trigger_gameplay(self):
        from quorune.compiler import program_generation as owner
        self.actual_returns(723014)
        original=owner._generated_ability_id;observed=[]
        def collapse(**values):
            if ':numeric-instance:' in str(values.get('node_id')):
                observed.append(values['node_id']);return f"trigger:{values['face_id']}:n{values['line']}"
            return original(**values)
        with patch.object(owner,'_generated_ability_id',side_effect=collapse):
            with self.assertRaises(AssertionError):self.actual_returns(723015)
        self.assertTrue(observed)

    def test_genuinely_verified_soulshift_owner_withdrawal_rejects_full_parent_preserving_old_keywords(self):
        import copy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from test_bound_effect_programs import record
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        document=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        discovered=set(discover_tests(ROOT).values());changed=copy.deepcopy(raw)
        next(r for r in changed['capabilities'] if r['id']=='trigger.keyword.soulshift.fixed').update(status='blocked',blockers=['Soulshift owner withdrawn'])
        registry=CapabilityRegistry(changed);index=build_index(registry_value=changed,declaration_source=document,discovered_test_ids=discovered)
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        self.assertNotEqual('exact',compile_oracle_card(self.db.lookup('Forked-Branch Garami'),capability_registry=registry,capability_profile='commander_review').status)
        for text in ('Flying','Annihilator 2','Soulshift 4, Soulshift 4. Draw a chosen card.'):
            card=replace(record(text,'Creature'),keywords=tuple(k for k in ('Flying','Annihilator','Soulshift') if k.casefold() in text.casefold()))
            expected='exact' if not text.startswith('Soulshift') else None
            if expected:self.assertEqual(expected,compile_oracle_card(card,capability_registry=registry).status)
            else:self.assertNotEqual('exact',compile_oracle_card(card,capability_registry=self.registry).status)


if __name__=='__main__':unittest.main()
