from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card,_reviewed_effect_template
from quorune.rules.capabilities import load_default_capability_registry
from quorune.kicked_target_replacement_model import (KICKED_TARGET_REPLACEMENT_CAPABILITY as CAP,
    bound_kicked_replacement_schema,validate_kicked_target_replacement_binding,validate_kicked_target_replacement_instruction)
from quorune.compiler.kicked_target_replacements import kicked_target_replacement_template
from scripts.build_test_database import build_fixture_database
import test_kicked_target_groups as controls
from quorune.rules.evidence_storage import read_evidence_document

NAMES=('Tear Asunder',"Bloodchief's Thirst",'Blood Beckoning')


class KickedTargetReplacementTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.KickedTargetGroupTests.session
    start=controls.KickedTargetGroupTests.start
    add=controls.KickedTargetGroupTests.add
    ready=controls.KickedTargetGroupTests.ready
    prepare=controls.KickedTargetGroupTests.prepare
    checkpoint=controls.KickedTargetGroupTests.checkpoint
    resolve=controls.KickedTargetGroupTests.resolve
    replay=controls.KickedTargetGroupTests.replay
    binding=controls.KickedTargetGroupTests.binding
    priority=controls.KickedTargetGroupTests.priority
    private_replay=controls.KickedTargetGroupTests.private_replay
    private=controls.KickedTargetGroupTests.private
    reject=controls.KickedTargetGroupTests.reject
    cast=controls.KickedTargetGroupTests.cast
    stats=controls.KickedTargetGroupTests.stats

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'kicked-target-replacements.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/kicked-target-replacement-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Paid replacement targets',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_two_branch_codecs_keep_complete_domains_and_refuse_conflicting_counts_unknown_or_unbound_results(self):
        text='Exile target artifact or enchantment. If this spell was kicked, exile target nonland permanent instead.'
        result=kicked_target_replacement_template(text,compile_component=lambda t:_reviewed_effect_template(t,card_name='Witness'))
        self.assertIsNotNone(result);wrapper=result[1][0];binding=wrapper['target_binding']
        self.assertEqual(['artifact','enchantment'],bound_kicked_replacement_schema(binding,False)['types_any'])
        self.assertEqual(['land'],bound_kicked_replacement_schema(binding,True)['types_none'])
        for value in (None,1,'kicked'):
            with self.assertRaises(ValueError):bound_kicked_replacement_schema(binding,value)
        for update in ({'schema_version':True},{'normal':{}},{'unknown':0},{'kicked':binding['normal']}):
            with self.assertRaises(ValueError):validate_kicked_target_replacement_binding({**binding,**update})
        drift=deepcopy(binding);drift['kicked']['up_to']=2
        with self.assertRaises(ValueError):validate_kicked_target_replacement_binding(drift)
        for update in ({'schema_version':True},{'cast_fact':1},{'unknown':1},{'effects':[{'op':'draw','player':'$controller','count':1}]}):
            with self.assertRaises(ValueError):validate_kicked_target_replacement_instruction({**wrapper,**update})
        for text in ['Exile target artifact. If this spell was kicked, exile target artifact instead.',
                'Draw a card. If this spell was kicked, destroy target creature instead.',
                'Destroy target creature. If this spell was kicked, destroy any number of target creatures instead.']:
            self.assertIsNone(kicked_target_replacement_template(text,compile_component=lambda t:_reviewed_effect_template(t,card_name='Witness')))

    def test_three_originals_have_complete_normal_registration_and_independent_both_branch_minima(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.start(793000)
        for name in NAMES:
            c=self.add(s.engine,name,zone='hand');bound=self.binding(s,c);self.assertTrue(bound['strict_capability_ready'],(name,bound['blockers']))
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,(name,ir.material_residuals))
            p=next(p for p in s.engine.semantics.programs_for_oracle(c.oracle_id) if CAP in p.capability_dependencies)
            for cap in p.capability_dependencies:
                deps=tuple(d for d in p.capability_dependencies if d!=cap)
                changed=replace(p,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,result['blockers'])

    def test_tear_asunder_paid_domain_is_nonland_and_unpaid_retains_artifact_enchantment(self):
        for kicked in (False,True):
            s=self.start(793010+kicked);source=self.add(s.engine,'Tear Asunder',zone='hand')
            artifact=self.add(s.engine,'Sol Ring',seat='B');creature=self.add(s.engine,'Generic Bound Body',seat='B');land=self.add(s.engine,'Forest',seat='B')
            private=self.private(s);self.ready(s)
            self.reject(s,{'action_id':'cast:'+source.ref,'targets':[land.ref],'pay':'auto',**({'cost_option':'kicked'} if kicked else {})},'A')
            if not kicked:self.reject(s,{'action_id':'cast:'+source.ref,'targets':[creature.ref],'pay':'auto'},'A')
            target=creature if kicked else artifact;self.cast(s,source,[target.ref],kicked=kicked)
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual('exile',s.state.cards[target.object_id].zone)
            self.assertEqual('battlefield',s.state.cards[artifact.object_id if kicked else creature.object_id].zone);self.private_replay(s,private)

    def test_bloodchief_paid_target_removes_only_mana_value_limit(self):
        for kicked in (False,True):
            s=self.start(793020+kicked);source=self.add(s.engine,"Bloodchief's Thirst",zone='hand')
            small=self.add(s.engine,'Grizzly Bears',seat='B');large=self.add(s.engine,'Hill Giant',seat='B');artifact=self.add(s.engine,'Sol Ring',seat='B')
            private=self.private(s);self.ready(s)
            self.reject(s,{'action_id':'cast:'+source.ref,'targets':[artifact.ref],'pay':'auto',**({'cost_option':'kicked'} if kicked else {})},'A')
            if not kicked:self.reject(s,{'action_id':'cast:'+source.ref,'targets':[large.ref],'pay':'auto'},'A')
            target=large if kicked else small;self.cast(s,source,[target.ref],kicked=kicked)
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[target.object_id].zone);self.private_replay(s,private)

    def test_beckoning_paid_count_is_exactly_two_distinct_owned_graveyard_creatures(self):
        for kicked in (False,True):
            s=self.start(793030+kicked);source=self.add(s.engine,'Blood Beckoning',zone='hand')
            first=self.add(s.engine,'Grizzly Bears',zone='graveyard');second=self.add(s.engine,'Hill Giant',zone='graveyard')
            enemy=self.add(s.engine,'Grizzly Bears',seat='B',zone='graveyard',ref='replacement-foreign-grave');artifact=self.add(s.engine,'Sol Ring',zone='graveyard')
            private=self.private(s);self.ready(s)
            invalid=([first.ref] if kicked else [first.ref,second.ref]);self.reject(s,{'action_id':'cast:'+source.ref,'targets':invalid,'pay':'auto',**({'cost_option':'kicked'} if kicked else {})},'A')
            if kicked:
                for refs in ([first.ref,first.ref],[first.ref,enemy.ref],[first.ref,artifact.ref]):self.reject(s,{'action_id':'cast:'+source.ref,'cost_option':'kicked','targets':refs,'pay':'auto'},'A')
            self.cast(s,source,[first.ref,second.ref] if kicked else [first.ref],kicked=kicked)
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual('hand',s.state.cards[first.object_id].zone)
            self.assertEqual('hand' if kicked else 'graveyard',s.state.cards[second.object_id].zone);self.private_replay(s,private)

    def test_one_eligible_card_advertises_unpaid_beckoning_without_paid_branch(self):
        s=self.start(793040);source=self.add(s.engine,'Blood Beckoning',zone='hand');target=self.add(s.engine,'Grizzly Bears',zone='graveyard')
        private=self.private(s);self.ready(s);rows=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        row=next((a for a in rows if a['id']=='cast:'+source.ref),None);self.assertIsNotNone(row)
        ids={o['id'] for o in row['cost_options']};self.assertIn('normal',ids);self.assertNotIn('kicked',ids)
        self.cast(s,source,[target.ref]);self.assertIsNone(self.resolve(s));self.assertEqual('hand',s.state.cards[target.object_id].zone);self.private_replay(s,private)

    def test_partial_paid_beckoning_loss_returns_survivor_and_does_not_follow_moved_card(self):
        s=self.start(793050);source=self.add(s.engine,'Blood Beckoning',zone='hand');first=self.add(s.engine,'Grizzly Bears',zone='graveyard');second=self.add(s.engine,'Hill Giant',zone='graveyard')
        response=self.add(s.engine,'Purify the Grave',seat='B',zone='hand');s.state.players['B'].mana_pool['W']=1
        private=self.private(s);self.ready(s);self.cast(s,source,[first.ref,second.ref],kicked=True)
        self.cast(s,response,[first.ref],seat='B');s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('exile',s.state.cards[first.object_id].zone);self.assertEqual('hand',s.state.cards[second.object_id].zone);self.private_replay(s,private)

    def test_paid_copy_keeps_replaced_domain_and_count_without_another_price(self):
        s=self.start(793060);source=self.add(s.engine,'Blood Beckoning',zone='hand');copy=self.add(s.engine,'Reverberate',zone='hand')
        cards=[self.add(s.engine,'Grizzly Bears',zone='graveyard',ref='replacement-copy-grave-'+str(i)) for i in range(4)]
        private=self.private(s);self.ready(s);self.cast(s,source,[cards[0].ref,cards[1].ref],kicked=True);before=sum(s.state.players['A'].mana_pool.values())
        self.cast(s,copy,[s.state.stack[-1].ref]);decision=self.resolve(s);self.assertIsNotNone(decision);self.assertEqual('semantic.choice',decision.kind)
        self.reject(s,{'action_id':'choose','targets':[cards[2].ref]},'A');s=self.replay(s,load=True)
        result=s.act('pilot:A',{'action_id':'choose','targets':[cards[2].ref,cards[3].ref]});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertTrue(all(s.state.cards[c.object_id].zone=='hand' for c in cards))
        self.assertEqual(before-2,sum(s.state.players['A'].mana_pool.values()));self.private_replay(s,private)

    def test_all_paid_targets_lost_does_not_return_cards_and_keeps_the_paid_cost(self):
        s=self.start(793070);source=self.add(s.engine,'Blood Beckoning',zone='hand')
        cards=[self.add(s.engine,'Grizzly Bears',zone='graveyard',ref='replacement-lost-'+str(i)) for i in range(2)]
        responses=[self.add(s.engine,'Purify the Grave',seat='B',zone='hand',ref='replacement-purify-'+str(i)) for i in range(2)]
        s.state.players['B'].mana_pool['W']=2;private=self.private(s);self.ready(s);before=sum(s.state.players['A'].mana_pool.values())
        self.cast(s,source,[c.ref for c in cards],kicked=True)
        for response,target in zip(responses,cards,strict=True):self.cast(s,response,[target.ref],seat='B')
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertTrue(all(s.state.cards[c.object_id].zone=='exile' for c in cards))
        self.assertEqual(before-4,sum(s.state.players['A'].mana_pool.values()));self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.private_replay(s,private)

    def test_current_reviewed_kicker_view_requires_exact_source_price_and_both_target_effect_branches(self):
        from quorune.oracle_ir import generated_programs
        from quorune.semantics import SemanticRegistry
        from quorune.card_programs.kicker_view_equivalence import shadowed_reviewed_kicker_target_keys
        record=self.db.lookup('Tear Asunder');generated=tuple(generated_programs(self.db,record,trust_level='trusted',capability_registry=self.registry,capability_profile='commander_review'))
        old=next(p for p in SemanticRegistry().programs_for_oracle(record.oracle_id) if p.ability_id=='spell:front')
        self.assertEqual({old.key},shadowed_reviewed_kicker_target_keys(record,generated,(old,)))
        for field in ('source_oracle_hash','source_rulings_hash'):
            changed=replace(old,provenance={**old.provenance,field:'0'*64})
            self.assertEqual(set(),shadowed_reviewed_kicker_target_keys(record,generated,(changed,)))
        changes=[]
        for update in ({'requirements':{'GENERIC':0,'B':1}},{'requirements':{'GENERIC':True,'B':1}},
                {'target_schema':{'zones':['battlefield'],'categories':['permanent'],'count':1,'types_any':['creature']}},
                {'target_schema':None},
                {'target_schema':{'zones':['battlefield'],'categories':['permanent'],'land':False,'count':2}},
                {'effects':[{'op':'destroy','card':'$target.0'}]},{'unknown':0}):
            value=deepcopy(old.cost_schema);value['optional_costs'][0].update(update);changes.append(replace(old,cost_schema=value))
        changes.extend([replace(old,effects=[{'op':'destroy','card':'$target.0'}]),
            replace(old,target_schema={'zones':['battlefield'],'categories':['permanent'],'types_any':['land'],'count':1}),
            replace(old,event_condition={'field':'cast_option','op':'eq','value':'kicked'}),
            replace(old,provenance={**old.provenance,'authored_by':'oracle-ir-v550'})])
        for changed in changes:self.assertEqual(set(),shadowed_reviewed_kicker_target_keys(record,generated,(changed,)))

    def test_fixed_stat_set_replacement_uses_one_or_two_complete_target_programs(self):
        for kicked in (False,True):
            s=self.start(793080+kicked);source=self.add(s.engine,'Generic Kicker Fixed Stat Set',zone='hand')
            first=self.add(s.engine,'Generic Bound Body',ref='replacement-stat-first');second=self.add(s.engine,'Generic Bound Body',seat='B',ref='replacement-stat-second')
            private=self.private(s);self.ready(s)
            self.cast(s,source,[first.ref,second.ref] if kicked else [first.ref],kicked=kicked)
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
            self.assertEqual((7,11) if kicked else (5,9),self.stats(s,first))
            self.assertEqual((7,11) if kicked else (2,6),self.stats(s,second));self.private_replay(s,private)

    def test_verified_withdrawal_and_fresh_source_or_forced_unpaid_schema_omissions_reject(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));next(c for c in raw['capabilities'] if c['id']==CAP).update(status='blocked',blockers=['withdrawn replaced targets'],dependency_fail_closed_status='not_run')
        registry=CapabilityRegistry(raw);decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        index=build_index(registry_value=raw,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()));registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        ir=compile_oracle_card(self.db.lookup('Tear Asunder'),capability_registry=registry,capability_profile='commander_review');blocks=[b for r in ir.material_residuals for b in r.blockers]
        self.assertTrue(any('status:'+CAP+':blocked' in b for b in blocks),blocks);self.assertFalse(any('evidence_index:unverified' in b for b in blocks))
        method='test_three_originals_have_complete_normal_registration_and_independent_both_branch_minima'
        with patch('quorune.compiler.kicked_target_replacements.kicked_target_replacement_template',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        method='test_tear_asunder_paid_domain_is_nonland_and_unpaid_retains_artifact_enchantment'
        original=bound_kicked_replacement_schema
        with patch('quorune.kicked_target_replacement_model.bound_kicked_replacement_schema',side_effect=lambda value,kicked:original(value,False)):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        method='test_three_originals_have_complete_normal_registration_and_independent_both_branch_minima'
        with patch('quorune.card_programs.kicker_view_equivalence.shadowed_reviewed_kicker_target_keys',return_value=set()):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
