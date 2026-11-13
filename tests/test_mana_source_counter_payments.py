"""CR 601.2h/602.2b: automatic mana never omits its source-counter cost."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT,packet_without_decision_capability
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.errors import GameRuleError
from quorune.mana_mode_effects import compile_mana_mode_effects,RemoveManaSourceCounters
from quorune.mana_source_counter_payment_model import (
    MANA_SOURCE_COUNTER_PAYMENT_VERSION_FIELD as FIELD,mana_source_counter_payment_fields,
    read_mana_source_counter_payment_version,
    MANA_COST_ORDER_VERSION_FIELD as ORDER_FIELD,mana_cost_order_fields,read_mana_cost_order_version,
)
from quorune.model import GameState
from quorune.record import authoritative_state_hash
from quorune.record_state_provenance import format_state_versions,validate_state_versions
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_fixed_mana_abilities as mana_actions
import quorune.mana_mode_effects as mode_effects
import quorune.rules.activation.commit as activation_commit


class ManaSourceCounterModelTests(unittest.TestCase):
    def test_version_and_counter_cost_codecs_reject_malformed_or_ambiguous_data(self):
        self.assertEqual({},mana_source_counter_payment_fields(None))
        self.assertEqual(1,read_mana_source_counter_payment_version({FIELD:1}))
        self.assertIsNone(read_mana_source_counter_payment_version({}))
        self.assertEqual({},mana_cost_order_fields(None))
        self.assertIsNone(read_mana_cost_order_version({}))
        self.assertEqual(1,read_mana_cost_order_version({ORDER_FIELD:1}))
        for value in (0,2,True,None,'1'):
            with self.subTest(value=value),self.assertRaises(ValueError):read_mana_source_counter_payment_version({FIELD:value})
            with self.subTest(cost_order=value),self.assertRaises(ValueError):read_mana_cost_order_version({ORDER_FIELD:value})
        cost={'schema_version':1,'counter_name':'charge','amount':1}
        effect,=compile_mana_mode_effects([{'op':'remove_source_counters','cost':cost}])
        self.assertIsInstance(effect,RemoveManaSourceCounters);self.assertEqual(1,effect.cost.amount)
        for changed in ({**cost,'amount':True},{**cost,'amount':0},{**cost,'extra':1},
                {'schema_version':2,'counter_name':None,'amount':1}):
            with self.subTest(cost=changed),self.assertRaises(GameRuleError):
                compile_mana_mode_effects([{'op':'remove_source_counters','cost':changed}])


class ManaSourceCounterPaymentTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=mana_actions.FixedManaResultRuntimeTests.session
    add=mana_actions.FixedManaResultRuntimeTests.add
    ready=mana_actions.FixedManaResultRuntimeTests.ready
    checkpoint=mana_actions.FixedManaResultRuntimeTests.checkpoint
    replay=mana_actions.FixedManaResultRuntimeTests.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'source-counter-mana.sqlite3'
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[row for row in base['cards'] if row['name']=='Generic Bound Plains'];base['rulings']=[]
        basic=Path(cls.temporary.name)/'basic.json';basic.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([basic,ROOT/'tests/fixtures/mana-source-counter-payment-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Mana source counter costs',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def scene(self,count,*,vivid=False,legacy=False):
        s=self.session(831100+count+10*vivid+100*legacy)
        if vivid:land=self.add(s.engine,'Vivid Creek')
        else:land=self.add(s.engine,'Tendo Ice Bridge')
        land.counters['charge']=count;land.tapped=False
        body=self.add(s.engine,'Grizzly Bears');spell=self.add(s.engine,'Giant Growth',zone='hand')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='counter-mana-private-hand')
        s.state.config.semantic_policy='trusted_only'
        if legacy:
            s.state.mana_source_counter_payment_version=None;s.state.mana_cost_order_version=None
        self.ready(s,land);self.checkpoint(s)
        return s,land,body,spell,private

    def finish(self,s,private):
        for seat in 'ABCD':
            text=json.dumps(packet_without_decision_capability(s.packet('pilot:'+seat,full=True)))
            if seat=='C':self.assertIn(private.ref,text)
            else:self.assertNotIn(private.ref,text)
        self.replay(s,load=True)

    def test_original_automatic_colored_payment_removes_one_named_counter_and_replays(self):
        for vivid in (False,True):
            with self.subTest(vivid=vivid):
                s,land,body,spell,private=self.scene(2,vivid=vivid)
                command={'action_id':'cast:'+spell.ref,'targets':[body.ref],'pay':'auto'}
                before=authoritative_state_hash(s.state);self.assertFalse(s.act('pilot:D',command).ok)
                self.assertEqual(before,authoritative_state_hash(s.state))
                accepted=s.act('pilot:A',command);self.assertTrue(accepted.ok,accepted.summary)
                self.assertEqual(1,s.state.cards[land.object_id].counters.get('charge',0))
                self.assertTrue(s.state.cards[land.object_id].tapped);self.assertEqual(1,len(s.state.stack))
                self.assertEqual('stack',s.state.cards[spell.object_id].zone)
                self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));self.finish(s,private)

    def test_zero_counter_source_cannot_offer_or_accept_a_colored_auto_cast(self):
        for vivid in (False,True):
            with self.subTest(vivid=vivid):
                s,land,body,spell,private=self.scene(0,vivid=vivid)
                rows=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                self.assertFalse(any(row['id']=='cast:'+spell.ref for row in rows))
                before=authoritative_state_hash(s.state)
                rejected=s.act('pilot:A',{'action_id':'cast:'+spell.ref,'targets':[body.ref],'pay':'auto'})
                self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(s.state))
                self.assertFalse(s.state.cards[land.object_id].tapped);self.finish(s,private)

    def test_explicit_activation_pays_once_and_zero_cost_mode_still_works(self):
        s,land,body,spell,private=self.scene(1)
        named=next(ability.ability_id for ability in s.engine._activated_abilities(land) if ability.source_counter_removal_cost is not None)
        rows=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        colored=next(row for row in rows if row.get('source')==land.ref and row.get('ability')==named)
        accepted=s.act('pilot:A',{'action_id':colored['id'],'mana_output':{'G':1}})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(0,s.state.cards[land.object_id].counters.get('charge',0))
        self.assertEqual(1,s.state.players['A'].mana_pool['G'])
        self.assertFalse(any(row['id']=='undo_mana' for row in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']))
        self.finish(s,private)
        s,land,body,spell,private=self.scene(0)
        free_id=next(ability.ability_id for ability in s.engine._activated_abilities(land)
            if ability.mana_ability and ability.source_counter_removal_cost is None)
        free=next(row for row in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row.get('source')==land.ref and row.get('ability')==free_id)
        accepted=s.act('pilot:A',{'action_id':free['id']});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(1,s.state.players['A'].mana_pool['C']);self.finish(s,private)

    def test_state_and_manifest_versions_are_strict_and_historical_absence_replays(self):
        s,land,body,spell,private=self.scene(1)
        data=s.state.to_dict();self.assertEqual(1,data[FIELD]);self.assertEqual(1,GameState.from_dict(data).mana_source_counter_payment_version)
        self.assertEqual(1,data[ORDER_FIELD]);self.assertEqual(1,GameState.from_dict(data).mana_cost_order_version)
        declared=format_state_versions(s.state);validate_state_versions({'format':declared},s.state)
        for value in (None,0,2,True):
            bad=dict(data);bad[FIELD]=value
            with self.assertRaises(ValueError):GameState.from_dict(bad)
            bad=dict(data);bad[ORDER_FIELD]=value
            with self.assertRaises(ValueError):GameState.from_dict(bad)
        bad=dict(declared);bad.pop(FIELD)
        with self.assertRaises(ValueError):validate_state_versions({'format':bad},s.state)
        bad=dict(declared);bad.pop(ORDER_FIELD)
        with self.assertRaises(ValueError):validate_state_versions({'format':bad},s.state)
        s,land,body,spell,private=self.scene(1,legacy=True)
        data=s.state.to_dict();self.assertNotIn(FIELD,data);self.assertIsNone(GameState.from_dict(data).mana_source_counter_payment_version)
        self.assertNotIn(ORDER_FIELD,data);self.assertIsNone(GameState.from_dict(data).mana_cost_order_version)
        self.assertNotIn(FIELD,format_state_versions(s.state))
        accepted=s.act('pilot:A',{'action_id':'cast:'+spell.ref,'targets':[body.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertEqual(1,s.state.cards[land.object_id].counters['charge'])
        self.finish(s,private)

    def life_scene(self,name,legacy):
        s=self.session(832000+int(legacy)+10*(name=='Shivan Reef'))
        land=self.add(s.engine,name);body=self.add(s.engine,'Grizzly Bears')
        spell=self.add(s.engine,'Unsummon',zone='hand')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='mana-cost-order-private-hand')
        if legacy:s.state.mana_cost_order_version=None
        s.state.config.semantic_policy='trusted_only';self.ready(s,land);self.checkpoint(s)
        return s,land,body,spell,private

    def test_automatic_life_cost_precedes_mana_but_damage_result_follows_it_and_legacy_order_replays(self):
        for name,legacy in (('Mana Confluence',False),('Mana Confluence',True),('Shivan Reef',False)):
            with self.subTest(name=name,legacy=legacy):
                s,land,body,spell,private=self.life_scene(name,legacy);observed=[]
                pay=mode_effects.pay_life_cost;damage=mode_effects.resolve_damage_batch
                def observe_pay(host,seat,amount):
                    observed.append(('cost',host.state.players[seat].mana_pool['U']))
                    return pay(host,seat,amount)
                def observe_damage(host,*args,**kwargs):
                    observed.append(('result',host.state.players['A'].mana_pool['U']))
                    return damage(host,*args,**kwargs)
                command={'action_id':'cast:'+spell.ref,'targets':[body.ref],'pay':'auto'}
                before=authoritative_state_hash(s.state);self.assertFalse(s.act('pilot:D',command).ok)
                self.assertEqual(before,authoritative_state_hash(s.state))
                with patch.object(mode_effects,'pay_life_cost',side_effect=observe_pay),patch.object(mode_effects,'resolve_damage_batch',side_effect=observe_damage):
                    accepted=s.act('pilot:A',command)
                self.assertTrue(accepted.ok,accepted.summary)
                expected=[('result',1)] if name=='Shivan Reef' else [('cost',int(legacy))]
                self.assertEqual(expected,observed);self.assertEqual(39,s.state.players['A'].life)
                self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));self.assertTrue(s.state.cards[land.object_id].tapped)
                self.finish(s,private)

    def test_explicit_life_cost_already_precedes_mana_and_invalid_output_rolls_back(self):
        for legacy in (False,True):
            with self.subTest(legacy=legacy):
                s,land,body,spell,private=self.life_scene('Mana Confluence',legacy)
                row=next(row for row in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    if row.get('source')==land.ref and row.get('action')=='activate')
                before=authoritative_state_hash(s.state)
                rejected=s.act('pilot:A',{'action_id':row['id'],'mana_output':{'C':1}})
                self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(s.state))
                observed=[];pay=activation_commit.pay_life_cost
                def observe_pay(host,seat,amount):
                    observed.append(host.state.players[seat].mana_pool['U']);return pay(host,seat,amount)
                with patch.object(activation_commit,'pay_life_cost',side_effect=observe_pay):
                    accepted=s.act('pilot:A',{'action_id':row['id'],'mana_output':{'U':1}})
                self.assertTrue(accepted.ok,accepted.summary);self.assertEqual([0],observed)
                self.assertEqual(39,s.state.players['A'].life);self.assertEqual(1,s.state.players['A'].mana_pool['U'])
                self.finish(s,private)
