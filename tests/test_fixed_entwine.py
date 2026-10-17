from __future__ import annotations

from pathlib import Path
from copy import deepcopy
from dataclasses import replace
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from common import ROOT
from quorune.carddb import CardDatabase,CardRecord
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry,CapabilityRegistry,capability_dependencies_for_node
from quorune.oracle_ir import compile_oracle_card,register_generated_programs
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.semantics import SemanticRegistry
from quorune.entwine_model import EntwineModeCost,ENTWINE_FIXED_CAPABILITY
from quorune.util import mana_cost_to_vector
import test_spree_mode_costs as spree
from scripts.build_test_database import build_fixture_database

FIXTURE=ROOT/'tests/fixtures/fixed-entwine-cards.json'
TEXT='Choose one —\n• Tap target permanent.\n• Untap target permanent.\nEntwine {1}'


class FixedEntwineCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.registry=load_default_capability_registry()

    def compile(self,text=TEXT,registry=None):
        record=CardRecord(oracle_id='fixture:entwine-boundary',name='Entwine Boundary',mana_cost='{U}',mana_value=1,
            type_line='Instant',oracle_text=text,power=None,toughness=None,loyalty=None,defense=None,colors=('U',),
            color_identity=('U',),keywords=('Entwine',),produced_mana=(),layout='normal',released_at='2026-01-01',
            legalities={'commander':'legal'},faces=(),raw={})
        return compile_oracle_card(record,capability_registry=registry or self.registry,capability_profile='commander_review')

    def test_entwine_model_preserves_one_or_all_cardinality_and_closed_wire_fields(self):
        vector=mana_cost_to_vector('{1}')[0];model=EntwineModeCost(('mode_1','mode_2','mode_3'),vector)
        self.assertEqual(model,EntwineModeCost.from_dict(model.to_dict()))
        self.assertEqual(4,len(model.selections()))
        self.assertFalse(any(model.additional_mana(('mode_2',)).values()))
        self.assertEqual(vector,model.additional_mana(model.mode_ids))
        for modes in ((),('mode_1','mode_2'),('mode_1','mode_1'),('unknown',)):
            with self.subTest(modes=modes):
                with self.assertRaises(ValueError):model.additional_mana(modes)
        bad=model.to_dict();bad['extra']=True
        with self.assertRaises(ValueError):EntwineModeCost.from_dict(bad)
        bad=model.to_dict();bad['mana']['GENERIC']=True
        with self.assertRaises(ValueError):EntwineModeCost.from_dict(bad)

    def test_entwine_compiler_and_cost_options_bind_all_modes_before_modifiers(self):
        from quorune.rules.casting.entwine_costs import with_entwine_mode_cost_options
        for text in (TEXT,TEXT.replace('{1}','{0}'),TEXT.replace('• Untap','• Draw a card.\n• Untap')):
            with self.subTest(text=text):
                ir=self.compile(text);self.assertEqual('exact',ir.status,ir.material_residuals)
                node,=ir.faces[0].nodes
                self.assertEqual(text,node.text);self.assertEqual((0,len(text)),(node.span.start,node.span.end))
                self.assertIn(ENTWINE_FIXED_CAPABILITY,node.capability_dependencies)
                program=SimpleNamespace(cost_schema=node.cost,target_schema=node.target_schema,coverage=node.mechanics)
                modes=list(node.target_schema['modes']);selected=list(reversed(modes))
                for base_mana in ('{U}','{0}'):
                    base={'id':'normal','kind':'normal','requirements':mana_cost_to_vector(base_mana)[0]}
                    options=with_entwine_mode_cost_options(program,node.cost,[base],{'modes':selected},hint=False)
                    self.assertEqual(1,len(options));self.assertEqual(modes,options[0]['entwine_modes'])
                    expected=mana_cost_to_vector(base_mana)[0]
                    for symbol,amount in node.cost['entwine']['mana'].items():expected[symbol]+=amount
                    self.assertEqual(expected,options[0]['requirements'])
                    self.assertEqual(mana_cost_to_vector(base_mana)[0],base['requirements'])
                base={'id':'normal','requirements':mana_cost_to_vector('{U}')[0]}
                self.assertEqual(len(modes)+1,len(with_entwine_mode_cost_options(program,node.cost,[base],{},hint=True)))
                invalid=[[],['unknown'],['mode_1','mode_1']]
                if len(modes)>2:invalid.append(['mode_1','mode_2'])
                for choice in invalid:
                    self.assertEqual([],with_entwine_mode_cost_options(program,node.cost,[base],{'modes':choice},hint=False))

    def test_entwine_unknown_costs_siblings_and_uncorrelated_price_data_fail_closed(self):
        for text in (TEXT.replace('{1}','{X}'),TEXT.replace('{1}','{W/U}'),TEXT.replace('Untap target permanent.','Rewrite history.'),
            TEXT+'\nDraw a card.',TEXT.replace('Entwine {1}','Entwine—Sacrifice a creature.')):
            with self.subTest(text=text):self.assertNotEqual('exact',self.compile(text).status)
        ir=self.compile();self.assertEqual('exact',ir.status);node=ir.faces[0].nodes[0]
        for change in ({'mode_ids':['mode_2','mode_1']},{'mana':{'GENERIC':1}}):
            cost=deepcopy(node.cost);cost['entwine'].update(change)
            self.assertFalse(capability_dependencies_for_node(effects=node.effects,target_schema=node.target_schema,
                mechanic_ids=node.mechanics,cost_schema=cost))
        for capability in (ENTWINE_FIXED_CAPABILITY,'choice.modal.fixed_nonrepeating'):
            raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
            next(row for row in raw['capabilities'] if row['id']==capability).update(status='blocked',blockers=['Entwine owner omitted'])
            self.assertNotEqual('exact',self.compile(registry=CapabilityRegistry(raw)).status)


class FixedEntwineRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=spree.SpreeModeCostRuntimeTests.session
    add=spree.SpreeModeCostRuntimeTests.add
    checkpoint=spree.SpreeModeCostRuntimeTests.checkpoint
    resolve=spree.SpreeModeCostRuntimeTests.resolve
    replay=spree.SpreeModeCostRuntimeTests.replay
    prepare=spree.SpreeModeCostRuntimeTests.prepare
    assert_closed=spree.SpreeModeCostRuntimeTests.assert_closed
    reject=spree.SpreeModeCostRuntimeTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'entwine.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',FIXTURE],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Entwine witness',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_actual_entwine_binds_both_targets_and_requires_the_extra_cost(self):
        session=self.session(70204201);engine=session.engine
        source=self.add(engine,"Dream's Grip",zone='hand')
        tapped=self.add(engine,'Generic Bound Body',seat='B',ref='entwine-tap-target')
        untapped=self.add(engine,'Generic Bound Body',seat='C',ref='entwine-untap-target')
        untapped.tapped=True
        private=self.add(engine,'Generic Bound Growth',seat='C',zone='hand',ref='entwine-private')
        self.assert_closed(session,source)
        self.prepare(session,mana={'C':1,'U':1});self.checkpoint(session)
        offered=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if row['id']=='cast:'+source.ref)
        both=['mode_1','mode_2'];targets=[tapped.ref,untapped.ref]
        cheap=next(row['id'] for row in offered['cost_options'] if row['target_schema']['legal_modes']==['mode_1'])
        self.reject(session,offered['id'],modes=both,targets=targets,cost_option=cheap,pay='auto')
        self.reject(session,offered['id'],modes=both,targets=targets,pay='manual',payment={'U':1})
        self.reject(session,offered['id'],'C',modes=both,targets=targets,pay='auto')
        self.reject(session,offered['id'],modes=['mode_1','mode_1'],targets=targets,pay='auto')
        result=session.act('pilot:A',{'action_id':offered['id'],'modes':list(reversed(both)),'targets':targets,'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual(both,session.state.stack[-1].modes)
        session=self.replay(session,load=True);self.resolve(session)
        self.assertTrue(session.state.cards[tapped.object_id].tapped)
        self.assertFalse(session.state.cards[untapped.object_id].tapped)
        for seat in 'ABD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_entwine_same_target_uses_printed_order_and_unpaid_cast_chooses_one(self):
        for entwined in (False,True):
            with self.subTest(entwined=entwined):
                session=self.session(70204202+entwined);engine=session.engine
                source=self.add(engine,"Dream's Grip",zone='hand')
                target=self.add(engine,'Generic Bound Body',seat='B')
                self.assert_closed(session,source)
                self.prepare(session,mana={'C':int(entwined),'U':1});self.checkpoint(session)
                offered=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if row['id']=='cast:'+source.ref)
                if not entwined:self.assertFalse(any(row['target_schema']['legal_modes']==['mode_1','mode_2'] for row in offered['cost_options']))
                modes=['mode_2','mode_1'] if entwined else ['mode_1']
                result=session.act('pilot:A',{'action_id':offered['id'],'modes':modes,'targets':[target.ref]*(2 if entwined else 1),'pay':'auto'})
                self.assertTrue(result.ok,result.summary);session=self.replay(session,load=True);self.resolve(session)
                self.assertEqual(not entwined,session.state.cards[target.object_id].tapped)
                self.replay(session,load=True)

    def test_actual_entwine_additional_cost_is_reduced_after_adding_and_survives_free_base(self):
        for free in (False,True):
            with self.subTest(free=free):
                session=self.session(70204204+free);engine=session.engine
                source=self.add(engine,"Dream's Grip",zone='hand')
                modifier=self.add(engine,'Goblin Electromancer') if not free else None
                target=self.add(engine,'Generic Bound Body',seat='B')
                self.assert_closed(session,source)
                if modifier is not None:self.assert_closed(session,modifier)
                if free:
                    from quorune.impulse_access import grant_temporary_cast_permission
                    from quorune.impulse_access_model import TemporaryCastPermissionGrant,ImpulseAccessDuration
                    grant_temporary_cast_permission(engine,card=source,
                        grant=TemporaryCastPermissionGrant(player='A',duration=ImpulseAccessDuration.END_OF_TURN,
                            not_before_turn_sequence=None,without_mana_cost=True,source='entwine-fixture-permission'),
                        reason='Existing typed free-base permission boundary')
                self.prepare(session,mana={'C':int(free),'U':int(not free)});self.checkpoint(session)
                offered=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if row['id']=='cast:'+source.ref)
                response={'action_id':offered['id'],'modes':['mode_1','mode_2'],'targets':[target.ref,target.ref],'pay':'auto'}
                if free:
                    option=next(row for row in offered['cost_options'] if row['target_schema']['legal_modes']==['mode_1','mode_2'] and not row['id'].startswith('normal+'))
                    response['cost_option']=option['id']
                    self.reject(session,offered['id'],modes=response['modes'],targets=response['targets'],cost_option=option['id'],pay='manual',payment={})
                result=session.act('pilot:A',response);self.assertTrue(result.ok,result.summary)
                self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
                self.assertEqual('{U}',session.engine._effective_card_data(session.state.cards[source.object_id])['mana_cost'])
                session=self.replay(session,load=True);self.resolve(session)
                self.assertFalse(session.state.cards[target.object_id].tapped)
                self.replay(session,load=True)

    def test_actual_rain_of_rust_retains_each_modes_target_domain_and_shared_target(self):
        for same in (False,True):
            with self.subTest(same=same):
                session=self.session(70204206+same);engine=session.engine
                source=self.add(engine,'Rain of Rust',zone='hand')
                artifact=self.add(engine,'Great Furnace',seat='B')
                land=self.add(engine,'Generic Bound Plains',seat='C',ref='entwine-other-land')
                self.assert_closed(session,source)
                self.prepare(session,mana={'C':6,'R':3});self.checkpoint(session)
                offered=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if row['id']=='cast:'+source.ref)
                result=session.act('pilot:A',{'action_id':offered['id'],'modes':['mode_1','mode_2'],
                    'targets':[artifact.ref,artifact.ref if same else land.ref],'pay':'auto'})
                self.assertTrue(result.ok,result.summary);self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
                session=self.replay(session,load=True);self.resolve(session)
                self.assertEqual('graveyard',session.state.cards[artifact.object_id].zone)
                self.assertEqual('battlefield' if same else 'graveyard',session.state.cards[land.object_id].zone)
                self.replay(session,load=True)

    def test_entwine_price_omission_is_killed_by_native_underpayment_check(self):
        import quorune.rules.casting.entwine_costs as owner
        original=owner.with_entwine_mode_cost_options;visited=[]
        def free_entwine(program,schema,options,response,*,hint):
            expanded=original(program,schema,options,response,hint=hint)
            if 'entwine' in schema:
                model=EntwineModeCost.from_dict(schema['entwine'])
                for option in expanded:
                    if tuple(option.get('entwine_modes',()))==model.mode_ids:
                        for symbol,amount in model.mana.items():option['requirements'][symbol]-=amount
                        visited.append(option['id'])
            return expanded
        with mock.patch.object(owner,'with_entwine_mode_cost_options',side_effect=free_entwine):
            with self.assertRaises(AssertionError):self.test_actual_entwine_binds_both_targets_and_requires_the_extra_cost()
        self.assertTrue(visited)

    def test_actual_entwine_creates_tokens_before_affecting_current_creatures(self):
        for entwined in (False,True):
            with self.subTest(entwined=entwined):
                session=self.session(70204208+entwined);engine=session.engine
                source=self.add(engine,'Goblin War Party',zone='hand')
                self.assert_closed(session,source)
                self.prepare(session,mana={'C':5 if entwined else 3,'R':2 if entwined else 1});self.checkpoint(session)
                offered=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if row['id']=='cast:'+source.ref)
                result=session.act('pilot:A',{'action_id':offered['id'],'modes':['mode_1','mode_2'] if entwined else ['mode_1'],'targets':[],'pay':'auto'})
                self.assertTrue(result.ok,result.summary);session=self.replay(session,load=True);self.resolve(session)
                tokens=[card for card in session.state.cards.values() if card.is_token and card.controller=='A' and card.zone=='battlefield']
                self.assertEqual(3,len(tokens))
                for token in tokens:
                    self.assertEqual((2,2) if entwined else (1,1),tuple(session.engine._numeric_stat(token.object_id,key) for key in ('power','toughness')))
                    self.assertEqual(entwined,'haste' in {word.casefold() for word in session.engine._effective_card_data(token)['keywords']})
                self.replay(session,load=True)

    def test_entwine_originals_require_complete_modes_and_normal_registration(self):
        expected={"Rain of Rust","Mirage Mockery","Abuna's Chant","Dream's Grip","Flourishing Strike",
            "Roar of the Kha","One Dozen Eyes","Unbounded Potential","Reap and Sow","Barbed Lightning","Goblin War Party"}
        for raw in json.loads(FIXTURE.read_text(encoding='utf-8'))['cards']:
            if 'Entwine' not in raw.get('keywords',[]):continue
            with self.subTest(name=raw['name']):
                card=self.db.lookup(raw['name']);registry=SemanticRegistry()
                register_generated_programs(self.db,registry,(card,),trust_level='provisional',
                    capability_registry=self.registry,capability_profile='commander_review',promote_exact_runtime_handlers=True,
                    promote_exact_trigger_programs=True,promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
                program=compile_best_available_card_program(self.db,card,semantic_registry=registry,capability_registry=self.registry,capability_profile='commander_review')
                binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
                self.assertEqual(raw['name'] in expected,binding['strict_capability_ready'],binding['blockers'])
