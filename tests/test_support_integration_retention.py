from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_fixed_direct_target_predicates import record
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from scripts.build_test_database import build_fixture_database
import test_mixed_permanent_targets as native_mixed


class SupportIntegrationRetentionModelTests(unittest.TestCase):
    def test_full_labeled_static_source_reaches_its_existing_owner(self):
        cases=(
            ('Max speed — This creature gets +1/+0.','Creature','continuous-fixed-public-state-characteristics-v1'),
            ('Domain — This spell costs {1} less to cast for each basic land type among lands you control.',
                'Artifact Creature','self-spell-cost-public-reduction-v1'),
            ('Raid — If you attacked this turn, you may have this creature enter as a copy of any creature on the battlefield.',
                'Creature','intrinsic-entry-copy-v4'),
            ('Cosmic Awareness — As long as an opponent has cast a spell this turn, you may cast spells as though they had flash.',
                'Enchantment','static-cast-timing-public-condition-v1'),
        )
        caps=load_default_capability_registry()
        for i,(text,kind,template) in enumerate(cases):
            with self.subTest(text=text):
                ir=compile_oracle_card(record(text,suffix=597000+i,type_line=kind),capability_registry=caps,capability_profile='commander_review')
                self.assertFalse(ir.material_residuals)
                node=ir.faces[0].nodes[0];self.assertTrue(node.exact)
                self.assertEqual(template,node.template_id);self.assertEqual(text,node.text)

    def test_temporary_homogeneous_tribe_union_preserves_its_existing_bodies(self):
        cases=(('Target player discards two cards. Ninjas and Rogues you control gain menace until end of turn.','Sorcery'),
            ('When this creature enters, Soldiers and Heroes you control gain indestructible until end of turn.','Creature'))
        caps=load_default_capability_registry()
        for i,(text,kind) in enumerate(cases):
            with self.subTest(text=text):
                ir=compile_oracle_card(record(text,suffix=597010+i,type_line=kind),capability_registry=caps,capability_profile='commander_review')
                self.assertFalse(ir.material_residuals)
                self.assertTrue(all(n.exact for f in ir.faces for n in f.nodes))

    def test_runtime_set_inference_uses_only_the_targets_that_query_consumes(self):
        from quorune.rules.fixed_resolution_characteristic_shapes import fixed_resolution_characteristics_runtime_dependencies
        text='Target player discards two cards. Ninjas and Rogues you control gain menace until end of turn.'
        ir=compile_oracle_card(record(text,suffix=597020),capability_registry=load_default_capability_registry(),capability_profile='commander_review')
        node=ir.faces[0].nodes[0]
        program=SimpleNamespace(effects=node.effects,target_schema=node.target_schema,coverage=node.mechanics)
        required=fixed_resolution_characteristics_runtime_dependencies(program)
        self.assertIn('state_query.permanent.receiver_union',required)
        effect=dict(next(e for e in node.effects if e.get('op')=='modify_all_matching_permanents_until_end_of_turn'))
        effect['predicate']={**effect['predicate'],'controller':'$target.0'}
        invalid=SimpleNamespace(effects=(effect,),target_schema={'zones':['battlefield'],'categories':['permanent'],'count':1},coverage=node.mechanics)
        with self.assertRaises(ValueError):fixed_resolution_characteristics_runtime_dependencies(invalid)

    def test_label_fallback_preserves_unowned_condition_boundaries_and_detects_early_stripping(self):
        import quorune.compiler.closed_static_nodes as owner
        from quorune.compiler.oracle_source_text import without_ability_word_label
        original=owner.closed_static_or_replacement_node
        def premature(**values):
            values['material_line']=without_ability_word_label(values['material_line']) or values['material_line']
            return original(**values)
        method='test_full_labeled_static_source_reaches_its_existing_owner'
        with patch.object(owner,'closed_static_or_replacement_node',side_effect=premature):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        caps=load_default_capability_registry()
        for i,text in enumerate(('Visit — This creature gets +1/+0.',"Visit — This creature can't be blocked.",'If you control a private secret — This creature gets +3/+3.')):
            ir=compile_oracle_card(record(text,suffix=597030+i,type_line='Creature'),capability_registry=caps,capability_profile='commander_review')
            self.assertTrue(ir.material_residuals)

    def test_resolution_union_adoption_keeps_mixed_and_branch_qualified_sets_closed(self):
        from quorune.compiler.fixed_resolution_characteristic_queries import fixed_resolution_characteristic_query_is_closed
        from quorune.compiler.public_state_queries import fixed_characteristic_battlefield_query_subject
        for subject in ('Treefolk and Forests you control','Skeletons you control and other Zombies you control',
                'Green creatures and white creatures you control'):
            parsed=fixed_characteristic_battlefield_query_subject(subject);self.assertIsNotNone(parsed,subject)
            query=replace(parsed[1],controller='$controller')
            self.assertFalse(fixed_resolution_characteristic_query_is_closed(query,target_schema=None),subject)


class SupportIntegrationRetentionRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=native_mixed.MixedPermanentTargetRuntimeTests.session
    add=native_mixed.MixedPermanentTargetRuntimeTests.add
    prepare=native_mixed.MixedPermanentTargetRuntimeTests.prepare
    checkpoint=native_mixed.MixedPermanentTargetRuntimeTests.checkpoint
    resolve=native_mixed.MixedPermanentTargetRuntimeTests.resolve
    replay=native_mixed.MixedPermanentTargetRuntimeTests.replay
    private_replay=native_mixed.MixedPermanentTargetRuntimeTests.private_replay
    priority=native_mixed.MixedPermanentTargetRuntimeTests.priority
    actions=native_mixed.MixedPermanentTargetRuntimeTests.actions
    reject=native_mixed.MixedPermanentTargetRuntimeTests.reject
    binding=native_mixed.MixedPermanentTargetRuntimeTests.binding
    resume=native_mixed.MixedPermanentTargetRuntimeTests.resume
    settle=native_mixed.MixedPermanentTargetRuntimeTests.settle
    advance=native_mixed.MixedPermanentTargetRuntimeTests.advance
    pay_cast=native_mixed.MixedPermanentTargetRuntimeTests.pay_cast

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'support-integration.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/support-integration-retention-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Existing support integration regressions',[
            DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def fixture(self,seed,name):
        s=self.session(seed);source=self.add(s.engine,name,zone='hand')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        return s,source,private

    def test_all_thirteen_original_lost_cards_retain_normal_complete_admission(self):
        names=('Burnout Bashtronaut',"Kaito's Pursuit",'Gastal Raider','Stratadon','Swiftwing Assailant',
            'Captain America, Unbowed','Walking Sarcophagus','Nesting Bot','Protean Raider','Yavimaya Sojourner',
            'Leyline Binding','Captain Mar-Vell, Space-Born','Streaking Oilgorger')
        for i,name in enumerate(names):
            with self.subTest(name=name):
                s,source,_=self.fixture(597100+i,name)
                self.assertTrue(self.binding(s,source)['strict_capability_ready'])

    def test_actual_domain_cost_counts_distinct_types_and_keeps_the_permanent_body(self):
        s,source,private=self.fixture(597120,'Stratadon')
        for name in ('Forest','Island','Plains','Swamp','Mountain'):self.add(s.engine,name)
        self.add(s.engine,'Forest',ref='second-forest')
        self.prepare(s,mana={'C':5});self.checkpoint(s)
        self.reject(s,dict(action_id='cast:'+source.ref,pay='manual',payment={'C':4}))
        self.pay_cast(s,source,payment={'C':5})
        s=self.resume(s);self.assertIsNone(self.settle(s))
        card=s.state.cards[source.object_id]
        self.assertEqual('battlefield',card.zone);self.assertIn('trample',s.engine._combat_keywords(card))
        self.assertEqual((5,5),tuple(s.engine._numeric_stat(card.object_id,k) for k in ('power','toughness')))
        self.private_replay(s,private)

    def test_actual_max_speed_stays_conditional_and_does_not_transfer_to_death_token(self):
        s,source,private=self.fixture(597130,'Nesting Bot')
        damage=self.add(s.engine,'Shock',zone='hand');removal=self.add(s.engine,'Murder',seat='B',zone='hand')
        s.state.players['A'].stats['speed']=3;s.state.players['B'].mana_pool.update({'C':1,'B':2})
        self.prepare(s,mana={'W':1,'R':1});self.checkpoint(s)
        self.pay_cast(s,source,payment={'W':1});self.assertIsNone(self.settle(s))
        self.assertEqual(1,s.engine._numeric_stat(source.object_id,'power'))
        self.pay_cast(s,damage,['B'],payment={'R':1});self.assertIsNone(self.settle(s))
        self.assertEqual(4,s.state.players['A'].stats['speed']);self.assertEqual(2,s.engine._numeric_stat(source.object_id,'power'))
        self.pay_cast(s,removal,[source.ref],payment={'C':1,'B':2},seat='B')
        s=self.resume(s);self.assertIsNone(self.settle(s))
        tokens=[c for c in s.state.cards.values() if c.is_token and c.controller=='A']
        self.assertEqual(1,len(tokens));self.assertEqual((1,1),tuple(s.engine._numeric_stat(tokens[0].object_id,k) for k in ('power','toughness')))
        self.assertEqual(4,s.state.players['A'].stats['speed'])
        self.private_replay(s,private)

    def test_kaito_discard_and_own_tribe_grant_keep_separate_scope_and_locked_recipients(self):
        s,source,private=self.fixture(597140,"Kaito's Pursuit")
        both=self.add(s.engine,'Universal Automaton');rogue=self.add(s.engine,'Nightshade Stinger')
        plain=self.add(s.engine,'Memnite');opposing=self.add(s.engine,'Universal Automaton',seat='B',ref='opposing-automaton')
        late=self.add(s.engine,'Nightshade Stinger',zone='hand',ref='late-rogue')
        self.prepare(s,mana={'C':2,'B':2});self.checkpoint(s)
        self.pay_cast(s,source,['B'],payment={'C':2,'B':1})
        decision=self.resolve(s);self.assertIsNotNone(decision);self.assertIn('pilot:B',s.pending_principals())
        before=set(s.state.players['B'].zones['hand']);chosen=[s.state.cards[oid].ref for oid in sorted(before)[:2]]
        command={'action_id':'choose','cards':chosen};self.reject(s,command,seat='A')
        result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        s=self.resume(s);self.assertIsNone(self.settle(s))
        self.assertEqual(len(before)-2,len(s.state.players['B'].zones['hand']))
        for card in (both,rogue):self.assertIn('menace',s.engine._combat_keywords(s.state.cards[card.object_id]))
        for card in (plain,opposing):self.assertNotIn('menace',s.engine._combat_keywords(s.state.cards[card.object_id]))
        self.pay_cast(s,late,payment={'B':1});self.assertIsNone(self.settle(s))
        self.assertNotIn('menace',s.engine._combat_keywords(s.state.cards[late.object_id]))
        self.advance(s,lambda x:x.state.active_player=='B')
        self.assertNotIn('menace',s.engine._combat_keywords(s.state.cards[both.object_id]))
        self.private_replay(s,private)

    def test_actual_captain_entry_grant_includes_self_and_locks_the_recipient_set(self):
        s,source,private=self.fixture(597150,'Captain America, Unbowed')
        both=self.add(s.engine,'Universal Automaton');soldier=self.add(s.engine,'Yotian Soldier');plain=self.add(s.engine,'Memnite')
        opposing=self.add(s.engine,'Universal Automaton',seat='B',ref='opposing-automaton')
        late=self.add(s.engine,'Yotian Soldier',zone='hand',ref='late-soldier')
        removal=self.add(s.engine,'Murder',seat='B',zone='hand');s.state.players['B'].mana_pool.update({'C':1,'B':2})
        self.prepare(s,mana={'C':6,'W':1});self.checkpoint(s)
        self.pay_cast(s,source,payment={'C':3,'W':1});s=self.resume(s);self.assertIsNone(self.settle(s))
        for card in (source,both,soldier):self.assertIn('indestructible',s.engine._combat_keywords(s.state.cards[card.object_id]))
        for card in (plain,opposing):self.assertNotIn('indestructible',s.engine._combat_keywords(s.state.cards[card.object_id]))
        self.pay_cast(s,removal,[source.ref],payment={'C':1,'B':2},seat='B');self.assertIsNone(self.settle(s))
        self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.pay_cast(s,late,payment={'C':3});self.assertIsNone(self.settle(s))
        self.assertNotIn('indestructible',s.engine._combat_keywords(s.state.cards[late.object_id]))
        self.advance(s,lambda x:x.state.active_player=='B')
        self.assertNotIn('indestructible',s.engine._combat_keywords(s.state.cards[source.object_id]))
        self.private_replay(s,private)

    def test_temporary_union_dependencies_are_specific_and_implicitly_required(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.card_programs.binding import bind_semantic_program_runtime
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        tests=set(discover_tests(ROOT).values())
        for cap in ('state_query.permanent.receiver_union','continuous.resolution.fixed_characteristics_until_end_of_turn'):
            changed=deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id']==cap).update(status='blocked',
                blockers=['withdrawn temporary subtype-union authority'],dependency_fail_closed_status='not_run')
            registry=CapabilityRegistry(changed)
            index=build_index(registry_value=changed,declaration_source=declarations,discovered_test_ids=tests)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            for name in ("Kaito's Pursuit",'Captain America, Unbowed'):
                ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
                blockers=[b for r in ir.material_residuals for b in r.blockers]
                self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),(name,blockers))
                self.assertFalse(any('evidence_index:unverified' in b for b in blockers),(name,blockers))
        s,source,_=self.fixture(597160,"Kaito's Pursuit")
        program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id)
            if any(e.get('op')=='modify_all_matching_permanents_until_end_of_turn' for e in p.effects))
        cap='state_query.permanent.receiver_union';deps=tuple(c for c in program.capability_dependencies if c!=cap)
        self.assertNotIn(cap,self.registry.closure(deps,profile='commander_review').reachable)
        missing=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
        bound=bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertTrue(any(cap in b for b in bound['blockers']),bound['blockers'])


if __name__=='__main__':unittest.main()
