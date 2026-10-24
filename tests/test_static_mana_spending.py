from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from itertools import product
from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.mana import auto_plan_payment,ManaPlanError
from quorune.mana_spending_model import ManaSpendingMode as Mode,ManaSpendingPolicy as Policy
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_myrel_composition as controls
from quorune.rules.evidence_storage import read_evidence_document

CAP='mana.spending.as_though'
NAMES=('Sunglasses of Urza','Chromatic Orrery','Vizier of the Menagerie')


class ManaSpendingModelTests(unittest.TestCase):
    def test_directed_modes_exact_actual_units_and_closed_inputs(self):
        white=Policy(frozenset({Mode.WHITE_AS_RED}));color=Policy(frozenset({Mode.ANY_COLOR}));kind=Policy(frozenset({Mode.ANY_TYPE}))
        self.assertEqual(1,white.allocate({'W':1},{'R':1})['W']);self.assertIsNone(white.allocate({'R':1},{'W':1}))
        self.assertIsNone(white.allocate({'W':1},{'W':1,'R':1}));self.assertIsNone(color.allocate({'G':1},{'C':1}))
        self.assertEqual(1,kind.allocate({'G':1},{'C':1})['G']);self.assertFalse(kind.permits('snow','C'))
        remaining,spent=white.pay({'W':2,'R':1},{'R':1},{'W':1});self.assertEqual((1,1),(remaining['W'],remaining['R']));self.assertEqual(1,spent['W']);self.assertEqual(0,spent['R'])
        for modes in ({Mode.ANY_COLOR},frozenset({'any_type'})):
            with self.assertRaises(ValueError):Policy(modes)
        with self.assertRaises(ValueError):white.pay({'W':2},{'R':1},{'W':2})

    def test_bounded_matching_agrees_with_independent_unit_assignment(self):
        colors='WRC'
        def possible(units,needs,mode):
            if not needs:return True
            target=needs[0]
            for i,actual in enumerate(units):
                allowed=actual==target or mode==Mode.ANY_TYPE or (mode==Mode.ANY_COLOR and target!='C') or (mode==Mode.WHITE_AS_RED and actual=='W' and target=='R')
                if allowed and possible(units[:i]+units[i+1:],needs[1:],mode):return True
            return False
        for mode in Mode:
            policy=Policy(frozenset({mode}))
            for counts in product(range(3),repeat=3):
                pool=dict(zip(colors,counts));units=tuple(c for c,n in pool.items() for _ in range(n))
                for amounts in product(range(3),repeat=3):
                    need=dict(zip(colors,amounts));required=tuple(c for c,n in need.items() for _ in range(n))
                    for generic in (0,1):
                        expected=len(units)>=len(required)+generic and possible(units,required,mode)
                        allocation=policy.allocate(pool,{**need,'GENERIC':generic})
                        self.assertEqual(expected,allocation is not None,(mode,pool,need,generic))
                        if allocation is not None:self.assertEqual(len(required)+generic,sum(allocation.values()))

    def test_planner_reserves_and_snow_do_not_change_type_provenance(self):
        policy=Policy(frozenset({Mode.WHITE_AS_RED}))
        plan=auto_plan_payment({'R':1},[],starting_pool={'W':2},reserve={'W':1},spending_policy=policy)
        self.assertEqual(1,plan.payment['W']);self.assertEqual(0,plan.payment['R'])
        plan=auto_plan_payment({'R':1},[],starting_pool={'W':2},starting_snow_pool={'W':1},snow_required=1,spending_policy=policy)
        self.assertEqual((1,1),(plan.payment['W'],plan.snow_payment['W']))
        with self.assertRaises(ManaPlanError):auto_plan_payment({},[],starting_pool={'G':1},snow_required=1,spending_policy=Policy(frozenset({Mode.ANY_TYPE})))


class ManaSpendingRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.MyrelCompositionTests.session
    add=controls.MyrelCompositionTests.add
    prepare=controls.MyrelCompositionTests.prepare
    checkpoint=controls.MyrelCompositionTests.checkpoint
    binding=controls.MyrelCompositionTests.binding
    resolve=controls.MyrelCompositionTests.resolve
    replay=controls.MyrelCompositionTests.replay
    private_replay=controls.MyrelCompositionTests.private_replay
    priority=controls.MyrelCompositionTests.priority
    actions=controls.MyrelCompositionTests.actions
    cast=controls.MyrelCompositionTests.cast
    activate=controls.MyrelCompositionTests.activate
    reject=controls.MyrelCompositionTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'static-mana-spending.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/static-mana-spending-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Static mana spending',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def top(self,s,name,ref):
        card=self.add(s.engine,name,zone='library',ref=ref);library=s.state.players['A'].zones['library']
        library.remove(card.object_id);library.append(card.object_id);card.known_to.clear();card.revealed_to.clear();return card

    def manual_activation(self,s,source,payment,*,targets=()):
        self.priority(s,'A');row=next(r for r in self.actions(s) if r['id'].startswith('activate:'+source.ref+':'))
        command={'action_id':row['id'],'pay':'manual','payment':payment,'targets':list(targets)}
        self.reject(s,command,seat='D');result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def test_originals_admit_all_printed_components_and_reject_undeclared_minimum(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.session(818000)
        for name in NAMES:
            source=self.add(s.engine,name,zone='hand');bound=self.binding(s,source);self.assertTrue(bound['strict_capability_ready'],(name,bound['blockers']))
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review');self.assertEqual('exact',ir.status,ir.material_residuals)
            program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if CAP in p.capability_dependencies)
            deps=tuple(c for c in program.capability_dependencies if c!=CAP)
            changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
            result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAP,result['blockers'])

    def test_real_sunglasses_manual_activation_and_adamant_keep_actual_white(self):
        s=self.session(818010);source=self.add(s.engine,NAMES[0],zone='hand');dragon=self.add(s.engine,'Shivan Dragon')
        fire=self.add(s.engine,'Slaying Fire',zone='hand');white=self.add(s.engine,'Elite Vanguard',zone='hand')
        enemy=self.add(s.engine,'Shock',seat='B',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['B'].mana_pool['W']=1;self.prepare(s,mana={'C':3,'W':4,'R':1});self.checkpoint(s)
        self.cast(s,source,payment={'C':3});self.assertIsNone(self.resolve(s))
        self.manual_activation(s,dragon,{'W':1});self.assertIsNone(self.resolve(s));self.assertEqual(6,s.engine._numeric_stat(dragon.object_id,'power'))
        self.cast(s,fire,['D'],payment={'W':3});item=next(i for i in s.state.stack if i.card_object_id==fire.object_id)
        self.assertEqual(('W',),tuple(item.mana_colors_spent));self.assertEqual(3,item.mana_spent_counts.count_for(s.state.cards[fire.object_id],'W'))
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual(37,s.state.players['D'].life)
        self.assertEqual(1,s.state.players['A'].mana_pool['R']);self.assertNotIn('cast:'+white.ref,{r['id'] for r in self.actions(s)})
        self.reject(s,{'action_id':'cast:'+white.ref,'pay':'manual','payment':{'R':1}},seat='A')
        self.priority(s,'B');self.assertNotIn('cast:'+enemy.ref,{r['id'] for r in self.actions(s,'B')})
        self.reject(s,{'action_id':'cast:'+enemy.ref,'targets':['D'],'pay':'manual','payment':{'W':1}},seat='B');self.private_replay(s,private)

    def test_real_orrery_colorless_mana_converge_explicit_colorless_and_draw(self):
        s=self.session(818020);source=self.add(s.engine,NAMES[1],zone='hand');flames=self.add(s.engine,'Radiant Flames',zone='hand')
        invalid=self.add(s.engine,'Spatial Contortion',zone='hand');destroy=self.add(s.engine,'Disenchant',zone='hand');later=self.add(s.engine,'Fugitive Wizard',zone='hand',ref='later_blue')
        own=self.add(s.engine,'Fugitive Wizard',ref='own_blue');self.add(s.engine,'Elite Vanguard');enemy=self.add(s.engine,'Fugitive Wizard',seat='B')
        counter=self.add(s.engine,'Counterspell',seat='B',zone='hand');key=self.add(s.engine,'Voltaic Key');dynamo=self.add(s.engine,'Thran Dynamo')
        plains=[self.add(s.engine,'Plains',ref='orrery_plains'+str(i)) for i in range(2)]
        forest=self.add(s.engine,'Forest');mountain=self.add(s.engine,'Mountain');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['B'].mana_pool['G']=2;self.prepare(s,mana={'C':7});self.checkpoint(s)
        self.cast(s,source,payment={'C':7});self.assertIsNone(self.resolve(s))
        row=next(r for r in self.actions(s) if r['id'].startswith('activate:'+source.ref+':') and 'mana' in json.dumps(r).casefold())
        result=s.act('pilot:A',{'action_id':row['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.assertEqual(5,s.state.players['A'].mana_pool['C'])
        self.cast(s,flames,payment={'C':3});item=next(i for i in s.state.stack if i.card_object_id==flames.object_id);self.assertEqual((),tuple(item.mana_colors_spent))
        self.priority(s,'B');self.assertNotIn('cast:'+counter.ref,{r['id'] for r in self.actions(s,'B')})
        self.reject(s,{'action_id':'cast:'+counter.ref,'targets':[item.ref],'pay':'manual','payment':{'G':2}},seat='B')
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual(0,s.state.cards[own.object_id].marked_damage);self.assertEqual('battlefield',s.state.cards[enemy.object_id].zone)
        for land in plains:self.activate(s,land)
        self.reject(s,{'action_id':'cast:'+invalid.ref,'targets':[enemy.ref],'pay':'manual','payment':{'W':2}},seat='A')
        self.manual_activation(s,key,{'C':1},targets=[source.ref]);self.assertIsNone(self.resolve(s));self.assertFalse(s.state.cards[source.object_id].tapped)
        before=len(s.state.players['A'].zones['hand']);self.priority(s,'A')
        draw=next(r for r in self.actions(s) if r['id'].startswith('activate:'+source.ref+':') and 'draw' in json.dumps(r).casefold())
        self.activate(s,forest);self.activate(s,mountain)
        self.priority(s,'A');draw=next(r for r in self.actions(s) if r['id'].startswith('activate:'+source.ref+':') and 'draw' in json.dumps(r).casefold())
        command={'action_id':draw['id'],'pay':'manual','payment':{'C':1,'W':2,'G':1,'R':1}};self.reject(s,command,seat='D');result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual(before+2,len(s.state.players['A'].zones['hand']))
        self.activate(s,dynamo);self.cast(s,destroy,[source.ref],payment={'C':2});self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.assertEqual(1,s.state.players['A'].mana_pool['C']);self.assertNotIn('cast:'+later.ref,{r['id'] for r in self.actions(s)})
        self.reject(s,{'action_id':'cast:'+later.ref,'pay':'manual','payment':{'C':1}},seat='A');self.private_replay(s,private)

    def test_real_vizier_top_creature_colorless_cost_purpose_snow_and_loss(self):
        s=self.session(818030);source=self.add(s.engine,NAMES[2],zone='hand');warden=self.top(s,'Warden of Geometries','top_warden')
        shock=self.add(s.engine,'Shock',zone='hand');ice=self.add(s.engine,'Icehide Golem',zone='hand');later=self.add(s.engine,'Fugitive Wizard',zone='hand')
        dragon=self.add(s.engine,'Shivan Dragon');humble=self.add(s.engine,'Humble',zone='hand')
        for i in range(2):self.add(s.engine,'Plains',ref='vizier_plains'+str(i))
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand');self.prepare(s,mana={'C':3,'G':2,'W':4});self.checkpoint(s)
        self.cast(s,source,payment={'C':3,'G':1});self.assertIsNone(self.resolve(s))
        for seat in 'ABCD':self.assertEqual(seat=='A',warden.ref in json.dumps(s.packet('pilot:'+seat,full=True)))
        self.cast(s,warden,payment={'W':4});item=next(i for i in s.state.stack if i.card_object_id==warden.object_id)
        self.assertEqual(('W',),tuple(item.mana_colors_spent));s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        offered={r['id'] for r in self.actions(s)};self.assertNotIn('cast:'+shock.ref,offered);self.assertNotIn('cast:'+ice.ref,offered)
        self.assertFalse(any(r['id'].startswith('activate:'+dragon.ref+':') for r in self.actions(s)))
        self.reject(s,{'action_id':'cast:'+shock.ref,'targets':['D'],'pay':'manual','payment':{'G':1}},seat='A')
        self.assertIn('cast:'+later.ref,offered);self.cast(s,humble,[source.ref]);self.assertIsNone(self.resolve(s))
        self.assertNotIn('cast:'+later.ref,{r['id'] for r in self.actions(s)})
        self.reject(s,{'action_id':'cast:'+later.ref,'pay':'manual','payment':{'G':1}},seat='A');self.private_replay(s,private)

    def test_real_sacrifice_price_spends_before_permission_source_leaves(self):
        s=self.session(818040);source=self.add(s.engine,NAMES[0],zone='hand');spell=self.add(s.engine,'Shrapnel Blast',zone='hand')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand');self.prepare(s,mana={'C':4,'W':1});self.checkpoint(s)
        self.cast(s,source,payment={'C':3});self.assertIsNone(self.resolve(s))
        self.priority(s,'A');row=next(r for r in self.actions(s) if r['id']=='cast:'+spell.ref)
        command={'action_id':row['id'],'targets':['D'],'sacrifice_cards':[source.ref],'pay':'manual','payment':{'C':1,'W':1}}
        self.reject(s,command,seat='D');result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual(35,s.state.players['D'].life);self.private_replay(s,private)

    def test_fixed_sacrifice_promotion_keeps_price_and_result_obligations(self):
        from quorune.compiler.fixed_sacrifice_result_promotion import closed_fixed_sacrifice_spell_result
        from quorune.compiler.program_generation import _closed_effect_recognizers
        s=self.session(818045);source=self.add(s.engine,'Shrapnel Blast',zone='hand')
        program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if p.event=='resolve')
        self.assertEqual('trusted',program.trust_level)
        check=lambda p:closed_fixed_sacrifice_spell_result(p,recognizers=_closed_effect_recognizers())
        self.assertTrue(check(program))
        malformed=deepcopy(program.cost_schema);malformed['additional_costs'][0]['count']=2
        self.assertFalse(check(replace(program,cost_schema=malformed)))
        self.assertFalse(check(replace(program,effects=[{'op':'damage','source':'$source','target':'$target.0','amount':0}])))
        deps=[c for c in program.capability_dependencies if c!='casting.additional_cost.fixed_sacrifice']
        self.assertFalse(check(replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())))
        self.assertFalse(check(replace(program,provenance={**program.provenance,'template_id':'unknown-price+damage-any-target-v1'})))

    def test_real_restricted_mana_does_not_become_unrestricted(self):
        s=self.session(818050);source=self.add(s.engine,NAMES[1],zone='hand');workshop=self.add(s.engine,"Mishra's Workshop")
        invalid=self.add(s.engine,'Divination',zone='hand');artifact=self.add(s.engine,'Etherium Sculptor',zone='hand')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand');self.prepare(s,mana={'C':7});self.checkpoint(s)
        self.cast(s,source,payment={'C':7});self.assertIsNone(self.resolve(s));self.activate(s,workshop)
        self.assertIn('cast:'+invalid.ref,{r['id'] for r in self.actions(s)})
        self.assertFalse(s.engine._cost_is_affordable('A',{'GENERIC':2,'U':1},exclude_sources={source.object_id},spend_context=s.engine._spell_mana_spend_context('Sorcery')))
        self.reject(s,{'action_id':'cast:'+invalid.ref,'pay':'manual','payment':{'C':3}},seat='A')
        self.cast(s,artifact,payment={'C':2});s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual('battlefield',s.state.cards[artifact.object_id].zone);self.private_replay(s,private)

    def test_verified_withdrawal_and_fresh_spending_or_purpose_omission_fail_closed(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text());changed=deepcopy(raw)
        next(c for c in changed['capabilities'] if c['id']==CAP).update(status='blocked',blockers=['withdrawn static spending authority'],dependency_fail_closed_status='not_run')
        registry=CapabilityRegistry(changed);index=build_index(registry_value=changed,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for name in NAMES:
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review');blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+CAP+':blocked' in b for b in blockers),blockers);self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        method='test_real_sunglasses_manual_activation_and_adamant_keep_actual_white'
        with patch('quorune.cost_payments.current_mana_spending_policy',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        method='test_real_vizier_top_creature_colorless_cost_purpose_snow_and_loss'
        with patch('quorune.semantic_runtime.mana_spending.mana_restriction_allows',return_value=True):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        method='test_real_sacrifice_price_spends_before_permission_source_leaves'
        with patch('quorune.compiler.fixed_sacrifice_result_promotion.closed_fixed_sacrifice_spell_result',return_value=False):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
