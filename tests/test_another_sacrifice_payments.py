"""CR118.12/400.7: optional sacrifice excludes the current source object."""
from dataclasses import replace
from copy import deepcopy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from common import ROOT
from quorune.compiler.fixed_effect_payment_templates import fixed_effect_payment_spec
from quorune.fixed_effect_payment import FixedEffectPaymentSpec
from quorune.object_query import ObjectQueryResult
from quorune.semantic_choices.context import SnapshotSemanticChoiceQuery,SemanticChoiceContext
from quorune.semantic_choices.fixed_effect_payment import prepare_fixed_payment_cost,complete_fixed_payment_cost
from quorune.semantic_choices.model import SemanticChoiceError


class AnotherSacrificePaymentOwnerTests(unittest.TestCase):
    def test_current_source_incarnation_and_distinct_if_when_target_timing(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        from quorune.semantic_runtime.values import resolve_semantic_value
        from test_granted_skulk import record
        spec=fixed_effect_payment_spec('sacrifice another creature')
        for identity,excluded in (('old-object','source-ref'),('new-object',None)):
            item=SimpleNamespace(controller='A',source_object_id='source-id',context={'source_logical_object_id':'old-object'})
            host=SimpleNamespace(state=SimpleNamespace(cards={'source-id':SimpleNamespace(
                ref='source-ref',zone='battlefield',phased_out=False,logical_object_id=identity)}))
            bound=FixedEffectPaymentSpec.from_dict(resolve_semantic_value(host,spec.to_dict(),item))
            self.assertEqual(excluded,bound.predicate.exclude_ref)
        registry=load_default_capability_registry()
        for link in ('If','When'):
            ir=compile_oracle_card(record(f'Whenever this creature attacks, you may sacrifice another creature. {link} you do, target player draws a card.','Creature — Ogre'),
                capability_registry=registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
            node=ir.faces[0].nodes[0]
            self.assertEqual(link=='When',node.target_schema is None)
            self.assertEqual(4 if link=='When' else 2,node.effects[0]['schema_version'])
            self.assertEqual('$source.zone_object',node.effects[0]['payment']['predicate']['exclude_ref'])
        for cost in ('sacrifice two other creatures','sacrifice another creature and pay {1}','sacrifice another creature with a mystery quality'):
            self.assertIsNone(fixed_effect_payment_spec(cost))

    def test_source_exclusion_omission_is_killed(self):
        import quorune.semantic_choices.fixed_effect_payment as owner
        original=owner.object_matches_query
        with patch.object(owner,'object_matches_query',side_effect=lambda row,query:original(row,replace(query,exclude_ref=None))):
            result=unittest.TestResult()
            AnotherSacrificePaymentOwnerTests('test_cost_owner_excludes_source_and_foreign_control_but_allows_borrowed_permanent').run(result)
        self.assertFalse(result.errors,result.errors);self.assertTrue(result.failures)

    def test_verified_another_sacrifice_withdrawal_keeps_legacy_sacrifice(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from test_granted_skulk import record
        text='Whenever this creature attacks, you may sacrifice another creature. If you do, draw a card.'
        current=load_default_capability_registry()
        positive=compile_oracle_card(record(text,'Creature — Ogre'),capability_registry=current,capability_profile='commander_review')
        self.assertEqual('exact',positive.status,positive.material_residuals)
        self.assertIn('cost.sacrifice.another_fixed_resolution',positive.faces[0].nodes[0].capability_dependencies)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        raw=deepcopy(raw);next(cap for cap in raw['capabilities'] if cap['id']=='cost.sacrifice.another_fixed_resolution').update(status='blocked',blockers=['source exclusion withdrawn'])
        registry=CapabilityRegistry(raw)
        index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        self.assertNotEqual('exact',compile_oracle_card(record(text,'Creature — Ogre'),capability_registry=registry,capability_profile='commander_review').status)
        legacy=compile_oracle_card(record(text.replace('another','a'),'Creature — Ogre'),capability_registry=registry,capability_profile='commander_review')
        self.assertEqual('exact',legacy.status,legacy.material_residuals)

    def test_cost_owner_excludes_source_and_foreign_control_but_allows_borrowed_permanent(self):
        spec=fixed_effect_payment_spec('sacrifice another creature')
        spec=replace(spec,predicate=replace(spec.predicate,controller='A',exclude_ref='SOURCE'))
        def row(ref,owner,controller):
            return ObjectQueryResult('object:'+ref,ref,ref,owner,controller,'battlefield',
                types=('creature',),logical_object_id='object:'+ref+'@0')
        source=row('SOURCE','A','A');own=row('OWN','A','A')
        borrowed=row('BORROWED','B','A');foreign=row('FOREIGN','A','B')
        query=SnapshotSemanticChoiceQuery(('A','B','C','D'),('A','B','C','D'),object_rows=(source,own,borrowed,foreign))
        context=SemanticChoiceContext('A','S1','A','Another sacrifice','SOURCE','SOURCE','fixture:payment',1,query)
        prepared=prepare_fixed_payment_cost(spec,{'op':'offer_optional_mana_payment'},context)
        self.assertEqual({'OWN','BORROWED'},set(prepared.request.choice.legal_refs))
        paid=complete_fixed_payment_cost(spec,prepared.continuation_effect,{'cards':['BORROWED']},query)
        self.assertEqual(('BORROWED',),paid.intents[0].object_refs)
        self.assertTrue(paid.intents[0].controlled_only);self.assertFalse(paid.intents[0].owned_only)
        self.assertFalse(complete_fixed_payment_cost(spec,prepared.continuation_effect,{'cards':[]},query).intents)
        for invalid in ('SOURCE','FOREIGN'):
            with self.subTest(invalid=invalid),self.assertRaises(SemanticChoiceError):
                complete_fixed_payment_cost(spec,prepared.continuation_effect,{'cards':[invalid]},query)
        changed=replace(query,object_rows=(source,own,replace(borrowed,logical_object_id='object:BORROWED@1'),foreign))
        with self.assertRaises(SemanticChoiceError):
            complete_fixed_payment_cost(spec,prepared.continuation_effect,{'cards':['BORROWED']},changed)
        absent=replace(spec,predicate=replace(spec.predicate,exclude_ref=None))
        reentered=replace(query,object_rows=(replace(source,logical_object_id='object:SOURCE@1'),))
        current=prepare_fixed_payment_cost(absent,{'op':'offer_optional_mana_payment'},replace(context,query=reentered))
        self.assertEqual(('SOURCE',),current.request.choice.legal_refs)

    def test_another_sacrifice_has_closed_versioned_source_exclusion(self):
        spec=fixed_effect_payment_spec('sacrifice another creature')
        self.assertIsNotNone(spec)
        self.assertEqual(('sacrifice',6,1),(spec.kind,spec.schema_version,spec.amount))
        self.assertEqual('$source.zone_object',spec.predicate.exclude_ref)
        self.assertEqual('$controller',spec.predicate.controller)
        self.assertEqual(spec,FixedEffectPaymentSpec.from_dict(spec.to_dict()))
        legacy=fixed_effect_payment_spec('sacrifice a creature')
        self.assertEqual(2,legacy.schema_version);self.assertIsNone(legacy.predicate.exclude_ref)
        with self.assertRaises(ValueError):replace(legacy,predicate=spec.predicate)
        with self.assertRaises(ValueError):replace(spec,kind='discard')
        with self.assertRaises(ValueError):replace(spec,amount=2)
        resolved=replace(spec,predicate=replace(spec.predicate,controller='A',exclude_ref='ROOT'))
        self.assertEqual('ROOT',FixedEffectPaymentSpec.from_dict(resolved.to_dict()).predicate.exclude_ref)
        absent=replace(resolved,predicate=replace(resolved.predicate,exclude_ref=None))
        self.assertIsNone(FixedEffectPaymentSpec.from_dict(absent.to_dict()).predicate.exclude_ref)
