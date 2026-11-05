"""Declared optional prices qualify source-linked generic discounts before payment."""
from dataclasses import replace
from copy import deepcopy
import json
import unittest
import bargain_fixture as fixtures
from common import ROOT
from test_qualified_zone_event_queries import query_record
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry


class BargainReductionCompilerTests(unittest.TestCase):
    def test_declared_discount_codec_and_dependencies_fail_closed(self):
        from quorune.bargain_model import FixedBargainSpec
        from quorune.declared_cost_reductions import DeclaredCostReductionSpec
        from quorune.semantic_runtime.declared_cost_reductions import DeclaredCostReductionHandler
        from quorune.semantic_runtime.context import SemanticNodeError
        from quorune.rules.capabilities import CapabilityRegistry
        text="Bargain\nThis spell costs {2} less to cast if it's bargained.\nDraw a card."
        record=replace(query_record(text),type_line='Sorcery',keywords=('Bargain',))
        ir=compile_oracle_card(record,capability_registry=load_default_capability_registry())
        descriptor=next(n for n in ir.faces[0].nodes if n.event=='cast.cost.modify').handlers[0]
        spec=DeclaredCostReductionHandler().validate(descriptor)
        self.assertEqual(spec,DeclaredCostReductionSpec.from_dict(spec.to_dict()))
        bargain=FixedBargainSpec()
        self.assertEqual(2,spec.reduction_for(bargain.declaration(True).to_dict()))
        self.assertEqual(0,spec.reduction_for(bargain.declaration(False).to_dict()))
        for facts in (None,{},bargain.declaration(True).to_dict() | {'unknown':True},
                      {'schema_version':1,'choices':[]},
                      {'schema_version':1,'choices':[{'kind':'bargain','instance_id':'other','chosen':True}]}):
            with self.assertRaises(ValueError):spec.reduction_for(facts)
        for field,value in (('schema_version',True),('generic_reduction',True),('generic_reduction',0),
                            ('generic_reduction',-2),('condition',{}),('unknown',True)):
            with self.assertRaises(ValueError):DeclaredCostReductionSpec.from_dict(spec.to_dict() | {field:value})
        with self.assertRaises(ValueError):
            DeclaredCostReductionSpec.from_dict(spec.to_dict() | {'condition':spec.condition.to_dict() | {'schema_version':True}})
        for field,value in (('schema_version',True),('requires_complete_card_program',False),('unknown',True)):
            with self.assertRaises(SemanticNodeError):DeclaredCostReductionHandler().validate(dict(descriptor) | {field:value})
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for cap in ('casting.cost.self_public_reduction','casting.additional_cost.declared_instance','casting.additional_cost.bargain'):
            changed=deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id']==cap).update(status='blocked',blockers=['discount owner withdrawn'])
            self.assertNotEqual('exact',compile_oracle_card(record,capability_registry=CapabilityRegistry(changed)).status)

    def test_linked_generic_discount_keeps_source_span_and_excludes_unlinked_grammar(self):
        text="Bargain\nThis spell costs {2} less to cast if it's bargained.\nDraw a card."
        record=replace(query_record(text),type_line='Sorcery',keywords=('Bargain',))
        registry=load_default_capability_registry()
        ir=compile_oracle_card(record,capability_registry=registry)
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node=next(n for n in ir.faces[0].nodes if n.event=='cast.cost.modify')
        self.assertEqual(text.index('This spell'),node.span.start)
        self.assertIn('casting.additional_cost.declared_instance',node.capability_dependencies)
        for altered in (text.replace('Bargain\n',''),text.replace('{2}','{G}'),
                        text.replace('{2}','{X}'),text.replace('Bargain\n','Bargain\nBargain\n'),
                        text+' Invent an unknown result.'):
            self.assertNotEqual('exact',compile_oracle_card(replace(record,oracle_text=altered),
                capability_registry=registry).status,altered)


class BargainReductionNativeTests(fixtures.BargainFixture,unittest.TestCase):
    def test_original_hamlet_paid_cast_uses_reduced_offer_and_preserves_colored_price(self):
        s=self.session(702166101)
        source=self.add(s.engine,'Hamlet Glutton',zone='hand')
        price=self.add(s.engine,'Darksteel Relic')
        self.add(s.engine,'Dauthi Voidwalker',seat='B')
        self.assert_closed(s,source)
        self.ready(s,{'C':3,'G':2})
        action,option=self.bargain_action(s,source)
        self.assertEqual(3,option['requirements']['GENERIC'])
        self.assertEqual(2,option['requirements']['G'])
        before=authoritative_state_hash(s.state)
        result=s.act('pilot:A',{'action_id':action['id'],'cost_option':option['id'],'pay':'auto'})
        self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(s.state))
        self.cast_bargain(s,source,price)
        self.assertEqual('exile',s.state.cards[price.object_id].zone)
        self.assertEqual('stack',s.state.cards[source.object_id].zone)
        self.assertEqual(7,s.engine.card_record(source).mana_value)
        s=self.replay(s,load=True)
        self.settle(s)
        self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.assertEqual(43,s.state.players['A'].life)
        self.assertEqual(6,s.engine._numeric_stat(source.object_id,'power'))
        self.private(s);self.replay(s,load=True)

    def test_original_ice_out_zero_generic_still_pays_blue_and_counters_actual_spell(self):
        s=self.session(702166102)
        source=self.add(s.engine,'Ice Out',zone='hand')
        price=self.add(s.engine,'Darksteel Relic')
        target=self.add(s.engine,'Divination',seat='B',zone='hand')
        self.add(s.engine,'Divination',zone='hand',ref='private-recipient-hand')
        self.assert_closed(s,source)
        s.state.players['A'].mana_pool.update({'U':2})
        self.prepare(s,actor='B',active='B',mana={'C':2,'U':1});self.checkpoint(s)
        self.cast_for(s,'B',target)
        target_ref=s.state.stack[-1].ref
        action,option=self.bargain_action(s,source)
        self.assertEqual(0,option['requirements']['GENERIC'])
        self.assertEqual(2,option['requirements']['U'])
        self.cast_bargain(s,source,price,[target_ref])
        s=self.replay(s,load=True);self.settle(s)
        self.assertEqual('graveyard',s.state.cards[target.object_id].zone)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.assertEqual('graveyard',s.state.cards[price.object_id].zone)
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
        self.private(s);self.replay(s,load=True)

    def test_original_stopgap_reduced_cast_returns_nonland_then_draws(self):
        s=self.session(702166103)
        source=self.add(s.engine,"Johann's Stopgap",zone='hand')
        price=self.add(s.engine,'Darksteel Relic')
        target=self.add(s.engine,'Grizzly Bears',seat='B')
        self.assert_closed(s,source);self.ready(s,{'C':1,'U':1})
        action,option=self.bargain_action(s,source)
        self.assertEqual(1,option['requirements']['GENERIC'])
        self.assertEqual(1,option['requirements']['U'])
        hand=len(s.state.players['A'].zones['hand'])
        self.cast_bargain(s,source,price,[target.ref])
        self.settle(s)
        self.assertEqual('hand',s.state.cards[target.object_id].zone)
        self.assertIn(target.object_id,s.state.players['B'].zones['hand'])
        self.assertEqual(hand,len(s.state.players['A'].zones['hand']))
        self.assertEqual('graveyard',s.state.cards[price.object_id].zone)
        self.private(s);self.replay(s,load=True)

    def test_original_hamlet_unpaid_cast_keeps_price_and_insufficient_colored_mana_is_not_offered(self):
        for chosen in (False,True):
            with self.subTest(missing_green=chosen):
                s=self.session(702166104+chosen)
                source=self.add(s.engine,'Hamlet Glutton',zone='hand')
                price=self.add(s.engine,'Darksteel Relic')
                self.assert_closed(s,source);self.ready(s,{'C':5,'G':1 if chosen else 2})
                if chosen:
                    actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    self.assertFalse(any(row['id']=='cast:'+source.ref for row in actions))
                    before=authoritative_state_hash(s.state)
                    result=s.act('pilot:A',{'action_id':'cast:'+source.ref,'cost_option':'normal',
                        'declared_cast_costs':{'schema_version':1,'choices':[{'kind':'bargain','instance_id':'front:bargain:1','chosen':True}]},'pay':'auto'})
                    self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(s.state))
                    self.assertEqual('battlefield',s.state.cards[price.object_id].zone)
                else:
                    action,option=self.bargain_action(s,source,chosen=False)
                    self.assertEqual(5,option['requirements']['GENERIC'])
                    self.cast_bargain(s,source,chosen=False);self.settle(s)
                    self.assertEqual('battlefield',s.state.cards[price.object_id].zone)
                    self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
                    self.assertEqual(43,s.state.players['A'].life)
                self.private(s);self.replay(s,load=True)
