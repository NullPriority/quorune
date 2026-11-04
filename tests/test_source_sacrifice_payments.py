"""CR118.12/201.5/400.7/701.21a: sacrifice the bound original source."""

from dataclasses import replace
from types import SimpleNamespace
import unittest

from quorune.fixed_effect_payment import FixedEffectPaymentSpec
from quorune.object_query import ObjectQueryResult
from quorune.semantic_choices.context import SnapshotSemanticChoiceQuery, SemanticChoiceContext
from quorune.semantic_choices.fixed_effect_payment import prepare_fixed_payment_cost, complete_fixed_payment_cost
from quorune.semantic_choices.model import SemanticChoiceError


class SourceSacrificePaymentOwnerTests(unittest.TestCase):
    def test_runtime_paid_discard_keeps_actor_distinct_from_known_affected_player(self):
        from copy import deepcopy
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        from quorune.semantic_choices.optional_effect import _represented_effect
        from test_granted_skulk import record
        text="Whenever this creature attacks and isn't blocked, you may sacrifice it. If you do, defending player discards three cards."
        ir=compile_oracle_card(record(text,'Creature — Thrull'),capability_registry=load_default_capability_registry())
        self.assertEqual('exact',ir.status,ir.material_residuals)
        effect=deepcopy(ir.faces[0].nodes[0].effects[0]['effects'][0]);effect['actor']='A';effect['players']=['C']
        query=SnapshotSemanticChoiceQuery(tuple('ABCD'),tuple('ABCD'))
        with self.assertRaises(SemanticChoiceError):_represented_effect(effect,actor='A',query=query)
        _represented_effect(effect,actor='A',query=query,allow_event_player_results=True)
        for change in ({'actor':'B'},{'players':['UNKNOWN']},{'players':['C','C']},{'unknown':True}):
            with self.subTest(change=change),self.assertRaises(SemanticChoiceError):
                _represented_effect({**effect,**change},actor='A',query=query,allow_event_player_results=True)

    def test_paid_event_player_result_retains_independent_occurrence_scope(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        from quorune.rules.fixed_effect_payment_shapes import fixed_payment_event_player_runtime_dependencies
        from test_granted_skulk import record
        text="Whenever this creature attacks and isn't blocked, you may sacrifice it. If you do, defending player discards three cards."
        ir=compile_oracle_card(record(text,'Creature — Thrull'),capability_registry=load_default_capability_registry())
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node=ir.faces[0].nodes[0]
        program=SimpleNamespace(event=node.event,event_condition=node.event_condition,active_zone=node.active_zone,
            effects=node.effects,target_schema=node.target_schema,coverage=(*node.mechanics,*node.runtime_coverage))
        self.assertIn('trigger.result.fixed_context_player_discard',fixed_payment_event_player_runtime_dependencies(program))
        for change in ({'event':'spell.cast'},{'event_condition':None},{'coverage':()}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                fixed_payment_event_player_runtime_dependencies(SimpleNamespace(**{**vars(program),**change}))
        siblings=SimpleNamespace(**{**vars(program),'effects':({'op':'draw','player':'$controller','count':1,'private':True},*program.effects)})
        self.assertIn('trigger.result.fixed_context_player_discard',fixed_payment_event_player_runtime_dependencies(siblings))
        with self.assertRaises(ValueError):
            fixed_payment_event_player_runtime_dependencies(SimpleNamespace(**{**vars(siblings),'event':'spell.cast'}))

    def test_source_control_filter_omission_is_killed(self):
        from unittest.mock import patch
        import quorune.semantic_choices.fixed_effect_payment as owner
        original=owner._source_sacrifice_payment_row
        def omit_control(spec,actor,query):
            row=query.object(spec.source_ref,zones=('battlefield',)) if spec.source_ref else None
            return row if row is not None and not row.phased_out else None
        with patch.object(owner,'_source_sacrifice_payment_row',side_effect=omit_control):
            result=unittest.TestResult()
            SourceSacrificePaymentOwnerTests('test_only_the_controlled_original_source_can_pay_and_stale_choices_reject').run(result)
        self.assertFalse(result.errors,result.errors);self.assertTrue(result.failures)

    def test_static_payment_shape_requires_the_canonical_source_reference(self):
        from copy import deepcopy
        from quorune.compiler.fixed_effect_payment_templates import fixed_effect_payment_template
        from quorune.oracle_ir import _reviewed_effect_template
        from quorune.rules.fixed_effect_payment_shapes import fixed_effect_payment_node_capabilities
        compiled=fixed_effect_payment_template('You may sacrifice this creature. If you do, draw a card.',
            compile_effect=lambda text:_reviewed_effect_template(text,card_name='Source'))
        _template,effects,schema,mechanics=compiled
        self.assertIn('cost.sacrifice.fixed_source_resolution',fixed_effect_payment_node_capabilities(
            effects=effects,target_schema=schema,mechanic_ids=mechanics))
        for ref in (None,'OTHER','$controller'):
            changed=deepcopy(effects);changed[0]['payment']['source_ref']=ref
            self.assertFalse(fixed_effect_payment_node_capabilities(effects=changed,target_schema=schema,mechanic_ids=mechanics))

    def test_verified_source_cost_withdrawal_keeps_ordinary_sacrifice(self):
        import json
        from common import ROOT
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from test_granted_skulk import record
        text='Whenever this creature attacks, you may sacrifice it. If you do, draw a card.'
        current=load_default_capability_registry()
        ir=compile_oracle_card(record(text,'Creature — Human'),capability_registry=current)
        self.assertEqual('exact',ir.status,ir.material_residuals)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        next(c for c in raw['capabilities'] if c['id']=='cost.sacrifice.fixed_source_resolution').update(status='blocked',blockers=['Source cost withdrawn'])
        registry=CapabilityRegistry(raw)
        index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        self.assertNotEqual('exact',compile_oracle_card(record(text,'Creature — Human'),capability_registry=registry).status)
        legacy=text.replace('sacrifice it','sacrifice a creature')
        self.assertEqual('exact',compile_oracle_card(record(legacy,'Creature — Human'),capability_registry=registry).status)

    def test_only_if_you_do_factory_opts_into_the_new_source_cost(self):
        from quorune.compiler.fixed_effect_payment_templates import fixed_effect_payment_spec,fixed_effect_payment_template
        from quorune.oracle_ir import _reviewed_effect_template
        self.assertIsNone(fixed_effect_payment_spec('sacrifice this creature'))
        spec=fixed_effect_payment_spec('sacrifice this creature',allow_source_sacrifice=True)
        self.assertEqual(('source_sacrifice',7,'$source.zone_object'),(spec.kind,spec.schema_version,spec.source_ref))
        compiled=fixed_effect_payment_template('You may sacrifice this creature. If you do, draw a card.',
            compile_effect=lambda text:_reviewed_effect_template(text,card_name='Source'))
        self.assertIsNotNone(compiled)
        self.assertEqual(spec.to_dict(),compiled[1][0]['payment'])
        self.assertIsNone(fixed_effect_payment_template('You may sacrifice this creature. When you do, draw a card.',
            compile_effect=lambda text:_reviewed_effect_template(text,card_name='Source')))

    def test_source_pronouns_require_each_subscription_to_prove_the_source(self):
        from quorune.compiler.fixed_counter_trigger_nodes import fixed_counter_trigger_binding,_public_state_source_self_trigger_binding
        from quorune.compiler.source_sacrifice_payments import normalized_source_sacrifice_payment_text
        text='You may sacrifice it. If you do, draw a card.'
        for head in ('Whenever this creature attacks',"Whenever this creature attacks and isn't blocked",
                'Whenever this creature deals combat damage to a player','When this creature enters'):
            binding=(fixed_counter_trigger_binding(head+', draw a card.',card_name='Source')
                or _public_state_source_self_trigger_binding(head+', draw a card.',card_name='Source'))
            self.assertIsNotNone(binding)
            self.assertEqual('You may sacrifice this permanent. If you do, draw a card.',
                normalized_source_sacrifice_payment_text(binding,text,card_name='Source'))
        for head in ('Whenever a creature you control enters','Whenever equipped creature attacks'):
            binding=fixed_counter_trigger_binding(head+', draw a card.',card_name='Source')
            self.assertIsNotNone(binding)
            self.assertEqual(text,normalized_source_sacrifice_payment_text(binding,text,card_name='Source'))
        mixed=SimpleNamespace(event=SimpleNamespace(value='permanent.enter.self'),event_condition={'event_subscriptions':[
            {'event':'permanent.enter.self','condition':None},
            {'event':'creature.enter','condition':{'field':'controller','op':'eq','value':'$source.controller'}}]})
        self.assertEqual(text,normalized_source_sacrifice_payment_text(mixed,text,card_name='Source'))
    def test_source_cost_codec_has_closed_version_reference_and_single_permanent(self):
        spec=FixedEffectPaymentSpec('source_sacrifice',schema_version=7,source_ref='$source.zone_object')
        self.assertEqual(spec,FixedEffectPaymentSpec.from_dict(spec.to_dict()))
        self.assertEqual('$source.zone_object',spec.to_dict()['source_ref'])
        self.assertIn('cost.sacrifice.fixed_source_resolution',spec.capabilities)
        for change in ({'schema_version':2},{'amount':2},{'source_ref':True},{'source_ref':''},{'counter_name':'charge'}):
            with self.subTest(change=change),self.assertRaises(ValueError):replace(spec,**change)
        with self.assertRaises(ValueError):FixedEffectPaymentSpec.from_dict({**spec.to_dict(),'unknown':True})

    def test_only_the_controlled_original_source_can_pay_and_stale_choices_reject(self):
        source=ObjectQueryResult('source-id','SOURCE','Source','B','A','battlefield',
            types=('enchantment',),logical_object_id='source@0')
        other=ObjectQueryResult('other-id','OTHER','Source','A','A','battlefield',
            types=('creature',),logical_object_id='other@0')
        query=SnapshotSemanticChoiceQuery(tuple('ABCD'),tuple('ABCD'),object_rows=(source,other))
        context=SemanticChoiceContext('A','S1','A','Source sacrifice','SOURCE','SOURCE','fixture:source',1,query)
        spec=FixedEffectPaymentSpec('source_sacrifice',schema_version=7,source_ref='SOURCE')
        prepared=prepare_fixed_payment_cost(spec,{'op':'offer_optional_mana_payment'},context)
        self.assertEqual(('SOURCE',),prepared.request.choice.legal_refs)
        paid=complete_fixed_payment_cost(spec,prepared.continuation_effect,{'cards':['SOURCE']},query)
        self.assertEqual(('SOURCE',),paid.intents[0].object_refs)
        self.assertTrue(paid.intents[0].controlled_only);self.assertFalse(paid.intents[0].owned_only)
        self.assertEqual('sacrifice',paid.intents[0].transition_kind.value)
        self.assertFalse(complete_fixed_payment_cost(spec,prepared.continuation_effect,{'cards':[]},query).intents)
        with self.assertRaises(SemanticChoiceError):
            complete_fixed_payment_cost(spec,prepared.continuation_effect,{'cards':['OTHER']},query)
        for row in (replace(source,controller='B'),replace(source,phased_out=True),replace(source,logical_object_id='source@1')):
            changed=replace(query,object_rows=(row,other))
            with self.subTest(row=row),self.assertRaises(SemanticChoiceError):
                complete_fixed_payment_cost(spec,prepared.continuation_effect,{'cards':['SOURCE']},changed)
        for row in (replace(source,controller='B'),replace(source,phased_out=True)):
            prepared=prepare_fixed_payment_cost(spec,{'op':'offer_optional_mana_payment'},replace(context,query=replace(query,object_rows=(row,other))))
            self.assertEqual((),prepared.request.choice.legal_refs)

    def test_returned_source_never_replaces_the_old_source_cost(self):
        from quorune.semantic_runtime.values import resolve_semantic_value
        symbolic=FixedEffectPaymentSpec('source_sacrifice',schema_version=7,source_ref='$source.zone_object')
        item=SimpleNamespace(controller='A',source_object_id='source-id',context={'source_logical_object_id':'source@0'})
        current=SimpleNamespace(ref='SOURCE',zone='battlefield',phased_out=False,logical_object_id='source@1')
        host=SimpleNamespace(state=SimpleNamespace(cards={'source-id':current}))
        bound=FixedEffectPaymentSpec.from_dict(resolve_semantic_value(host,symbolic.to_dict(),item))
        self.assertIsNone(bound.source_ref)
        row=ObjectQueryResult('source-id','SOURCE','Source','A','A','battlefield',types=('creature',),logical_object_id='source@1')
        query=SnapshotSemanticChoiceQuery(tuple('ABCD'),tuple('ABCD'),object_rows=(row,))
        context=SemanticChoiceContext('A','S1','A','Old source sacrifice','SOURCE','SOURCE','fixture:source',1,query)
        prepared=prepare_fixed_payment_cost(bound,{'op':'offer_optional_mana_payment'},context)
        self.assertFalse(prepared.request.public_context['payable'])
        self.assertEqual((),prepared.request.choice.legal_refs)
