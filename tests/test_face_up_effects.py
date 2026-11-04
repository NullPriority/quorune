"""Closed targeted face-up effects preserve target authority and physical status."""
from dataclasses import replace
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from common import ROOT
from test_qualified_zone_event_queries import query_record
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry


class FaceUpEffectCompilerTests(unittest.TestCase):
    def test_targeted_face_up_has_exact_face_status_and_controller_scope(self):
        text='Turn target face-down creature an opponent controls face up.'
        registry=load_default_capability_registry()
        record=replace(query_record(text),type_line='Instant')
        ir=compile_oracle_card(record,capability_registry=registry)
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node=next(row for row in ir.faces[0].nodes if row.kind=='spell_ability')
        self.assertTrue(node.target_schema['face_down'])
        self.assertEqual('opponent',node.target_schema['controller_relation'])
        self.assertEqual(1,node.target_schema['count'])
        self.assertIn('permanent.face_up.effect',node.capability_dependencies)
        self.assertEqual(({'op':'turn_face_up','schema_version':1,'card':'$target.0'},),node.effects)
        for bad in ('Turn target creature face down.','Turn any number of face-down creatures face up.',
                    text+' Invent an unknown result.'):
            self.assertNotEqual('exact',compile_oracle_card(replace(record,oracle_text=bad),capability_registry=registry).status,bad)

    def test_targeted_face_up_withdrawn_dependencies_and_producer_remain_residual(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        record=replace(query_record('Turn target face-down creature an opponent controls face up.'),type_line='Instant')
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        discovered=set(discover_tests(ROOT).values())
        for capability in ('permanent.face_up.effect','target.revalidate_resolution'):
            changed=deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id']==capability).update(
                status='blocked',blockers=['Face-up dependency withdrawn'])
            registry=CapabilityRegistry(changed)
            index=build_index(registry_value=changed,declaration_source=declarations,discovered_test_ids=discovered)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            with self.subTest(capability=capability):
                self.assertNotEqual('exact',compile_oracle_card(record,capability_registry=registry).status)
                self.assertEqual('exact',compile_oracle_card(replace(query_record('Draw a card.'),type_line='Instant'),capability_registry=registry).status)
        with patch('quorune.compiler.face_up_effect_templates.targeted_face_up_template',return_value=None):
            with self.assertRaises(AssertionError):
                self.test_targeted_face_up_has_exact_face_status_and_controller_scope()

    def test_face_up_instruction_lowering_rejects_unknown_or_forged_fields(self):
        from quorune.face_up_effect_model import face_up_instruction,face_up_node_capabilities,face_up_target_schema
        from quorune.semantic_runtime.context import ReadOnlyHandlerContext,SemanticNodeError
        from quorune.semantic_runtime.effect_handlers import DOMAIN_EFFECT_HANDLERS
        context=ReadOnlyHandlerContext.from_sequences(actor='B',default_reason='closed face-up instruction',
            seats=('A','B'),active_seats=('A','B'),apnap_order=('A','B'))
        handler=next(row for row in DOMAIN_EFFECT_HANDLERS if row.operation=='turn_face_up')
        valid=face_up_instruction('P1')
        plan=handler.lower(valid,context)
        self.assertEqual(valid,dict(plan.intents[0].effect))
        for changed in ({**valid,'schema_version':True},{**valid,'schema_version':2},
                {**valid,'card':False},{**valid,'card':''},{**valid,'card':' P1'},
                {**valid,'reason':''},{**valid,'count':2},{**valid,'controller':'A'},
                {**valid,'_runtime_source':{'object_id':'forged'}}):
            with self.subTest(changed=changed),self.assertRaises(SemanticNodeError):handler.lower(changed,context)
        for effect,target in (({**face_up_instruction(),'schema_version':True},face_up_target_schema('opponent')),
                (face_up_instruction(),{**face_up_target_schema('opponent'),'count':True}),
                (face_up_instruction(),{**face_up_target_schema('opponent'),'face_down':1})):
            self.assertEqual((),face_up_node_capabilities(effects=(effect,),target_schema=target,
                mechanics={'fixed-target-face-up','cr-115-targets'}))
        self.assertFalse(hasattr(context,'state'))
